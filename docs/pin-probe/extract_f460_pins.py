#!/usr/bin/env python3
"""F460 管脚表小样本提取（可复现探针）。

来源: DS_HC32F460系列数据手册_Rev1.61.pdf
  - 表 2-1 引脚功能表（p.33-35）: 行 = LQFP100 pin#; 列 = VFBGA100/LQFP64/QFN60/LQFP-QFN48 编号 + Name + 复用功能
  - 表 2-3 端口配置（p.37）: 每封装的 Port bit 位图（GPIO 焊盘引出的 ground truth）
  - 图 2-1 引脚配置图（p.27-32）: 各封装管脚号↔焊盘名（交叉核对用，本脚本不解析图）

产出: /tmp/f460-pin-probe/f460_terminals.csv
  契约: part_or_package, terminal, pad, kind, source_table, source_page
  - part_or_package: LQFP100 / VFBGA100 / LQFP64 / QFN60 / LQFP48 / QFN48
    （表 2-1 的 LQFP/QFN48 列两封装共用管脚号；LQFP100 列同时覆盖
     HC32F460PETB/PCTB，即样本 JCTA 与 PCTB 共用该列）
  - terminal: 封装管脚号（LQFP100 为表 2-1 行号本身）
  - pad: 焊盘名（GPIO Pxy 或电源/复位/NC 等非 GPIO 管脚名）
  - kind: gpio | power | reset | nc | vcap | xtalandmode（分类规则见 classify()）
  - source_table / source_page: 出处

断言（全部满足才写 CSV）:
  A1 行数 = 100（表 2-1 LQFP100 列满）
  A2 每封装管脚号连续 1..N（N=100/100/64/60/48/48）
  A3 LQFP48/QFN48/QFN60 GPIO 焊盘集合 ⊆ LQFP100 GPIO 焊盘集合
  A4 各封装 GPIO 焊盘数 == DS 表 2-3 Total（83/83/52/50/38/38）
  A5 每封装 GPIO 焊盘的 port bit 与表 2-3 位图逐位一致
"""
import csv
import re
import sys
import collections
from pathlib import Path

try:
    import pypdf
except ImportError:
    sys.exit("need pypdf: pip3 install pypdf")

PDF = "/Volumes/aigo_1t/GitRepo/hc32-rs-workspace/xh_ll_sdk/HC32F460_DDL_Rev3.3.0/documents/DS_HC32F460系列数据手册_Rev1.61.pdf"
OUT = str(Path(__file__).resolve().parent / "hc32f460_pin_terminals.csv")

# 表 2-1 数据行正则：
# <LQFP100pin> <VFBGA BGA坐标|-> <LQFP64|-> <QFN60|-> <LQFP48|-> <Name> <rest...>
ROW = re.compile(
    r"^(?P<lqfp100>\d{1,3})\s+"
    r"(?P<vfbga>(?:[A-N][0-9]{1,2}|-))\s+"
    r"(?P<lqfp64>(?:\d{1,2}|-))\s+"
    r"(?P<qfn60>(?:\d{1,2}|-))\s+"
    r"(?P<lqfp48>(?:\d{1,2}|-))\s+"
    r"(?P<name>\S.*)$"
)

