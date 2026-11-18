#!/usr/bin/env python3
r"""用 chiptool 生成两仓库的 data/registers/。

流程：
1. 对每个 SVD 跑 `chiptool extract-all`（老代 SVD 缺 <addressBlock><usage>，
   自动修补副本到 build/svd_fixed/，原始 SVD 不动），原始 IR 输出到 build/chiptool/；
2. 按 docs/peripheral-version-map.json 的已确认映射生成规范命名 YAML；
   校验实例、布局与来源摘要，未知布局报错，不再按遍历顺序编号；
3. 产 data/csv/register_versions.csv（series,instance,kind,version,block），
   供 csv_to_yaml.py 生成 family YAML 的 registers 引用；derivedFrom 外设继承
   父实例的版本。

依赖：chiptool（cargo install --path 本地克隆，GitHub 直连受限时先 git clone）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

import support_scope

WS = Path(__file__).resolve().parents[2]
BUILD = WS / "build"

VER_FIELDS = ["series", "instance", "kind", "version", "block"]


def tag(e: ET.Element) -> str:
    return e.tag.split("}")[-1]


def svd_peripherals(svd: Path) -> list[tuple[str, str | None]]:
    """[(外设名, derivedFrom 父名或 None)]。"""
    root = ET.parse(svd).getroot()
    out = []
    for p in root.iter():
        if tag(p) == "peripheral":
            name = next((c.text or "").strip() for c in p if tag(c) == "name")
            parent = p.attrib.get("derivedFrom")
            out.append((name, parent))
    return out


def resolve_derived(pairs: list[tuple[str, str | None]]) -> dict[str, str]:
    parent = dict(pairs)
    resolved = {}
    for name, _ in pairs:
        seen = set()
        cur = name
        while parent.get(cur) and cur not in seen:
            seen.add(cur)
            cur = parent[cur]
        resolved[name] = cur
    return resolved


def fix_svd(svd: Path, fixed_dir: Path) -> Path:
    """老代 SVD 的 <addressBlock> 缺 <usage>，复制补上（不动原始文件）。"""
    fixed_dir.mkdir(parents=True, exist_ok=True)
    text = svd.read_text(encoding="utf-8")
    if "<usage>" not in text.split("<addressBlock>")[1].split("</addressBlock>")[0]:
        text = text.replace(
            "</addressBlock>",
            "<usage>registers</usage>\n            </addressBlock>")
    out = fixed_dir / f"{svd.parent.parent.name}_{svd.name}"
    out.write_text(text, encoding="utf-8")
    return out


def extract(svd: Path, out_dir: Path, fixed_dir: Path) -> bool:
    out_dir.mkdir(parents=True, exist_ok=True)
    use_svd = svd
    if "<usage>" not in svd.read_text(encoding="utf-8"):
        use_svd = fix_svd(svd, fixed_dir)
    proc = subprocess.run(
        ["chiptool", "extract-all", "--svd", str(use_svd), "--output", str(out_dir)],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        print(f"FAIL {svd}: {(proc.stderr or proc.stdout)[:300]}", file=sys.stderr)
        return False
    return True


def norm_name(base: str) -> str:
    return base.replace("[%s]", "")


def expand_reg(item: dict) -> set[tuple]:
    """寄存器项展开为 (展开名, 偏移, bit_size, access, fieldset)。"""
    arr = item.get("array")
    out = set()
    if isinstance(arr, dict) and "len" in arr:
        for i in range(arr["len"]):
            out.add((f"{norm_name(item['name'])}{i}",
                     item["byte_offset"] + i * arr["stride"],
                     item.get("bit_size", 32), item.get("access", "read-write"),
                     item.get("fieldset")))
    elif isinstance(arr, dict) and "offsets" in arr:
        for i, off in enumerate(arr["offsets"]):
            out.add((f"{norm_name(item['name'])}{i}", off,
                     item.get("bit_size", 32), item.get("access", "read-write"),
                     item.get("fieldset")))
    else:
        out.add((item["name"], item["byte_offset"], item.get("bit_size", 32),
                 item.get("access", "read-write"), item.get("fieldset")))
    return out


def expand_field(fld: dict) -> set[tuple]:
    """字段展开为 (展开名, 位偏移, 位宽, enum)。"""
    arr = fld.get("array")
    out = set()
    enum = fld.get("enum")
    if isinstance(arr, dict) and "len" in arr:
        for i in range(arr["len"]):
            out.add((f"{norm_name(fld['name'])}{i}",
                     fld["bit_offset"] + i * arr["stride"],
                     fld.get("bit_size", 1), enum))
    elif isinstance(arr, dict) and "offsets" in arr:
        # Cursed 位域：直接取每个 offset
        for i, off in enumerate(arr["offsets"]):
            out.add((f"{norm_name(fld['name'])}{i}", off,
                     fld.get("bit_size", 1), enum))
    else:
        bo = fld.get("bit_offset")
        out.add((fld["name"], bo if isinstance(bo, int) else 0,
                 fld.get("bit_size", 1), enum))
    return out


def signature(ir: dict) -> tuple:
    """布局签名：块内容 + fieldset 内容 + enum 内容（展开数组后）。"""
    parts = []
    for k in sorted(ir):
        v = ir[k]
        if k.startswith("block/"):
            items: set[tuple] = set()
            for it in v.get("items", []):
                items |= expand_reg(it)
            parts.append(("B", k, tuple(sorted(items))))
        elif k.startswith("fieldset/"):
            fields: set[tuple] = set()
            for f in v.get("fields", []):
                fields |= expand_field(f)
            parts.append(("F", k, v.get("bit_size", 32), tuple(sorted(fields))))
        elif k.startswith("enum/"):
            variants = tuple(sorted((x.get("name", ""),
                                     x.get("value", 0))
                                    for x in v.get("variants", [])))
            parts.append(("E", k, v.get("bit_size", 32), variants))
    return tuple(parts)


def load_series_ir(raw_dir: Path) -> dict[str, dict]:
    """{instance: IR}（实例名 = 文件名主段）。"""
    out = {}
    for f in sorted(raw_dir.glob("*.yaml")):
        ir = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        inst = re.split(r"__|\.", f.stem)[0]
        out[inst] = ir
    return out


def rename_block(ir: dict, kind: str) -> dict:
    """把顶层外设块重命名为 block/<KIND>，其余键保留。"""
    out = {}
    top_key = None
    for k, v in ir.items():
        if k.startswith("block/"):
            # 外设主块：与 fieldset 不同，取第一个 block 键（extract-all 每文件
            # 以单个外设为主块；inner cluster block 名不动）
            if top_key is None:
                top_key = k
                out[f"block/{kind.upper()}"] = v
            else:
                out[k] = v
        else:
            out[k] = v
    return out


def render_layout(ir: dict, rep: tuple[str, str], cover: str) -> str:
    """布局 YAML 的最终文本：文件头 + 排版后的 IR（digest 校验与写出共用）。"""
    header = (f"# 由 chiptool extract-all 生成（hc32-data-tools/chiptool_registers.py）。\n"
              f"# 布局代表：{rep[0]}/{rep[1]}；覆盖系列：{cover}。\n")
    return header + yaml.dump(ir, allow_unicode=True, sort_keys=False,
                              default_flow_style=False, width=100)


def process_repo(repo: Path, work: Path, fixed: Path) -> dict:
    svd_dir = repo / "svd"
    out_reg = repo / "data" / "registers"
    out_reg.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    side_of_repo = repo.name.removeprefix("hc32-").removesuffix("-data")
    series_ir: dict[str, dict[str, dict]] = {}
    series_derived: dict[str, dict[str, str]] = {}
    for svd in sorted(svd_dir.glob("*.svd")):
        s = svd.stem
        skip = support_scope.unsupported(side_of_repo).get(s)
        if skip:
            print(f"SKIP {s}: {skip}")
            continue
        raw = work / repo.name / s
        if not extract(svd, raw, fixed):
            continue
        series_ir[s] = load_series_ir(raw)
        series_derived[s] = resolve_derived(svd_peripherals(svd))

    # Approved manifest is authoritative: never allocate numbers from traversal order.
    manifest_path = repo / "docs" / "peripheral-version-map.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest["stage"] != "active":
        raise ValueError("Activate the reviewed peripheral version map before generation")
    side = manifest["side"]
    expected = {(s, inst) for s, parents in series_derived.items()
                for inst, parent in parents.items() if parent in series_ir.get(s, {})}
    covered = set()
    outputs = {}
    for layout in manifest["layouts"]:
        members = [(s, inst) for s, names in layout["instances"].items() for inst in names]
        candidates = set()
        for key in members:
            if key not in expected or key in covered:
                raise ValueError(f"Missing/duplicate reviewed instance: {key}")
            covered.add(key)
            s, inst = key
            candidates.add((s, series_derived[s][inst]))
        s, inst = min(candidates)
        block = layout["block"]
        ir = rename_block(series_ir[s][inst], block)
        sig = signature(ir)
        for ss, ii in candidates:
            if signature(rename_block(series_ir[ss][ii], block)) != sig:
                raise ValueError(f"Layout drift: {layout['source']} / {ss}/{ii}; review mapping")
        cover = ", ".join(sorted({ss for ss, _ in members}))
        content = render_layout(ir, (s, inst), cover)
        if hashlib.sha256(content.encode()).hexdigest() != layout["source_sha256"]:
            raise ValueError(f"Source changed for {layout['source']}; review manifest digest")
        if layout["source"] in outputs:
            raise ValueError(f"Duplicate output: {layout['source']}")
        outputs[layout["source"]] = content
        if layout["status"] == "excluded":
            # 已按规范名激活的布局保持原名（<kind>_<side>_vN → kind=<kind>,
            # version=<side>_vN），避免排除时连带动文件与 PAC 模块改名；
            # 原始名排除项（如 dcu_v1、dmc_common）仍按最后一个下划线拆分。
            m = re.search(r"_(?:ddl|ll)_", layout["source"])
            if m:
                kind = layout["source"][:m.start()]
                version = layout["source"][m.start() + 1:]
            else:
                kind, version = layout["source"].rsplit("_", 1)
        else:
            kind, version = layout["kind"], f"{side}_{layout['version']}"
        for ss, ii in members:
            rows.append({"series": ss, "instance": ii, "kind": kind,
                         "version": version, "block": block})
    if expected != covered:
        raise ValueError(f"Unreviewed instances: {sorted(expected - covered)}")
    # All input validation precedes mutations; remove only obsolete register YAMLs.
    for old in out_reg.glob("*.yaml"):
        if old.stem not in outputs:
            old.unlink()
    for stem, content in outputs.items():
        (out_reg / f"{stem}.yaml").write_text(content, encoding="utf-8")
    n_files = len(outputs)
    rows.sort(key=lambda r: (r["series"], r["instance"]))
    with open(repo / "data" / "csv" / "register_versions.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=VER_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{repo.name}: registers {n_files} 文件，实例映射 {len(rows)} 行",
          file=sys.stderr)
    return {"registers": n_files, "instances": len(rows)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", default=[],
                    help="数据仓库目录（可多次）；默认两个")
    args = ap.parse_args()
    repos = [Path(r) for r in args.repo] or [WS / "hc32-ddl-data", WS / "hc32-ll-data"]
    work = BUILD / "chiptool"
    fixed = BUILD / "svd_fixed"
    for repo in repos:
        process_repo(repo, work, fixed)


if __name__ == "__main__":
    main()
