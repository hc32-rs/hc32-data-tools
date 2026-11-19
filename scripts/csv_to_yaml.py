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
import xml.etree.ElementTree as ET
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


# ---------- 门控极性 / 总线域 / 功能时钟来源（RM 已核对系列表） ----------
# 覆盖范围：当前仅 HC32F420、HC32F460 两系列（人工逐条核对过 RM），
# 其余 44 个系列三个键全部留空（不输出），属未确认项——这是保守行为，
# 防止从 FCG 编号或 SDK 命名推导出未经核实的总线/极性；批量回填
# 需逐系列读 RM 存储器映射表与门控位描述后登记到本表。
# polarity：DDL 系列 SYSCTRL_*EN/PERI*_CLKEN 门控 1=使能（SDK Sysctrl_SetPeripheralGate
#           / SYSCTRL_PeriphClkEnable 写 bFlag）；LL 系列 PWC_FCG0..3 门控 0=使能
#           （SDK FCG_Fcg<x>PeriphClockCmd ENABLE 分支 CLR_REG32_BIT；复位值 0xFFFF_FFFF）。
#           注意：polarity 只随登记进 RCC_META 的系列输出——虽然按 SDK 域
#           DDL=high/LL=low 规律性强，其余系列未逐个核对前仍不输出。
# bus：RM 存储器映射表的外设所属总线域，逐实例登记（禁止从 FCG 编号推导——
#      F460 FCG1 同时挂 APB1 USART1/2 与 APB2 USART3/4）。
# kernel_clock：外设功能时钟的时钟树来源。type=fixed 表示由总线域唯一决定；
#      type=selectable 表示有专用选择寄存器（编码值→时钟节点名）。
# 证据（文件 + 表号）：
#   HC32F420：RM_HC32F420_Rev1.30 表1-1/1-2（APB0 0x4000_0000..0x4000_7FFF）；
#             图3-1 时钟架构图 To USART0..3 由 SYSCTRL_APBEN0 门控、PCLK0 供电；
#             SDK usart.c USART_BaudRateCal(..., SYSCTRL_GetPCLK0(), ...)。
#   HC32F460：RM_HC32F460_Rev1.71 表1-1（0x4001_D000 USART_1 行标注 APB1 时钟:PCLK1；
#             0x4002_1000/1400 USART_3/4 行标注 APB2 时钟:PCLK1）；
#             RM 5.7.14 PWC_FCG1 位描述 b24=USART1（0=使能）；
#             SDK hc32_ll_usart.c USART_GetBusClockFreq 读 SCFGR.PCLK1S。

_RCC_F420_APB0 = "apb0"

