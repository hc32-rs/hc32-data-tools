"""emit_pins 多封装行为测试（独立 fixture，不碰现网数据仓库）。

分支覆盖：
  T1 同型号多封装、路由一致 → 合并为型号级单文件（stats 只记型号）
  T2 同型号多封装、路由分歧 → SystemExit（显式拒绝，不静默合并）
  T3 同型号多封装、任一封装缺管脚表基准 → SystemExit
  T4 单封装型号（现网形态回归）→ 型号级单文件，计数不变
  T5 已登记型号的探针 CSV 缺失或没有数据行 → SystemExit
  T6 陈旧的型号级 YAML 未经本轮验证 → 不挂接，重生时清理
"""
import csv
import shutil
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import csv_to_yaml as c2y

WS = Path(tempfile.mkdtemp(prefix="pins-multitest-"))
PROBE = WS / "hc32-data-tools" / "docs" / "pin-probe"
MUX = WS / "hc32-data-tools" / "docs" / "pin-mux-probe"
REPO = WS / "hc32-ll-data"


def setup(routes, terminals, chips_rows):
    shutil.rmtree(WS, ignore_errors=True)
    PROBE.mkdir(parents=True)
    MUX.mkdir(parents=True)
    (REPO / "data" / "csv").mkdir(parents=True)
    (REPO / "data" / "pins").mkdir(parents=True)
    with open(MUX / "routes.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["series", "pad", "peripheral", "signal",
                                          "selector_kind", "selector_value", "source"])
        w.writeheader()
        w.writerows(routes)
    with open(PROBE / "terminals.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sample_part", "part_or_package", "terminal",
                                          "pad", "kind", "source_table", "source_page"])
        w.writeheader()
        w.writerows(terminals)
    with open(REPO / "data" / "csv" / "chips.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["part_no", "series", "package"])
        w.writeheader()
        w.writerows(chips_rows)
    # peripherals.csv：只需 series+instance 列
    with open(REPO / "data" / "csv" / "peripherals.csv", "w", newline="") as f:
        f.write("series,instance\nHC32F460,USART3\n")
    # monkeypatch：探针根指到 fixture，源表指到 fixture 文件
    c2y._pin_probe_root = lambda: WS / "hc32-data-tools"
    c2y.PIN_ROUTE_SOURCES = {"HC32F460": ("docs/pin-mux-probe/routes.csv",
                                          "docs/pin-probe/terminals.csv")}
    c2y.PIN_TERMINAL_SAMPLE_PARTS = {"HC32F460": {"HC32F460XXTA": "LQFP48"}}


def rt(pad, sig="TX", val="36"):
    return {"series": "HC32F460", "pad": pad, "peripheral": "USART3",
            "signal": sig, "selector_kind": "fsel", "selector_value": val,
            "source": "test"}


def term(pkg, pad, gpio=True):
    return {"sample_part": "", "part_or_package": pkg, "terminal": "1",
            "pad": pad, "kind": "gpio" if gpio else "power",
            "source_table": "t", "source_page": "p"}


def expect_exit(fn, kw):
    try:
        fn(**kw)
    except SystemExit as e:
        assert "封装" in str(e) or "分歧" in str(e), f"exit 文本不合预期: {e}"
        return str(e)
    raise AssertionError("应当 SystemExit 却正常返回")


def t1_consistent_merge():
    # XX 型号两封装 LQFP48+QFN48；PA0/PA1 两焊盘路由，两封装都引出 → 一致合并
    setup(
        routes=[rt("PA0"), rt("PA1", "RX", "37")],
        terminals=[term("LQFP48", "PA0"), term("LQFP48", "PA1"),
                   term("QFN48", "PA0"), term("QFN48", "PA1")],
        chips_rows=[{"part_no": "HC32F460XXTA", "series": "HC32F460", "package": "LQFP48"},
                    {"part_no": "HC32F460XXTA", "series": "HC32F460", "package": "QFN48"}],
    )
    ctx = {"chips": list(csv.DictReader(open(REPO / "data/csv/chips.csv"))),
           "peripherals": [{"series": "HC32F460", "instance": "USART3"}]}
    stats = c2y.emit_pins(REPO, ctx)
    files = sorted(p.name for p in (REPO / "data/pins").glob("*.yaml"))
    assert files == ["HC32F460_HC32F460XXTA.yaml"], files
    assert stats["HC32F460XXTA"] == 2, stats
    body = (REPO / "data/pins/HC32F460_HC32F460XXTA.yaml").read_text()
    assert "LQFP48+QFN48" in body, "header 应记录两封装"
    assert "PA0" in body and "PA1" in body
    print("T1 PASS: 一致多封装 → 型号级单文件（header 记 LQFP48+QFN48，2 条路由）")


def t2_divergent_reject():
    # LQFP48 引出 PA0/PA1，QFN48 只引出 PA0 → 交集不同 → 拒绝
    setup(
        routes=[rt("PA0"), rt("PA1", "RX", "37")],
        terminals=[term("LQFP48", "PA0"), term("LQFP48", "PA1"), term("QFN48", "PA0")],
        chips_rows=[{"part_no": "HC32F460XXTA", "series": "HC32F460", "package": "LQFP48"},
                    {"part_no": "HC32F460XXTA", "series": "HC32F460", "package": "QFN48"}],
    )
    ctx = {"chips": list(csv.DictReader(open(REPO / "data/csv/chips.csv"))),
           "peripherals": [{"series": "HC32F460", "instance": "USART3"}]}
    msg = expect_exit(c2y.emit_pins, {"repo": REPO, "ctx": ctx})
    assert "分歧" in msg, msg
    assert not list((REPO / "data/pins").glob("*.yaml")), "拒绝时不得写任何文件"
    print("T2 PASS: 分歧多封装 → SystemExit（分歧），零文件落盘")


def t3_missing_pkg_data_reject():
    # QFN48 无管脚表行 → 缺交集基准 → 拒绝
    setup(
        routes=[rt("PA0")],
        terminals=[term("LQFP48", "PA0")],
        chips_rows=[{"part_no": "HC32F460XXTA", "series": "HC32F460", "package": "LQFP48"},
                    {"part_no": "HC32F460XXTA", "series": "HC32F460", "package": "QFN48"}],
    )
    ctx = {"chips": list(csv.DictReader(open(REPO / "data/csv/chips.csv"))),
           "peripherals": [{"series": "HC32F460", "instance": "USART3"}]}
    msg = expect_exit(c2y.emit_pins, {"repo": REPO, "ctx": ctx})
    assert "无管脚表数据" in msg, msg
    print("T3 PASS: 任一封装缺基准 → SystemExit（无管脚表数据）")


def t4_single_package_regression():
    # 单封装（现网 JCTA 形态）回归：型号级单文件
    setup(
        routes=[rt("PA0"), rt("PA1", "RX", "37")],
        terminals=[term("LQFP48", "PA0"), term("LQFP48", "PA1")],
        chips_rows=[{"part_no": "HC32F460XXTA", "series": "HC32F460", "package": "LQFP48"}],
    )
    ctx = {"chips": list(csv.DictReader(open(REPO / "data/csv/chips.csv"))),
           "peripherals": [{"series": "HC32F460", "instance": "USART3"}]}
    stats = c2y.emit_pins(REPO, ctx)
    files = [p.name for p in (REPO / "data/pins").glob("*.yaml")]
    assert files == ["HC32F460_HC32F460XXTA.yaml"], files
    assert stats["HC32F460XXTA"] == 2
    print("T4 PASS: 单封装回归 → 型号级单文件（2 条）")


def t5_missing_or_empty_probe_reject():
    rows = [{"part_no": "HC32F460XXTA", "series": "HC32F460", "package": "LQFP48"}]
    for probe in (MUX / "routes.csv", PROBE / "terminals.csv"):
        for missing in (True, False):
            setup([rt("PA0")], [term("LQFP48", "PA0")], rows)
            if missing:
                probe.unlink()
            else:
                probe.write_text(probe.read_text().splitlines()[0] + "\n")
            stale = REPO / "data/pins/HC32F460_HC32F460XXTA.yaml"
            stale.write_text("USART3: {pins: [{pin: PE6, signal: TX, func: 36}]}\n")
            try:
                c2y.emit_pins(REPO, c2y.read_ctx(REPO))
            except SystemExit as e:
                assert "CSV 缺失或没有数据行" in str(e) and probe.name in str(e), e
            else:
                raise AssertionError(f"应拒绝缺失或为空的探针：{probe.name}")
    print("T5 PASS: 路由表或封装表缺失/为空，拒绝生成")


def t6_stale_file_is_not_attached():
    rows = [{"part_no": "HC32F460XXTA", "series": "HC32F460", "package": "LQFP48"}]
    setup([rt("PA0")], [term("LQFP48", "PA0")], rows)
    ctx = c2y.read_ctx(REPO)
    ctx["svd_meta"] = [{"series": "HC32F460", "cpu": "CM4"}]
    stale = REPO / "data/pins/HC32F460_HC32F460XXTA.yaml"
    stale.write_text("USART3: {pins: [{pin: PE6, signal: TX, func: 36}]}\n")
    [chip] = c2y.emit_chips(REPO, ctx, {}, set())
    assert "include_pins" not in chip.read_text()

    c2y.PIN_TERMINAL_SAMPLE_PARTS = {}
    assert c2y.emit_pins(REPO, ctx) == {}
    assert not stale.exists()
    print("T6 PASS: 不挂接旧文件，清理未受支持的旧产物")


if __name__ == "__main__":
    t1_consistent_merge()
    t2_divergent_reject()
    t3_missing_pkg_data_reject()
    t4_single_package_regression()
    t5_missing_or_empty_probe_reject()
    t6_stale_file_is_not_attached()
    print("ALL PASS")
