#!/usr/bin/env python3
r"""对比 chiptool 试验输出（hc32-data-tools/chiptool-trial/）与现有数据
（<repo>/data/csv/registers.csv + data/registers/*.yaml）。

对比维度：
1. 覆盖率：chiptool 每系列成功解析的外设/寄存器/字段数 vs registers.csv；
2. 寄存器级差异：名称+偏移集合差（chiptool 数组展开后对比）；
3. 字段级差异：名称+位偏移+位宽集合差；
4. enum 数量（chiptool 支持、HC32 SVD 实际是否有值）。

输出：hc32-data-tools/chiptool-trial/COMPARE.md
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import yaml

WS = Path(__file__).resolve().parents[2]
TRIAL = WS / "tools" / "chiptool-trial"


def expand_array(item: dict) -> list[tuple[str, int]]:
    """把 chiptool 的 array 声明展开成 (名字, 偏移) 列表。"""
    arr = item.get("array")
    if not arr:
        return [(item["name"], item["byte_offset"])]
    out = []
    if isinstance(arr, dict) and "len" in arr:  # RegularArray {len, stride}
        base = item["name"].replace("[%s]", "")
        for i in range(arr["len"]):
            out.append((f"{base}{i}", item["byte_offset"] + i * arr["stride"]))
    elif isinstance(arr, dict) and "offsets" in arr:  # CursedArray
        for i, off in enumerate(arr["offsets"]):
            out.append((f"{item['name'].replace('[%s]', '')}{i}", off))
    return out


def load_chiptool(dirp: Path) -> dict:
    """{instance: {"regs": {(name,off)}, "fields": {(name,off,size)}, "enums": n}}"""
    res = {}
    for f in sorted(dirp.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        regs, fields, enums = set(), set(), 0
        blocks = [k for k in data if k.startswith("block/")]
        # 一个外设文件可能含多个 block（cluster），主 block 取文件名对应项
        for k, v in data.items():
            if k.startswith("enum/"):
                enums += 1
            elif k.startswith("fieldset/"):
                for fld in v.get("fields", []):
                    bo = fld.get("bit_offset")
                    if not isinstance(bo, int):
                        bo = 0  # Cursed 位域极少见，忽略细分
                    fields.add((fld["name"], bo, fld.get("bit_size", 1)))
            elif k.startswith("block/"):
                for it in v.get("items", []):
                    for name, off in expand_array(it):
                        regs.add((name, off))
        inst = f.stem.split("__")[0].split(".")[0]
        res[inst] = {"regs": regs, "fields": fields, "enums": enums,
                     "blocks": [b[6:] for b in blocks]}
    return res


def load_mine(repo: Path) -> dict:
    """{series: {instance: {"regs":…, "fields":…}}}（来自 registers.csv）"""
    per = defaultdict(lambda: {"regs": set(), "fields": set()})
    with open(repo / "data" / "csv" / "registers.csv", newline="",
              encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = per[(r["series"], r["instance"])]
            d["regs"].add((r["register"], int(r["offset"])))
            if r["field"]:
                d["fields"].add((r["field"], int(r["bit_offset"]),
                                 int(r["bit_size"])))
    out = defaultdict(dict)
    for (s, i), d in per.items():
        out[s][i] = d
    return out


def main() -> None:
    lines = [
        "# chiptool extract-all 试验对比报告",
        "",
        "说明：",
        "- chiptool 每个唯一寄存器布局产出一个 YAML（`derivedFrom` 外设不单独产出），",
        "  所以“chiptool 外设”列 < “CSV 外设”列属预期，不是数据缺失；",
        "- 14 个老代 DDL SVD 缺 `<addressBlock><usage>`，试验脚本已自动修补副本后解析",
        "（修补文件在 `_svd_fixed/`，原始 SVD 未改动）；",
        "- 寄存器/字段差异列 = 仅 chiptool 有 / 仅 CSV 有的条目数（名称+偏移/位域归一后）。",
        "",
    ]
    for repo in ("hc32-ddl-data", "hc32-ll-data"):
        mine = load_mine(WS / repo)
        lines += [f"## {repo}", "",
                  "| 系列 | chiptool 外设 | CSV 外设 | 寄存器一致 | 寄存器差异(仅chiptool/仅CSV) | 字段差异(仅chiptool/仅CSV) | enum |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        tot_same_reg = tot_reg = tot_same_fld = tot_fld = 0
        n_enum = 0
        for series_dir in sorted((TRIAL / repo).iterdir()):
            s = series_dir.name
            ct = load_chiptool(series_dir)
            mv = mine.get(s, {})
            # chiptool 实例名 → CSV 实例名对齐（大小写不敏感）
            mv_ci = {k.upper(): k for k in mv}
            reg_diff_ct = reg_diff_csv = 0
            fld_diff_ct = fld_diff_csv = 0
            same_reg = same_fld = 0
            for inst, cd in ct.items():
                md = mv.get(mv_ci.get(inst.upper(), ""), None)
                if md is None:
                    reg_diff_ct += len(cd["regs"])
                    continue
                only_ct = cd["regs"] - md["regs"]
                only_csv = md["regs"] - cd["regs"]
                reg_diff_ct += len(only_ct)
                reg_diff_csv += len(only_csv)
                same_reg += len(cd["regs"] & md["regs"])
                tot_reg += len(cd["regs"] | md["regs"])
                fo_ct = cd["fields"] - md["fields"]
                fo_csv = md["fields"] - cd["fields"]
                fld_diff_ct += len(fo_ct)
                fld_diff_csv += len(fo_csv)
                same_fld += len(cd["fields"] & md["fields"])
                tot_fld += len(cd["fields"] | md["fields"])
            n_enum += sum(c["enums"] for c in ct.values())
            csv_peri = len(mv)
            lines.append(f"| {s} | {len(ct)} | {csv_peri} | {same_reg} | "
                         f"{reg_diff_ct}/{reg_diff_csv} | {fld_diff_ct}/{fld_diff_csv} | "
                         f"{sum(c['enums'] for c in ct.values())} |")
            tot_same_reg += same_reg
            tot_same_fld += same_fld
        reg_rate = tot_same_reg / tot_reg * 100 if tot_reg else 0
        fld_rate = tot_same_fld / tot_fld * 100 if tot_fld else 0
        lines += ["", f"**寄存器级一致率 {reg_rate:.1f}%，字段级一致率 {fld_rate:.1f}%，"
                  f"enum 定义 {n_enum} 个。**", ""]
    out = TRIAL / "COMPARE.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告已写入 {out}")
    print("\n".join(lines[-6:]))


if __name__ == "__main__":
    main()
