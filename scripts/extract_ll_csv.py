#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_ll_csv.py — 小华 HC32 LL SDK 数据提取（hc32-ll-data）

从 xh_ll_sdk/ 下各 SDK 目录提取：
  chips.csv        DS 数据手册订购信息/型号对比表 -> 型号主表
  interrupts.csv   设备头 IRQn_Type 枚举         -> 中断向量
  dma.csv          设备头 EVT_SRC_* 枚举 + AOS 通道 -> DMA 请求路由
  peripherals.csv  SVD 外设 + hc32_ll_fcg.h 门控  -> 外设实例
  memory.csv       设备头 SRAM_BASE + 链接脚本    -> Flash/SRAM 基址

输出到 hc32-ll-data/data/csv/，列头见 hc32-data-tools/CSV_SCHEMA.md。

用法:
  python3 hc32-data-tools/extract_ll_csv.py [--sdk-root X] [--out Y] [--series HC32F460,...]

DMA 语义说明:
  LL SDK 没有 DDL 风格的 DMA_SRC_* 外设请求宏（高/低 4 位 DSL/SPL 编码不适用）。
  LL 系列 DMA 触发源 = AOS 事件：9 位事件编号（设备头 EVT_SRC_* 枚举值）写入
  AOS->DMAx_TRGSELy，任意事件可路由到任意通道。因此 dma.csv 每行 channel 填
  RM 原始通道范围（如 0-3 / 0-7），request 去重、一行一个外设信号。
