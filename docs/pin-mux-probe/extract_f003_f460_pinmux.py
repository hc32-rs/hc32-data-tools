#!/usr/bin/env python3
"""HC32F003(DDL) / HC32F460(LL) UART·SPI 引脚复用路由小样本提取（可复现探针）。

为 pins 数据缺口（hc32-data-tools README 缺口 1）建立信号级路由样本：
每行一条 (pad, peripheral, signal) 路由，带 selector_kind/selector_value 与来源。

来源（只读 SDK 内文档）：
  DDL F003:
    - RM_HC32F005_F003系列参考手册_Rev2.50.pdf p.76 表 6-2 端口复用表
      （PxADS=1 模拟列 / PxADS=0 Px_sel=0..7 数字列，每焊盘一行、每列一个信号）
    - DS_HC32F005_F003系列数据手册_Rev3.00.pdf 第 4 章引脚功能说明（p.21-24）
      作为第二证据源交叉核对（信号序列一致性断言 A2）
  LL F460:
    - DS_HC32F460系列数据手册_Rev1.61.pdf 表 2-1 引脚功能表（p.33-35）
      行=LQFP100 管脚号，Func0..15 列为每焊盘独立信号；
      Func32~63 列为 Func_Grp1/2 分组共享（表 2-2 p.36 展开）
    - SDK 例程 GPIO_SetFunc 调用作为抽查基准（断言 A6/A7）

PDF 解析说明（两代文档的共同难点：空单元格）：
  - 纯文本模式会把表格空单元格吞掉，必须用 pypdf visitor 的 (x, y) 坐标重建列位。
  - F003 RM 表 6-2 每行是单文本流：列锚点（Px_sel=0..7 表头 x）定位跨单元格的
    新文本段起点；段内 ≥2 空格的间隙 = 空单元格（间隙 n 个空格 ⇒ n-1 个空格位，
    经 P03（3 空格=2 空）/P25（2 空格=1 空）双向验证）。
  - F460 DS 表 2-1 行内换行（TIMA_1_PWM1/TIM + A_1_CLKA 两行）按
    "以 /、_ 或 TIM 结尾且偏离行锚 >2pt 的片段与下一片段直接拼接" 规则还原；
    多 token 尾段（如 x≈707.4 的 'SPI2_SS2 - SDIO2_D2 …'）按列锚点顺序向
    连续槽位铺开。
  - 两侧算法均以 SDK 例程的 GPIO_SetFunc/Gpio_SetAfMode 调用值做逐点断言。

产物（本目录）：
  - f003_uart_spi_routes.csv / f460_uart_spi_routes.csv
  - 字段契约与覆盖说明见 README.md

记录契约（两 CSV 同列头）：
  series, pad, peripheral, signal, selector_kind, selector_value, source
  - pad: 芯片逻辑焊盘名（F003 Pxy 形如 P25；F460 PA9 形）
  - peripheral: family YAML 外设实例名（F003: UART0/UART1/SPI；F460: USART1..4/SPI1..4）
  - signal: 规范信号名（TX/RX/SCK/MISO/MOSI/CK/SS0..SS3——DS 原名带前缀，
    如 UART1_TXD → UART1.TX、SPI_SCK → SPI.SCK）
  - selector_kind: 本样本全部为 fsel（单一功能选择寄存器直接可选：
    F003 Px_sel 1..7 / F460 PFSRxy.FSEL[5:0]）。F003 模拟功能走 PxADS=1、
    P00/SWD 等固定功能无选择位，均不产生 UART/SPI 路由，仅在 README 留档
  - selector_value: 寄存器编码：F003 = Px_sel 值（1..7；0 为 GPIO 不产出）；
    F460 = FSEL 值（Func 列号；32..63 为组共享编号）
  - source: 文档出处（表号+页码）

断言（全绿才写 CSV）：
  A1 F003 RM 表 6-2 GPIO 行数 = 16（P01..P03,P14,P15,P23..P27,P31..P36；
     P00 为复位固定功能不入表；P27/P31 焊盘名带 /SWDIO、/SWCLK 后缀剥掉）
  A2 F003 每焊盘 RM 信号序列 == DS 第 4 章同名焊盘信号序列（双源逐行核对）
  A3 F003 UART/SPI 路由条数 = 40（UART0×8 + UART1×6 + SPI×26，见 README 计数表）
  A4 F003 SDK 例程抽查：uart_int（P35/P36=UART1 Af1）与 spi_master
     （P25=SCK Af1、P03=CS Af2、P24=MOSI Af5、P23=MISO Af5）逐点相等
  A5 F460 表 2-1 行数 = 100（LQFP100 列满；与 pin-probe 探针一致）
  A6 F460 每行槽位拼装完整（GPO 落 f0、EVENTOUT 落 f15）
  A7 F460 SDK 例程抽查：usart（PE6=USART4_TX 36、PB9=USART4_RX 37）；
     spi（PA7=SS0 42、PA8=SCK 43、PB0=MOSI 40、PC5=MISO 41）
  A8 F460 USARTx_TX/RX 全部来自 Func32..47 组共享段（表 2-2），grp 列一致
"""
import csv
import re
import sys
from pathlib import Path