# 表 2-3 端口配置（手工录入，DS p.37）——独立于表 2-1 的第二证据源
PORT_MAPS = {
    "LQFP100": {
        "PortA": list(range(16)), "PortB": list(range(16)), "PortC": list(range(16)),
        "PortD": list(range(16)), "PortE": list(range(16)), "PortH": [0, 1, 2],
    },
    "VFBGA100": None,  # same as LQFP100
    # 位序注意：DS 表 2-3 每行从 bit15（左）到 bit0（右）。A5 双源校验防止转录位序错误
    # （初版曾把 LQFP64 PortD 记为 [15]、LQFP48 PortC 记为 [0,1,2]，均被表 2-1 行 83/15-17 证伪）。
    "LQFP64": {
        "PortA": list(range(16)), "PortB": list(range(16)), "PortC": list(range(16)),
        "PortD": [2], "PortH": [0, 1, 2],
    },
    "QFN60": {
        "PortA": list(range(16)), "PortB": list(range(16)),
        "PortC": [b for b in range(16) if b not in (3, 6)],
        "PortE": [15], "PortH": [0, 1, 2],
    },
    "LQFP48": {
        "PortA": list(range(16)), "PortB": list(range(16)),
        "PortC": [13, 14, 15], "PortH": [0, 1, 2],
    },
    "QFN48": None,  # same as LQFP48
}
PORT_MAPS["VFBGA100"] = PORT_MAPS["LQFP100"]
PORT_MAPS["QFN48"] = PORT_MAPS["LQFP48"]
EXPECTED_GPIO_TOTAL = {"LQFP100": 83, "VFBGA100": 83, "LQFP64": 52, "QFN60": 50, "LQFP48": 38, "QFN48": 38}
EXPECTED_PIN_N = {"LQFP100": 100, "VFBGA100": 100, "LQFP64": 64, "QFN60": 60, "LQFP48": 48, "QFN48": 48}
# 表 2-1 中 JCTA(LQFP48) 列出的 LQFP/QFN48 公共列；QFN60 列只在 QFN60 头下
PKG_COLS = [("LQFP100", "lqfp100"), ("VFBGA100", "vfbga"), ("LQFP64", "lqfp64"), ("QFN60", "qfn60"), ("LQFP48", "lqfp48"), ("QFN48", "lqfp48")]

NONGPIO_PADS = {"VCC", "VSS", "AVCC", "AVSS", "VREFH", "VCAP_1", "VCAP_2", "NRST", "NC"}
# VFBGA100：表 2-1 中行 19/27/28（VCC/AVSS/AVCC，LQFP100 冗余电源脚）的 VFBGA 列为 "-"，
# 即该封装无对应球。图 2-1（p.28）网格共 100 球，其中 3 球为表 2-1 未映射的电源/参考球
# （含 VFBGA 特有的 VREFL 独立球 K1；LQFP100 上 AVSS/VREFL 合并为脚 20）。
# 文本抽取无法无歧义对齐这些球的列位 → 不臆测坐标，留档为未确认项（仅电源类，无 GPIO 影响）。
VFBGA_NOBALL_ROWS = {19: "VCC", 27: "AVSS", 28: "AVCC"}
GPIO_RE = re.compile(r"^P([A-EH])(\d{1,2})(/.*)?$")


def classify(name: str) -> str:
    base = name.split("/")[0]
    if base == "VSS" or base.startswith("AVSS"):
        return "power"
    if base in ("VCC", "AVCC", "VREFH"):
        return "power"
    if base.startswith("VCAP"):
        return "vcap"
    if base == "NRST":
        return "reset"
    if base == "NC":
        return "nc"
    if base == "PB11":  # PB11/MD 复位模式输入脚，本体仍是 GPIO 焊盘
        return "gpio"
    if GPIO_RE.match(base):
        return "gpio"
    if base.startswith("PC14") or base.startswith("PC15") or base.startswith("PH0") or base.startswith("PH1"):
        return "gpio"
    return "unknown"


