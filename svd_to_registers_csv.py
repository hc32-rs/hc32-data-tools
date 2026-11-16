#!/usr/bin/env python3
r"""把两个数据仓库的 SVD 解析成 registers.csv（寄存器/字段行）+ svd_meta.csv。

registers.csv 列（与 hc32-data-tools/CSV_SCHEMA.md 风格一致）：
  series,kind,register,offset,description,field,bit_offset,bit_size,access,field_description

- 一行 = 一个字段；无字段的寄存器也产出一行（field 列留空）。
- kind = 外设名去数字后缀小写（USART1 → usart，GPIOA → gpio）。
- derivedFrom 外设展开继承寄存器；<dim> 寄存器数组展开为 CHn 形式。
- offset 十进制（字节）。

svd_meta.csv 列：
  series,cpu,device_name,peripheral_count
供 csv_to_yaml.py 生成芯片 YAML 的 cores 段。

用法：
  python3 svd_to_registers_csv.py                # 处理两个仓库
  python3 svd_to_registers_csv.py --repo hc32-ll-data
"""
from __future__ import annotations

import argparse
import csv
import re
import xml.etree.ElementTree as ET
from pathlib import Path

REG_FIELDS = [
    "series", "instance", "kind", "register", "offset", "description",
    "field", "bit_offset", "bit_size", "access", "field_description",
]
META_FIELDS = ["series", "cpu", "device_name", "peripheral_count"]


def tag(e: ET.Element) -> str:
    return e.tag.split("}")[-1]


def find(e: ET.Element, name: str) -> ET.Element | None:
    for c in e:
        if tag(c) == name:
            return c
    return None


def findtext(e: ET.Element, name: str) -> str:
    c = find(e, name)
    return (c.text or "").strip() if c is not None else ""


def kind_of(periph_name: str) -> str:
    """USART1 → usart；GPIOA → gpio；TIMA0 → tima。"""
    base = re.match(r"[A-Za-z]+", periph_name)
    return base.group(0).lower() if base else periph_name.lower()


def parse_int(s: str) -> int:
    s = s.strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    m = re.match(r"^(0[xXbB])?[0-9a-fA-F+#]*", s)
    return int(s, 0)


def bit_range(field: ET.Element) -> tuple[int, int]:
    """返回 (bit_offset, bit_size)。"""
    lsb = find(field, "lsb")
    msb = find(field, "msb")
    if lsb is not None and msb is not None:
        lo, hi = parse_int(lsb.text or "0"), parse_int(msb.text or "0")
        return lo, hi - lo + 1
    bo = find(field, "bitOffset")
    if bo is not None:
        bw = find(field, "bitWidth")
        lo = parse_int(bo.text or "0")
        size = parse_int(bw.text or "1") if bw is not None else 1
        return lo, size
    br = findtext(field, "bitRange")
    m = re.match(r"\[(\d+):(\d+)\]", br)
    if m:
        hi, lo = int(m.group(1)), int(m.group(2))
        return lo, hi - lo + 1
    return 0, 0


def expand_dim(name: str, elem: ET.Element) -> list[tuple[str, int]]:
    """处理 <dim>/<dimIncrement>/<dimIndex>，返回 [(名字, 额外偏移)]。"""
    dim = findtext(elem, "dim")
    if not dim:
        return [(name, 0)]
    inc = parse_int(findtext(elem, "dimIncrement") or "4")
    idx_txt = findtext(elem, "dimIndex")
    if idx_txt:
        if "-" in idx_txt:
            a, b = idx_txt.split("-", 1)
            indices = list(range(int(a), int(b) + 1))
        else:
            indices = [i.strip() for i in idx_txt.split(",")]
    else:
        indices = list(range(int(dim)))
    out = []
    for i, idx in enumerate(indices):
        n = name.replace("%s", str(idx))
        out.append((n, i * inc))
    return out


