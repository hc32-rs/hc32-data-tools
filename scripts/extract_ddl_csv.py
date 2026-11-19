#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract HC32 DDL SDK data (20 series) into the hc32-ddl-data CSV set.

Outputs (to --out, default <workspace>/hc32-ddl-data/data/csv/):
  chips.csv        - DS ordering-info tables -> one row per orderable part number
  interrupts.csv   - device header IRQn_Type enums -> one row per IRQ
  dma.csv          - dmac.h trigger tables -> one row per DMA request (TRI_SEL index)
  peripherals.csv  - SVD peripherals (+ clock gate bits from sysctrl/clk driver)
  memory.csv       - template linker .icf -> flash/SRAM base addresses

Usage:
  python3 hc32-data-tools/extract_ddl_csv.py [--sdk-root DIR] [--out DIR] [--series NAME]...

Single-series failures are isolated: the series keeps its rows in the other CSVs
and failed sections are logged to stderr and recorded in the run summary.
"""

import argparse
import csv
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

try:
    import pypdf
except ImportError:  # pragma: no cover
    pypdf = None

_WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SDK_ROOT = os.path.join(_WS, "xh_ddl_sdk")
DEFAULT_SVD_DIR = os.path.join(_WS, "hc32-ddl-data", "svd")
DEFAULT_OUT = os.path.join(_WS, "hc32-ddl-data", "data", "csv")

CHIPS_HEADER = ["part_no", "series", "freq_mhz", "flash_kb", "sram_kb", "package",
                "pin_count", "gpio", "vdd_min", "vdd_max", "uart", "spi", "i2c",
                "i2s", "can", "usb", "adc", "dac", "timer", "rtc", "wdt", "touchkey",
                "lcd", "opa", "cmp", "other", "url", "source"]
INT_HEADER = ["series", "name", "irq", "description"]
# channels 列 = 该系列 DMAC 物理通道数（dmac.h DmaCh/DMA_CHANNEL 枚举 + RM 佐证，
# 如 A052 RM 第 12 章"DMAC 具有 5 条独立的 DMA 通道"、A136 RM"2 条"）。
# 老的 channel 列仍是 TRI_SEL 全局触发源索引，与物理通道无关。
DMA_HEADER = ["series", "dma", "channel", "request", "channels"]
PERI_HEADER = ["series", "instance", "kind", "base_address", "bus", "fcg_reg",
               "fcg_field", "interrupts", "notes"]
# 与 LL 侧同构（见 CSV_SCHEMA.md）：DDL 老系列的 IAR 模板 .icf 只有单一
# ROM/RAM 区，sram_kb/extra_regions 由 csv_to_yaml 回退 chips.csv 标称填充
MEM_HEADER = ["series", "flash_base", "sram_base", "sram_kb", "extra_regions",
              "notes"]

# ----------------------------------------------------------------------------
# SDK -> series registry
# ----------------------------------------------------------------------------

# model_prefixes: DS part-number prefixes belonging to each series of this SDK.
SDKS = [
    dict(dir="HC32A052_DLL_Rev1.0.1", series=["HC32A052"], svd="HC32A052.svd",
         prefixes={"HC32A052": ["HC32A052"]}),
    dict(dir="HC32A136_DDL_Rev2.1.0", series=["HC32A136"], svd="HC32A136.svd",
         prefixes={"HC32A136": ["HC32A136"]}),
    dict(dir="HC32F002_DDL_Rev1.2.1", series=["HC32F002"], svd="HC32F002.svd",
         prefixes={"HC32F002": ["HC32F002"]}),
    dict(dir="HC32F003_DDL_Rev2.0.0", series=["HC32F003"], svd="HC32F003.svd",
         prefixes={"HC32F003": ["HC32F003"]}),
    dict(dir="HC32F005_DDL_Rev2.0.0", series=["HC32F005"], svd="HC32F005.svd",
         prefixes={"HC32F005": ["HC32F005"]}),
    dict(dir="HC32F030_DDL_Rev2.2.0", series=["HC32F030"], svd="HC32F030.svd",
         prefixes={"HC32F030": ["HC32F030"]}),
    dict(dir="HC32F052_DDL_Rev1.1.0", series=["HC32F052"], svd="HC32F052.svd",
         prefixes={"HC32F052": ["HC32F052"]}),
    dict(dir="HC32F072_DDL_Rev1.2.0", series=["HC32F072"], svd="HC32F072.svd",
         prefixes={"HC32F072": ["HC32F072"]}),
    dict(dir="HC32F17x_DDL_Rev1.4.0", series=["HC32F17x"], svd="HC32F17x.svd",
         prefixes={"HC32F17x": ["HC32F17"]}),
    dict(dir="HC32F19x_DDL_Rev1.4.0", series=["HC32F19x"], svd="HC32F19x.svd",
         prefixes={"HC32F19x": ["HC32F19"]}),
    dict(dir="HC32F420_DDL_Rev1.3.1", series=["HC32F420"], svd="HC32F420.svd",
         prefixes={"HC32F420": ["HC32F420"]}),
    dict(dir="HC32L021_DDL_Rev1.2.1", series=["HC32L021"], svd="HC32L021.svd",
         prefixes={"HC32L021": ["HC32L021"]}),
    dict(dir="HC32L07x_DDL_Rev1.2.0", series=["HC32L07x"], svd="HC32L07x.svd",
         prefixes={"HC32L07x": ["HC32L07"]}),
    dict(dir="HC32L110_DDL_Rev1.3.2", series=["HC32L110"], svd="HC32L110.svd",
         prefixes={"HC32L110": ["HC32L110"]}),
    dict(dir="HC32L12x_DDL_Rev1.1.0", series=["HC32L12x"], svd="HC32L12x.svd",
         prefixes={"HC32L12x": ["HC32L12"]}),
    dict(dir="HC32L13x_DDL_Rev2.2.0", series=["HC32L13x"], svd="HC32L13x.svd",
         prefixes={"HC32L13x": ["HC32L13"]}),
    dict(dir="HC32L16x_DDL_Rev1.1.1", series=["HC32L16x"], svd="HC32L16x.svd",
         prefixes={"HC32L16x": ["HC32L16"]}),
    dict(dir="HC32L17x_DDL_Rev1.4.0", series=["HC32L17x"], svd="HC32L17x.svd",
         prefixes={"HC32L17x": ["HC32L17"]}),
    dict(dir="hc32l186_ddl", series=["HC32L18x"], svd="HC32L18x.svd",
         prefixes={"HC32L18x": ["HC32L18"]}),
    dict(dir="HC32L19x_DDL_Rev1.4.0", series=["HC32L19x"], svd="HC32L19x.svd",
         prefixes={"HC32L19x": ["HC32L19"]}),
]


def warn(msg):
    sys.stderr.write("[warn] %s\n" % msg)


# ----------------------------------------------------------------------------
# Generic C header parsing helpers
# ----------------------------------------------------------------------------

def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def find_device_header(sdk_dir):
    """Device header (contains IRQn_Type), excluding interrupts_*/core_*."""
    pat = os.path.join(sdk_dir, "mcu", "**", "*.h")
    cands = []
    for p in glob.glob(pat, recursive=True):
        base = os.path.basename(p).lower()
        if base.startswith(("interrupts_", "core_", "arm_", "system_")):
            continue
        txt = read_text(p)
        if "IRQn_Type" in txt and "_IRQn" in txt:
            cands.append((p, txt))
    if not cands:
        return None, ""
    # Prefer the one that defines peripheral bases (PERIPH_BASE) and is biggest
    cands.sort(key=lambda kv: ("PERIPH_BASE" not in kv[1], -len(kv[1])))
    return cands[0]


def parse_irq_enum(header_text):
    """Parse IRQn_Type enum -> list of (name, value, description)."""
    m = re.search(r"typedef\s+enum[^{]*\{([^}]*)\}\s*IRQn_Type", header_text, re.S)
    if not m:
        return []
    body = m.group(1)
    out = []
    cur = -1
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        desc = ""
        cm = re.search(r"/\*!<\s*(.*?)\s*\*/\s*$", line)
        if cm:
            desc = cm.group(1).strip()
        core = re.sub(r"/\*.*?\*/", "", line).strip().rstrip(",").strip()
        if not core or core.startswith("//"):
            continue
        vm = re.match(r"([A-Za-z_]\w*)(?:\s*=\s*(-?\w+))?\s*$", core)
        if not vm:
            continue
        name, val = vm.group(1), vm.group(2)
        if val is None:
            val = cur + 1
        else:
            try:
                val = int(val, 0)
            except ValueError:
                continue
        cur = val
        if not name.endswith("_IRQn"):
            continue
        out.append((name[:-5], val, desc))
    return out


# ----------------------------------------------------------------------------
# Clock gate extraction (SYSCTRL / CLK peripheral gate)
# ----------------------------------------------------------------------------

GATE_ENUM_NAMES = [
    "en_sysctrl_peripheral_gate_t",
    "en_clk_peripheral_gate_t",
    "en_sysctrl_periph_clock_t",
]


def _resolve_cexpr(expr, pos_defs, mask_defs):
    """Resolve a C constant expression built from names, ints and '+'/'|'."""
    expr = expr.strip().strip("()")
    total = 0
    ok = True
    for tok in re.split(r"\s*\|\s*|\s*\+\s*", expr):
        tok = tok.strip().rstrip("uUlL")
        if not tok:
            continue
        if re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", tok):
            total += int(tok, 0)
        elif tok in pos_defs:
            total += pos_defs[tok]
        elif tok in mask_defs:
            total += mask_defs[tok]
        elif tok in mask_defs or (tok + "_Pos") in pos_defs:
            total += pos_defs.get(tok + "_Pos", 0)
        elif tok.endswith("_Msk") and (tok[:-4] + "_Pos") in pos_defs:
            total += pos_defs[tok[:-4] + "_Pos"]
        elif (tok + "_Pos") in pos_defs:
            total += pos_defs[tok + "_Pos"]
        else:
            ok = False
    return total if ok else None


def parse_gate_def(sdk_dir, header_text):
    """Return list of (enum_member, bit_value) and register map.

    Returns (gates, regmap) where gates = [(name, (register, bit), desc)]
    and regmap resolves values >= 0x20 style masks to register names.
    """
    driver_inc = os.path.join(sdk_dir, "driver", "inc")
    drv_src = os.path.join(sdk_dir, "driver", "src")

    # 1) bit position / mask macros from device header + sysctrl.h
    pos_defs = {}
    for m in re.finditer(r"#define\s+(\w+_Pos)\s+\(?(\d+)U?\)?", header_text):
        pos_defs[m.group(1)] = int(m.group(2))
    mask_defs = {}
    sysctrl_h = os.path.join(driver_inc, "sysctrl.h")
    clk_h = sysctrl_h if os.path.exists(sysctrl_h) else os.path.join(driver_inc, "clk.h")
    htext = read_text(clk_h)
    for m in re.finditer(r"#define\s+(_PERICLK_REG\d_MSK)\s+\(?(0x[0-9A-Fa-f]+)U?\)?", htext):
        mask_defs[m.group(1)] = int(m.group(2), 0)
    for m in re.finditer(r"#define\s+(\w+_Pos)\s+\(?\s*(\d+)U?\s*\)?", htext):
        pos_defs.setdefault(m.group(1), int(m.group(2)))

    # 2) gate enum members
    entries = []  # (name, expr, desc)
    enum_txt = None
    for ename in GATE_ENUM_NAMES:
        m = re.search(r"typedef\s+enum[^{]*\{([^}]*)\}\s*" + re.escape(ename),
                      htext, re.S)
        if m:
            enum_txt = m.group(1)
            break
    if enum_txt is None:
        # HC32F002 style: SYSCTRL_PERICLK_xxx alias defines
        entries = []
        for m in re.finditer(r"#define\s+SYSCTRL_PERICLK_(\w+)\s+\(?([^\n()]*)\)?", htext):
            entries.append(("SysctrlPeripheral" + m.group(1), m.group(2), ""))
    else:
        for line in enum_txt.splitlines():
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("/*"):
                continue
            # capture trailing doxygen comments for description
            desc = ""
            cm = re.search(r"/\*[!<]*\s*(.*?)\s*\*/\s*$", line)
            if cm:
                desc = cm.group(1).strip()
            line = re.sub(r"/\*.*?\*/", "", line).strip()
            line = re.sub(r"//.*$", "", line).strip().rstrip(",").strip()
            em = re.match(r"([A-Za-z_]\w*)\s*=\s*(.*)$", line)
            if not em:
                continue
            entries.append((em.group(1), em.group(2).strip(), desc))

    # 3) register/mask map from driver source (SetPeripheralGate / PeriphClkEnable)
    regmap = []  # [(mask_value_or_None, register_name)] ; None = default
    default_reg = None
    for src_name in ("sysctrl.c", "clk.c"):
        stext = read_text(os.path.join(drv_src, src_name))
        if not stext:
            continue
        fm = re.search(r"(?:Sysctrl_SetPeripheralGate|SYSCTRL_PeriphClkEnable|"
                       r"SYSCTRL_PeriphClockEnable|Clk_SetPeripheralGate)"
                       r"\s*\([^)]*\)\s*\{", stext)
        if not fm:
            continue
        # take until the matching closing brace of the function (brace counting,
        # NOT a fixed char window: a fixed window truncated PeriphClkEnable's
        # final else-branch, making default_reg 'AHB0EN' instead of 'APB0EN'
        # and mis-bussing every default-branch peripheral on HC32F420).
        depth = 1
        end = fm.end()
        for off in range(fm.end(), len(stext)):
            if stext[off] == "{":
                depth += 1
            elif stext[off] == "}":
                depth -= 1
                if depth == 0:
                    end = off
                    break
        body = stext[fm.end():end]
        masks = re.findall(r"(?:enPeripheral|\w+)\s*&\s*([A-Za-z_]\w*|0[xX][0-9a-fA-F]+u)",
                           body)
        regs = re.findall(r"(?:M0P_SYSCTRL|HC_SYSCTRL|SYSCTRL|M0P_CLOCK|CLOCK)->\s*([A-Z_0-9]+)",
                          body)
        regs = [r for r in regs if "EN" in r or "CLKEN" in r or r.startswith(("APB", "AHB"))]
        pairs = []
        for i, mk in enumerate(masks):
            if i < len(regs) - 1:
                mv = mask_defs.get(mk)
                if mv is None and re.fullmatch(r"0[xX][0-9a-fA-F]+u?", mk):
                    mv = int(mk.rstrip("u"), 0)
                pairs.append((mv, regs[i]))
        if regs:
            default_reg = regs[-1]
        regmap = pairs
        break
    regmap.append((None, default_reg))

    gates = []
    for name, expr, desc in entries:
        val = _resolve_cexpr(expr, pos_defs, mask_defs)
        if val is None:
            warn("gate %s: cannot resolve expr %r" % (name, expr))
            continue
        reg = default_reg
        bit = val
        for mv, rg in regmap:
            if mv is not None and (val & mv) == mv and val >= mv:
                reg = rg
                bit = val - mv
                break
        if reg is None:
            warn("gate %s: no register resolved" % name)
            continue
        gates.append((name, (reg, bit), desc))
    return gates


# ----------------------------------------------------------------------------
# DMA trigger extraction
# ----------------------------------------------------------------------------

def _norm_dma_req(raw):
    """Normalize a DMA request enum/define name to PERIF_SIGNAL style."""
    s = raw
    s = re.sub(r"^DMA_TRISRC_", "", s)
    s = re.sub(r"^Dma", "", s)
    s = re.sub(r"Trig$", "", s)
    s = s.replace("SW", "SOFT") if s in ("SW", "SOFTWARE") else s
    s = re.sub(r"TXE$", "TX", s)
    s = re.sub(r"RXNE$", "RX", s)
    s = re.sub(r"TXFULL$", "TX", s)
    s = re.sub(r"RXFULL$", "RX", s)
    s = re.sub(r"(_?)SINGLE_EOC$", "SQR", s)
    # boundary: digit followed by letter -> underscore (Uart0Rx -> UART0_RX)
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"(\d)([A-Z])", r"\1_\2", s)
        s = re.sub(r"([a-z])([A-Z])", lambda m: m.group(1) + "_" + m.group(2), s)
    s = re.sub(r"_{2,}", "_", s).strip("_")
    s = s.upper()
    return s


def parse_dma_channels(sdk_dir):
    """Parse dmac.h channel enum -> physical channel count.

    两种枚举风格：DmaCh0/1/4（M0 系列老驱动）与 DMA_CHANNEL0..5（F420）。
    取最大编号 +1；枚举缺失时返回 0（调用方按 2 通道老布局回退并告警）。
    """
    path = os.path.join(sdk_dir, "driver", "inc", "dmac.h")
    text = read_text(path)
    if not text:
        return 0
    best = -1
    for m in re.finditer(r"^\s+(?:DmaCh(\d+)|DMA_CHANNEL(\d+))\s*=", text, re.M):
        n = int(m.group(1) or m.group(2))
        best = max(best, n)
    return best + 1


def parse_dma_triggers(sdk_dir):
    """Parse dmac.h trigger table.

    Old-DDL semantics: each DMA channel has its own TRI_SEL field; the value
    is a global trigger-source index (0x00 = software, >=0x20 hardware request
    number). Any request can be routed to any channel by software.
    Returns list of (trigger_index, request_name, description).
    """
    path = os.path.join(sdk_dir, "driver", "inc", "dmac.h")
    text = read_text(path)
    if not text:
        return []
    out = []
    seen = set()
    # enum style: DmaXxxTrig = value (decimal or hex)
    for m in re.finditer(r"(Dma\w*Trig)\s*=\s*(0[xX][0-9A-Fa-f]+|\d+)U?\s*,"
                         r"[ \t]*(?:/\*!<\s*(.*?)\s*\*/)?",
                         text):
        idx = int(m.group(2), 0)
        desc = m.group(3) or ""
        if idx == 0:  # software trigger
            continue
        req = _norm_dma_req(m.group(1))
        if req in ("SOFT", "SOFTWARE"):
            continue
        if req not in seen:
            seen.add(req)
            out.append((idx, req, desc))
    # define style: DMA_TRISRC_xxx (0xNN << ...)
    for m in re.finditer(r"#define\s+DMA_TRISRC_(\w+)\s+\((0x[0-9A-Fa-f]+)U?\s*<<[^)]*\)\s*/\*!<\s*(.*?)\s*\*/", text):
        idx = int(m.group(2), 0)
        desc = m.group(3) or ""
        req = _norm_dma_req(m.group(1))
        if req in ("SOFT", "SOFTWARE"):
            continue
        if req not in seen:
            seen.add(req)
            out.append((idx, req, desc))
    out.sort()
    return out


# ----------------------------------------------------------------------------
# SVD parsing
# ----------------------------------------------------------------------------

def parse_svd(svd_path):
    """Parse SVD -> list of dicts(name, kind, base, notes). Expand derivedFrom.

    Peripheral register-view duplicates (TIMx_MODE0/1/23) sharing one base are
    collapsed into a single instance.
    """
    tree = ET.parse(svd_path)
    root = tree.getroot()
    per_node = root.find("peripherals")
    raw = {}
    order = []
    for p in per_node.findall("peripheral"):
        name = p.findtext("name").strip()
        base = p.findtext("baseAddress")
        derived = p.get("derivedFrom")
        raw[name] = dict(name=name, base=base, derived=derived, node=p)
        order.append(name)
    out = []
    seen = set()
    for name in order:
        info = raw[name]
        base = info["base"]
        n = name
        if base is None and info["derived"]:
            parent = raw.get(info["derived"])
            if parent is not None and parent["base"]:
                base = parent["base"]
        if base is None:
            continue
        # collapse register view duplicates: TIM0_MODE0 -> TIM0
        core = re.sub(r"_?MODE[0-9]*$", "", n)
        key = (core, base.upper())
        if key in seen:
            continue
        seen.add(key)
        kind = re.sub(r"[\d_]+$", "", core).lower()
        if core.isdigit() or not kind:
            kind = core.lower()
        notes = ""
        if core != n:
            notes = "SVD register views %s collapsed (same base)" % name
        out.append(dict(instance=core, kind=kind, base=base, notes=notes))
    return out


# ----------------------------------------------------------------------------
# Memory extraction from template linker file
# ----------------------------------------------------------------------------

def parse_memory_icf(sdk_dir):
    cands = glob.glob(os.path.join(sdk_dir, "example", "**", "*.icf"), recursive=True)
    # prefer the template icf; skip *_ram.icf (RAM boot configs)
    cands = [c for c in cands if not c.lower().endswith("_ram.icf")]
    tmpl = [c for c in cands if "template" in c.lower()]
    cands = tmpl + [c for c in cands if c not in tmpl]
    for path in cands:
        txt = read_text(path)
        rom_s = re.search(r"region_ROM_start__\s*=\s*(0x[0-9A-Fa-f]+)", txt)
        rom_e = re.search(r"region_ROM_end__\s*=\s*(0x[0-9A-Fa-f]+)", txt)
        ram_s = re.search(r"region_RAM_start__\s*=\s*(0x[0-9A-Fa-f]+)", txt)
        ram_e = re.search(r"region_RAM_end__\s*=\s*(0x[0-9A-Fa-f]+)", txt)
        if rom_s and ram_s:
            notes = "from %s" % os.path.relpath(path, sdk_dir)
            if rom_e:
                size_kb = (int(rom_e.group(1), 16) - int(rom_s.group(1), 16) + 1) // 1024
                notes += ", ROM %dK (max model config)" % size_kb
            if ram_e:
                size_kb = (int(ram_e.group(1), 16) - int(ram_s.group(1), 16) + 1) // 1024
                notes += ", RAM %dK" % size_kb
            return rom_s.group(1), ram_s.group(1), notes
    return "", "", ""


# ----------------------------------------------------------------------------
# DS PDF ordering-info table parsing
# ----------------------------------------------------------------------------

MODEL_RE = re.compile(r"HC32[A-Z][0-9]{2}[A-Z0-9]{0,8}(?:-[A-Z0-9]{2,10})?")
MODEL_START_RE = re.compile(r"HC32[A-Z][0-9]{2}[A-Z0-9]{0,8}(?:-[A-Z0-9]{2,10})?")
CONT_PKG_RE = re.compile(r"^(LQFP\d+(?:TR)?|LQ\d+|QFN\d+\(?\d*?\*?\d*\)?(?:TR)?|"
                         r"QN\d+(?:TR)?|ZFN\d+(?:TR)?|UFN\d+(?:TR)?|TSSOP\d+(?:TR)?|"
                         r"SOP\d+|SFN\d+(?:TR)?|SSOP\d+(?:TR)?|DIP\d+)$")
ATTR_LABEL_RE = re.compile(r"主频|Flash|RAM|GPIO|封装|Package|内核|电压|Frequency|"
                           r"Vdd|I/O|工作温度|管脚|Timer|USART|UART|SPI|I2C|ADC", re.I)

ROW_KEYS = [
    # (aliases/keywords regex, csv field, transform) - order matters
    (r"主频|Frequency|频率", "freq_mhz", "freq"),
    (r"Flash", "flash_kb", "kb"),
    (r"RAM|SRAM", "sram_kb", "kb"),
    (r"GPIO|^I/O(\b|$)|[^A-Za-z]I/O", "gpio", "gpio"),
    (r"电压|Vdd|工作电压", "vdd", "vdd"),
    (r"工作温度|温度范围|Operating Temperature", "temp", "temp"),
    (r"封装形式|封装|Package", "package", "package"),
    (r"管脚数|引脚数|^Pins?$", "pin_count", "num"),
    (r"LPUART", "lpuart", "num"),
    (r"USART|UART", "uart", "num"),
    (r"^SPI\b|SPI", "spi", "num"),
    (r"I2C|I²C|HSI2C", "i2c", "num"),
    (r"I2S", "i2s", "num"),
    (r"\bCAN\b", "can", "num"),
    (r"USB", "usb", "num"),
    (r"ADC", "adc", "adc"),
    (r"DAC", "dac", "num"),
    (r"定时器|^Timer\b(?!3)|^TIMER\b(?!3)", "timer", "timer"),
    (r"RTC", "rtc", "check"),
    (r"IWDT", "iwdt", "check"),
    (r"WWDT", "wwdt", "check"),
    (r"^WDT\b|看门狗", "wdt", "check"),
    (r"Touch|触控|TSI", "touchkey", "check"),
    (r"LCD", "lcd", "check"),
    (r"^OP\b|\bOPA\b|OPAMP|运放", "opa", "num"),
    (r"Comp\b|VC(omp)?|比较器|\bCMP\b", "cmp", "num"),
    (r"内核|Core|CPU", "core", "text"),
    (r"Part\s?Number|产品型号|商业代码|^型号|出货形式|包装|脚间距|芯片厚度|丝印",
     None, "skip"),
    (r"DMA|PCA|PWM|CRC|HDIV|DIV|TRNG|RNG|AES|OTS|温度传感器|识别|唯一|GTIM|BTIM|"
     r"ATIM|ADTIM|CTIMER|ATIMER|LPT|低功耗|基本|基础|通用|高级|商业代码|PK|USBFS",
     None, "other"),
]


def _classify_row(label):
    """Map an attribute label to (csv_field, transform)."""
    label = label.strip()
    low = label.lower()
    despaced = re.sub(r"[\s_]+", "", label)
    for pat, field, trans in ROW_KEYS:
        if re.search(pat, label, re.I) or re.search(pat, low) or \
                re.search(pat, despaced, re.I):
            # priority checks to disambiguate overlaps
            return field, trans
    return None, "other"


def _to_num(val):
    m = re.search(r"\d+", str(val))
    return m.group(0) if m else ""


def _to_kb(val):
    s = str(val).upper().replace(",", "")
    m = re.search(r"(\d+)\s*(K|M)?B?", s)
    if not m:
        return ""
    n = int(m.group(1))
    if m.group(2) == "M":
        n *= 1024
    return str(n)


def _split_pkg(val):
    """'TSSOP20' / 'QFN20(3*3)' / 'LQFP64' -> (package, pin_count)."""
    s = str(val).strip()
    m = re.search(r"(LQFP|TSSOP|QFN|QN|ZFN|SOP|SSOP|SFN|DIP|LQ|SO)(\d+)", s, re.I)
    if not m:
        return "", ""
    name = m.group(1).upper()
    pins = m.group(2)
    if name == "LQ":
        name = "LQFP"
    if name in ("QN", "ZFN", "UFN"):
        name = "QFN"
    if name == "SO":
        name = "SOP"
    return name + pins, pins


def _to_vdd(val):
    s = str(val).replace("～", "~").replace("—", "~")
    m = re.search(r"(\d+(?:\.\d+)?)\s*~\s*(\d+(?:\.\d+)?)", s)
    if m:
        return m.group(1), m.group(2)
    return "", ""


def _to_freq(val):
    m = re.search(r"(\d+)\s*M?H?z", str(val), re.I)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)", str(val))
    return m.group(1) if m else ""


def _tokens_with_x(line):
    """Split a layout-mode line into (x, text) tokens."""
    return [(m.start(), m.group(0)) for m in re.finditer(r"[^\s]+", line)]


def _is_order_page(text_plain):
    if re.search(r"版本修订记录|版本记录|文档版本", text_plain):
        return False
    if not re.search(r"订购信息|订货信息|Part\s?Number|商业代码|产品选型", text_plain, re.I):
        return False
    if not re.search(r"HC32[A-Z][0-9]{2}", text_plain):
        return False
    # must contain at least one attribute label (avoids TOC pages)
    return ATTR_LABEL_RE.search(text_plain) is not None


def _fix_model_token(tok):
    """Join split tokens like 'HC32F030K8TA-LQFP64' when spaced."""
    return tok.replace(" ", "")


def _junk_line(text):
    return bool(re.search(r"系列|手册|Rev\s?\d|www\.|xhsc|\.cn\b|版本|记录|目\s?录|^\d+\s*$",
                          text))


def _merge_val_tokens(val_toks, max_gap=15):
    """Merge value tokens that sit close together (split pieces of one cell)."""
    if not val_toks:
        return []
    out = [[val_toks[0][0], val_toks[0][1]]]
    for x, t in val_toks[1:]:
        if x - out[-1][0] <= max_gap:
            out[-1][1] = out[-1][1] + " " + t
        else:
            out.append([x, t])
    return [(x, t) for x, t in out]


def parse_order_column_page(page, pdf_name, page_idx):
    """Column-layout ordering table (models in columns)."""
    text = page.extract_text(extraction_mode="layout") or ""
    lines = text.splitlines()
    toklines = [_tokens_with_x(l) for l in lines]

    # 1) locate the model header block: consecutive lines containing model
    #    starts (with package suffix or trailing '-') or wrapped package parts.
    header = []  # (x, raw)
    header_end = None
    in_header = False
    for li, toks in enumerate(toklines):
        joined = "".join(t[1] for t in toks)
        starts_all = [(x, t) for x, t in toks
                      if MODEL_START_RE.fullmatch(t) or
                      (t.startswith("HC32") and t.endswith("-"))]
        strong = [t for _, t in starts_all if "-" in t]
        if not in_header:
            if strong and not _junk_line(joined):
                in_header = True
            else:
                continue
        if _junk_line(joined):
            break
        starts = starts_all
        conts = [(x, t) for x, t in toks if CONT_PKG_RE.fullmatch(t)]
        bare = [(x, t) for x, t in toks
                if MODEL_START_RE.fullmatch(t.rstrip("-")) and "-" not in t
                and not t.endswith("-")]
        if not starts and not conts and not bare:
            header_end = li
            break
        for x, t in starts:
            header.append((x, t))
        for x, t in bare:
            header.append((x, t))
        for x, t in conts:
            # attach to the model-start column with the closest x position
            if not header:
                header.append((x, t))
                continue
            best_k = min(range(len(header)),
                         key=lambda k: abs(header[k][0] - x))
            hx, ht = header[best_k]
            if ht.endswith("-"):
                header[best_k] = (hx, ht + t)
            else:
                header[best_k] = (hx, ht)  # already complete; ignore stray token
    if header_end is None:
        header_end = len(lines)
    if not header:
        return []
    header.sort(key=lambda kv: kv[0])
    models = [dict(x=x, raw=t.replace(" ", "")) for x, t in header]
    xs = [m["x"] for m in models]
    if len(xs) >= 2:
        gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1) if xs[i + 1] > xs[i]]
        colgap = min(gaps) if gaps else 10
    else:
        colgap = 20
    tol = max(colgap * 0.45, 4)

    rows = []  # (label, {col_idx: value})
    pending = None  # buffered continuation awaiting a package-labeled row
    ncols = len(xs)

    def _vals_from(val_toks):
        val_toks = _merge_val_tokens(val_toks)
        if len(val_toks) == 1 and ncols > 1:
            vx = val_toks[0][0]
            if xs[0] - tol <= vx <= xs[-1] + tol:
                return {i: val_toks[0][1] for i in range(ncols)}
        if len(val_toks) == ncols:
            # one value per column, in order (layout x may drift)
            return {i: t for i, (x, t) in enumerate(val_toks)}
        vals = {}
        for x, t in val_toks:
            ci = _nearest_col(x, xs, tol)
            if ci is not None:
                vals[ci] = (vals.get(ci, "") + " " + t).strip()
        return vals

    def _append_cont(vals):
        if rows:
            lab, prev = rows[-1]
            for ci, v in vals.items():
                prev[ci] = (prev.get(ci, "") + " " + v).strip()
            rows[-1] = (lab, prev)

    for li in range(header_end, len(lines)):
        toks = toklines[li]
        if not toks:
            continue
        joined = "".join(t[1] for t in toks)
        if re.search(r"版本修订记录|订购前，请联系|www\.xhsc", joined):
            break
        if MODEL_START_RE.search(joined) and len(rows) > 0:
            break  # a second table block starts
        label_toks = [t for x, t in toks if x < min(xs) - tol]
        # vertical label fragments (single CJK chars) are not labels
        label_toks = [t for t in label_toks
                      if not (len(t) == 1 and ord(t[0]) > 0x4E00)]
        val_toks = [(x, t) for x, t in toks if x >= min(xs) - tol]
        label = " ".join(label_toks).strip()
        if not val_toks:
            continue
        vals = _vals_from(val_toks)
        if not label or re.fullmatch(r"\d+\s*bit", label, re.I):
            # continuation line; package values may precede their label line
            nonempty = [v for v in vals.values() if v]
            if nonempty and all(CONT_PKG_RE.fullmatch(re.sub(r"[（(].*$", "", v))
                                for v in nonempty):
                pending = vals
            else:
                _append_cont(vals)
            continue
        if pending is not None:
            if _is_pkg_label(label):
                # merge buffered package names into this package row
                for ci, v in pending.items():
                    vals[ci] = (v + " " + vals.get(ci, "")).strip()
            else:
                _append_cont(pending)
            pending = None
        rows.append((label, vals))
    if pending is not None:
        _append_cont(pending)

    parsed = [dict(label=label, vals=[vals.get(i, "") for i in range(len(models))])
              for label, vals in rows]
    return dict(models=models, rows=parsed)


def _nearest_col(x, xs, tol):
    best, bestd = None, None
    for i, cx in enumerate(xs):
        d = abs(x - cx)
        if bestd is None or d < bestd:
            best, bestd = i, d
    if bestd is not None and bestd <= tol:
        return best
    return None


def _is_pkg_label(label):
    return bool(re.search(r"封装|Package|封\s?装", label, re.I))


def _junk_header_tok(t):
    if re.fullmatch(r"\d+", t):
        return True
    if "/" in t and t != "I/O":
        return True
    for bad in ("系列", "手册", "Rev", "www.", "订购信息", ".cn"):
        if bad in t:
            return True
    return False


def parse_order_plain_page(page, pdf_name, page_idx):
    """Plain-text column parser for DS PDFs whose layout mode is fragmented
    (models listed on one line, attribute rows as 'label v1 v2 ...')."""
    text = page.extract_text() or ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    def _join_frags(toks):
        """Join artifacts like ('H', 'C32F030E8PA-TSSOP28')."""
        out = []
        i = 0
        while i < len(toks):
            t = toks[i]
            if t == "H" and i + 1 < len(toks) and toks[i + 1].startswith("C32"):
                out.append(t + toks[i + 1])
                i += 2
            else:
                out.append(t)
                i += 1
        return out

    models = []  # raw names
    rows = []    # (label, [values]) aligned to models
    frag = ""    # model name assembled across wrapped lines
    for line in lines:
        toks = _join_frags(line.split())
        joined = " ".join(toks)
        if re.search(r"版本修订记录|订购前，请联系|www\.xhsc|丝印", joined):
            if models or frag:
                break
            continue
        mtoks = [t for t in toks if MODEL_START_RE.fullmatch(t) or
                 (t.startswith("HC32") and t.endswith("-"))]

        def _flush():
            nonlocal frag
            if frag:
                models.append(frag)
                frag = ""
        if len(mtoks) >= 2:
            _flush()
            models.extend(mtoks)
            continue
        if mtoks and len(mtoks) == 1 and not rows:
            # single model token, maybe an incomplete wrapped name
            t = mtoks[0]
            if t.endswith("-") or "-" not in t:
                _flush()
                frag = t
                continue
        if toks and len(toks) <= 2 and not rows:
            # possible wrapped model pieces ('HC32F176PATA' / '-LQFP100' /
            # 'SFN20TR' / 'HC32L110C6UA-')
            t0 = toks[0]
            t1 = toks[1] if len(toks) > 1 else ""
            if MODEL_START_RE.fullmatch(t0) and "-" not in t0 and not t1:
                if frag and MODEL_START_RE.fullmatch(frag) and "-" not in frag:
                    models.append(frag)
                frag = t0
                continue
            if (t0.startswith("-") or CONT_PKG_RE.fullmatch(t0)) and frag:
                if t0.startswith("-") or frag.endswith("-"):
                    cand = frag + t0
                else:
                    cand = frag + "-" + t0
                frag = cand
                if CONT_PKG_RE.search(cand.split("-", 1)[1] if "-" in cand else ""):
                    models.append(frag)
                    frag = ""
                continue
            if frag and t1 and CONT_PKG_RE.fullmatch(t1):
                if frag.endswith("-"):
                    frag = frag + t1
                else:
                    frag = frag + "-" + t1
                models.append(frag)
                frag = ""
                continue
        _flush()
        if not models:
            continue
        if mtoks:
            break  # next table block
        if not toks:
            continue
        # attribute row: choose label split so values align with the columns
        ncols = len(models)
        chosen = None
        if len(toks) == ncols and all(re.fullmatch(r"\d+", t) for t in toks):
            # unlabeled all-numeric row (e.g. GPIO counts without a label)
            chosen = ("GPIO", toks)
        for k in range(1, min(4, len(toks)) + 1):
            vals = toks[k:]
            if len(vals) == ncols or len(vals) == 1 or \
                    (ncols and len(vals) % ncols == 0):
                chosen = (" ".join(toks[:k]), vals)
                break
        if chosen is None:
            chosen = (toks[0], toks[1:])
        label, vals = chosen
        vals = [v for v in vals if v != "|"]
        if not vals:
            continue
        vals2 = []
        if len(vals) == ncols:
            vals2 = vals
        elif len(vals) == 1:
            vals2 = vals * ncols
        elif ncols and len(vals) % ncols == 0:
            g = len(vals) // ncols
            vals2 = ["".join(vals[i * g:(i + 1) * g]) for i in range(ncols)]
        else:
            continue
        rows.append((label, vals2))
    _flush()
    if len(models) < 2 or len(rows) < 2:
        return []
    parsed = [dict(label=label, vals=vals) for label, vals in rows]
    return dict(models=[dict(x=i, raw=m) for i, m in enumerate(models)],
                rows=parsed)


def parse_order_row_page(page, pdf_name, page_idx):
    """Row-layout ordering table (models in rows, attributes in columns)."""
    text = page.extract_text() or ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    data_row_re = re.compile(r"HC32[A-Z][0-9]{2}[A-Z0-9]{2,8}-[A-Z0-9]{2,12}$")
    header = []
    rows = []
    seen_first = False
    for line in lines:
        toks = line.split()
        if not toks:
            continue
        has_model = [t for t in toks if data_row_re.fullmatch(t.replace(" ", ""))]
        if has_model:
            seen_first = True
            mi = next(i for i, t in enumerate(toks)
                      if data_row_re.fullmatch(t.replace(" ", "")))
            model = toks[mi].replace(" ", "")
            vals = toks[mi + 1:]
            rows.append((model, list(header), vals))
        elif not seen_first:
            toks_clean = [t for t in toks if not _junk_header_tok(t)]
            if toks_clean and toks_clean[0].startswith("(") and header:
                # continuation like '(12Bit)' belongs to the previous header cell
                header[-1] = header[-1] + toks_clean[0]
                toks_clean = toks_clean[1:]
            header.extend(toks_clean)
    if not rows:
        return []
    ncols = min(len(v) for _, _, v in rows) + 1  # +1: the part-number cell
    # align header cells with the data column count by merging words
    heads = list(rows[0][1])
    while len(heads) > ncols:
        merged = False
        for i in range(len(heads) - 1):
            if re.fullmatch(r"[A-Za-z]{2,8}", heads[i]) and \
                    re.fullmatch(r"[A-Za-z]{2,8}", heads[i + 1]):
                heads[i] = heads[i] + heads[i + 1]
                del heads[i + 1]
                merged = True
                break
        if not merged:
            i = len(heads) - 2
            heads[i] = heads[i] + heads[i + 1]
            del heads[i + 1]
    rows = [(m, heads, v) for m, _, v in rows]
    models = [dict(x=i, raw=m) for i, (m, _, _) in enumerate(rows)]
    prow = []
    for m, heads, vals in rows:
        ncol = len(heads)
        # values may exceed or fall short of the header columns; keep as-is
        vv = list(vals)[:ncol] if len(vals) >= ncol else vals
        prow.append(dict(label="", heads=heads, vals=vv, model=m))
    return dict(models=models, rows=prow, rowmode=True)


def interpret_chip_rows(parse_res, series, prefixes, pdf_name):
    """Turn parsed ordering-table rows into chip dicts filtered by prefix."""
    models = parse_res["models"]
    rows = parse_res["rows"]
    rowmode = parse_res.get("rowmode", False)
    chips = [dict(part_no="", freq_mhz="", flash_kb="", sram_kb="", package="",
                  pin_count="", gpio="", vdd_min="", vdd_max="", uart="", spi="",
                  i2c="", i2s="", can="", usb="", adc="", dac="", timer="",
                  rtc="", wdt="", touchkey="", lcd="", opa="", cmp="", other=[])
             for _ in models]
    if rowmode:
        # rows[i] corresponds to models[i]; attributes across columns.
        # heads[0] is the part-number cell.
        for ci, chip in enumerate(chips):
            heads = list(rows[ci]["heads"])
            vals = list(rows[ci]["vals"])
            if heads and re.search(r"part.?number|型号|商业代码", heads[0], re.I):
                heads = heads[1:]
            if len(vals) > len(heads):
                # extra values (header shorter than data) -> keep as other
                extra = vals[len(heads):]
                vals = vals[:len(heads)]
                chip["other"].extend("col%d=%s" % (len(heads) + k, v)
                                     for k, v in enumerate(extra))
            for h, v in zip(heads, vals):
                if v:
                    _apply_attr(chip, h, v)
    else:
        for row in rows:
            field, trans = _classify_row(row["label"])
            for ci, val in enumerate(row["vals"]):
                if not val:
                    continue
                chip = chips[ci]
                if field:
                    _apply_attr(chip, row["label"], val)
                else:
                    chip["other"].append("%s=%s" % (row["label"].strip(), val.strip()))

    out = []
    for chip in models_zip(models, chips):
        raw = chip["raw"]
        model = raw.replace(" ", "")
        m = re.match(r"(HC32[A-Z0-9]+)-([A-Za-z0-9]+)$", model)
        if m:
            part, pkgsfx = m.group(1), m.group(2)
        else:
            part, pkgsfx = model.rstrip("-"), ""
        if not any(model.startswith(p) for p in prefixes):
            continue
        row = dict(chip)
        row["part_no"] = part
        row["series"] = series
        row["other"] = "|".join(dict.fromkeys(row["other"]))
        row["url"] = ""
        row["source"] = "DS"
        if not row["package"] and pkgsfx:
            pkg, pins = _split_pkg(pkgsfx)
            row["package"] = pkg
            if not row["pin_count"]:
                row["pin_count"] = pins
        out.append(row)
    return out


def models_zip(models, chips):
    for m, c in zip(models, chips):
        d = dict(c)
        d["raw"] = m["raw"]
        yield d


def _apply_attr(chip, label, val):
    field, trans = _classify_row(label)
    v = val.strip()
    lab = re.sub(r"\s+", "", label)
    if trans == "skip":
        return
    if trans == "freq":
        chip["freq_mhz"] = _to_freq(v)
    elif trans == "kb":
        if re.search(r"flash", re.sub(r"[\s_]+", "", label), re.I):
            chip["flash_kb"] = _to_kb(v)
        else:
            chip["sram_kb"] = _to_kb(v)
    elif trans == "gpio":
        mm = re.match(r"(\d+)", v)
        chip["gpio"] = mm.group(1) if mm else ""
        if "+" in v:
            chip["other"].append("gpio_note=%s" % v)
    elif trans == "vdd":
        lo, hi = _to_vdd(v)
        if lo:
            chip["vdd_min"], chip["vdd_max"] = lo, hi
    elif trans == "temp":
        chip["other"].append("temp=%s" % v.replace("℃", "C"))
    elif trans == "package":
        pkg, pins = _split_pkg(v)
        if pkg:
            chip["package"] = pkg
            if not chip["pin_count"]:
                chip["pin_count"] = pins
        else:
            chip["other"].append("package_note=%s" % v)
    elif trans == "num":
        if field == "lpuart":
            chip["other"].append("LPUART=%s" % _to_num(v))
            return
        key = field if field in chip else None
        n = _to_num(v)
        if key:
            if n:
                chip[key] = n
        elif field == "pin_count":
            chip["pin_count"] = n
    elif trans == "adc":
        chm = re.search(r"(\d+)\s*ch", v, re.I)
        if chm:
            chip["adc"] = chm.group(1)
        else:
            n = _to_num(v)
            if n:
                if "bit" in v.lower():
                    chip["other"].append("ADC_res=%s" % v)
                else:
                    chip["other"].append("ADC_units=%s" % n)
    elif trans == "timer":
        if re.match(r"^\d+$", v):
            if not chip["timer"]:
                chip["timer"] = v
            else:
                chip["other"].append("timer_%s=%s" % (lab or "sub", v))
        else:
            chip["other"].append("timer=%s" % v)
    elif trans == "check":
        present = v not in ("-", "—", "x", "X", "")
        if field in ("iwdt", "wwdt"):
            n = _to_num(v) if present else ""
            chip["other"].append("%s=%s" % (field.upper(), n or ("1" if present else "0")))
            return
        key = field if field in chip else None
        if key and present:
            if re.fullmatch(r"\d+", v):
                chip[key] = v
            else:
                chip[key] = _to_num(v) or "1"
                if field in ("lcd", "touchkey") or not re.match(r"^\d+\s*ch", v):
                    chip["other"].append("%s=%s" % (field.upper(), v))
    elif trans == "text":
        chip["other"].append("%s=%s" % (lab, v))
    else:
        chip["other"].append("%s=%s" % (lab, v))


def extract_ds_feature_freq_vdd(sdk_dir):
    """Fallback max freq / VDD range from the DS feature pages (pages 2-3).

    Used only when the ordering table does not carry the attribute.
    """
    docs = glob.glob(os.path.join(sdk_dir, "documents", "DS_*.pdf"))
    if not docs or pypdf is None:
        return "", ("", "")
    try:
        reader = pypdf.PdfReader(docs[0])
        txt = ""
        for p in reader.pages[1:3]:
            txt += (p.extract_text() or "") + "\n"
    except Exception:
        return "", ("", "")
    fm = re.search(r"(\d+)\s*MHz\s*Cortex", txt)
    vm = re.search(r"(\d\.\d+)\s*V?\s*[~～]\s*(\d\.\d+)\s*V", txt)
    freq = fm.group(1) if fm else ""
    vdd = (vm.group(1), vm.group(2)) if vm else ("", "")
    return freq, vdd


def extract_chips(sdk_dir, series_prefixes):
    """series_prefixes: {series: [prefixes]} -> rows grouped per series."""
    docs = glob.glob(os.path.join(sdk_dir, "documents", "DS_*.pdf"))
    if not docs:
        raise RuntimeError("no DS PDF found")
    pdf = docs[0]
    if pypdf is None:
        raise RuntimeError("pypdf unavailable")
    reader = pypdf.PdfReader(pdf)
    results = {s: [] for s in series_prefixes}
    for i, page in enumerate(reader.pages):
        try:
            plain = page.extract_text() or ""
        except Exception:
            continue
        if not _is_order_page(plain):
            continue
        try:
            cands = []
            for fn, rowmode in ((parse_order_column_page, False),
                                (parse_order_plain_page, False),
                                (parse_order_row_page, True)):
                try:
                    r2 = fn(page, os.path.basename(pdf), i)
                except Exception as e2:
                    continue
                if not r2 or not r2.get("models") or not r2.get("rows"):
                    continue
                n_m, n_r = len(r2["models"]), len(r2["rows"])
                if not (n_m >= 2 and n_r >= 2) and not (n_m >= 1 and n_r >= 4):
                    continue
                cands.append((n_m, n_r, rowmode, r2))
            if not cands:
                continue
            # prefer the parse with the most models, then most attribute rows;
            # ties keep the earlier (column) parser
            cands.sort(key=lambda c: (-c[0], -c[1]))
            n_m, n_r, rowmode, res = cands[0]
        except Exception as e:
            warn("page %d of %s failed: %s" % (i, pdf, e))
            continue
        for series, prefixes in series_prefixes.items():
            rows = interpret_chip_rows(res, series, prefixes, os.path.basename(pdf))
            results[series].extend(rows)
    # dedupe on (part_no, package); drop rows without any attribute
    out = {}
    for series, rows in results.items():
        seen = set()
        dd = []
        for r in rows:
            if not any((r["freq_mhz"], r["flash_kb"], r["sram_kb"],
                        r["gpio"], r["vdd_min"], r["package"])):
                continue
            key = (r["part_no"], r["package"])
            if key in seen:
                continue
            seen.add(key)
            dd.append(r)
        out[series] = dd
    return out, os.path.basename(pdf)


# ----------------------------------------------------------------------------
# Instance <-> gate / IRQ matching
# ----------------------------------------------------------------------------

PORT_ALIAS = {"PORT": "GPIO"}
INST_ALIAS = {"DMA": "DMAC", "TICK": "SYSCTRL", "SWD": "SYSCTRL",
              "TRIM": "CLK_TRIM", "CLKTRIM": "CLK_TRIM", "TIM": "TIM",
              "I2CSLV": "I2CSLV", "ADVTIM": "ADTIM"}


def _gate_targets(member, instances):
    """Map gate enum member name -> list of SVD instance names."""
    body = member
    for pre in ("SysctrlPeripheral", "ClkPeripheral", "PeriphClock"):
        if body.startswith(pre):
            body = body[len(pre):]
            break
    low = re.sub(r"[_\s]", "", body).lower()

    def _core_aliases(inst):
        c = re.sub(r"_?MODE[0-9]*$", "", inst).lower()
        c = re.sub(r"[_\s]", "", c)
        out = {c}
        if "lptimer" in c:
            out.add(c.replace("lptimer", "lptim"))
        if "adtimer" in c:
            out.add(c.replace("adtimer", "adtim"))
        return out

    cores = {}
    for inst in instances:
        for c in _core_aliases(inst):
            cores.setdefault(c, inst)
    cores = sorted(cores.items(), key=lambda kv: -len(kv[0]))
    targets = []
    i = 0
    n = len(low)
    while i < n:
        matched = False
        for core, inst in cores:
            if low.startswith(core, i):
                targets.append(inst)
                # digit run expansion: BTIM0 + '12' -> BTIM1 BTIM2
                nxt = i + len(core)
                j = nxt
                while j < n and low[j].isdigit():
                    mb = re.match(r"^(.*?)(\d+)$", core)
                    if mb:
                        cand = mb.group(1) + low[j]
                        for c2, i2 in cores:
                            if c2 == cand:
                                targets.append(i2)
                                break
                    j += 1
                i = nxt
                matched = True
                break
        if not matched:
            i += 1
    # explicit shared-group / alias fixes
    fixes = {
        "gpio": [inst for inst in instances if inst.upper().startswith("GPIO")],
        "pa": [i2 for i2 in instances if i2 == "GPIOA"],
        "pb": [i2 for i2 in instances if i2 == "GPIOB"],
        "pc": [i2 for i2 in instances if i2 == "GPIOC"],
        "pd": [i2 for i2 in instances if i2 == "GPIOD"],
        "basetim": [i2 for i2 in instances if re.match(r"^BT\d+$", i2)] or
                   [i2 for i2 in instances if re.match(r"^TIM\d+$", i2)],
        "bt": [i2 for i2 in instances if re.match(r"^BT\d+$", i2)] or
              [i2 for i2 in instances if re.match(r"^TIM[012]$", i2)],
        "advtim": [i2 for i2 in instances if re.match(r"^ADTIM\d+$", i2)],
        "adc": [i2 for i2 in instances if re.match(r"^ADC\d?$", i2)],
        "adcbgr": [i2 for i2 in instances if re.match(r"^ADC\d?$", i2) or i2 == "BGR"],
        "vclvd": [i2 for i2 in instances if i2.startswith("VC") or i2 == "LVD"],
        "tick": [], "swd": [],
        "wwt": [i2 for i2 in instances if i2 == "WWDT"],
        "iwt": [i2 for i2 in instances if i2 == "IWDT"],
        "dma": [i2 for i2 in instances if i2 == "DMAC"],
        "usb": [i2 for i2 in instances if i2.startswith("USB")],
        "rng": [i2 for i2 in instances if i2 == "TRNG"],
        "trng": [i2 for i2 in instances if i2 == "TRNG"],
        "div": [i2 for i2 in instances if i2 == "HDIV"],
        "trim": [i2 for i2 in instances if "TRIM" in i2.upper()],
        "clktrim": [i2 for i2 in instances if "TRIM" in i2.upper()],
        "tim3": [i2 for i2 in instances if re.match(r"^A?TIM3$", i2)],
        "btim": [i2 for i2 in instances if re.match(r"^BT\d+$", i2)] or
                [i2 for i2 in instances if re.match(r"^TIM[012]$", i2)],
        "lptim": [i2 for i2 in instances if i2.startswith("LPTIM") or
                  i2.startswith("LPTIMER")],
        "lptimer": [i2 for i2 in instances if i2.startswith("LPTIM") or
                    i2.startswith("LPTIMER")],
    }
    if low in fixes:
        targets = fixes[low]
    # family fallback: strip trailing digits and match digitless cores
    if not targets:
        base = re.sub(r"\d+$", "", low)
        if base:
            for core, inst in cores:
                if core == base or core.startswith(base) or base.startswith(core):
                    targets.append(inst)
        # CTIMx (DS/RM name) == GTIMx (SVD name) on old-DDL parts
        m = re.fullmatch(r"ctim(\d)", low)
        if m:
            cand = "GTIM" + m.group(1)
            targets = [i2 for i2 in instances if i2.upper() == cand]
    seen = set()
    out = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _irq_targets(irq_name, instances):
    """Map IRQ enum name (no _IRQn) -> list of instance names."""
    instset = {i.upper(): i for i in instances}
    digitless = {}
    for i in instances:
        digitless.setdefault(re.sub(r"\d+$", "", i.upper()), []).append(i)
    expanded = set()
    parts = irq_name.split("_")
    base = None
    i = 0
    while i < len(parts):
        tok = parts[i]
        cand = tok.upper()
        if base and re.fullmatch(r"\d+", cand):
            mb = re.match(r"^(.*?)(\d+)$", base)
            if mb and (mb.group(1) + cand) in instset:
                expanded.add(mb.group(1) + cand)
                base = mb.group(1) + cand
                i += 1
                continue
        hit = None
        for j in range(len(parts), i, -1):
            cand2 = "_".join(parts[i:j]).upper()
            if cand2 in instset:
                hit = (j, cand2)
                break
        if hit:
            j, cand2 = hit
            expanded.add(cand2)
            base = cand2.split("_")[-1]
            i = j
            continue
        m = re.fullmatch(r"PORT([A-Z])", cand)
        if m and ("GPIO" + m.group(1)) in instset:
            expanded.add("GPIO" + m.group(1))
            base = "GPIO" + m.group(1)
            i += 1
            continue
        m = re.match(r"^([A-Z]+)", cand)
        if m:
            stem = m.group(1)
            if stem in instset:
                expanded.add(stem)
                base = stem
            elif (stem + "C") in instset:
                expanded.add(stem + "C")
                base = stem + "C"
            elif stem in digitless:
                for t in digitless[stem]:
                    expanded.add(t.upper())
                base = stem
        i += 1
    return sorted(expanded)


# ----------------------------------------------------------------------------
# Per-SDK driver
# ----------------------------------------------------------------------------

def extract_sdk(cfg, sdk_root, svd_dir):
    sdk_dir = os.path.join(sdk_root, cfg["dir"])
    result = {
        "sdk": cfg["dir"],
        "series": cfg["series"],
        "chips": {},
        "interrupts": {},
        "dma": {},
        "peripherals": {},
        "memory": {},
        "issues": [],
    }
    dev_hdr_path, dev_hdr = find_device_header(sdk_dir)
    if not dev_hdr:
        result["issues"].append("device header not found")

    # interrupts (same for all series of the SDK)
    if dev_hdr:
        irqs = parse_irq_enum(dev_hdr)
        for s in cfg["series"]:
            result["interrupts"][s] = [
                dict(name=n, irq=v, description=d) for n, v, d in irqs]
        if not irqs:
            result["issues"].append("IRQn_Type parse failed")

    # clock gates
    try:
        gates = parse_gate_def(sdk_dir, dev_hdr or "")
    except Exception as e:
        gates = []
        result["issues"].append("gate parse failed: %s" % e)

    # SVD peripherals
    svd_path = os.path.join(svd_dir, cfg["svd"])
    try:
        pers = parse_svd(svd_path)
    except Exception as e:
        pers = []
        result["issues"].append("svd parse failed: %s" % e)
    inst_names = [p["instance"] for p in pers]

    gate_by_inst = {}
    unmatched_gates = []
    for name, (reg, bit), desc in gates:
        targets = _gate_targets(name, inst_names)
        if not targets:
            unmatched_gates.append(name)
            continue
        for t in targets:
            gate_by_inst.setdefault(t, []).append((reg, name, desc))
    if unmatched_gates:
        result["issues"].append("gate members without SVD target: %s" %
                                ", ".join(unmatched_gates))

    # composite timer units: BTIM register blocks are clocked together with the
    # GTIM/CTIM unit at the nearest lower base (F420 CTIM0..2, F002 GTIM, ...)
    base_of = {p["instance"]: int(p["base"], 0) for p in pers}
    gtim_gates = [(base_of[i], gi[0]) for i, gi in gate_by_inst.items()
                  if re.fullmatch(r"GTIM\d*", i) and base_of.get(i) is not None]
    if gtim_gates:
        for p in pers:
            if not re.fullmatch(r"BTIM\d+", p["instance"]) or \
                    gate_by_inst.get(p["instance"]):
                continue
            b = int(p["base"], 0)
            cands = [g for g in gtim_gates if g[0] <= b]
            if not cands:
                continue
            gbase, (reg, member, desc) = max(cands, key=lambda g: g[0])
            gate_by_inst[p["instance"]] = [(reg, member, desc)]
            if p["notes"]:
                p["notes"] += "; "
            p["notes"] += "clocked by %s (composite timer unit)" % member

    # IRQ -> instance map
    irq_by_inst = {}
    for n, v, d in (parse_irq_enum(dev_hdr) if dev_hdr else []):
        for t in _irq_targets(n, inst_names):
            irq_by_inst.setdefault(t, []).append(n)

    for s in cfg["series"]:
        rows = []
        matched = 0
        for p in pers:
            gi = gate_by_inst.get(p["instance"], [])
            fcg_reg = gi[0][0] if gi else ""
            fcg_field = ";".join(dict.fromkeys(g[1] for g in gi))
            irqs = ";".join(irq_by_inst.get(p["instance"], []))
            if fcg_field:
                matched += 1
            notes = p["notes"]
            if len(gi) > 1:
                notes = (notes + "; " if notes else "") + \
                    "shared gate: %s" % fcg_field
            rows.append(dict(instance=p["instance"], kind=p["kind"],
                             base_address=p["base"], bus="",
                             fcg_reg=fcg_reg, fcg_field=fcg_field,
                             interrupts=irqs, notes=notes))
        result["peripherals"][s] = rows
        result.setdefault("peri_stats", {})[s] = (len(pers), matched)

    # DMA
    try:
        trigs = parse_dma_triggers(sdk_dir)
    except Exception as e:
        trigs = []
        result["issues"].append("dma parse failed: %s" % e)
    dma_chs = parse_dma_channels(sdk_dir)
    if trigs and not dma_chs:
        # 枚举缺失的 SDK 按老 DDL 双通道布局回退（全部受影响 RM 均为 2 条通道）
        dma_chs = 2
        result["issues"].append("dmac.h channel enum not found, fallback 2ch")
    for s in cfg["series"]:
        result["dma"][s] = [dict(dma="DMA1", channel=str(idx), request=req,
                                 description=desc, channels=str(dma_chs))
                            for idx, req, desc in trigs]

    # memory
    for s in cfg["series"]:
        fb, sb, notes = parse_memory_icf(sdk_dir)
        result["memory"][s] = dict(flash_base=fb, sram_base=sb,
                                   sram_kb="", extra_regions="", notes=notes)

    # chips
    try:
        chips = extract_chips(sdk_dir, cfg["prefixes"])[0]
    except Exception as e:
        chips = {}
        result["issues"].append("chips parse failed: %s" % e)
    # fill freq/vdd from the DS feature pages when the ordering table lacks them
    fb_freq, (fb_vmin, fb_vmax) = extract_ds_feature_freq_vdd(sdk_dir)
    for s in cfg["series"]:
        for row in chips.get(s, []):
            if not row["freq_mhz"] and fb_freq:
                row["freq_mhz"] = fb_freq
            if not row["vdd_min"] and fb_vmin:
                row["vdd_min"], row["vdd_max"] = fb_vmin, fb_vmax
        result["chips"][s] = chips.get(s, [])
    return result


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def write_csv(path, header, rows):
    # lineterminator="\n"：仓库行尾策略为全 LF（.gitattributes），
    # csv 模块默认 CRLF 会污染工作区 diff。
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(h, "") for h in header])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sdk-root", default=DEFAULT_SDK_ROOT)
    ap.add_argument("--svd-dir", default=DEFAULT_SVD_DIR)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--series", nargs="*", default=None,
                    help="only process these series")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    chips_rows, int_rows, dma_rows, peri_rows, mem_rows = [], [], [], [], []
    summary = []

    for cfg in SDKS:
        if args.series and not (set(args.series) & set(cfg["series"])):
            continue
        try:
            res = extract_sdk(cfg, args.sdk_root, args.svd_dir)
        except Exception as e:
            warn("SDK %s failed: %s" % (cfg["dir"], e))
            for s in cfg["series"]:
                summary.append(dict(sdk=cfg["dir"], series=s, chips=0, irq=0,
                                    dma=0, peri=0, issues="EXTRACT FAILED: %s" % e))
            continue
        for s in cfg["series"]:
            c = res["chips"].get(s, [])
            i2 = res["interrupts"].get(s, [])
            d = res["dma"].get(s, [])
            p = res["peripherals"].get(s, [])
            m = res["memory"].get(s, {})
            chips_rows.extend(c)
            for r in i2:
                int_rows.append(dict(series=s, **r))
            for r in d:
                dma_rows.append(dict(series=s, dma=r["dma"], channel=r["channel"],
                                     request=r["request"],
                                     channels=r.get("channels", "")))
            peri_rows.extend(dict(series=s, **r) for r in p)
            if m:
                mem_rows.append(dict(series=s, **m))
            n_peri, n_gate = res.get("peri_stats", {}).get(s, (0, 0))
            summary.append(dict(sdk=cfg["dir"], series=s, chips=len(c), irq=len(i2),
                                dma=len(d), peri=len(p),
                                peri_gate_matched=n_gate,
                                issues="; ".join(res["issues"])))

    write_csv(os.path.join(args.out, "chips.csv"), CHIPS_HEADER, chips_rows)
    write_csv(os.path.join(args.out, "interrupts.csv"), INT_HEADER, int_rows)
    write_csv(os.path.join(args.out, "dma.csv"), DMA_HEADER, dma_rows)
    write_csv(os.path.join(args.out, "peripherals.csv"), PERI_HEADER, peri_rows)
    write_csv(os.path.join(args.out, "memory.csv"), MEM_HEADER, mem_rows)

    print("%-22s %-10s %6s %6s %6s %6s  %s" %
          ("sdk", "series", "chip", "irq", "dma", "peri", "issues"))
    for s in summary:
        print("%-22s %-10s %6d %6d %6d %6d  %s" %
              (s["sdk"], s["series"], s["chips"], s["irq"], s["dma"], s["peri"],
               s["issues"][:100]))
    tot = [sum(x[st] for x in summary) for st in ("chips", "irq", "dma", "peri")]
    print("TOTAL chips=%d interrupts=%d dma=%d peripherals=%d memory=%d" %
              (tot[0], tot[1], tot[2], tot[3], len(mem_rows)))


if __name__ == "__main__":
    main()