"""

import argparse
import csv
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None

from support_scope import unsupported as unsupported_series

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

CHIPS_HEADER = ["part_no", "series", "freq_mhz", "flash_kb", "sram_kb", "package",
                "pin_count", "gpio", "vdd_min", "vdd_max", "uart", "spi", "i2c",
                "i2s", "can", "usb", "adc", "dac", "timer", "rtc", "wdt",
                "touchkey", "lcd", "opa", "cmp", "other", "url", "source"]
INT_HEADER = ["series", "name", "irq", "description"]
DMA_HEADER = ["series", "dma", "channel", "request", "trigger_id"]
PERI_HEADER = ["series", "instance", "kind", "base_address", "bus", "fcg_reg",
               "fcg_field", "interrupts", "notes"]
# sram_kb/extra_regions 见 CSV_SCHEMA.md：主 SRAM 大小与独立保留区
# （RAMB/RET_RAM 等）分列，DS 标称的 sram_kb 是否并入保留区因系列而异，
# 故一律以链接脚本/RM 内存映射表为准（旧列 sram_hi_base 系误认地址别名，已废弃）
MEM_HEADER = ["series", "flash_base", "sram_base", "sram_kb", "extra_regions",
               "notes"]

# EVT_SRC 中不能作为 DMA 触发源的事件（RM 表"可否选择为内部触发源"为 "-"）
DMA_EXCLUDE_PAT = re.compile(
    r"_ERR$|_PEERR$|_COLERR$|_SWI_IRQ|^EVT_SRC_MAX$")

PART_RE = re.compile(
    r"HC32[A-Z]\d{1,3}[A-Z0-9]{1,6}\s*-\s*\n?\s*"
    r"(?:LQ\w{1,7}|QFN\w{1,7}|ZFN\w{1,7}|TFN\w{1,7}|TFBGA\w{1,7}|VFBGA\w{1,7}|"
    r"BGA\w{1,7}|SOP\w{1,7}|TSSOP\w{1,7}|QSOP\w{1,7}|SSOP\w{1,7}|CSP\w{1,7}|"
    r"WLCSP\w{0,7}|KGF\w{1,7}|DIP\w{1,7}|US\w{1,7})")
# F155 等版式里型号被拆成两行且无连字符：HC32F155 / FATH-LQ32
PART_PAIR_RE = re.compile(
    r"^\s*(HC32[A-Z]\d{1,3}[A-Z0-9]{0,6})\s*\n\s*"
    r"([A-Z0-9]{1,8}-\s*(?:LQ|QFN|ZFN|TFN|TFBGA|VFBGA|BGA|SOP|CSP|KGF|DIP|US)"
    r"\w{0,7})", re.M)
PKG_CODE_RE = re.compile(
    r"(LQFP|LQG|LQ|HQFP|LQIP|QFN|TQFN|EQFN|ZFN|TFN|TFBGA|TSSOP|QSOP|SSOP|SOP|"
    r"VFBGA|BGA|CSP|WLCSP|KGF|DIP|US)(\d{0,3})")

ORD_KEYWORDS = ("订购信息", "订货信息", "订购前", "型号功能对比表", "订货代码")
BAD_PAGE_KEYWORDS = ("版本修订记录", "图索引", "表索引")

BOOL_WORDS = {"✓", "√", "Y", "YES"}

# 表格分组标题行（出现在值行之前，剥离后重新匹配）。
# 注意不能包含会被行标签复用的词（如 TIMER/Power supply/封装）。
GROUP_PREFIXES = [
    "Memory", "Connectivity", "Communcatio", "Communication Interfaces",
    "Timers", "Analog", "模拟", "通信接口", "定时器和计数器", "调试接口",
    "包装", "功能", "内核", "Clocks",
]

DS_ROW_SKIP = ("www.xhsc.com.cn", "订购前，请联系销售窗口", "版权所有")

# --------------------------------------------------------------------------
# 行标签规则: (regex[match 行首], 列名)
# --------------------------------------------------------------------------

LABEL_RULES = [
    (re.compile(r"(part\s*number|产品型号|商业代码|订货代码)", re.I), "parts"),
    (re.compile(r"(主频|频率|frequency)", re.I), "freq"),
    (re.compile(r"(program\s*flash|programflash|code\s*flash|flash\s*\(|"
                r"^flash$|^flash\b|程序\s*flash)", re.I), "flash"),
    (re.compile(r"edata\s*flash", re.I), "edataflash"),
    (re.compile(r"data\s*flash", re.I), "dataflash"),
    (re.compile(r"(sram|ram\s*\(|^ram\b)", re.I), "sram"),
    (re.compile(r"(引脚数|pin\s*count)", re.I), "pin"),
    (re.compile(r"(通用\s*io|^io\b|^i/o\b|^gpio\b|gpio数)", re.I), "gpio"),
    (re.compile(r"(最低工作电压|vdd.*min|vcc.*min)", re.I), "vdd_min"),
    (re.compile(r"(最高工作电压|vdd.*max|vcc.*max)", re.I), "vdd_max"),
    (re.compile(r"(电源电压|工作电压|电压|power\s*supply|operating\s*voltage)",
                re.I), "vdd"),
    (re.compile(r"(工作温度|温度范围|温度|t\s*emp|temp)", re.I), "temp"),
    (re.compile(r"(package|封装)", re.I), "package"),
    (re.compile(r"(^u\(s\)art\b|^usart\b|^uart\b)", re.I), "uart"),
    (re.compile(r"lpuart", re.I), "lpuart"),
    (re.compile(r"^lin\b", re.I), "lin"),
    (re.compile(r"(^quadspi|^qspi)", re.I), "qspi"),
    (re.compile(r"^spi\b", re.I), "spi"),
    (re.compile(r"^i2s\b", re.I), "i2s"),
    (re.compile(r"^i2c\b", re.I), "i2c"),
    (re.compile(r"(^can\b|can\s*fd)", re.I), "can"),
    (re.compile(r"(usb)", re.I), "usb"),
    (re.compile(r"^sdio", re.I), "sdio"),
    (re.compile(r"(12\s*[-位~]?\s*(bit\s*)?adc|^adc\b)", re.I), "adc"),
    (re.compile(r"(12\s*[-位~]?\s*(bit\s*)?dac|^dac\b)", re.I), "dac"),
    (re.compile(r"(timer\s*\d|timera|timerb|timer4|timer6|timer0|timer2|gtim|"
                r"atim|lptim|hrpwm|高级定时器|通用定时器|16\s*位定时器|"
                r"低功耗定时器|电机控制定时器|实时时钟定时器)", re.I), "timer"),
    (re.compile(r"(^rtc\b|实时时钟$)", re.I), "rtc"),
    (re.compile(r"(wdt|iwdt|wwdt|swdt|看门狗)", re.I), "wdt"),
    (re.compile(r"(keyscan|触摸|touch)", re.I), "touchkey"),
    (re.compile(r"^lcd\b", re.I), "lcd"),
    (re.compile(r"(opa|pga|放大器|运放)", re.I), "opa"),
    (re.compile(r"(^cmp\b|比较器)", re.I), "cmp"),
    (re.compile(r"^lvd\b", re.I), "lvd"),
    (re.compile(r"(^dma\b|dma控制器)", re.I), "dma"),
    (re.compile(r"^dcu\b", re.I), "dcu"),
    (re.compile(r"^otp\b", re.I), "otp"),
    (re.compile(r"^crc\b", re.I), "crc"),
    (re.compile(r"(aes|sha|trng|pvd|加密|pv)", re.I), "feat"),
]


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def norm_key(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def parse_int_token(tok):
    """'4' / '4ch' / '1unit' / '256KB' / '2MB' -> int；失败返回 None。"""
    m = re.search(r"(\d+)\s*(KB|MB|GB)?", str(tok))
    if not m:
        return None
    v = int(m.group(1))
    unit = (m.group(2) or "").upper()
    if unit == "KB":
        return v
    if unit == "MB":
        return v * 1024
    if unit == "GB":
        return v * 1024 * 1024
    return v


def parse_range(tok):
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~～]\s*(\d+(?:\.\d+)?)", str(tok))
    if m:
        return m.group(1), m.group(2)
    return None, None


def strip_group_prefix(line):
    for g in GROUP_PREFIXES:
        if line.upper().startswith(g.upper()):
            rest = line[len(g):].lstrip()
            if rest:
                return rest
    return line


VALUE_LIKE_RE = re.compile(r"^[\d✓√(-(：:]|（|^(voltages?|range)\b", re.I)
# 脚注引用（如 "（2）（3）"），并值行时不合并
FOOTNOTE_RE = re.compile(r"^[（(]\d+[）)]([（(]\d+[）)])*$")


def label_of_tokens(words):
    """词序列 -> (lab, rowname, j) 或 (None, None, None)。"""
    for k in range(1, min(7, len(words)) + 1):
        cand = " ".join(words[:k])
        for rx, lb in LABEL_RULES:
            if rx.search(cand):
                # 吸收紧随的单位括号: (KB) (M Hz) （V） （2）（3）
                rowname = cand
                j = k
                while j < len(words):
                    w = words[j]
                    if w.startswith(("(", "（")):
                        rowname = rowname + " " + w
                        j += 1
                        if not w.endswith((")", "）")) and j < len(words):
                            rowname = rowname + " " + words[j]
                            j += 1
                        continue
                    # CAN FD / LIN 等协议限定词并入标签
                    if lb == "can" and j < len(words) and \
                            words[j].upper() == "FD":
                        rowname = rowname + " FD"
                        j += 1
                    break
                return lb, rowname, j
    return None, None, None


# --------------------------------------------------------------------------
# 设备头解析
# --------------------------------------------------------------------------

ENUM_RE = re.compile(r"typedef\s+enum\s*(?:\w+\s*)?\{(.*?)\}\s*\w+\s*;", re.S)
ENUM_ENTRY_RE = re.compile(
    r"^\s*(\w+)\s*=\s*(.+?),(\s*/\*+\s*(.*?)\s*\*+/)?\s*$", re.M)


def parse_enums(text):
    out = []
    for m in ENUM_RE.finditer(text):
        body = m.group(1)
        entries = []
        for em in ENUM_ENTRY_RE.finditer(body):
            name, val, comment = em.group(1), em.group(2).strip(), em.group(4)
            try:
                v = int(val.rstrip("UuLl"), 0)
            except ValueError:
                continue
            entries.append((name, v, (comment or "").strip()))
        if entries:
            out.append(entries)
    return out


def find_device_header(sdk_root, sdk_dir):
    pats = os.path.join(sdk_root, sdk_dir, "drivers", "cmsis", "Device", "*",
                        "*", "Include", "hc32*.h")
    cands = [c for c in sorted(glob.glob(pats))
             if not os.path.basename(c).startswith(
                 ("system_", "hc32f1xx", "hc32f3xx", "hc32f4xx", "hc32m1xx",
                  "hc32m4xx", "hc32k1xx", "hc32a4xx"))]
    return cands


def find_driver_inc(sdk_root, sdk_dir):
    return os.path.join(sdk_root, sdk_dir, "drivers", "hc32_ll_driver", "inc")


# --------------------------------------------------------------------------
# interrupts.csv
# --------------------------------------------------------------------------

def extract_interrupts(series, dev_text, svd_int_desc):
    irq_entries = None
    for entries in parse_enums(dev_text):
        names = [n for n, _, _ in entries]
        if sum(1 for n in names if n.endswith("_IRQn")) > len(names) / 2:
            irq_entries = entries
            break
    if not irq_entries:
        return []
    rows = []
    for name, val, comment in irq_entries:
        short = name[:-5] if name.endswith("_IRQn") else name
        desc = ""
        if comment:
            desc = re.sub(r"^\s*\d+\s+", "", comment).strip()
        if not desc and val >= 0:
            if short in svd_int_desc:
                desc = svd_int_desc[short]
            elif re.match(r"^INT\d{3}$", short):
                desc = "共享中断向量，经 INTC INT_SEL/INT_VSSEL 选择外设中断源"
            elif not svd_int_desc:
                desc = ""
        rows.append([series, short, val, desc])
    return rows


# --------------------------------------------------------------------------
# dma.csv
# --------------------------------------------------------------------------

def extract_dma(series, dev_text, aos_text, svd_names, dmac_text=""):
    evt = None
    for entries in parse_enums(dev_text):
        names = [n for n, _, _ in entries]
        if sum(1 for n in names if n.startswith("EVT_SRC_")) > len(names) / 2 \
                and len(names) > 10:
            evt = entries
            break

    units = {}
    if aos_text:
        for m in re.finditer(r"#define\s+AOS_DMA(\d*)_(\d+)\s", aos_text):
            u = m.group(1) or "1"
            units.setdefault(u, set()).add(int(m.group(2)))

    svd_dma = sorted([n for n in svd_names if re.match(r"^DMA\w*$", n)])

    # 风格 C（F175 等旧式外设集）：DMA_<信号>_REQ 固定 TRISEL 编号
    reqs_fixed = []
    if dmac_text:
        for m in re.finditer(
                r"#define\s+DMA_(\w+?)_(?:REQ|DREQ)\s+\((0x[0-9A-Fa-f]+)UL"
                r"\s*<<\s*DMAC_CONFA0_TRISEL_POS\)", dmac_text):
            sig = m.group(1)
            reqs_fixed.append((sig, int(m.group(2), 16)))
    if not evt and reqs_fixed:
        dma_list = svd_dma or ["DMAC"]
        rows = []
        seen = set()
        for sig, num in sorted(set(reqs_fixed), key=lambda x: x[1]):
            if sig in seen:
                continue
            seen.add(sig)
            for d in dma_list:
                rows.append([series, d, "0-3", sig, str(num)])
        note = ("F175 风格：DMA_<信号>_REQ 固定 TRISEL 编号（0x40~0x55），"
                "写入通道 CONFA0.TRISEL 选择请求，通道 0~3 自由路由；"
                "trigger_id 为去移位后的原始请求编号")
        return rows, note
    if not evt:
        return [], "device header 无 EVT_SRC 枚举且无 DMA_*_REQ 宏"

    dma_list = svd_dma or (["DMA1"] if units else [])
    if not dma_list:
        return [], "SVD 无 DMA 实例且无 AOS 通道定义"

    reqs = sorted(set((n[len("EVT_SRC_"):], v) for n, v, _ in evt
                      if n.startswith("EVT_SRC_")
                      and not DMA_EXCLUDE_PAT.search(n)))
    rows = []
    for d in dma_list:
        key = norm_key(d)
        chs = None
        for u, s in units.items():
            if key == norm_key("DMA" + u):
                chs = sorted(s)
                break
        if chs is None and units:
            chs = sorted(set().union(*units.values()))
        if not chs:
            chs = [0, 1, 2, 3]
        chan = "%d-%d" % (min(chs), max(chs))
        for r, v in reqs:
            rows.append([series, d, chan, r, str(v)])
    note = ("LL 无 DMA_SRC_* 宏；EVT_SRC_* 经 AOS DMAx_TRGSELy 路由，任意请求"
            "可路由任意通道，channel 填 RM 通道范围；trigger_id 为 EVT_SRC_* "
            "枚举数值（十进制，即写 AOS->DMAx_TRGSELy 的 9 位事件编号）")
    return rows, note


# --------------------------------------------------------------------------
# peripherals.csv
# --------------------------------------------------------------------------

def kind_of(instance):
    n = instance.upper()
    m = re.match(r"^(TMR[0246])", n)
    if m:
        return m.group(1).lower()
    k = re.sub(r"(\d+|_ECER|_COMMON|_T)$", "", n).lower()
    return k or n.lower()


def extract_fcg(sdk_root, sdk_dir):
    path = os.path.join(find_driver_inc(sdk_root, sdk_dir), "hc32_ll_fcg.h")
    if not os.path.exists(path):
        return {}
    txt = open(path, encoding="utf-8", errors="ignore").read()
    fcg = {}
    for m in re.finditer(r"#define\s+FCG(\d)_PERIPH_(\w+)\s+\((\w+)\)", txt):
        fcg[norm_key(m.group(2))] = ("FCG" + m.group(1), m.group(3))
    return fcg


def extract_peripherals(series, svd_path, fcg):
    tree = ET.parse(svd_path)
    peris = tree.getroot().findall("peripherals/peripheral")
    by_name = {(p.findtext("name") or "").strip(): p for p in peris}

    def base_address(p, seen):
        base = (p.findtext("baseAddress") or "").strip()
        if base:
            return "0x%08X" % int(base, 0)
        parent = p.get("derivedFrom")
        if parent not in by_name or parent in seen:
            raise ValueError(f"{series}: cannot resolve base address for {p.findtext('name')}")
        return base_address(by_name[parent], seen | {parent})

    rows = []
    for p in peris:
        name = (p.findtext("name") or "").strip()
        base = base_address(p, {name})
        deriv = p.get("derivedFrom")
        ints = []
        for it in p.findall("interrupt"):
            iname = (it.findtext("name") or "").strip()
            if iname.endswith("_IRQn"):
                iname = iname[:-5]
            ints.append(iname)
        notes = []
        if deriv:
            notes.append("derivedFrom " + deriv)
        fc = fcg.get(norm_key(name))
        fcg_reg, fcg_field = fc if fc else ("", "")
        rows.append([series, name, kind_of(name), base, "", fcg_reg, fcg_field,
                     ";".join(ints), "; ".join(notes)])
    return rows, len(peris)


# --------------------------------------------------------------------------
# memory.csv
# --------------------------------------------------------------------------

def parse_linker(sdk_root, sdk_dir):
    cands = []
    for sub in ("Source/GCC/linker", "Source/IAR/linker"):
        cands += glob.glob(os.path.join(sdk_root, sdk_dir, "drivers", "cmsis",
                                        "Device", "*", "*", sub, "*"))
    best = None  # (flash, regions, fname)
    for f in sorted(cands):
        txt = open(f, encoding="utf-8", errors="ignore").read()
        flash = None
        regions = []
        if f.endswith(".ld"):
            for m in re.finditer(
                    r"(\w+)\s*\(\s*[rwx]+\s*\)\s*:\s*ORIGIN\s*=\s*"
                    r"(0x[0-9A-Fa-f]+)\s*,\s*LENGTH\s*=\s*([\d.]+)\s*(K|M|G)?",
                    txt):
                name, origin, ln, unit = m.groups()
                mult = {"K": 1, "M": 1024, "G": 1024 * 1024}.get(
                    (unit or "K").upper(), 1)
                kb = float(ln) * mult
                regions.append((name, int(origin, 16), kb))
                if name.upper().startswith(("FLASH", "ROM")):
                    if flash is None or kb > flash[2]:
                        flash = (name, int(origin, 16), kb)
        else:
            def sym(n):
                m = re.search(r"define\s+symbol\s+__ICFEDIT_region_%s__\s*=\s*"
                              r"(0x[0-9A-Fa-f]+)" % n, txt)
                return int(m.group(1), 16) if m else None
            rs, re_, ss, se = sym("ROM_start"), sym("ROM_end"), \
                sym("RAM_start"), sym("RAM_end")
            if rs is not None and re_ is not None:
                kb = (re_ - rs + 1) / 1024
                regions.append(("FLASH", rs, kb))
                flash = ("FLASH", rs, kb)
            if ss is not None and se is not None:
                kb = (se - ss + 1) / 1024
                regions.append(("RAM", ss, kb))
        if regions:
            fk = flash[2] if flash else 0
            if best is None or fk > (best[0][2] if best[0] else 0):
                best = (flash, regions, os.path.basename(f))
    if best is None:
        return None, [], ""
    return best


def extract_memory(series, sdk_root, sdk_dir):
    flash, regions, fname = parse_linker(sdk_root, sdk_dir)
    # 主 SRAM 以链接脚本 RAM 区为准：设备头 SRAM_BASE 是系列级别名基址
    # （如 M441 头写 0x1FFF8000，实际 32K 主区从 0x1FFFC000 起），
    # 且跨 0x20000000 的主区是 SRAMH/SRAM0 两个物理 bank（RM 内存映射表），
    # 并非地址别名，故不再用 SRAM_BASE 覆盖链接脚本基址。
    ram_region = next((r for r in regions
                       if r[0].upper().startswith("RAM")
                       and "RET" not in r[0].upper()
                       and "RAMB" != r[0].upper()), None)
    sram = ram_region[1] if ram_region else None
    fb = "%#010x" % flash[1] if flash else "0x00000000"
    sb = "%#010x" % sram if sram is not None else ""
    skb = ""
    if ram_region is not None:
        kb = ram_region[2]
        skb = "%d" % int(kb) if kb == int(kb) else "%g" % kb
    # 独立保留区（RAMB/RET_RAM）：链接脚本中与主区地址不连续的独立 RAM bank，
    # 语义为掉电保持 SRAM（RM 表，非主区的一部分，也非地址别名）
    extras = []
    if ram_region is not None:
        main_end = ram_region[1] + int(ram_region[2] * 1024)
        for name, origin, kb in regions:
            if name.upper() not in ("RAMB", "RET_RAM"):
                continue
            if origin < main_end and ram_region[1] < origin + int(kb * 1024):
                continue
            kb_s = "%d" % int(kb) if kb == int(kb) else "%g" % kb
            extras.append("%s@%#x/%sK" % (name, origin, kb_s))
    notes = []
    if fname:
        notes.append("linker " + fname)
    if ram_region is not None and \
            ram_region[1] < 0x20000000 < main_end:
        notes.append("主区跨 0x20000000 = SRAMH/SRAM0 两个物理 bank"
                     "（见 RM 内存映射表），非地址别名")
    return [series, fb, sb, skb, ";".join(extras), "; ".join(notes)]


# --------------------------------------------------------------------------
# chips.csv（DS PDF）
# --------------------------------------------------------------------------

class DsDoc:
    def __init__(self, path):
        self.path = path
        self.reader = PdfReader(path)
        self._texts = None

    def texts(self):
        if self._texts is None:
            self._texts = []
            for p in self.reader.pages:
                try:
                    self._texts.append(p.extract_text() or "")
                except Exception:
                    self._texts.append("")
        return self._texts


def find_ordering_pages(doc):
    groups, cur = [], []
    for i, t in enumerate(doc.texts()):
        if any(b in t for b in BAD_PAGE_KEYWORDS):
            continue
        has_kw = any(k in t for k in ORD_KEYWORDS)
        n_parts = len(parse_parts(t))
        if has_kw and n_parts >= 1:
            if cur and i == cur[-1] + 1:
                cur.append(i)
            else:
                if cur:
                    groups.append(cur)
                cur = [i]
        elif cur and i == cur[-1] + 1 and n_parts >= 2:
            cur.append(i)
    if cur:
        groups.append(cur)
    return groups


def parse_parts(text):
    parts = []
    for m in PART_RE.finditer(text):
        s = re.sub(r"\s+", "", m.group(0)).replace("-", "-", 1)
        s = re.sub(r"-(?=LQ|QFN|ZFN|TFN|VFBGA|BGA|SOP|TSSOP|QSOP|SSOP|CSP|"
                   r"WLCSP|KGF|DIP|US)", "-", s, count=1)
        if s not in parts:
            parts.append(s)
    if len(parts) < 2:
        for m in PART_PAIR_RE.finditer(text):
            s = m.group(1) + re.sub(r"\s+", "", m.group(2))
            if s not in parts:
                parts.append(s)
    return parts


def split_cells(content, nparts):
    """行内容 -> nparts 个单元格；无法可靠对齐时返回 None。"""
    toks = content.split()
    if not toks:
        return None
    if len(toks) == nparts:
        return toks
    if len(toks) == 1:
        return toks * nparts
    if len(toks) > nparts and len(toks) % nparts == 0:
        k = len(toks) // nparts
        return [" ".join(toks[i * k:(i + 1) * k]) for i in range(nparts)]
    cells = []
    plus = False
    for t in toks:
        if t in ("+", "＋") and cells:
            cells[-1] += " +"
            plus = True
            continue
        if plus:
            cells[-1] += " " + t
            plus = False
        elif re.match(r"^[\d✓√(（]", t) or not cells:
            cells.append(t)
        else:
            cells[-1] += " " + t
    if len(cells) == nparts:
        return cells
    if len(cells) == 1:
        return cells * nparts  # 共享值
    return None


def adc_cell(c, ctx, other, rowname=""):
    m = re.search(r"(\d+)\s*unit", c, re.I)
    mstar = re.search(r"(\d+)\s*\*\s*(\d+)\s*ch", c, re.I)
    mch = re.search(r"(\d+)\s*ch", c, re.I)
    if re.search(r"12\s*[-位~]?\s*(bit)?", rowname + " " + c, re.I):
        other.add("ADC 12bit")
    if m:
        ctx["adc"] = ctx["adc"] or m.group(1)
        if mch:
            other.add("ADC %sch" % mch.group(1))
    elif mstar:
        ctx["adc"] = ctx["adc"] or mstar.group(1)
        other.add("ADC %sch" % mstar.group(2))
    elif mch:
        if ctx["adc"]:
            other.add("ADC %sch" % mch.group(1))
        else:
            ctx["adc"] = mch.group(1)
    else:
        v = parse_int_token(c)
        if v:
            if ctx["adc"]:
                other.add("ADC %sch" % v)
            else:
                ctx["adc"] = v


def classify(lab, rowname, cells, ctxs, base, single=False):
    for i, cell in enumerate(cells):
        c = cell.strip()
        ctx = ctxs[base + i]
        other = ctx["other"]
        if c in ("", "-"):
            continue
        # 单型号表里布尔特性行出现数字（列合并伪影）时按存在处理
        if single and lab in ("feat", "rtc", "wdt", "touchkey", "crc") \
                and re.fullmatch(r"\d+", c):
            c = "✓"
        if lab == "freq":
            ctx["freq"] = ctx["freq"] or (parse_int_token(c) or "")
        elif lab == "flash":
            ctx["flash"] = ctx["flash"] or (parse_int_token(c) or "")
        elif lab == "dataflash":
            v = parse_int_token(c)
            if v:
                other.add("DataFlash=%sK" % v)
        elif lab == "edataflash":
            v = parse_int_token(c)
            if v:
                other.add("EDataFlash=%sK" % v)
        elif lab == "sram":
            ctx["sram"] = ctx["sram"] or (parse_int_token(c) or "")
        elif lab == "pin":
            ctx["pin"] = ctx["pin"] or (parse_int_token(c) or "")
        elif lab == "gpio":
            ctx["gpio"] = ctx["gpio"] or (parse_int_token(c) or "")
        elif lab == "vdd_min":
            ctx["vdd_min"] = ctx["vdd_min"] or re.sub(r"[Vv]$", "", c)
        elif lab == "vdd_max":
            ctx["vdd_max"] = ctx["vdd_max"] or re.sub(r"[Vv]$", "", c)
        elif lab == "vdd":
            lo, hi = parse_range(c)
            if lo:
                ctx["vdd_min"] = ctx["vdd_min"] or lo
                ctx["vdd_max"] = ctx["vdd_max"] or hi
        elif lab == "temp":
            other.add("temp " + c)
        elif lab == "package":
            ms = PKG_CODE_RE.findall(c.replace("（", "(").replace("）", ")"))
            if ms:
                pkg = ms[0][0] + ms[0][1]
                ctx["package"] = ctx["package"] or pkg
                if ms[0][1]:
                    ctx["pin"] = ctx["pin"] or ms[0][1]
        elif lab == "uart":
            v = parse_int_token(c)
            ctx["uart"] = ctx["uart"] or (v or "")
            if "LIN" in c.upper():
                other.add("USART LIN")
        elif lab == "lpuart":
            v = parse_int_token(c)
            if v:
                other.add("LPUART=%s" % v)
        elif lab == "lin":
            v = parse_int_token(c)
            if v:
                other.add("LIN=%s" % v)
        elif lab == "qspi":
            v = parse_int_token(c)
            if v:
                other.add("QSPI=%s" % v)
        elif lab == "spi":
            ctx["spi"] = ctx["spi"] or (parse_int_token(c) or "")
        elif lab == "i2s":
            ctx["i2s"] = ctx["i2s"] or (parse_int_token(c) or "")
        elif lab == "i2c":
            ctx["i2c"] = ctx["i2c"] or (parse_int_token(c) or "")
        elif lab == "can":
            v = parse_int_token(c)
            ctx["can"] = ctx["can"] or (v or "")
            if "FD" in c.upper():
                other.add("CAN-FD")
        elif lab == "usb":
            v = parse_int_token(c)
            ctx["usb"] = ctx["usb"] or (v or "")
        elif lab == "adc":
            adc_cell(c, ctx, other, rowname)
        elif lab == "dac":
            mstar = re.search(r"(\d+)\s*\*\s*(\d+)\s*ch", c, re.I)
            mu = re.search(r"(\d+)\s*unit", c, re.I)
            mch = re.search(r"(\d+)\s*ch", c, re.I)
            if mstar:
                ctx["dac"] = ctx["dac"] or mstar.group(1)
                other.add("DAC %sch" % mstar.group(2))
            elif mu:
                ctx["dac"] = ctx["dac"] or mu.group(1)
                if mch:
                    other.add("DAC %sch" % mch.group(1))
            elif mch:
                ctx["dac"] = ctx["dac"] or mch.group(1)
            else:
                ctx["dac"] = ctx["dac"] or (parse_int_token(c) or "")
        elif lab == "timer":
            v = parse_int_token(c)
            if v:
                if re.search(r"rtc|实时时钟", rowname, re.I):
                    ctx["rtc"] = ctx["rtc"] or v
                else:
                    words = [w for w in rowname.split()
                             if re.search(r"[A-Za-z\u4e00-\u9fff]", w)]
                    nm = words[-1] if words else "TMR"
                    ctx["timer_list"].add("%s=%s" % (nm, v))
        elif lab == "rtc":
            v = parse_int_token(c)
            ctx["rtc"] = ctx["rtc"] or ("1" if (v or c in BOOL_WORDS) else "")
        elif lab == "wdt":
            kind = None
            up = c.upper()
            if up.startswith("IWDT") or "独立" in c:
                kind = "IWDT"
            elif up.startswith("WWDT") or "窗口" in c:
                kind = "WWDT"
            elif up.startswith("SWDT") or "安全" in c:
                kind = "SWDT"
            v = parse_int_token(c)
            if kind:
                other.add("%s=%s" % (kind, v if v else "1"))
            elif v:
                ctx["wdt"] = ctx["wdt"] or v
            elif c in BOOL_WORDS:
                ctx["wdt"] = ctx["wdt"] or "1"
        elif lab == "touchkey":
            if re.search(r"keyscan", rowname, re.I):
                v = parse_int_token(c)
                other.add("KEYSCAN=%s" % (v or "1"))
            else:
                v = parse_int_token(c)
                ctx["touchkey"] = ctx["touchkey"] or \
                    (v or ("1" if c in BOOL_WORDS else ""))
        elif lab == "lcd":
            v = parse_int_token(c)
            ctx["lcd"] = ctx["lcd"] or (v or ("1" if c in BOOL_WORDS else ""))
        elif lab == "opa":
            ctx["opa"] = ctx["opa"] or (parse_int_token(c) or "")
        elif lab == "cmp":
            ctx["cmp"] = ctx["cmp"] or (parse_int_token(c) or "")
        elif lab == "lvd":
            v = parse_int_token(c)
            if v:
                other.add("LVD=%s" % v)
        elif lab in ("dma", "dcu", "otp", "crc", "feat", "sdio"):
            nm = rowname.strip() or lab.upper()
            nm = re.sub(r"\s+", "", nm)
            if c in BOOL_WORDS:
                other.add(nm)
            else:
                v = parse_int_token(c)
                if v:
                    unit = "KB" if re.search(r"\d\s*KB", c, re.I) else ""
                    other.add("%s=%s%s" % (nm, v, unit))


def new_ctx():
    return {"freq": "", "flash": "", "sram": "", "pin": "", "gpio": "",
            "vdd_min": "", "vdd_max": "", "package": "", "uart": "", "spi": "",
            "i2c": "", "i2s": "", "can": "", "usb": "", "adc": "", "dac": "",
            "rtc": "", "wdt": "", "touchkey": "", "lcd": "", "opa": "",
            "cmp": "", "timer_list": set(), "other": set()}


def _ok_ints(toks, lo, hi):
    return all(t.isdigit() and lo <= int(t) <= hi for t in toks)


ORPHAN_VALIDATORS = [
    ("freq", lambda ts: _ok_ints(ts, 8, 600)),
    ("flash", lambda ts: _ok_ints(ts, 16, 8192)),
    ("otp", lambda ts: _ok_ints(ts, 16, 4096)),
    ("gpio", lambda ts: _ok_ints(ts, 4, 512)),
    ("temp", lambda ts: all(TEMP_RE.search(t) for t in ts)),
    ("uart", lambda ts: _ok_ints(ts, 0, 24)),
    ("spi", lambda ts: _ok_ints(ts, 0, 16)),
    ("i2c", lambda ts: _ok_ints(ts, 0, 16)),
    ("i2s", lambda ts: _ok_ints(ts, 0, 16)),
    ("can", lambda ts: _ok_ints(ts, 0, 16)),
    ("qspi", lambda ts: _ok_ints(ts, 0, 8)),
    ("timer", lambda ts: _ok_ints(ts, 0, 64)),
    ("adc", lambda ts: all(re.search(r"\d+\s*(unit|ch)", t, re.I)
                           for t in ts)),
    ("dac", lambda ts: all(re.search(r"\d+\s*(unit|ch)", t, re.I)
                           for t in ts)),
    ("dma", lambda ts: all(re.search(r"\d+\s*(unit|ch|\*)", t, re.I)
                           for t in ts)),
    ("sram", lambda ts: all(re.fullmatch(r"[0-9.+]+\s*(KB|kb|Mb)?", t)
                            and parse_int_token(t) is not None
                            and 2 <= parse_int_token(t) <= 4096
                            for t in ts)),
]
# 全 1 值行（DVP/EXMC 之类的未知外设计数）只允许喂布尔特性列
BOOL_ONLY_LABS = ("rtc", "wdt", "touchkey", "lcd")


TEMP_RE = re.compile(r"-?\d+\s*~\s*-?\d+|℃")
RANGE_RE = re.compile(r"\d+(?:\.\d+)?\s*[~～-]\s*\d+(?:\.\d+)?")


def cells_plausible(lab, cells):
    """单元格值是否在该列的合理域内（防打散版式的串值）。"""
    if cells is None:
        return False
    vals = []
    for c in cells:
        v = parse_int_token(c)
        if v is not None:
            vals.append(v)
    if lab == "temp":
        return all(TEMP_RE.search(c) for c in cells)
    if lab in ("vdd", "vdd_min", "vdd_max"):
        def _vok(c):
            if c in ("", "-"):
                return True
            if parse_range(c) != (None, None):
                return True
            return bool(re.fullmatch(r"\d+(?:\.\d+)?", c.strip()))
        return all(_vok(c) for c in cells)
    if not vals:
        return lab in ("package", "parts", "feat")
    if lab == "flash":
        return all(8 <= v <= 8192 for v in vals)
    if lab == "sram":
        return all(2 <= v <= 4096 for v in vals)
    if lab == "gpio":
        return all(4 <= v <= 512 for v in vals)
    if lab in ("uart", "spi", "i2c", "i2s", "can", "qspi", "lvd"):
        return all(0 <= v <= 32 for v in vals)
    if lab in ("adc", "dac"):
        for c in cells:
            if re.search(r"bit|位", c, re.I) and \
                    not re.search(r"ch|unit", c, re.I):
                return False
        return all(v <= 64 for v in vals)
    if lab == "timer":
        return all(v <= 128 for v in vals)
    if lab == "freq":
        return all(8 <= v <= 600 for v in vals)
    return True


def parse_ctx_group(series, doc, pages, n_all):
    """一组订购页 -> (all_parts, ctxs)。n_all 从本组型号数得出。"""
    raw_lines = []
    for pg in pages:
        raw_lines += doc.texts()[pg].splitlines()
    text = "\n".join(raw_lines)
    all_parts = parse_parts(text)
    ctxs = [new_ctx() for _ in range(max(n_all, len(all_parts)))]

    labeled = []  # (lab, rowname, content)
    orphans = []  # 未对上标签的整行值（版式打散的表格）
    for ln in raw_lines:
        s = ln.strip()
        if not s or any(k in s for k in DS_ROW_SKIP):
            continue
        s2 = strip_group_prefix(s)
        words = s2.split()
        lab, rowname, j = label_of_tokens(words)
        if lab:
            labeled.append([lab, rowname, " ".join(words[j:])])
            continue
        # 续行：仅当上一行值未填满且本行像值时并入（过滤脚注引用）
        toks = [t for t in s2.split() if not FOOTNOTE_RE.match(t)]
        if labeled and toks and len(labeled[-1][2].split()) < n_all \
                and VALUE_LIKE_RE.match(toks[0]):
            labeled[-1][2] = (labeled[-1][2] + " " + " ".join(toks)).strip()
        elif n_all > 1 and (len(toks) == n_all
                            or (len(toks) > n_all and len(toks) % n_all == 0)):
            orphans.append(toks)

    # 先按标签解析
    hungry = []  # 有标签但没拿到值的行
    for lab, rowname, content in labeled:
        if lab == "parts":
            continue
        if lab == "adc":
            # 剥离行首的 "12bit"/"12位 ADC" 位宽前缀，避免破坏逐列对齐
            content = re.sub(r"^\s*12\s*[-位~]?\s*(bit\s*)?(adc\s*)?", "",
                             content, flags=re.I)
        elif lab == "package":
            content = re.sub(r"^\s*(type|类型|形式)\b\s*", "", content,
                             flags=re.I)
        elif lab in ("vdd", "vdd_min", "vdd_max"):
            content = re.sub(r"^\s*(voltages?|range)\b\s*", "", content,
                             flags=re.I)
            content = re.sub(r"\s*([~～-])\s*", r"\1", content)
        cells = split_cells(content, n_all)
        # 单值行（1~4 个 token 且含范围/温度）视为整行共享
        if cells is not None and n_all > 1 and lab in ("vdd", "vdd_min",
                                                       "vdd_max", "temp") \
                and len(content.split()) <= 4 \
                and (RANGE_RE.search(content) or "℃" in content):
            cells = [content.strip()] * n_all
        if cells is None or not any(c.strip() for c in cells) \
                or not cells_plausible(lab, cells):
            hungry.append((lab, rowname))
            continue
        classify(lab, rowname, cells, ctxs, 0, single=(n_all == 1))

    # 打散版式（如 F472）：孤儿值行按语义验证器分配给饥饿标签
    hungry_labs = lambda: [h[0] for h in hungry]
    # 全 1 行最后处理，避免 DVP/EXMC 之类未知计数误占布尔/计数列
    orphans.sort(key=lambda ts: all(t == "1" for t in ts))
    for toks in orphans:
        all_ones = all(t == "1" for t in toks)
        for lab, vfn in ORPHAN_VALIDATORS:
            if lab not in hungry_labs():
                continue
            if all_ones and lab not in BOOL_ONLY_LABS:
                continue
            if vfn(toks):
                classify(lab, lab, toks, ctxs, 0, single=(n_all == 1))
                hungry = [h for h in hungry if h[0] != lab]
                break
    # 电压/温度范围的短孤儿行（如 "1.8 ~ 3.6"）按共享值广播
    for toks in orphans:
        j = " ".join(toks)
        if RANGE_RE.search(j) and not TEMP_RE.search(j) \
                and "vdd" in hungry_labs():
            classify("vdd", "vdd", [j] * n_all, ctxs, 0, single=(n_all == 1))
        elif TEMP_RE.search(j) and "temp" in hungry_labs():
            classify("temp", "temp", [j] * n_all, ctxs, 0, single=(n_all == 1))
    return all_parts, ctxs


def freq_fallback(doc):
    """从 DS 前 3 页找 '最高工作频率 32MHz' 之类的最高主频。"""
    for pg in range(min(3, len(doc.texts()))):
        t = doc.texts()[pg]
        m = re.search(r"(?:最高|最大)?(?:工作)?(?:主频|频[率率])[^0-9\n]{0,12}"
                      r"(\d+)\s*MHz", t)
        if m:
            return m.group(1)
    return ""


def merge_ctx(dst, src):
    for k, v in src.items():
        if k == "timer_list" or k == "other":
            dst[k].update(v)
        elif not dst[k] and v:
            dst[k] = v


def build_chip_rows(series, parts, all_parts, ctxs):
    rows = []
    for p in parts:
        i = all_parts.index(p)
        ctx = ctxs[i]
        part_no = p.split("-")[0]
        pkg, pin = ctx["package"], ctx["pin"]
        m = PKG_CODE_RE.search(p)
        suffix_pkg = m.group(0) if m else ""
        suffix_pin = m.group(2) if m else ""
        if suffix_pkg.startswith("LQG"):
            suffix_pkg = "LQFP" + suffix_pkg[3:]
        elif suffix_pkg.startswith("LQ") and not suffix_pkg.startswith("LQFP"):
            suffix_pkg = "LQFP" + suffix_pkg[2:]
        # 优先用带数字的封装；行解析缺数字时退回型号后缀
        if not pkg or (not re.search(r"\d", pkg) and re.search(r"\d", suffix_pkg)):
            pkg = pkg or suffix_pkg
            if not re.search(r"\d", pkg):
                pkg = suffix_pkg
        if not pin:
            pin = suffix_pin
        timer_vals = sorted(ctx["timer_list"])
        timer = ";".join(t.split("=")[1] for t in timer_vals)
        other = set(ctx["other"])
        if timer_vals:
            other.add(";".join(timer_vals))
        rows.append([
            part_no, series, ctx["freq"],
            "" if (ctx["flash"] and ctx["freq"]
                   and str(ctx["flash"]) == str(ctx["freq"])) else ctx["flash"],
            ctx["sram"], pkg, pin,
            ctx["gpio"], ctx["vdd_min"], ctx["vdd_max"], ctx["uart"],
            ctx["spi"], ctx["i2c"], ctx["i2s"], ctx["can"], ctx["usb"],
            ctx["adc"], ctx["dac"], timer, ctx["rtc"], ctx["wdt"],
            ctx["touchkey"], ctx["lcd"], ctx["opa"], ctx["cmp"],
            "|".join(sorted(other)), "", "DS",
        ])
    return rows


def extract_chips_for_series(series, doc, groups, part_prefix=""):
    # 找到型号最多的一组作为列基准（订购表）
    main = max(groups, key=lambda g: len(parse_parts(
        "\n".join(doc.texts()[i] for i in g))))
    all_parts = parse_parts(
        "\n".join(doc.texts()[i] for i in main))
    n_all = len(all_parts)
    if any(not p.startswith(series) for p in all_parts):
        parts = [p for p in all_parts if p.startswith(series)]
    else:
        parts = all_parts
    if part_prefix:
        parts = [p for p in parts if p.startswith(part_prefix)]
    if not parts:
        return [], ["未找到型号"]

    # 合并所有组的上下文（先解析的组优先，空字段由后续组补齐）
    merged = [new_ctx() for _ in range(n_all)]
    warns = []
    used = 0
    for g in groups:
        gp, gc = parse_ctx_group(series, doc, g, n_all)
        if len(gp) != n_all:
            continue  # 型号数不一致的组（页眉/引脚表噪音）跳过
        used += 1
        for i in range(n_all):
            merge_ctx(merged[i], gc[i])
    if used == 0:
        warns.append("订购组列数不一致，仅用主组")
        _, gc = parse_ctx_group(series, doc, main, n_all)
        for i in range(n_all):
            merged[i] = gc[i]
    if not merged[0]["freq"]:
        fb = freq_fallback(doc)
        if fb:
            for c in merged:
                c["freq"] = fb
    return build_chip_rows(series, parts, all_parts, merged), warns


def extract_chips(series, sdk_root, sdk_dir, no_ds_note=None):
    if PdfReader is None:
        return [], ["pypdf 不可用"]
    ds = sorted(glob.glob(os.path.join(sdk_root, sdk_dir, "documents",
                                       "DS_*.pdf")))
    if not ds:
        if no_ds_note:
            row = [""] * len(CHIPS_HEADER)
            row[0], row[1] = "", series
            row[25] = no_ds_note
            row[27] = "none"
            return [row], []
        return [], ["无 DS 数据手册"]
    doc = DsDoc(ds[0])
    groups = find_ordering_pages(doc)
    if not groups:
        return [], ["未找到订购信息页"]
    rows, warns = extract_chips_for_series(series, doc, groups)
    if not rows:
        return [], warns or ["订购页解析失败"]
    seen, out = set(), []
    for r in rows:
        if r[0] and r[0] not in seen:
            seen.add(r[0])
            out.append(r)
    return out, warns


# --------------------------------------------------------------------------
# SVD
# --------------------------------------------------------------------------

def workspace_root():
    # scripts/ 在 hc32-data-tools 仓库内，工作区根还要再上两级
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def find_svd(series):
    p = os.path.join(workspace_root(), "hc32-ll-data", "svd", series + ".svd")
    return p if os.path.exists(p) else None


def svd_data(svd_path):
    """(int_desc, peri_names, int_count, peris_xml)。"""
    int_desc, names = {}, []
    if not svd_path:
        return int_desc, names, 0
    try:
        tree = ET.parse(svd_path)
    except Exception:
        return int_desc, names, 0
    root = tree.getroot()
    for it in root.findall(".//interrupt"):
        n = (it.findtext("name") or "").strip()
        d = (it.findtext("description") or "").strip()
        if n.endswith("_IRQn"):
            n = n[:-5]
        if n and d:
            int_desc[n] = d
    names = [(p.findtext("name") or "").strip()
             for p in root.findall("peripherals/peripheral")]
    return int_desc, names, len(root.findall(".//interrupt"))


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def write_csv(out_dir, name, header, rows):
    # lineterminator="\n"：仓库行尾策略为全 LF（.gitattributes），
    # csv 模块默认 CRLF 会污染工作区 diff。
    path = os.path.join(out_dir, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return path, len(rows)


def validate(all_dma, all_peri, stats):
    """按 CSV_SCHEMA.md 做基本校验，返回告警列表。"""
    warns = []
    seen = {}
    for s, d, ch, req, tid in all_dma:
        key = (s, d, req)
        if key in seen:
            warns.append("dma request 重复: %s/%s/%s" % key)
        seen[key] = True
        if not re.match(r"^\d+-\d+$|^\d+$", ch):
            warns.append("dma channel 非法: %s/%s/%s" % (s, d, ch))
        if not re.match(r"^\d+$", str(tid)):
            warns.append("dma trigger_id 非空数值缺失: %s/%s/%s" % (s, d, req))
    for st in stats:
        if st["peripherals"] and st["peripherals"] < st["svd_peripherals"]:
            warns.append("%s: peripherals(%d) < SVD(%d)"
                         % (st["series"], st["peripherals"],
                            st["svd_peripherals"]))
    return warns


NOTES_TMPL = """# LL 侧 CSV 提取说明（hc32-data-tools/extract_ll_csv.py 自动生成）