try:
    import pypdf
except ImportError:
    sys.exit("need pypdf: pip3 install pypdf")

WS = Path(__file__).resolve().parents[3]
RM_F003 = WS / "xh_ddl_sdk/HC32F003_DDL_Rev2.0.0/documents/RM_HC32F005_F003系列参考手册_Rev2.50.pdf"
DS_F003 = WS / "xh_ddl_sdk/HC32F003_DDL_Rev2.0.0/documents/DS_HC32F005_F003系列数据手册_Rev3.00.pdf"
DS_F460 = WS / "xh_ll_sdk/HC32F460_DDL_Rev3.3.0/documents/DS_HC32F460系列数据手册_Rev1.61.pdf"
OUT_F003 = Path(__file__).resolve().parent / "f003_uart_spi_routes.csv"
OUT_F460 = Path(__file__).resolve().parent / "f460_uart_spi_routes.csv"

FIELDS = ["series", "pad", "peripheral", "signal", "selector_kind", "selector_value", "source"]

# ---------------------------------------------------------------- F003 (RM 表 6-2)

# RM p.76 表 6-2 列锚点（Px_sel 表头片段实测 x 坐标）
F003_SEL_ANCHORS = [142.6, 193.4, 244.6, 295.7, 346.8, 397.9, 449.6, 500.8]
F003_PAD_RE = re.compile(r"\b(P[0-3]\d)(?:/(?:SWDIO|SWCLK))?\b")

# 文档信号 → (peripheral, 规范 signal)。只收 UART/USART 与 SPI；
# 其余信号（TIM/PCA/I2C/时钟输出等）用于断言 A2 的全序列核对，不产出路由。
F003_SIGNAL_MAP = {
    "UART0_RXD": ("UART0", "RX"),
    "UART0_TXD": ("UART0", "TX"),
    "UART1_RXD": ("UART1", "RX"),
    "UART1_TXD": ("UART1", "TX"),
    "SPI_SCK":  ("SPI", "SCK"),
    "SPI_CS":   ("SPI", "CS"),
    "SPI_MISO": ("SPI", "MISO"),
    "SPI_MOSI": ("SPI", "MOSI"),
}
# DS 第 4 章非 UART/SPI 信号的行首白名单（用于 A2 的序列拼接；
# 模拟/固定功能行不进序列）
F003_FUNC_RE = re.compile(
    r"^(UART\d_[TR]XD|I2C_S(?:D[AL]|C[AL])|TIM\d+_\w+|SPI_(?:SCK|CS|MISO|MOSI)|PCA_ECI|PCA_CH\d|"
    r"SWDI[OA]|SWCLK|HCLK_OUT|PCLK_OUT|RCH_OUT|RCL_OUT|XTH_OUT|XTL_OUT|XTHI|XTHO|"
    r"LVD_OUT|VC\d_?OUT|ADC_RDY|ECI|IR_OUT|NC|LVDIN\d.*|AIN\d.*|VCIN\d|XTLI|XTLO|ADC_VREF)\b"
)