def port_of(name: str):
    base = name.split("/")[0]
    m = GPIO_RE.match(base)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def main():
    r = pypdf.PdfReader(PDF)
    lines = []
    for i in (32, 33, 34):  # DS p.33-35, 表 2-1
        lines += (r.pages[i].extract_text() or "").splitlines()

    rows = {}
    for raw in lines:
        line = raw.strip()
        m = ROW.match(line)
        if not m:
            continue
        pin100 = int(m.group("lqfp100"))
        # 0 行是表头残留（"0 - GPO ..."），管脚号从 1 开始
        if pin100 == 0:
            continue
        if pin100 in rows:
            sys.exit(f"duplicate LQFP100 pin row: {pin100}")
        rows[pin100] = m

    # A1: 100 行
    assert len(rows) == 100, f"A1 failed: got {len(rows)} rows (expect 100)"

    # 展开成 per-package terminal 表
    out = []
    for pin100 in sorted(rows):
        m = rows[pin100]
        name = m.group("name").split()[0]  # Name 列; 之后是 Analog/EIRQ/复用功能
        for pkg, col in PKG_COLS:
            v = m.group(col)
            if v == "-":
                continue
            if col == "lqfp100":
                terminal = str(pin100)
            elif col == "vfbga":
                terminal = v  # BGA 球坐标（如 B2），非数字
            else:
                terminal = v
            # 样本型号：表 2-1 的 LQFP100 列覆盖 PCTB（也覆盖 PETB；PEHB=VFBGA100 走球坐标列），
            # LQFP48 列覆盖 JCTA（也覆盖 JETA/JEUA）；sample_part 只标注两个样本型号
            sample_part = {"LQFP48": "HC32F460JCTA", "LQFP100": "HC32F460PCTB"}.get(pkg, "")
            out.append({
                "sample_part": sample_part,
                "part_or_package": pkg,
                "terminal": terminal,
                "pad": name,
                "kind": classify(name),
                "source_table": "表2-1 引脚功能表",
                "source_page": "p.33-35",
            })

    # A2: 数字管脚封装 1..N 连续不重；VFBGA100 球坐标唯一且满 100
    by_pkg = collections.defaultdict(list)
    for rec in out:
        by_pkg[rec["part_or_package"]].append(rec["terminal"])
    for pkg, terms in by_pkg.items():
        if pkg == "VFBGA100":
            # 表 2-1 仅映射 97 球（行 19/27/28 无球）；全唯一即可，缺球留档
            assert len(terms) == 97 and len(set(terms)) == 97, \
                f"A2 failed for VFBGA100: n={len(terms)} unique={len(set(terms))}"
        else:
            nums = sorted(int(t) for t in terms)
            assert nums == list(range(1, EXPECTED_PIN_N[pkg] + 1)), \
                f"A2 failed for {pkg}: n={len(nums)}"

    # A3: 小封装 GPIO 焊盘 ⊆ LQFP100 GPIO 焊盘
    gpio100 = {r["pad"] for r in out if r["part_or_package"] == "LQFP100" and r["kind"] == "gpio"}
    for pkg in ("VFBGA100", "LQFP64", "QFN60", "LQFP48", "QFN48"):
        gp = {r["pad"] for r in out if r["part_or_package"] == pkg and r["kind"] == "gpio"}
        extra = gp - gpio100
        assert not extra, f"A3 failed: {pkg} has GPIO pads absent from LQFP100: {sorted(extra)}"

    # A4: GPIO 焊盘数 == 表 2-3 Total
    for pkg in EXPECTED_GPIO_TOTAL:
        gp = [r for r in out if r["part_or_package"] == pkg and r["kind"] == "gpio"]
        assert len(gp) == EXPECTED_GPIO_TOTAL[pkg], \
            f"A4 failed for {pkg}: {len(gp)} GPIO pads (expect {EXPECTED_GPIO_TOTAL[pkg]})"

    # A5: GPIO 焊盘的 (port,bit) 与表 2-3 位图逐位一致
    for pkg in EXPECTED_GPIO_TOTAL:
        want = set()
        for port, bits in PORT_MAPS[pkg].items():
            letter = port[-1]
            for b in bits:
                want.add((letter, b))
        got = set()
        for r in out:
            if r["part_or_package"] == pkg and r["kind"] == "gpio":
                pb = port_of(r["pad"])
                if pb:
                    got.add(pb)
        assert got == want, f"A5 failed for {pkg}: only-in-table2-1={sorted(got-want)} only-in-table2-3={sorted(want-got)}"

    unknown = {r["pad"] for r in out if r["kind"] == "unknown"}
    assert not unknown, f"classify() left unknown pads: {sorted(unknown)}"

    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sample_part", "part_or_package", "terminal", "pad", "kind", "source_table", "source_page"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(out)

    # 摘要
    print(f"rows: {len(out)} terminal records from 100 LQFP100 rows")
    for pkg in ("LQFP100", "VFBGA100", "LQFP64", "QFN60", "LQFP48", "QFN48"):
        recs = [r for r in out if r["part_or_package"] == pkg]
        kinds = collections.Counter(r["kind"] for r in recs)
        print(f"  {pkg:9s} n={len(recs):3d} kinds={dict(kinds)}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