来源：`xh_ll_sdk/` 下 27 个 SDK 目录（设备头、hc32_ll_driver、SVD、DS/RM PDF）。
HC32F175 已暂停支持（IP 多代缝合，见 hc32-data-tools/scripts/support_scope.py），
默认跳过，本目录各表不含其行；`--series HC32F175` 可强制单独重提。
输出：本目录 5 个 CSV，列头见 `hc32-data-tools/CSV_SCHEMA.md`。可重复运行：
`python3 hc32-data-tools/extract_ll_csv.py [--sdk-root X] [--out Y] [--series ...]`

## 各系列行数统计

| 系列 | chips | interrupts | dma | peripherals | SVD 外设数 | FCG 匹配 | 备注 |
|---|---:|---:|---:|---:|---:|---:|---|
{rows}

## DMA 编码语义结论

**LL SDK 与旧 DDL 的 DMA_SRC_*（DSL/SPL 高低 4 位编码）完全不同**：所有 LL 系列
的 `hc32_ll_dma.h` 只有 `DMA_SRC_ADDR_FIX/INC/DEC` 之类的通道控制宏，没有外设
请求宏。LL 的 DMA 触发源走 AOS（外设事件路由）：

- 触发源 = 设备头 `EVT_SRC_*` 枚举值（9 位事件编号，0x000~0x1FF），写入
  `AOS->DMAx_TRGSELy`（多单元系列）或 `AOS->DMA_TRGSELy`（单单元系列）；