# SDK 例程抽查基准（ddl gpio.c Gpio_SetAfMode 写 Px_SEL）：
#   uart_int 例程：P35=UART1_TX(GpioAf1)、P36=UART1_RX(GpioAf1)
#   board_stkhc32f003.h + spi_master 例程：
#     SCK=P25(GpioAf1)、CS=P03(GpioAf2)、MOSI=P24(GpioAf5)、MISO=P23(GpioAf5)
F003_EXPECT = [
    ("P35", "UART1_TXD", 1), ("P36", "UART1_RXD", 1),
    ("P25", "SPI_SCK", 1), ("P03", "SPI_CS", 2),
    ("P24", "SPI_MOSI", 5), ("P23", "SPI_MISO", 5),
]


def _f003_page_fragments(reader, pno):
    parts = []
    def vis(text, cm, tm, fd, fs):
        t = text.strip()
        if t:
            parts.append((float(tm[5]), float(tm[4]), t))
    reader.pages[pno].extract_text(visitor_text=vis)
    return parts


def _f003_line_rows(parts):
    """按 y 分视觉行（±4.5pt），返回 [(y, [(x, text)])] 按表序（y 降序）。"""
    lines = {}
    for y, x, t in parts:
        key = None
        for k in lines:
            if abs(k - y) <= 4.5:
                key = k
                break
        if key is None:
            key = y
        lines.setdefault(key, []).append((x, t))
    return sorted(lines.items(), key=lambda kv: -kv[0])


def _f003_anchor_index(fx):
    for i, a in enumerate(F003_SEL_ANCHORS):
        if fx <= a + 1.5:
            return i
    return 8  # 越界（右侧文本）


def _f003_fill(tail, si, slots):
    """把一段行内文本按 2+ 空格间隙切组向连续 sel 槽位铺开。

    间隙 n 个空格 ⇒ 跳过 n-1 个空槽（P03 'TIM6_CHB⎵⎵⎵PCA_ECI' 3 空格=2 空、
    P25 2 空格=1 空双向验证）；段首间隙（焊盘名后的对齐空格）不跳。
    """
    expect_sig = True
    for piece in re.split(r"(\s{2,})", tail.strip()):
        if not piece:
            continue
        if piece.isspace():
            if not expect_sig:
                si += len(piece) - 1
                expect_sig = True
            continue
        for tk in piece.split():
            if si > 7:
                return
            if tk and si not in slots:
                slots[si] = tk
            si += 1
        expect_sig = False


def f003_extract_rm():
    """RM 表 6-2 → {pad: {sel1..7: 信号}}。"""
    r = pypdf.PdfReader(str(RM_F003))
    parts = _f003_page_fragments(r, 75)  # RM p.76
    rows = {}
    for y, cells in _f003_line_rows(parts):
        frags = sorted((float(x), t) for x, t in cells)
        pad = None
        frag_idx = -1
        pad_end = 0
        for i, (fx, t) in enumerate(frags):
            m = F003_PAD_RE.search(t)
            if m and fx < F003_SEL_ANCHORS[0]:
                pad = m.group(1)
                frag_idx = i
                pad_end = m.end()
                break
        if not pad or pad == "P00":
            continue  # P00 = 复位输入固定功能；表外行（表头/注释）无焊盘名
        slots = {}
        for fi, (fx, t) in enumerate(frags):
            if fi < frag_idx:
                continue  # 焊盘单元格左侧 = 模拟列（PxADS=1），不产出数字路由
            if fi == frag_idx:
                _f003_fill(t[pad_end:], 1, slots)
            else:
                _f003_fill(t, _f003_anchor_index(fx), slots)
        rows[pad] = slots
    return rows


def f003_extract_ds():
    """DS 第 4 章（p.21-24 纯文本）→ {pad: [信号序列]}（跨页连续）。

    DS 版式：每焊盘块以 'GPIO Pxy …' 行开头，随后逐行列出复用信号，
    与 RM 表 6-2 的 Px_sel=1..7 非空单元格同序（断言 A2 据此双源核对）。
    """
    r = pypdf.PdfReader(str(DS_F003))
    text = "\n".join(r.pages[i].extract_text() or "" for i in range(20, 24))
    pad_start = re.compile(r"^GPIO\s+(P[0-3]\d)\b")
    lists = {}
    cur = None
    for line in text.splitlines():
        s = line.strip()
        m = pad_start.match(s)
        if m:
            cur = m.group(1)
            lists.setdefault(cur, [])
            continue
        if re.match(r"^\d+(\s+\d+){2}\s+\S", s):  # 'N M K Pxy' 管脚号行噪声
            continue
        if cur and F003_FUNC_RE.match(s):
            tok = s.split()[0]
            if tok != "GPIO":
                lists[cur].append(tok)
    return lists


