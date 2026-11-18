#!/usr/bin/env python3
r"""把 data/csv/ 下的提取结果转换成 ch32-data 风格的 YAML 目录。

输入（<repo>/data/csv/）：
  chips.csv / website_chips.csv   型号主表（DS 行优先，website 行补缺）
  interrupts.csv                  中断向量
  dma.csv                         DMA 触发源（DDL: channel=TRI_SEL 索引；
                                  LL: 5 列带 trigger_id=AOS 事件号）
  peripherals.csv                 外设实例（地址 + FCG 门控）
  memory.csv                      Flash/SRAM 基址
  registers.csv                   寄存器/字段行（svd_to_registers_csv.py 产出）
  svd_meta.csv                    SVD CPU 标签

输出（<repo>/data/）：
  chips/<PART>.yaml               每个订货型号一个文件
  family/<series>.yaml            系列全部外设实例（含寄存器 kind/version 引用）
  interrupts/<series>.yaml        IRQ 号表
  dma/<series>_<DMA>.yaml         请求→触发源映射
  memory/<series>_F<n>S<n>.yaml   每种 Flash/SRAM 组合一个内存布局
  registers/<kind>_<version>.yaml 寄存器块（block/… + fieldset/… 格式）

用法：
  python3 scripts/csv_to_yaml.py                        # 两个仓库都转换
  python3 scripts/csv_to_yaml.py --repo hc32-ll-data    # 只转一个
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

# ---------- 常量表 ----------

# website 系列（单片型号名）→ 数据手册/SDK 系列名（x 为通配）
SERIES_ALIAS = {
    "HC32F170": "HC32F17x", "HC32F176": "HC32F17x",
    "HC32F190": "HC32F19x", "HC32F196": "HC32F19x",
    "HC32L072": "HC32L07x", "HC32L073": "HC32L07x",
    "HC32L130": "HC32L13x", "HC32L136": "HC32L13x",
    "HC32L160": "HC32L16x", "HC32L166": "HC32L16x",
    "HC32L170": "HC32L17x", "HC32L176": "HC32L17x",
    "HC32L180": "HC32L18x", "HC32L186": "HC32L18x",
    "HC32L190": "HC32L19x", "HC32L196": "HC32L19x",
}

CPU_MAP = {"CM4": "cortex-m4", "CM0+": "cortex-m0plus", "CM0P": "cortex-m0plus",
           "CM0": "cortex-m0", "CM3": "cortex-m3", "CM7": "cortex-m7"}

WEBSITE_BASE = "https://www.xhsc.com.cn"


class HexInt(int):
    """YAML 输出为 0x 十六进制的整数。"""


def _hex_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:int", f"0x{data:X}")


yaml.add_representer(HexInt, _hex_representer)


def dump_yaml(data) -> str:
    return yaml.dump(data, allow_unicode=True, sort_keys=False,
                     default_flow_style=False, width=100)


# ---------- CSV 读取 ----------

def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


PRUNED: list[str] = []


def prune(out_dir: Path, keep: set[str]) -> None:
    """删除不再由当前 CSV 产出的陈旧 YAML（与 chiptool_registers.py 同规则）。"""
    for old in sorted(out_dir.glob("*.yaml")):
        if old.name not in keep:
            old.unlink()
            PRUNED.append(f"{out_dir.parent.name}/{old.name}")


def read_ctx(repo: Path) -> dict:
    c = repo / "data" / "csv"
    ctx = {name: read_csv(c / f"{name}.csv")
           for name in ("chips", "website_chips", "interrupts", "dma",
                        "peripherals", "memory", "register_versions", "svd_meta")}
    return ctx


def map_series(name: str) -> str:
    return SERIES_ALIAS.get(name, name)


def series_from_part(part: str, repo_series: set[str]) -> str | None:
    """按型号前缀在仓库系列集合里找系列（支持 x 通配）。"""
    part_u = part.upper()
    for s in sorted(repo_series, key=len, reverse=True):
        rx = re.escape(s.upper()).replace(r"x", ".")
        if re.match(rx, part_u):
            return s
    return None


# ---------- 各输出段 ----------


def emit_interrupts(repo: Path, ctx: dict) -> int:
    out_dir = repo / "data" / "interrupts"
    out_dir.mkdir(parents=True, exist_ok=True)
    by_series: dict[str, list[dict]] = defaultdict(list)
    for r in ctx["interrupts"]:
        by_series[r["series"]].append(r)
    n = 0
    for s, rows in sorted(by_series.items()):
        lines = [f"# {s} 中断向量（来自设备头 IRQn_Type 枚举，hc32-data-tools/csv_to_yaml.py 生成）"]
        body = {}
        for r in sorted(rows, key=lambda r: int(r["irq"] or 0)):
            irq = int(r["irq"])
            if irq < 0:
                continue  # 内核中断（NMI/HardFault 等）不进向量表
            body[r["name"]] = irq
        if not body:
            continue
        for name, irq in body.items():
            lines.append(f"{name}: {irq}")
        (out_dir / f"{s}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        n += 1
    if by_series:
        prune(out_dir, {f"{s}.yaml" for s in by_series})
    return n


def emit_dma(repo: Path, ctx: dict) -> int:
    out_dir = repo / "data" / "dma"
    out_dir.mkdir(parents=True, exist_ok=True)
    is_ll = "trigger_id" in (ctx["dma"][0] if ctx["dma"] else {})
    by_group: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in ctx["dma"]:
        by_group[(r["series"], r["dma"])].append(r)
    n = 0
    for (s, dma), rows in sorted(by_group.items()):
        if is_ll:
            header = (f"# {s} {dma} DMA 触发源（AOS 路由语义）。\n"
                      f"# 值 = 设备头 EVT_SRC_* 事件号，写入 AOS->DMAx_TRGSELy；\n"
                      f"# 任意请求可路由到任意通道（channel 列为可选范围，见 EXTRACTION_NOTES）。\n")
        else:
            header = (f"# {s} {dma} DMA 触发源（老 DDL TRI_SEL 语义）。\n"
                      f"# 值 = DMAC_CONFAx.TRI_SEL 全局触发源索引；\n"
                      f"# 请求与物理通道非固定连线，运行时软件路由。\n")
        body = {}
        for r in rows:
            val = (r.get("trigger_id") or r.get("channel") or "").strip()
            if val and val not in ("-",):
                body[r["request"]] = int(val)
        if not body:
            continue
        lines = header.splitlines() + [f"{k}: {v}" for k, v in sorted(body.items())]
        (out_dir / f"{s}_{dma}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        n += 1
    if by_group:
        prune(out_dir, {f"{s}_{dma}.yaml" for s, dma in by_group})
    return n


def emit_memory(repo: Path, ctx: dict) -> dict[tuple[str, str, str], str]:
    out_dir = repo / "data" / "memory"
    out_dir.mkdir(parents=True, exist_ok=True)
    mem_base = {r["series"]: r for r in ctx["memory"]}
    combos: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for r in ctx["chips"]:
        f, s = r.get("flash_kb", "").strip(), r.get("sram_kb", "").strip()
        if f and s:
            combos[(r["series"], f, s)].append(r["part_no"])
    files: dict[tuple[str, str, str], str] = {}
    for (s, f, sz) in sorted(combos):
        base = mem_base.get(s)
        if base is None:
            continue
        fname = f"{s}_F{f}S{sz}.yaml"
        doc = {"include_memory": None, "memory": []}
        regions = []
        fb = (base.get("flash_base") or "").strip()
        sb = (base.get("sram_base") or "").strip()
        if fb:
            regions.append({
                "name": "FLASH", "kind": "flash",
                "address": HexInt(int(fb, 16)), "size": f"{f}K",
                "access": {"read": True, "write": True, "execute": True},
            })
        if sb:
            regions.append({
                "name": "SRAM", "kind": "ram",
                "address": HexInt(int(sb, 16)), "size": f"{sz}K",
                "access": {"read": True, "write": True, "execute": True},
            })
        if not regions:
            continue
        doc = {"memory": regions}
        body = (f"# {s} Flash {f}K / SRAM {sz}K 的内存布局"
                f"（型号：{', '.join(sorted(combos[(s, f, sz)])[:4])}…）。\n"
                f"# 基址来自设备头/链接脚本（见 data/csv/memory.csv notes）。\n")
        (out_dir / fname).write_text(body + dump_yaml(doc), encoding="utf-8")
        files[(s, f, sz)] = fname
    if combos:
        prune(out_dir, set(files.values()))
    return files


def emit_family(repo: Path, ctx: dict, inst_ver: dict) -> dict[str, int]:
    out_dir = repo / "data" / "family"
    out_dir.mkdir(parents=True, exist_ok=True)
    by_series: dict[str, list[dict]] = defaultdict(list)
    for r in ctx["peripherals"]:
        by_series[r["series"]].append(r)
    stats = {}
    for s, rows in sorted(by_series.items()):
        items = []
        for r in sorted(rows, key=lambda r: r["instance"]):
            inst = r["instance"]
            ver = inst_ver.get((s, inst))
            if ver is None:
                # register_versions.csv 缺该实例（罕见：SVD 外设无寄存器块）
                ver = {"kind": r["kind"], "version": "", "block": r["kind"].upper()}
            item = {"name": inst}
            try:
                item["address"] = HexInt(int(r["base_address"], 16))
            except (ValueError, KeyError):
                pass
            item["registers"] = {"kind": ver["kind"], "block": ver["block"]}
            if ver["version"]:
                item["registers"]["version"] = ver["version"]
            rcc = {}
            if (r.get("bus") or "").strip():
                rcc["bus_clock"] = r["bus"].strip()
            if (r.get("fcg_reg") or "").strip():
                rcc["enable"] = {
                    "register": r["fcg_reg"].strip(),
                    "field": (r.get("fcg_field") or "").strip(),
                }
            if rcc:
                item["rcc"] = rcc
            ints = [x.strip() for x in (r.get("interrupts") or "").split(";") if x.strip()]
            if ints:
                item["interrupts"] = [{"signal": x, "interrupt": x} for x in ints]
            items.append(item)
        header = (f"# {s} 系列外设实例（hc32-data-tools/csv_to_yaml.py 生成）。\n"
                  f"# 地址来自 SVD；时钟门控来自设备头 FCG/PWC 位定义；\n"
                  f"# 引脚复用（pins）暂缺，待从数据手册 GPIO 章节补齐。\n")
        (out_dir / f"{s}.yaml").write_text(header + dump_yaml(items), encoding="utf-8")
        stats[s] = len(items)
    if by_series:
        prune(out_dir, {f"{s}.yaml" for s in by_series})
    return stats


def emit_chips(repo: Path, ctx: dict, mem_files: dict) -> list[Path]:
    out_dir = repo / "data" / "chips"
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_series = {r["series"] for r in ctx["peripherals"]}
    cpu_by_series = {r["series"]: r.get("cpu", "") for r in ctx["svd_meta"]}
    mem_base_series = {r["series"] for r in ctx["memory"]}

    # 合并 website 行：DS 优先，website 补缺字段、补缺型号
    web_by_part: dict[str, dict] = {}
    for r in ctx["website_chips"]:
        web_by_part.setdefault(r["part_no"], r)
    merged: dict[str, dict] = {}
    for r in ctx["chips"]:
        part = r["part_no"].strip()
        if not part or not re.match(r"^HC", part):
            continue  # 跳过占位/空行
        merged[part] = dict(r)
    for part, wrow in web_by_part.items():
        s = map_series(wrow["series"])
        if part in merged:
            d = merged[part]
            for k, v in wrow.items():
                if not (d.get(k) or "").strip() and v:
                    d[k] = v
        else:
            # website 独有型号：系列映射到仓库已有的系列才收
            if s in repo_series:
                wrow = dict(wrow, series=s)
                merged[part] = wrow

    written = []
    for part, row in sorted(merged.items()):
        s = row["series"]
        if s not in repo_series:
            s2 = series_from_part(part, repo_series)
            if s2:
                s = row["series"] = s2
            else:
                continue
        pkg = (row.get("package") or "").strip()
        doc = {
            "name": part,
            "family": s,
            "packages": [{"name": part, "package": pkg}] if pkg else [],
        }
        f, sz = (row.get("flash_kb") or "").strip(), (row.get("sram_kb") or "").strip()
        if (s, f, sz) in mem_files and s in mem_base_series:
            doc["include_memory"] = f"../memory/{mem_files[(s, f, sz)]}"
            doc["memory_sizes"] = {"FLASH": f"{f}K", "SRAM": f"{sz}K"}
        url = (row.get("url") or "").strip()
        doc["docs"] = [{
            "type": "homepage", "title": "Homepage", "name": "",
            "url": url or WEBSITE_BASE,
        }]
        core = CPU_MAP.get((cpu_by_series.get(s) or "").upper(), "")
        if not core:
            continue
        core_dict = {
            "name": core,
            "arch": "arm",
            "peripherals": [],
            "include_peripherals": [f"../family/{s}.yaml"],
        }
        if (repo / "data" / "interrupts" / f"{s}.yaml").exists():
            core_dict["include_interrupts"] = f"../interrupts/{s}.yaml"
        dma_files = sorted((repo / "data" / "dma").glob(f"{s}_*.yaml"))
        if dma_files:
            core_dict["include_dma_channels"] = {
                p.stem.split(f"{s}_", 1)[1]: f"../dma/{p.name}" for p in dma_files
            }
        doc["cores"] = [core_dict]
        raw = {k: (v if v else "") for k, v in row.items() if k != "part_no"}
        raw["Part NO."] = part
        doc["_raw"] = raw
        text = (f"# {part}（hc32-data-tools/csv_to_yaml.py 生成，数据来源见 _raw.source）\n"
                + dump_yaml(doc))
        fp = out_dir / f"{part}.yaml"
        fp.write_text(text, encoding="utf-8")
        written.append(fp)
    if merged:
        prune(out_dir, {p.name for p in written})
    return written


# ---------- 主流程 ----------

def convert_repo(repo: Path) -> dict:
    PRUNED.clear()
    ctx = read_ctx(repo)
    if not ctx["chips"]:
        raise SystemExit(f"{repo}: 缺少 data/csv/chips.csv")
    # registers 由 chiptool_registers.py 生成，这里只读它的实例→版本映射
    inst_ver = {(r["series"], r["instance"]):
                {"kind": r["kind"], "version": r["version"], "block": r["block"]}
                for r in ctx["register_versions"]}
    stats = {}
    stats["interrupts"] = emit_interrupts(repo, ctx)
    stats["dma"] = emit_dma(repo, ctx)
    mem_files = emit_memory(repo, ctx)
    stats["memory"] = len(mem_files)
    stats["family"] = emit_family(repo, ctx, inst_ver)
    chips = emit_chips(repo, ctx, mem_files)
    stats["chips"] = len(chips)
    return stats


def validate(repo: Path) -> list[str]:
    """逐个 yaml.safe_load 校验输出。"""
    errors = []
    n = 0
    for sub in ("chips", "family", "interrupts", "dma", "memory", "registers"):
        for p in sorted((repo / "data" / sub).glob("*.yaml")):
            n += 1
            try:
                yaml.safe_load(p.read_text(encoding="utf-8"))
            except yaml.YAMLError as e:
                errors.append(f"{p}: {e}")
    print(f"  校验 {n} 个 YAML：{'全部通过' if not errors else f'{len(errors)} 个失败'}")
    for e in errors[:10]:
        print("   ", e)
    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", default=[],
                    help="数据仓库目录（可多次）；默认 hc32-ddl-data 与 hc32-ll-data")
    args = ap.parse_args()
    ws = Path(__file__).resolve().parents[2]
    repos = [Path(r) for r in args.repo] or [ws / "hc32-ddl-data", ws / "hc32-ll-data"]
    for repo in repos:
        print(f"== {repo.name} ==")
        stats = convert_repo(repo)
        print(f"  chips: {stats['chips']} 个型号")
        print(f"  memory: {stats['memory']} 个布局")
        print(f"  interrupts: {stats['interrupts']} 个系列，dma: {stats['dma']} 个文件")
        fam = stats["family"]
        print(f"  family: {len(fam)} 个系列，外设实例合计 {sum(fam.values())}")
        print("  （registers/ 由 chiptool_registers.py 生成，此处不重写）")
        if PRUNED:
            print(f"  清理陈旧文件 {len(PRUNED)} 个：", ", ".join(PRUNED[:8]),
                  "…" if len(PRUNED) > 8 else "")
        validate(repo)


if __name__ == "__main__":
    main()