- 任意 DMA 请求可路由到任意通道（F460 RM 第 11 章 AOS、第 14 章 DMA 证实，
  TRGSEL 复位值 0x1FF，字段 TRGSEL[8:0]）；
- 因此 dma.csv 每行 `channel` 填 RM 原始通道范围（如 `0-3`、`0-7`），一行一个
  外设请求信号，`request` 为 `EVT_SRC_` 前缀去掉后的信号名；
- `trigger_id` 列 = 写入 TRGSEL 的事件编号数值（十进制）：AOS 系列为
  `EVT_SRC_*` 枚举值；F175 为 `DMA_<信号>_REQ` 去移位后的原始请求编号
  （0x40~0x55，即十进制 64~85，写通道 CONFA0.TRISEL）。
- 排除了 RM 表 10-2 中"不可选择为内部触发源"的事件：`*_ERR`、`*_PEERR`、
  `*_COLERR`、软件中断 `*_SWI_IRQ`；DMA 自身 TC/BTC 事件按 RM 可作触发源，保留；
- 通道数取自 `hc32_ll_aos.h` 的 `AOS_DMA<x>_<n>` 宏（按控制器记录），
  DMA 实例名取自 SVD；F115/F120 无 DMA 外设，dma 行数为 0。
- 例外（已暂停支持的 F175，旧式外设集，仅作 SDK 语义记录）：无 AOS/EVT_SRC，
  DMAC 用固定请求编号——`DMA_<信号>_REQ = (0xNN << DMAC_CONFA0_TRISEL_POS)`
  （0x40~0x55），写入各通道 CONFA0.TRISEL 选择请求源，4 个通道（CH0~3）仍为
  自由路由。