def f003_routes():
    rm = f003_extract_rm()

    # A1: 16 个 GPIO 焊盘行
    want_pads = {"P01", "P02", "P03", "P14", "P15", "P23", "P24", "P25", "P26",
                 "P27", "P31", "P32", "P33", "P34", "P35", "P36"}
    assert set(rm) == want_pads, f"A1 failed: {sorted(set(rm) ^ want_pads)}"

    # A2: RM 每焊盘非空 sel 信号序列 == DS 第 4 章序列
    # 别名：DS 第 4 章用短名（RM 拼法差异）；RM 的 NC 为空槽标记不算信号。
    # RM 独有信号（DS 第 4 章未列）：P33 sel5=XTL_OUT —— DS Rev3.00 引脚表无此项，
    # 与 RM Rev2.50 表 6-2 不一致，留档为未确认项（README），A2 跳过
    DS_ALIAS = {"ECI": "PCA_ECI", "VC0OUT": "VC0_OUT", "VC1OUT": "VC1_OUT"}
    RM_ONLY_UNCONFIRMED = {("P33", "XTL_OUT")}
    ds = f003_extract_ds()
    for pad, slots in rm.items():
        rm_seq = [slots[i] for i in range(1, 8)
                  if i in slots and slots[i] != "NC" and (pad, slots[i]) not in RM_ONLY_UNCONFIRMED]
        ds_seq = [DS_ALIAS.get(c, c) for c in (ds.get(pad) or [])]
        it = iter(ds_seq)
        for sig in rm_seq:
            for cand in it:
                if cand == sig or cand.startswith(sig) or sig.startswith(cand):
                    break
            else:
                raise AssertionError(f"A2 failed: {pad} RM signal {sig} not in DS order {ds_seq}")

    # 产出路由
    routes = []
    for pad in sorted(rm, key=lambda p: (int(p[1]), int(p[2]))):
        for sel in sorted(rm[pad]):
            sig = rm[pad][sel]
            if sig in F003_SIGNAL_MAP:
                peri, canonical = F003_SIGNAL_MAP[sig]
                routes.append({
                    "series": "HC32F003", "pad": pad, "peripheral": peri,
                    "signal": canonical, "selector_kind": "fsel",
                    "selector_value": str(sel), "source": "RM表6-2 端口复用表 p.76",
                })

    # A3: 理论条数（表 6-2 内 UART/SPI 单元格逐格计数，见 README 计数表）
    #   UART0: RXD×4(P01,P15,P27,P36) TXD×4(P02,P14,P31,P35)
    #   UART1: TXD×4(P01,P23,P32,P35) RXD×4(P02,P24,P33,P36)
    #   SPI:   SCK×3(P01,P15,P25) CS×3(P02,P03,P14)
    #          MISO×3(P23,P27,P35) MOSI×3(P24,P26,P36) → 合计 28
    n = len(routes)
    assert n == 28, f"A3 failed: got {n} routes (expect 28)"
    from collections import Counter
    dist = Counter((rt["peripheral"], rt["signal"]) for rt in routes)
    assert dist == Counter({
        ("UART0", "RX"): 4, ("UART0", "TX"): 4,
        ("UART1", "RX"): 4, ("UART1", "TX"): 4,
        ("SPI", "SCK"): 3, ("SPI", "CS"): 3,
        ("SPI", "MISO"): 3, ("SPI", "MOSI"): 3,
    }), f"A3 failed: distribution {dist}"

    # A4: SDK 例程抽查（焊盘,文档信号)→Px_sel 值逐点相等
    lookup = {(rt["pad"], rm[rt["pad"]][int(rt["selector_value"])]): int(rt["selector_value"])
              for rt in routes}
    for pad, doc_sig, sel in F003_EXPECT:
        got = lookup.get((pad, doc_sig))
        assert got == sel, f"A4 failed: {pad} {doc_sig} expect sel={sel}, got={got}"
    return routes