def collect_registers(p_elem: ET.Element) -> list[dict]:
    """外设 → [{register, offset, description, field, ...}]（展开 dim/cluster）。"""
    regs_node = find(p_elem, "registers")
    rows: list[dict] = []
    if regs_node is None:
        return rows

    def walk(node: ET.Element, base_off: int) -> None:
        for child in node:
            t = tag(child)
            if t == "cluster":
                off = parse_int(findtext(child, "addressOffset") or "0x0")
                for sub_name, extra in expand_dim(findtext(child, "name") or "", child):
                    walk_sub = sub_name
                    # cluster 内寄存器名带 cluster 前缀
                    inner: list[dict] = []
                    saved_rows = len(rows)
                    walk(child, base_off + off + extra)
                    for row in rows[saved_rows:]:
                        row["register"] = f"{walk_sub}.{row['register']}"
                    inner = None
            elif t == "register":
                off = parse_int(findtext(child, "addressOffset") or "0x0")
                desc = findtext(child, "description")
                access = findtext(child, "access")
                rname = findtext(child, "name") or ""
                for sub_name, extra in expand_dim(rname, child):
                    fields_node = find(child, "fields")
                    fields = list(fields_node) if fields_node is not None else []
                    if fields:
                        for f in fields:
                            fname = findtext(f, "name") or ""
                            bo, bs = bit_range(f)
                            rows.append({
                                "register": sub_name,
                                "offset": base_off + off + extra,
                                "description": desc,
                                "field": fname,
                                "bit_offset": bo,
                                "bit_size": bs,
                                "access": findtext(f, "access") or access,
                                "field_description": findtext(f, "description"),
                            })
                    else:
                        rows.append({
                            "register": sub_name,
                            "offset": base_off + off + extra,
                            "description": desc,
                            "field": "", "bit_offset": 0, "bit_size": 0,
                            "access": access, "field_description": "",
                        })

    walk(regs_node, 0)
    return rows


def parse_svd(path: Path) -> tuple[list[dict], dict]:
    tree = ET.parse(path)
    root = tree.getroot()
    device_name = findtext(root, "name") or path.stem
    cpu = ""
    cpu_node = find(root, "cpu")
    if cpu_node is not None:
        cpu = findtext(cpu_node, "name")
    peripherals = find(root, "peripherals")
    plist = list(peripherals) if peripherals is not None else []

    # 展开 derivedFrom：寄存器/位场继承
    by_name: dict[str, ET.Element] = {}
    for p in plist:
        by_name[findtext(p, "name") or ""] = p
    reg_cache: dict[str, list[dict]] = {}

    def regs_of(p: ET.Element, depth: int = 0) -> list[dict]:
        pname = findtext(p, "name") or ""
        if pname in reg_cache:
            return reg_cache[pname]
        rows = collect_registers(p)
        if not rows and depth < 4:
            parent_attr = p.attrib.get("derivedFrom", "")
            parent = by_name.get(parent_attr)
            if parent is not None:
                rows = regs_of(parent, depth + 1)
        reg_cache[pname] = rows
        return rows

    meta = {
        "series": path.stem,
        "cpu": cpu,
        "device_name": device_name,
        "peripheral_count": len(plist),
    }
    rows: list[dict] = []
    for p in plist:
        pname = findtext(p, "name") or ""
        series = path.stem
        kind = kind_of(pname)
        for r in regs_of(p):
            rows.append({
                "series": series,
                "instance": pname,
                "kind": kind,
                "register": r["register"],
                "offset": r["offset"],
                "description": r["description"],
                "field": r["field"],
                "bit_offset": r["bit_offset"],
                "bit_size": r["bit_size"],
                "access": r["access"],
                "field_description": r["field_description"],
            })
    return rows, meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", default=[],
                    help="数据仓库目录（可多次）；默认 hc32-ddl-data 与 hc32-ll-data")
    ap.add_argument("--svd-dir", help="覆盖 SVD 目录（配合单个 --repo）")
    args = ap.parse_args()

    ws = Path(__file__).resolve().parent.parent
    repos = [Path(r) for r in args.repo] or [ws / "hc32-ddl-data", ws / "hc32-ll-data"]
    for repo in repos:
        svd_dir = Path(args.svd_dir) if args.svd_dir else repo / "svd"
        out_dir = repo / "data" / "csv"
        out_dir.mkdir(parents=True, exist_ok=True)
        all_rows: list[dict] = []
        metas: list[dict] = []
        for svd in sorted(svd_dir.glob("*.svd")):
            rows, meta = parse_svd(svd)
            all_rows.extend(rows)
            metas.append(meta)
            print(f"{repo.name}/{svd.name}: {len(rows)} 行（{meta['peripheral_count']} 外设, cpu={meta['cpu'] or '?'}）",
                  file=__import__("sys").stderr)
        with open(out_dir / "registers.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=REG_FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(all_rows)
        with open(out_dir / "svd_meta.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=META_FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(metas)
        print(f"{repo.name}: registers.csv 共 {len(all_rows)} 行 → {out_dir}",
              file=__import__("sys").stderr)


if __name__ == "__main__":
    main()