## 其它假设与说明

- interrupts.csv：以设备头 `IRQn_Type` 为准。共享向量系列（F460/F45x/A460/F448/
  A448/F467/F4A0/F4A2/F4A8/A4A8/A4A0/M441 及 M0+ 的 INTxxx 段）的向量名 INTnnn
  不对应具体外设，description 统一标注"共享中断向量"；外设中断源→向量的路由是
  运行时经 INTC INT_SEL/INT_VSSEL 配置的，无法静态给出，peripherals.csv 的
  interrupts 列对这些系列留空。SVD 中断节点数>0 的系列（F334/F336/F431/M413/
  K116/K118/M458/F472/A472）用 SVD 补充 description，peripherals 的 interrupts
  列填 SVD 外设节点下的中断源名。
- peripherals.csv：地址取自 `hc32-ll-data/svd/*.svd`（缺少显式 baseAddress 的派生外设才继承父实例地址）；
  FCG 门控取自 `hc32_ll_fcg.h` 的 `FCG<n>_PERIPH_<实例>` 宏映射到的位宏
  （`PWC_FCG*_*` / `CMU_FCG_*` / F175 的 `SYSCTRL_APBEN*_*`），fcg_reg 填 FCG0~3，
  fcg_field 填实际位宏名。GPIO/CMU/RMU 等常开模块无门控，留空。