# ---------------------------------------------------------------- F460 (DS 表 2-1/2-2)

# DS p.33-35 表 2-1 列锚点 x（visitor tm 实测）
F460_COLS = [
    ("lead", 0, 200), ("analog", 200, 300), ("eirq", 300, 345), ("trace", 345, 395),
    ("f0", 395, 432), ("f1", 432, 462), ("f2", 462, 500), ("f3", 500, 545),
    ("f4", 545, 595), ("f5", 595, 645), ("f6", 645, 690), ("f7", 690, 730),
    ("f8", 730, 768), ("f9", 768, 803), ("f10", 803, 850), ("f11", 850, 890),
    ("f12", 890, 918), ("f13", 918, 946), ("f14", 946, 980), ("f15", 980, 1015),
    ("f1631", 1015, 1055), ("grp", 1055, 1120),
]
F460_X_BY_NAME = {"f0": 416.8, "f1": 445.1, "f2": 480.6, "f3": 523.2, "f4": 572.6,
                  "f5": 622.2, "f6": 672.0, "f7": 707.4, "f8": 750.0, "f9": 785.5,
                  "f10": 820.9, "f11": 877.6, "f12": 906.0, "f13": 934.3, "f14": 962.6,
                  "f15": 997.9, "f1631": 1033.3, "grp": 1075.9}

# 表 2-2 Func32~47（DS p.36）：Func 列号 → (grp, 信号)
# 表 2-2 Func32~47：两组各占同一 FSEL 值域（Grp1/Grp2 同号不同信号），
# 按 (grp, fno) 双键索引
F460_GRP = {}
for i, s in enumerate("USART1_TX USART1_RX USART1_RTS USART1_CTS USART2_TX USART2_RX "
                      "USART2_RTS USART2_CTS SPI1_MOSI SPI1_MISO SPI1_SS0 SPI1_SCK "
                      "SPI2_MOSI SPI2_MISO SPI2_SS0 SPI2_SCK".split()):
    F460_GRP[("Func_Grp1", 32 + i)] = s
for i, s in enumerate("USART3_TX USART3_RX USART3_RTS USART3_CTS USART4_TX USART4_RX "
                      "USART4_RTS USART4_CTS SPI3_MOSI SPI3_MISO SPI3_SS0 SPI3_SCK "
                      "SPI4_MOSI SPI4_MISO SPI4_SS0 SPI4_SCK".split()):
    F460_GRP[("Func_Grp2", 32 + i)] = s

# 表 2-1 每焊盘独立列（Func1..15）中的 USART/SPI 信号 → (peripheral, canonical)
F460_LOCAL_MAP = {
    "USART1_CK": ("USART1", "CK"), "USART2_CK": ("USART2", "CK"),
    "USART3_CK": ("USART3", "CK"), "USART4_CK": ("USART4", "CK"),
    "SPI1_SS1": ("SPI1", "SS1"), "SPI1_SS2": ("SPI1", "SS2"), "SPI1_SS3": ("SPI1", "SS3"),
    "SPI2_SS1": ("SPI2", "SS1"), "SPI2_SS2": ("SPI2", "SS2"), "SPI2_SS3": ("SPI2", "SS3"),
}
F460_GRP_SIG_RE = re.compile(r"^(USART(\d)_(TX|RX|RTS|CTS)|SPI(\d)_(MOSI|MISO|SS0|SCK))$")


def _f460_grp_signal(sig):
    m = F460_GRP_SIG_RE.match(sig)
    if not m:
        return None
    if m.group(1).startswith("USART"):
        return f"USART{m.group(2)}", m.group(3)
    return f"SPI{m.group(4)}", m.group(5)


# SDK 例程抽查基准（projects/ev_hc32f460_lqfp100_v2 examples）：
#   usart_uart_int/polling：USART4 RX=PB9 FUNC37 TX=PE6 FUNC36
#   spi_int：SPI1 SS=PA7 FUNC42 SCK=PA8 FUNC43 MOSI=PB0 FUNC40 MISO=PC5 FUNC41
F460_EXPECT = [
    ("PB9", "USART4", "RX", 37), ("PE6", "USART4", "TX", 36),
    ("PA7", "SPI1", "SS0", 42), ("PA8", "SPI1", "SCK", 43),
    ("PB0", "SPI1", "MOSI", 40), ("PC5", "SPI1", "MISO", 41),
]