RCC_META: dict = {
    "HC32F420": {
        "polarity": "high",
        # RM 表1-2：0x4000_0000-0x4000_7FFF 全部 APB0 域（USART0-3/I2C0-1/SPI0-1/
        # CTIM0-2/ATIM0/ATIM3）；APB1EN 门控的 ADC/WWDT/CTRIM/LVD/VC 属 APB1 域，
        # AHB0EN 的 DMAC/CRC/FLASH 属 AHBPR0，AHB1EN 的 GPIOA-D 属 AHBPR1。
        "bus": {i: _RCC_F420_APB0 for i in
                ("USART0", "USART1", "USART2", "USART3",
                 "I2C0", "I2C1", "SPI0", "SPI1",
                 "BTIM0", "BTIM1", "BTIM2", "BTIM3", "BTIM4", "BTIM5",
                 "BTIM6", "BTIM7", "BTIM8",
                 "GTIM0", "GTIM1", "GTIM2", "ATIM0", "ATIM3")},
        "kernel_clock": {i: {"type": "fixed", "name": "pclk0"} for i in
                         ("USART0", "USART1", "USART2", "USART3")},
    },
    "HC32F460": {
        "polarity": "low",
        # RM 表1-1：USART_1/2 属 APB1 域（0x4001_x000，时钟:PCLK1），
        # USART_3/4 属 APB2 域（0x4002_x000，时钟同为 PCLK1）。
        "bus": {"USART1": "apb1", "USART2": "apb1",
                "USART3": "apb2", "USART4": "apb2"},
        "kernel_clock": {i: {"type": "fixed", "name": "pclk1"} for i in
                         ("USART1", "USART2", "USART3", "USART4")},
    },
}


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
        regions = []
        fb = (base.get("flash_base") or "").strip()
        sb = (base.get("sram_base") or "").strip()
        # 主 SRAM 大小优先取 memory.csv 的 sram_kb（链接脚本实测）：
        # DS 标称 sram_kb 可能把独立保留区（RAMB/RET_RAM）并入总数
        # （如 A448 标称 68K = 主区 64K + RAMB 4K），直接照抄会合成
        # 不存在的连续大区；无 sram_kb 列或非整数时回退 DS 标称值。
        skb = (base.get("sram_kb") or "").strip()
        if not re.fullmatch(r"\d+", skb):
            skb = sz if re.fullmatch(r"\d+", sz) else ""
        if fb:
            regions.append({
                "name": "FLASH", "kind": "flash",
                "address": HexInt(int(fb, 16)), "size": f"{f}K",
                "access": {"read": True, "write": True, "execute": True},
            })
        if sb and skb:
            regions.append({
                "name": "SRAM", "kind": "ram",
                "address": HexInt(int(sb, 16)), "size": f"{skb}K",
                "access": {"read": True, "write": True, "execute": True},
            })
        # 独立保留区（RAMB/RET_RAM 等）逐区输出，保持物理 bank 与地址别名区分
        for spec in (base.get("extra_regions") or "").split(";"):
            spec = spec.strip()
            m = re.fullmatch(r"(\w+)@(0x[0-9A-Fa-f]+)/(\w+)K", spec)
            if not m:
                continue
            name, addr, size = m.group(1), int(m.group(2), 16), m.group(3)
            regions.append({
                "name": name, "kind": "ram",
                "address": HexInt(addr), "size": f"{size}K",
                "access": {"read": True, "write": True, "execute": True},
            })
        if not regions:
            continue
        doc = {"memory": regions}
        body = (f"# {s} Flash {f}K / SRAM {sz}K 的内存布局"
                f"（型号：{', '.join(sorted(combos[(s, f, sz)])[:4])}…）。\n"
                f"# 主 SRAM {skb}K + 独立保留区来自 SDK 链接脚本/RM 内存映射表\n"
                f"#（见 data/csv/memory.csv sram_kb/extra_regions/notes）；DS 标称\n"
                f"# SRAM {sz}K 可能并入保留区（如 68K=主区 64K+RAMB 4K）。\n")
        (out_dir / fname).write_text(body + dump_yaml(doc), encoding="utf-8")
        files[(s, f, sz)] = fname
    if combos:
        prune(out_dir, set(files.values()))
    return files


# ---------- MODE 视图关联 ----------

# SVD 把一个物理定时器拆成多个寄存器视图外设（同基址）：
#   TIM0_MODE0 / TIM0_MODE1 / TIM0_MODE23（下划线）
#   ATIM0MODE0 / ATIM0MODE1 / ATIM0MODE23（无下划线）
# peripherals.csv 存物理名（TIM0/ATIM0，地址+门控+中断宿主），
# register_versions.csv 存 SVD 视图名（含版本）。这里建立反向关联：
# 视图名剥离 MODE 后缀 → 物理实例，一条 family 项挂 views 列表，
# 物理身份（地址/FCG/中断）不变，每视图保留 SVD 原名作审计溯源键。
MODE_VIEW_RE = re.compile(r"^(?P<core>.+?)_?MODE(?P<num>0|1|23)$")
MODE_ORDER = {"MODE0": 0, "MODE1": 1, "MODE23": 2}


