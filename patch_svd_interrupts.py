#!/usr/bin/env python3
r"""把外设中断节点补回两仓库 SVD（数据源：data/interrupts/*.yaml + data/family/*.yaml）。

HC32 多数 SVD 的外设 <interrupt> 节点缺失或 value=0（旧值不可信）。本脚本按
data/family/<系列>.yaml 每个外设的 interrupts: 段（实例→中断）与
data/interrupts/<系列>.yaml（中断名→CMSIS IRQ 编号，来自设备头 IRQn_Type 枚举），
对实例名精确匹配的 SVD <peripheral>：

1. 移除该外设下既有 <interrupt> 节点，再按 family YAML 顺序注入
   <name>/<description>/<value>（description 为 "<name> interrupt" 占位文本）；
2. 中断名在 interrupts YAML 中查不到编号的条目：跳过并记入报告，不做猜测；
   外设的全部条目都查不到时整个外设不动（旧节点保留并在报告标注）；
3. family 中无 interrupts: 段的外设不注入；实例在 SVD 中不存在的不注入；
   整系列没有任何 interrupts: 段（LL 共享 INTxxx 重定向向量模型，外设事件
   运行时经 INTC/AOS 路由，无外设专属 NVIC 向量）→ 整系列跳过，不改其 SVD。

写回为文本级行插入/删除：XML 声明、缩进、属性顺序、注释、其余行一律不变，
插入行沿用该文件自身行尾（DDL CRLF / LL LF），保证 git diff 只有中断节点增删。

用法（在 hc32-rs-workspace 下）：
  python3 hc32-data-tools/patch_svd_interrupts.py             # 两仓库全部系列
  python3 hc32-data-tools/patch_svd_interrupts.py --repo hc32-ddl-data
  python3 hc32-data-tools/patch_svd_interrupts.py --series HC32F030 --dry-run

修改 SVD 前须先在对应仓库 svd/CHANGELOG.md 登记（AGENTS.md 规则）。
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

WS = Path(__file__).resolve().parent.parent


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_family(repo: Path, series: str) -> dict[str, list[dict]]:
    """{实例名: [{signal, interrupt}, ...]}（仅含有 interrupts: 段的外设）。"""
    path = repo / "data" / "family" / f"{series}.yaml"
    if not path.exists():
        return {}
    out: dict[str, list[dict]] = {}
    for p in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
        ints = p.get("interrupts")
        if ints:
            out[p["name"]] = ints
    return out


def load_interrupts(repo: Path, series: str) -> dict[str, int] | None:
    path = repo / "data" / "interrupts" / f"{series}.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {k: int(v) for k, v in data.items()}


def peripheral_blocks(lines: list[str]) -> list[tuple[int, int, str]]:
    """[(起始行, 结束行含 </peripheral>, 实例名)]（外设块互不嵌套）。"""
    blocks = []
    i = 0
    while i < len(lines):
        if re.match(r"\s*<peripheral(\s|>)", lines[i]):
            j = i
            name = ""
            while j < len(lines):
                m = re.match(r"\s*<name>(.*?)</name>", lines[j])
                if m and not name:
                    name = m.group(1).strip()
                if re.match(r"\s*</peripheral>", lines[j]):
                    break
                j += 1
            blocks.append((i, j, name))
            i = j + 1
        else:
            i += 1
    return blocks


def interrupt_ranges(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """外设块内既有 <interrupt> 节点的行区间 [start, end)（含闭标签行）。"""
    out = []
    i = start
    while i < end:
        if re.match(r"\s*<interrupt(\s|>)", lines[i]):
            j = i
            while j < end and not re.match(r"\s*</interrupt>", lines[j]):
                j += 1
            out.append((i, j + 1))
            i = j + 1
        else:
            i += 1
    return out


def old_nodes(lines: list[str], start: int, end: int) -> list[tuple[str, str]]:
    """既有节点 [(name, value)]，供报告留档。"""
    out = []
    for a, b in interrupt_ranges(lines, start, end):
        seg = "".join(lines[a:b])
        name = re.search(r"<name>(.*?)</name>", seg)
        value = re.search(r"<value>(.*?)</value>", seg)
        out.append((name.group(1) if name else "?", value.group(1) if value else "?"))
    return out


def patch_svd(text: str, fam_map: dict[str, list[dict]],
              irq: dict[str, int]) -> tuple[str, dict]:
    """返回 (新文本, 本系列报告)。不改文本时原样返回。"""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    rep: dict = {
        "injected_peripherals": 0, "injected_nodes": 0,
        "removed_old_nodes": 0, "kept_old_peripherals": [],
        "unresolved_entries": [],   # (外设, 中断名)
        "removed_not_replaced": [],  # (外设, 旧节点名=值)
        "unmatched_instances": [],   # family 实例 SVD 中不存在
    }
    # 实例名 → [块区间]（同名外设全部处理）
    by_name: dict[str, list[tuple[int, int]]] = {}
    for a, b, name in peripheral_blocks(lines):
        by_name.setdefault(name, []).append((a, b))

    changed = False
    # 待处理块按文档位置倒序排列：修改只影响其后行号，先改后面的块不破坏
    # 前面块的（仍有效的）行区间
    targets = []
    for inst, entries in fam_map.items():
        blocks = by_name.get(inst)
        if blocks is None:
            rep["unmatched_instances"].append(inst)
            continue
        for a, b in blocks:
            targets.append((a, b, inst, entries))
    targets.sort(key=lambda t: t[0], reverse=True)
    for a, b, inst, entries in targets:
        resolved = [(e.get("signal") or e.get("interrupt") or "", irq[e["interrupt"]])
                    for e in entries if e.get("interrupt") in irq]
        unresolved = [e.get("interrupt") for e in entries
                      if e.get("interrupt") not in irq]
        rep["unresolved_entries"] += [(inst, u) for u in unresolved]
        olds = old_nodes(lines, a, b)
        if not resolved:
            if olds:  # 全部条目未解析：外设不动，旧节点保留并标注
                rep["kept_old_peripherals"].append((inst, olds))
            continue
        # 块内子元素缩进取 <peripheral> 下一行前导空白
        child_ind = re.match(r"(\s*)", lines[a + 1]).group(1)
        ind1, ind2 = child_ind, child_ind + "    "
        new_nodes = []
        for sig, num in resolved:
            new_nodes += [
                f"{ind1}<interrupt>{eol}",
                f"{ind2}<name>{esc(sig)}</name>{eol}",
                f"{ind2}<description>{esc(sig)} interrupt</description>{eol}",
                f"{ind2}<value>{num}</value>{eol}",
                f"{ind1}</interrupt>{eol}",
            ]
        # 插入点：<registers> 之前，否则 </peripheral> 之前
        at = b
        for k in range(a, b):
            if re.match(r"\s*<registers(\s|>)", lines[k]):
                at = k
                break
        # 先移除既有节点（自后向前删区间，均在本块内），再插入新节点
        injected_names = {sig for sig, _ in resolved}
        for ra, rb in reversed(interrupt_ranges(lines, a, b)):
            for nm, vl in old_nodes(lines, ra, rb):
                if nm not in injected_names:
                    rep["removed_not_replaced"].append((inst, f"{nm}={vl}"))
            rep["removed_old_nodes"] += 1
            del lines[ra:rb]
            if ra < at:
                at -= rb - ra
        lines[at:at] = new_nodes
        rep["injected_peripherals"] += 1
        rep["injected_nodes"] += len(resolved)
        changed = True

    if not changed:
        return text, rep
    new_text = "".join(lines)
    ET.fromstring(new_text)  # 写前自检：必须仍是合法 XML
    return new_text, rep


def process_repo(repo: Path, dry_run: bool, only: list[str] | None) -> None:
    print(f"## {repo.name}")
    for svd in sorted((repo / "svd").glob("*.svd")):
        series = svd.stem
        if only and series not in only:
            continue
        irq = load_interrupts(repo, series)
        fam_map = load_family(repo, series)
        if irq is None or not fam_map:
            n_peri = len(fam_map)
            print(f"  SKIP {series}: family 无 interrupts: 段（{n_peri} 个映射）"
                  f"——共享 INTxxx 重定向向量模型，外设事件运行时经 INTC/AOS 路由，"
                  f"无外设专属 NVIC 向量，不注入")
            continue
        text = svd.read_bytes().decode("utf-8")
        new_text, rep = patch_svd(text, fam_map, irq)
        if new_text == text:
            print(f"  ---  {series}: 无变更（已注入过或无可注入条目；未解析名 "
                  f"{len(rep['unresolved_entries'])} 个）")
        else:
            if not dry_run:
                svd.write_bytes(new_text.encode("utf-8"))
            print(f"  {'DRY ' if dry_run else 'OK  '}{series}: 注入 "
                  f"{rep['injected_nodes']} 节点 / {rep['injected_peripherals']} 外设，"
                  f"移除旧节点 {rep['removed_old_nodes']} 个")
        if rep["unmatched_instances"]:
            print(f"      family 实例不在 SVD 中（不注入）: "
                  f"{', '.join(rep['unmatched_instances'])}")
        if rep["unresolved_entries"]:
            names = sorted({n for _, n in rep["unresolved_entries"]})
            print(f"      interrupts YAML 查不到编号（跳过 {len(rep['unresolved_entries'])} 条目）: "
                  f"{', '.join(names)}")
        for inst, olds in rep["kept_old_peripherals"]:
            print(f"      {inst}: 条目均未解析，保留旧节点 "
                  f"({', '.join(f'{n}={v}' for n, v in olds)})")
        if rep["removed_not_replaced"]:
            print(f"      旧节点已删但无替换注入: "
                  f"{', '.join(f'{i}:{n}' for i, n in rep['removed_not_replaced'])}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", default=[],
                    help="数据仓库目录（可多次）；默认 hc32-ddl-data 与 hc32-ll-data")
    ap.add_argument("--series", action="append", default=[],
                    help="只处理指定系列（可多次）")
    ap.add_argument("--dry-run", action="store_true", help="只打印报告，不写回")
    args = ap.parse_args()
    repos = [Path(r) for r in args.repo] or [WS / "hc32-ddl-data", WS / "hc32-ll-data"]
    for repo in repos:
        process_repo(repo, args.dry_run, args.series or None)


if __name__ == "__main__":
    sys.exit(main())