def _f460_col_of(x):
    for name, lo, hi in F460_COLS:
        if lo <= x < hi:
            return name
    return None


def _f460_unwrap(frags, y0):
    """列内片段按 y 降序拼接；以 /、_ 或 TIM 结尾且偏离行锚 >2pt 的片段
    与下一片段直接相连（行内换行的信号名还原）。"""
    frags = sorted(frags, key=lambda f: (-f[0], f[1]))
    out = []
    pend = None
    for y, x, t in frags:
        if pend is not None:
            out.append(pend + t)
            pend = None
        elif t.endswith(("/", "_", "TIM")) and abs(y - y0) > 2:
            pend = t
        else:
            out.append(t)
    if pend:
        out.append(pend)
    return " ".join(out).split()


def f460_extract():
    r = pypdf.PdfReader(str(DS_F460))
    all_f = []
    for pno in (32, 33, 34):  # DS p.33-35 表 2-1
        parts = []
        def vis(text, cm, tm, fd, fs):
            t = text.strip()
            if t:
                parts.append((float(tm[5]), float(tm[4]), t))
        r.pages[pno].extract_text(visitor_text=vis)
        all_f += [(pno, y, x, t) for (y, x, t) in parts]

    # 行锚：以管脚号开头且在最左列的片段
    anchors = []
    for pno, y, x, t in all_f:
        if re.match(r"^\d{1,3}\s", t) and x < 80:
            anchors.append((pno, y, int(t.split()[0])))
    anchors = [a for a in anchors if 1 <= a[2] <= 100]
    # A5: 100 行、管脚号 1..100 各一
    assert len(anchors) == 100, f"A5 failed: {len(anchors)} rows"
    assert sorted(a[2] for a in anchors) == list(range(1, 101)), "A5 failed: pin set"

    # 统一 27-token 列模型：每行 27 个表列（0 pin … 5 pad 6 analog 7 eirq 8 trace
    # 9 f0 … 24 f15 25 f16~31 26 grp）。片段起点 x 映射到起始列，token 向连续列铺开；
    # 行内换行（'TIMA_1_PWM1/TIM' + 'A_1_CLKA'、'EIRQ3/WKUP0_' + '3'、
    # 'ADC1_IN3/PGAVSS/CMP' + '1_INP4'）按同列 y0±4 片段直接拼接还原。
    COL_START = [(56.0, 0), (90.0, 1), (126.0, 2), (155.0, 3), (180.0, 4), (218.0, 5),
                 (253.7, 6), (317.5, 7), (360.1, 8), (416.8, 9), (445.1, 10), (480.6, 11),
                 (523.2, 12), (572.6, 13), (622.2, 14), (672.0, 15), (707.4, 16),
                 (750.0, 17), (785.5, 18), (820.9, 19), (877.6, 20), (906.0, 21),
                 (934.3, 22), (962.6, 23), (997.9, 24), (1033.3, 25), (1075.9, 26)]

    def start_tok(x):
        best = 0
        for ax, idx in COL_START:
            if x >= ax - 3:
                best = idx
        return best

    WRAP_END = re.compile(r"(/TIM|/$|WKUP\d_$|INM\d/$|INP\d/$|/$)")

    rows = {}
    for pno, y0, pin in anchors:
        band = [(x, y, t) for (pp, y, x, t) in all_f
                if pp == pno and y0 - 5.5 <= y <= y0 + 5.5]
        uppers = [f for f in band if f[1] > y0 + 2]
        lowers = [f for f in band if f[1] < y0 - 2]
        used = set()
        merged = []
        for fx, fy, t in sorted(band, key=lambda f: (f[0], -f[1])):
            if (fx, fy, t) in used:
                continue
            if fy > y0 + 2 and WRAP_END.search(t + "$"):
                cand = [g for g in lowers if abs(g[0] - fx) < 4
                        and (g[0], g[1], g[2]) not in used]
                if cand:
                    gx, gy, gt = min(cand, key=lambda g: abs(g[1] - fy))
                    merged.append((fx, fy, t + gt))
                    used.add((gx, gy, gt))
                    continue
            merged.append((fx, fy, t))
        toks = [None] * 27
        for fx, fy, t in sorted(merged, key=lambda f: (f[0], -f[1])):
            st = start_tok(fx)
            for j, tk in enumerate(t.split()):
                if st + j > 26:
                    break
                if toks[st + j] is None:
                    toks[st + j] = tk
        rows[pin] = toks

    # 行记录：pad=toks[5]；'- E3 …' 等 VFBGA 专属续行无焊盘名
    out_rows = {}
    for pin, toks in rows.items():
        pad = toks[5]
        if not pad or not re.match(r"^P[A-H]", pad):
            continue
        rec = {"pin": pin, "page": None, "pad": pad,
               "analog": toks[6], "eirq": toks[7], "trace": toks[8], "grp": toks[26]}
        for fi in range(16):
            v = toks[9 + fi]
            rec[f"f{fi}"] = None if v == "-" else v
        rec["f1631"] = None if toks[25] == "-" else toks[25]
        out_rows[pin] = rec

    # A6: 槽位完整性——GPO 落 f0（表头约定 Func0=GPO）。
    # PB11/MD 为输入专用（RM 9.3.1：无输出功能），是唯一合法例外。
    for pin, rec in out_rows.items():
        if rec["pad"].startswith("PB11/"):
            continue
        assert rec["f0"] == "GPO", f"A6 failed: pin {pin} ({rec['pad']}) f0={rec['f0']!r}"
        assert rec["f15"] in ("EVENTOUT", None), \
            f"A6 failed: pin {pin} f15={rec['f15']!r}"

    # 路由产出：每焊盘独立列（Func1..15）+ Func32..47 组共享（按 grp 列展开）
    routes = []
    for pin in sorted(out_rows):
        rec = out_rows[pin]
        pad = rec["pad"]
        if not re.match(r"^P[A-H]\d", pad):
            continue
        for fi in range(1, 16):
            sig = rec.get(f"f{fi}")
            if sig and sig in F460_LOCAL_MAP:
                peri, canonical = F460_LOCAL_MAP[sig]
                routes.append({
                    "series": "HC32F460", "pad": pad, "peripheral": peri,
                    "signal": canonical, "selector_kind": "fsel",
                    "selector_value": str(fi),
                    "source": "DS表2-1 引脚功能表 p.33-35",
                })
        grp = rec["grp"]
        if grp in ("Func_Grp1", "Func_Grp2"):
            for fno in range(32, 48):
                sig = F460_GRP.get((grp, fno))
                if not sig:
                    continue
                mapped = _f460_grp_signal(sig)
                if not mapped:
                    continue
                peri, canonical = mapped
                routes.append({
                    "series": "HC32F460", "pad": pad, "peripheral": peri,
                    "signal": canonical, "selector_kind": "fsel",
                    "selector_value": str(fno),
                    "source": "DS表2-1+2-2 Func32~63 p.33-36",
                })

    # A7: SDK 例程抽查
    have = {(rt["pad"], rt["peripheral"], rt["signal"]): int(rt["selector_value"])
            for rt in routes}
    for pad, peri, canonical, func in F460_EXPECT:
        got = have.get((pad, peri, canonical))
        assert got == func, \
            f"A7 failed: {pad} {peri}.{canonical} expect func={func}, got={got}"

    # A8: USARTx_TX/RX 全部来自 Func32..47 组共享段
    for rt in routes:
        if rt["peripheral"].startswith("USART") and rt["signal"] in ("TX", "RX"):
            assert 32 <= int(rt["selector_value"]) <= 47, \
                f"A8 failed: {rt['pad']} {rt['peripheral']}.{rt['signal']} value={rt['selector_value']}"
    return routes


def main():
    f003 = f003_routes()
    f460 = f460_extract()
    for path, routes in ((OUT_F003, f003), (OUT_F460, f460)):
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(routes)
        print(f"{path.name}: {len(routes)} routes")
    print("F003 SDK spot-checks (A4):",
          ", ".join(f"{p}:{d}=sel{v}" for p, d, v in F003_EXPECT))
    print("F460 SDK spot-checks (A7):",
          ", ".join(f"{p}:{pe}.{s}=func{v}" for p, pe, s, v in F460_EXPECT))


if __name__ == "__main__":
    main()