def build_views(register_versions: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """register_versions.csv → {(series, 物理实例): [视图, ...]}。

    视图按 MODE0→MODE1→MODE23 语义序输出；每项含 SVD 视图原名。
    """
    views: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in register_versions:
        m = MODE_VIEW_RE.match(r["instance"])
        if not m:
            continue
        views[(r["series"], m.group("core"))].append({
            "name": r["instance"],
            "mode": f"MODE{m.group('num')}",
            "kind": r["kind"],
            "version": r["version"],
            "block": r["block"],
        })
    for lst in views.values():
        lst.sort(key=lambda v: MODE_ORDER[v["mode"]])
    return views


def check_views(views: dict, ctx: dict) -> list[str]:
    """视图关联完整性校验，返回错误清单（非空即中止写文件）。

    - 同物理实例的各视图 kind/block 一致，且 kind 与 peripherals.csv 吻合；
    - 双向核对：peripherals.csv 带"视图合并"note 的行必须有视图，
      有视图的行必须带 note（提取侧 collapse 与本表须互证）；
    - 物理实例不得同时持有精确名版本（防单视图形态混淆）。
    """
    errors: list[str] = []
    inst_ver = {(r["series"], r["instance"]) for r in ctx["register_versions"]}
    by_inst: dict[tuple[str, str], dict] = {}
    for r in ctx["peripherals"]:
        by_inst[(r["series"], r["instance"])] = r

    for (s, core), lst in sorted(views.items()):
        kinds = {v["kind"] for v in lst}
        blocks = {v["block"] for v in lst}
        if len(kinds) > 1 or len(blocks) > 1:
            errors.append(f"{s} {core}: 视图 kind/block 不一致 {sorted(kinds)}/{sorted(blocks)}")
        row = by_inst.get((s, core))
        if row is None:
            errors.append(f"{s} {core}: 视图物理实例不在 peripherals.csv")
            continue
        if row["kind"] not in kinds:
            errors.append(f"{s} {core}: peripherals kind={row['kind']} 与视图 kind {sorted(kinds)} 不吻合")
        if (s, core) in inst_ver:
            errors.append(f"{s} {core}: 同时存在精确名版本与 MODE 视图")
        modes = [v["mode"] for v in lst]
        if len(set(modes)) != len(modes) or not set(modes) <= set(MODE_ORDER):
            errors.append(f"{s} {core}: 视图 mode 异常 {modes}")

    note_set = {k for k, r in by_inst.items()
                if "SVD register views" in (r.get("notes") or "")}
    view_set = set(views)
    for k in sorted(note_set - view_set):
        errors.append(f"{k[0]} {k[1]}: peripherals 标记视图合并但 register_versions 无视图")
    for k in sorted(view_set - note_set):
        errors.append(f"{k[0]} {k[1]}: 有 MODE 视图但 peripherals 未标记合并")
    return errors


# ---------- 外设中断引用归一 ----------

# 证据不足的条目保留在审计 CSV，不生成静态向量引用。
# 依据：SVD <value> 声称的 IRQ 号在设备头 IRQn_Type 枚举中不存在（头枚举跳号），
# 或信号无 SVD 值可查。核实过设备头后逐条登记，见交付报告。
UNRESOLVED_INTERRUPTS: dict[tuple[str, str, str], str] = {
    # F472/A472 头枚举 162(MDIO) 后直接跳到 164(I2C1)，无 163 号向量
    ("HC32A472", "PLA", "PLA"): "头枚举跳号 163，无 163 号向量可指",
    ("HC32F472", "PLA", "PLA"): "头枚举跳号 163，无 163 号向量可指",
    # F334/F336 头枚举 135(USART4_TCI) 后直接跳到 137(USART1_WUPI)
    ("HC32F334", "PLA", "PLAINT"): "头枚举跳号 136，无 136 号向量可指",
    ("HC32F336", "PLA", "PLAINT"): "头枚举跳号 136，无 136 号向量可指",
    # K116/K118 头枚举 27(EMB) 后直接跳到 29(CMP)
    ("HC32K116", "PWC", "LVD"): "头枚举跳号 28，无 28 号向量可指",
    ("HC32K118", "PWC", "LVD"): "头枚举跳号 28，无 28 号向量可指",
}


def load_svd_signals(repo: Path, is_ll: bool) -> dict[str, dict[str, list[tuple[str, int | None]]]]:
    """保留节点顺序，允许同一 SVD 信号名对应多个 IRQ（如 F472 TMR2_1）。"""
    out = {}
    for svd in sorted((repo / "svd").glob("*.svd")):
        series = {}
        root = ET.parse(svd).getroot()
        for p in root.findall("peripherals/peripheral"):
            name = (p.findtext("name") or "").strip()
            if not name:
                continue
            for it in p.findall("interrupt"):
                signal = (it.findtext("name") or "").strip()
                if is_ll:
                    signal = signal.removesuffix("_IRQn")
                if not signal:
                    continue
                val = it.findtext("value")
                v = int(val.strip()) if val and val.strip().lstrip("-").isdigit() else None
                series.setdefault(name, []).append((signal, v))
        if series:
            out[svd.stem] = series
    return out


def resolve_signal(signal: str, irq: int | None,
                   primary: dict[str, str], by_num: dict[int, str]) -> str | None:
    """信号名 → 向量表规范名。

    优先级（与 README“中断引用归一”一节一致）：
    1. 信号名即设备头枚举名（或其同号别名）→ 同号组首个名
       （= hc32-data-gen 向量去重保留的主名）；
    2. SVD <value> 给出的 IRQ 号 → 该号首个枚举名（异名归一，如 ADC_1→ADC1）；
    3. 都查不到 → 不生成静态引用。
    """
    if signal in primary:
        return primary[signal]
    return by_num.get(irq) if irq is not None else None


def emit_family(repo: Path, ctx: dict, inst_ver: dict,
                 views: dict[tuple[str, str], list[dict]]) -> dict[str, int]:
    out_dir = repo / "data" / "family"
    out_dir.mkdir(parents=True, exist_ok=True)
    by_series: dict[str, list[dict]] = defaultdict(list)
    for r in ctx["peripherals"]:
        by_series[r["series"]].append(r)

    # 设备头 IRQn 枚举（CSV 行序 = 枚举序）→ 同号组首名（去重后的向量主名）
    irq_pairs: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in ctx["interrupts"]:
        if int(r["irq"] or 0) >= 0:
            irq_pairs[r["series"]].append((r["name"], int(r["irq"])))
    primary: dict[str, dict[str, str]] = {}
    by_num: dict[str, dict[int, str]] = {}
    for s, pairs in irq_pairs.items():
        groups: dict[int, list[str]] = defaultdict(list)
        for n, q in pairs:
            groups[q].append(n)
        primary[s] = {n: groups[q][0] for n, q in pairs}
        by_num[s] = {q: names[0] for q, names in groups.items()}

    svd_signals = load_svd_signals(repo, any("trigger_id" in row for row in ctx["dma"][:1]))
    unresolved: list[str] = []
    known_unresolved: list[str] = []
    outputs = {}
    no_regs: list[str] = []
    stats = {}
    report: list[str] = []
    for s, rows in sorted(by_series.items()):
        items = []
        for r in sorted(rows, key=lambda r: r["instance"]):
            inst = r["instance"]
            item_views = views.get((s, inst))
            ver = inst_ver.get((s, inst))
            if item_views is None and ver is None:
                # register_versions.csv 缺该实例且无 MODE 视图（罕见：SVD 外设无寄存器块）
                no_regs.append(f"{s} {inst}")
            item = {"name": inst}
            try:
                item["address"] = HexInt(int(r["base_address"], 16))
            except (ValueError, KeyError):
                pass
            if item_views is not None:
                # MODE 拆分实例：物理身份在本项，寄存器按 SVD 视图分挂。
                # 不输出无 version 的 registers 块（data-gen 侧会降级）。
                item["views"] = [
                    {"name": v["name"], "mode": v["mode"],
                     "kind": v["kind"], "version": v["version"], "block": v["block"]}
                    for v in item_views
                ]
                report.append(f"{s} {inst} @ {r['base_address']}: " +
                              ", ".join(f"{v['name']}({v['mode']})={v['version']}"
                                         for v in item_views))
            else:
                ver = ver or {"kind": r["kind"], "version": "", "block": r["kind"].upper()}
                item["registers"] = {"kind": ver["kind"], "block": ver["block"]}
                if ver["version"]:
                    item["registers"]["version"] = ver["version"]
            rcc = {}
            if (r.get("fcg_reg") or "").strip():
                rcc["enable"] = {
                    "register": r["fcg_reg"].strip(),
                    "field": (r.get("fcg_field") or "").strip(),
                }
                # 门控极性 + 总线域 + 功能时钟来源（RM 已核对系列表，
                # 见 emit_peripherals 顶部 RCC_META 注释；未登记系列不输出，
                # HAL 侧视为未确认）。
                meta = RCC_META.get(s)
                if meta:
                    rcc["polarity"] = meta["polarity"]
                    bus = meta.get("bus", {}).get(inst)
                    if bus:
                        rcc["bus"] = bus
                    kc = meta.get("kernel_clock", {}).get(inst)
                    if kc:
                        item["kernel_clock"] = kc
            if rcc:
                item["rcc"] = rcc
            ints = [x.strip() for x in (r.get("interrupts") or "").split(";") if x.strip()]
            if ints:
                source = svd_signals.get(s, {}).get(inst, [])
                # CSV 与 SVD 同实例的信号序列一致时，才按位置取 IRQ。
                values = ([value for _, value in source]
                          if [name for name, _ in source] == ints else [None] * len(ints))
                refs = []
                for sig, irq in zip(ints, values):
                    ref = resolve_signal(sig, irq,
                                         primary.get(s, {}), by_num.get(s, {}))
                    if ref is None:
                        reason = UNRESOLVED_INTERRUPTS.get((s, inst, sig))
                        if reason:
                            known_unresolved.append(f"{s} {inst}: {sig}（{reason}）")
                        else:
                            unresolved.append(f"{s} {inst}: {sig}（SVD 值 {irq}，头枚举无此名/号）")
                        continue
                    refs.append({"signal": sig, "interrupt": ref})
                if refs:
                    item["interrupts"] = refs
            items.append(item)
        pin_note = (
            "# 已登记型号的引脚复用由 chips YAML 的 include_pins 引用型号级 data/pins/。\n"
            if s in PIN_ROUTE_SOURCES
            else "# 引脚复用（pins）暂缺，待从数据手册 GPIO 章节补齐。\n"
        )
        header = (f"# {s} 系列外设实例（hc32-data-tools/csv_to_yaml.py 生成）。\n"
                  f"# 地址来自 SVD；时钟门控来自设备头 FCG/PWC 位定义；\n"
                  f"# MODE 拆分定时器（TIMx/ATIMx）用 views 挂 SVD 视图（同基址寄存器布局\n"
                  f"# 叠影，工作模式由控制寄存器 MD 位运行时选择），地址/门控/中断在物理实例；\n"
                  f"{pin_note}")
        outputs[s] = header + dump_yaml(items)
        stats[s] = len(items)
    # MODE 视图关联报告：逐实例列出 物理名@地址 → 视图(SVD名)=版本。
    if report:
        print(f"  MODE 视图关联：{len(report)} 个物理实例")
        for line in report:
            print(f"    {line}")
    # 无版本且无视图的实例：当前应为 0，有则显式列出（不静默降级）。
    if no_regs:
        print(f"  WARNING: {len(no_regs)} 个实例既无版本也无 MODE 视图"
              f"（生成侧将降级为纯地址外设）：", file=sys.stderr)
        for x in no_regs:
            print(f"    {x}", file=sys.stderr)
    # 已知证据不足条目之外的未解析引用：不猜测，打印并退出非零，
    # 防止新系列把未归一引用悄悄带进 family YAML / 芯片 JSON。
    if unresolved:
        print(f"  ERROR: {len(unresolved)} 个中断引用无法解析（不在头枚举/SVD 无号）：",
              file=sys.stderr)
        for u in unresolved:
            print(f"    {u}", file=sys.stderr)
        raise SystemExit(1)
    for line in known_unresolved:
        print(f"  WARNING: 未生成静态中断引用：{line}", file=sys.stderr)
    for s, body in outputs.items():
        (out_dir / f"{s}.yaml").write_text(body, encoding="utf-8")
    if outputs:
        prune(out_dir, {f"{s}.yaml" for s in outputs})
    return stats


def _dma_controller_channels(ctx: dict) -> dict[str, int]:
    """按 (series, dma 控制器) 汇总物理通道数。

    - LL：dma.csv 的 channel 列是 RM 通道范围（如 `0-5`，来自设备头
      hc32_ll_aos.h 的 AOS_DMA<x>_<n> 宏），取 max+1；
    - DDL：channels 列是 dmac.h DmaCh/DMA_CHANNEL 枚举数
      （A052/F052=5、F420=6、其余 2，RM 第 12 章佐证）。
    同一 (series, dma) 出现多个值时取最大（老 CSV 行间混写时保守取宽）。
    """
    out: dict[str, int] = {}
    for r in ctx["dma"]:
        m = re.fullmatch(r"(\d+)-(\d+)", (r.get("channel") or "").strip())
        n = 0
        if m:
            n = int(m.group(2)) + 1
        elif re.fullmatch(r"\d+", (r.get("channel") or "").strip()):
            # DDL 老语义：channel 列 = TRI_SEL 索引，不是通道数；忽略
            n = 0
        chs = (r.get("channels") or "").strip()
        if re.fullmatch(r"\d+", chs):
            n = max(n, int(chs))
        if n:
            key = f"{r['series']}/{r['dma']}"
            out[key] = max(out.get(key, 0), n)
    return out


def emit_chips(repo: Path, ctx: dict, mem_files: dict,
               pin_parts: set[str]) -> list[Path]:
    out_dir = repo / "data" / "chips"
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_series = {r["series"] for r in ctx["peripherals"]}
    cpu_by_series = {r["series"]: r.get("cpu", "") for r in ctx["svd_meta"]}
    mem_base = {r["series"]: r for r in ctx["memory"]}
    mem_base_series = set(mem_base)
    dma_ch_count = _dma_controller_channels(ctx)

    # 合并 website 行：DS 优先，website 补缺字段、补缺型号
    web_by_part: dict[str, dict] = {}
    for r in ctx["website_chips"]:
        web_by_part.setdefault(r["part_no"], r)
    # 同 part_no 多行（如 F003/F005 C?PA 的 SOP20+TSSOP20）逐行保留 package，
    # 其余列取首行（DS 订货表多行间仅封装不同）；之前 dict() 覆盖会丢掉一种封装
    part_pkgs: dict[str, list[str]] = {}
    merged: dict[str, dict] = {}
    for r in ctx["chips"]:
        part = r["part_no"].strip()
        if not part or not re.match(r"^HC", part):
            continue  # 跳过占位/空行
        pkgs = part_pkgs.setdefault(part, [])
        pkg = (r.get("package") or "").strip()
        if pkg and pkg not in pkgs:
            pkgs.append(pkg)
        merged.setdefault(part, dict(r))
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
        # packages：同型号多封装全量列出（ch32-data 同构字段本就是列表）
        pkgs = part_pkgs.get(part) or [(row.get("package") or "").strip()]
        pkgs = [p for p in pkgs if p]
        doc = {
            "name": part,
            "family": s,
            "packages": [{"name": part, "package": p} for p in pkgs],
        }
        f, sz = (row.get("flash_kb") or "").strip(), (row.get("sram_kb") or "").strip()
        if (s, f, sz) in mem_files and s in mem_base_series:
            doc["include_memory"] = f"../memory/{mem_files[(s, f, sz)]}"
            # SRAM 大小与 memory 布局 YAML 的主区一致（链接脚本实测，
            # DS 标称可能并入保留区）；resolve_memory 会用该值覆盖区大小
            base = mem_base.get(s, {})
            skb = (base.get("sram_kb") or "").strip()
            if not re.fullmatch(r"\d+", skb):
                skb = sz
            doc["memory_sizes"] = {"FLASH": f"{f}K", "SRAM": f"{skb}K"}
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
        # 仅挂接本轮 emit_pins 已验证并写出的型号。
        if part in pin_parts:
            core_dict["include_pins"] = [f"../pins/{s}_{part}.yaml"]
        dma_files = sorted((repo / "data" / "dma").glob(f"{s}_*.yaml"))
        if dma_files:
            core_dict["include_dma_channels"] = {
                p.stem.split(f"{s}_", 1)[1]: f"../dma/{p.name}" for p in dma_files
            }
            # core.dma_channels：控制器物理通道清单（ch32-data 同构字段）。
            # 通道数来自 dma.csv（LL: AOS 通道宏范围 / DDL: dmac.h 枚举列），
            # 找不到计数时跳过该控制器并留告警，不猜默认值。
            chans = []
            for p in dma_files:
                ctrl = p.stem.split(f"{s}_", 1)[1]
                n = dma_ch_count.get(f"{s}/{ctrl}")
                if not n:
                    print(f"  WARN: {s}/{ctrl} dma 通道数未知，core.dma_channels 跳过该控制器")
                    continue
                chans.extend(
                    {"name": f"{ctrl}_CH{i}", "dma": ctrl, "channel": i}
                    for i in range(n)
                )
            if chans:
                core_dict["dma_channels"] = chans
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


# ---------- 引脚路由 × 封装管脚交集（pins 小样本） ----------

# 数据源：两个探针（docs/ 下，可复现、断言全绿）：
#   pin-mux-probe/  Agent1 路由表：<series>_<sample>.csv，pad→(peripheral,signal,fsel)
#   pin-probe/      封装管脚表：hc32f460_pin_terminals.csv，
#                   (part_or_package,terminal)→pad
# 交集语义：路由的 pad 必须在该型号该封装的 GPIO 焊盘集合内，才挂进该型号。
# 产出：data/pins/<series>_<PART>.yaml（型号级单文件；peripheral 实例 → pins 列表，
# ch32-data 同构的 {pin, signal, func}），chips YAML 的 core.include_pins 引用。
# 生成侧在 data-gen 按 include 合并（与 include_peripherals 同构）。

# 探针路由表 → 系列路由清单。后续系列补 pins 数据时往这里加条目即可。
PIN_ROUTE_SOURCES = {
    # series: (路由 CSV 相对路径, 封装管脚 CSV 相对路径)
    "HC32F460": ("docs/pin-mux-probe/f460_uart_spi_routes.csv",
                 "docs/pin-probe/hc32f460_pin_terminals.csv"),
    # F003 路由表已就绪（f003_uart_spi_routes.csv，28 条），
    # 但封装管脚表未提取，无交集基准，暂不接入。
}
# 样本型号 → 封装名（pin-probe 契约）。
PIN_TERMINAL_SAMPLE_PARTS = {
    "HC32F460": {"HC32F460JCTA": "LQFP48", "HC32F460PCTB": "LQFP100"},
}


def _pin_probe_root() -> Path:
    return Path(__file__).resolve().parents[1]  # hc32-data-tools/


def read_pin_probe(series: str, relative_path: str) -> list[dict]:
    path = _pin_probe_root() / relative_path
    rows = read_csv(path)
    if not rows:
        raise SystemExit(f"emit_pins: {series} 引脚探针 CSV 缺失或没有数据行：{path}")
    return rows


def emit_pins(repo: Path, ctx: dict) -> dict[str, int]:
    """路由表 × 封装管脚逐封装交集 → 按型号归组的 data/pins/<series>_<PART>.yaml。

    只处理已有两种探针数据的系列；路由表覆盖的型号必须在 chips.csv 中
    （packages 对得上封装），否则报错——防止路由表与型号表脱钩。
    """
    out_dir = repo / "data" / "pins"
    out_dir.mkdir(parents=True, exist_ok=True)
    stats: dict[str, int] = {}
    written: set[str] = set()

    # 型号 → [封装]（emit_chips 同口径：同型号多行全收）
    part_pkgs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in ctx["chips"]:
        part = row["part_no"].strip()
        pkg = (row.get("package") or "").strip()
        if part and part in PIN_TERMINAL_SAMPLE_PARTS.get(row["series"], {}):
            pkgs = part_pkgs[(row["series"], part)]
            if pkg and pkg not in pkgs:
                pkgs.append(pkg)

    for series, (route_path, terminal_path) in sorted(PIN_ROUTE_SOURCES.items()):
        sample_parts = {
            part: pkg for part, pkg in PIN_TERMINAL_SAMPLE_PARTS.get(series, {}).items()
            if (series, part) in part_pkgs
        }
        if not sample_parts:
            continue
        routes = read_pin_probe(series, route_path)
        terminals = read_pin_probe(series, terminal_path)
        # 封装 → GPIO 焊盘集合（pin-probe kind=gpio 行）
        pkg_pads: dict[str, set[str]] = defaultdict(set)
        for t in terminals:
            if t.get("kind") == "gpio":
                pkg_pads[t["part_or_package"]].add(t["pad"])
        for part, pkg in sample_parts.items():
            pkgs = part_pkgs[(series, part)]
            if pkg not in pkgs:
                raise SystemExit(
                    f"emit_pins: {series} 样本型号 {part} 的封装 {pkg} "
                    f"不在 chips.csv（{pkgs}）")

        # 交集完整性：路由表 pad 全集必须 ⊆ 封装管脚表已知焊盘（防管脚表漏行）
        if pkg_pads:
            all_route_pads = {r["pad"] for r in routes}
            missing = all_route_pads - set().union(*pkg_pads.values())
            if missing:
                raise SystemExit(
                    f"emit_pins: {series} 路由表 pad 不在任何封装管脚表中: "
                    f"{sorted(missing)}")
        # 校验路由表外设实例在 family YAML（peripherals.csv）中存在
        insts = {r["instance"] for r in ctx["peripherals"]
                 if r["series"] == series}
        unknown = {r["peripheral"] for r in routes} - insts
        if unknown:
            raise SystemExit(
                f"emit_pins: {series} 路由表外设不在 peripherals.csv: "
                f"{sorted(unknown)}")

        # 逐 (part, package) 求交集；**同型号多封装**按型号归组：
        # 生成的 chips YAML 的 include_pins 是型号级单引用，Peripheral.pins
        # 无封装维度——两个封装的路由完全一致时合并为型号级文件，
        # 分歧或任一封装缺管脚表基准时**显式拒绝**（不静默合并/丢归属）。
        by_part: dict[str, dict[str, dict]] = {}
        for part in sorted(sample_parts):
            for pkg_i in part_pkgs[(series, part)]:
                pads = pkg_pads.get(pkg_i)
                if not pads:
                    raise SystemExit(
                        f"emit_pins: {series} {part} 的封装 {pkg_i} 无管脚表数据，"
                        f"无法求路由×封装交集（多封装型号拒绝合并："
                        f"Peripheral.pins 无封装维度）")
                hits = [r for r in routes if r["pad"] in pads]
                inter: dict[str, list[dict]] = defaultdict(list)
                for r in hits:
                    inter[r["peripheral"]].append(
                        {"pin": r["pad"], "signal": r["signal"],
                         "func": int(r["selector_value"])})
                # 文件条目形态 = serde PeripheralWithPins：{ 实例: { pins: [...] } }
                # （inter 是 defaultdict，须转普通 dict 才能安全 dump_yaml）
                inter_doc = {p: {"pins": list(pins)}
                             for p, pins in sorted(inter.items())}
                prev = by_part.get(part)
                if prev is not None and prev != inter_doc:
                    raise SystemExit(
                        f"emit_pins: {series} {part} 的多封装路由存在分歧，"
                        f"Peripheral.pins 无封装维度无法共存"
                        f"（需引入按封装的路由结构后再接入该型号）")
                by_part[part] = inter_doc
        for part, inter_doc in sorted(by_part.items()):
            pkgs = "+".join(part_pkgs[(series, part)])
            n = sum(len(v["pins"]) for v in inter_doc.values())
            header = (f"# {series} {part}（{pkgs}）引脚路由（csv_to_yaml.py 生成）。\n"
                      f"# 来源：DS 表 2-1/2-2 引脚功能表（pin-mux-probe）× 表 2-1\n"
                      f"# 封装管脚列（pin-probe）逐封装交集；{n} 条，func=FSEL 编码。\n")
            name = f"{series}_{part}.yaml"
            (out_dir / name).write_text(header + dump_yaml(inter_doc),
                                        encoding="utf-8")
            written.add(name)
            stats[part] = n
    prune(out_dir, written)
    return stats




def convert_repo(repo: Path) -> dict:
    PRUNED.clear()
    ctx = read_ctx(repo)
    if not ctx["chips"]:
        raise SystemExit(f"{repo}: 缺少 data/csv/chips.csv")
    # registers 由 chiptool_registers.py 生成，这里只读它的实例→版本映射
    inst_ver = {(r["series"], r["instance"]):
                {"kind": r["kind"], "version": r["version"], "block": r["block"]}
                for r in ctx["register_versions"]}
    # MODE 视图反向关联（SVD 视图名 → 物理实例），完整性校验不过即中止
    views = build_views(ctx["register_versions"])
    errs = check_views(views, ctx)
    if errs:
        print(f"ERROR: MODE 视图关联校验失败 {len(errs)} 条：", file=sys.stderr)
        for e in errs:
            print(f"  {e}", file=sys.stderr)
        raise SystemExit(1)
    stats = {}
    stats["interrupts"] = emit_interrupts(repo, ctx)
    stats["dma"] = emit_dma(repo, ctx)
    mem_files = emit_memory(repo, ctx)
    stats["memory"] = len(mem_files)
    stats["family"] = emit_family(repo, ctx, inst_ver, views)
    # chips YAML 只引用本轮验证通过的引脚路由。
    pin_stats = emit_pins(repo, ctx)
    if pin_stats:
        stats["pins"] = pin_stats
    chips = emit_chips(repo, ctx, mem_files, set(pin_stats))
    stats["chips"] = len(chips)
    return stats


def validate(repo: Path) -> list[str]:
    """逐个 yaml.safe_load 校验输出。"""
    errors = []
    n = 0
    for sub in ("chips", "family", "interrupts", "dma", "memory", "registers", "pins"):
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