- memory.csv：flash_base 取 GCC/IAR 链接脚本 FLASH 区起点（均为 0x00000000）；
  sram_base/sram_kb 取链接脚本主 RAM 区（设备头 `SRAM_BASE` 是系列级别名基址，
  如 M441 头写 0x1FFF8000 而实际主区从 0x1FFFC000 起，故不再用它覆盖链接脚本）；
  extra_regions 列出与主区地址不连续的独立保留区（RAMB/RET_RAM，掉电保持 SRAM，
  如 0x200F0000 4K），格式 `名称@地址/大小K`，分号分隔。主区跨 0x20000000 是
  SRAMH/SRAM0 两个物理 bank 的边界（见各 RM 内存映射表），不是地址别名，
  因此不再输出 sram_hi_base 伪别名列。
- chips.csv：解析 DS PDF 订购信息/型号功能对比表。F120、M120 的 SDK 无 DS
  （仅 RM，RM 无型号表），各留一行占位并在 other 标注。HC32F155 订购页版式打散，
  只按两行配对恢复出型号名+封装+主频回退，其余属性列为空。F472/A472/F467/F4A2/
  F4A8/A4A0 等新版式标签与值分离，用"孤儿值行 + 语义验证器"恢复大部分列，
  仍有零星缺口（如 F467 的 I2C/Timer、F4A8 的 GPIO/ADC、A472 的 SRAM/Flash）。
  part_no 一律去掉 `-封装` 后缀；同一 die 的 LQFP/LQG（环保）变体按 part_no 去重。
  HC32A460 订购表为单列合并版式，CAN/SDIO/USB/DMA 列的数值疑似伪影（SVD 实际为
  CAN×1、SDIOC×2、USBFS×1、DMA×2），以 SVD 为准。
- kind 列：实例名去数字后缀小写，TMR01→tmr0、TMR41→tmr4、TMR61→tmr6 等
  Timer 家族保留序号。

## 校验

- dma.csv：(series, dma, request) 无重复；channel 为 `a-b` 范围且合法；
- peripherals.csv 行数应等于 SVD 顶层 peripheral 数（含派生）；
- interrupts 行数 = 设备头 IRQn_Type 枚举项数（含内核负向量）。

## 生成时间

{ts}
"""


def build_notes(stats):
    import datetime
    rows = []
    for st in stats:
        warn = "; ".join(st["warnings"]) if st["warnings"] else ""
        if len(warn) > 60:
            warn = warn[:57] + "..."
        rows.append("| %s | %d | %d | %d | %d | %d | %d | %s |" % (
            st["series"], st["chips"], st["interrupts"], st["dma"],
            st["peripherals"], st["svd_peripherals"], st["fcg_matched"],
            warn))
    return NOTES_TMPL.format(rows="\n".join(rows),
                             ts=datetime.datetime.now().isoformat(
                                 sep=" ", timespec="seconds"))


def main():
    ws = workspace_root()
    ap = argparse.ArgumentParser(description="HC32 LL SDK -> CSV")
    ap.add_argument("--sdk-root", default=os.path.join(ws, "xh_ll_sdk"))
    ap.add_argument("--out", default=os.path.join(ws, "hc32-ll-data", "data",
                                                  "csv"))
    ap.add_argument("--series", default="",
                    help="逗号分隔系列过滤，如 HC32F460,HC32K116")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    only = set(x.strip().upper() for x in args.series.split(",") if x.strip())
    sdk_dirs = sorted(d for d in os.listdir(args.sdk_root)
                      if os.path.isdir(os.path.join(args.sdk_root, d)))
    all_chips, all_ints, all_dma, all_peri, all_mem = [], [], [], [], []
    stats = []
    NO_DS = {"HC32F120": "SDK 无 DS 数据手册（仅 RM），订购信息未提取",
             "HC32M120": "SDK 无 DS 数据手册（仅 RM），订购信息未提取"}

    for sdk in sdk_dirs:
        m = re.match(r"(HC32[A-Z0-9]+)_", sdk)
        if not m:
            continue
        series = m.group(1)
        if only and series not in only:
            continue
        skip = unsupported_series("ll").get(series)
        if skip and not only:
            print("== %s 跳过：%s" % (series, skip))
            continue
        print("== %s (%s)" % (series, sdk))
        warns = []
        try:
            dev_files = find_device_header(args.sdk_root, sdk)
            dev_text = open(dev_files[0], encoding="utf-8",
                            errors="ignore").read() if dev_files else ""
            svd_path = find_svd(series)
            int_desc, peri_names, svd_nint = svd_data(svd_path)

            # interrupts
            try:
                rows = extract_interrupts(series, dev_text, int_desc)
            except Exception as e:
                rows = []
                warns.append("interrupts 失败: %s" % e)
            all_ints += rows
            n_int = len(rows)

            # dma
            dmanote = ""
            try:
                inc = find_driver_inc(args.sdk_root, sdk)
                aos = os.path.join(inc, "hc32_ll_aos.h")
                aos_text = open(aos, encoding="utf-8", errors="ignore").read() \
                    if os.path.exists(aos) else ""
                dmac_text = ""
                for dh in glob.glob(os.path.join(inc, "hc32_ll_dma*.h")):
                    dmac_text += open(dh, encoding="utf-8",
                                      errors="ignore").read()
                rows, dmanote = extract_dma(series, dev_text, aos_text,
                                            peri_names, dmac_text)
            except Exception as e:
                rows = []
                dmanote = "失败: %s" % e
                warns.append("dma 失败: %s" % e)
            all_dma += rows
            n_dma = len(rows)

            # peripherals
            fcg_n = 0
            try:
                fcg = extract_fcg(args.sdk_root, sdk)
                rows, n_peri_all = extract_peripherals(series, svd_path, fcg)
                fcg_n = sum(1 for r in rows if r[5])
                all_peri += rows
                n_peri = len(rows)
            except Exception as e:
                n_peri = n_peri_all = 0
                warns.append("peripherals 失败: %s" % e)

            # memory
            try:
                all_mem.append(extract_memory(series, args.sdk_root,
                                              sdk))
            except Exception as e:
                warns.append("memory 失败: %s" % e)

            # chips
            try:
                rows, cwarns = extract_chips(series, args.sdk_root, sdk,
                                             NO_DS.get(series))
                warns += cwarns
            except Exception as e:
                rows = []
                warns.append("chips 失败: %s" % e)
            all_chips += rows
            n_chips = len(rows)

            print("   chips=%d interrupts=%d dma=%d peripherals=%d (svd %d/"
                  "%d int, fcg %d)" % (n_chips, n_int, n_dma, n_peri,
                                       n_peri_all, svd_nint, fcg_n))
            stats.append({
                "series": series, "sdk": sdk, "chips": n_chips,
                "interrupts": n_int, "dma": n_dma, "peripherals": n_peri,
                "svd_peripherals": n_peri_all, "svd_interrupts": svd_nint,
                "fcg_matched": fcg_n, "dma_note": dmanote, "warnings": warns,
            })
        except Exception as e:
            print("   SERIES FAIL:", e)
            stats.append({"series": series, "sdk": sdk, "chips": 0,
                          "interrupts": 0, "dma": 0, "peripherals": 0,
                          "svd_peripherals": 0, "svd_interrupts": 0,
                          "fcg_matched": 0, "dma_note": "",
                          "warnings": ["系列级失败: %s" % e]})

    write_csv(args.out, "chips.csv", CHIPS_HEADER, all_chips)
    write_csv(args.out, "interrupts.csv", INT_HEADER, all_ints)
    write_csv(args.out, "dma.csv", DMA_HEADER, all_dma)
    write_csv(args.out, "peripherals.csv", PERI_HEADER, all_peri)
    write_csv(args.out, "memory.csv", MEM_HEADER, all_mem)
    vwarns = validate(all_dma, all_peri, stats)
    for w in vwarns:
        print("VALIDATE:", w)
    with open(os.path.join(args.out, "EXTRACTION_NOTES.md"), "w",
              encoding="utf-8") as f:
        f.write(build_notes(stats))
    import json
    with open(os.path.join(args.out, "_series_stats.json"), "w",
              encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    print("done: %d chips, %d ints, %d dma, %d peri, %d mem, %d validate warns"
          % (len(all_chips), len(all_ints), len(all_dma), len(all_peri),
             len(all_mem), len(vwarns)))


if __name__ == "__main__":
    main()
