#!/usr/bin/env python3
r"""chiptool 试验脚本：批量对两仓库 SVD 跑 chiptool extract-all，输出到独立目录，
不触碰现有 data/。随后可用 compare_chiptool.py 与现有 registers.csv 对比。

用法：
  python3 chiptool_trial.py                  # 全部 SVD
  python3 chiptool_trial.py --series HC32F460
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
TRIAL = WS / "tools" / "chiptool-trial"


def fix_svd(svd: Path, fixed_dir: Path) -> Path:
    """老代 SVD 的 <addressBlock> 缺 <usage> 标签，svd-parser 严格拒绝；
    复制一份到试验目录补上（不改动原始 SVD）。"""
    fixed_dir.mkdir(parents=True, exist_ok=True)
    text = svd.read_text(encoding="utf-8")
    if "<usage>" not in text.split("<addressBlock>")[1].split("</addressBlock>")[0]:
        text = text.replace("</addressBlock>",
                            "<usage>registers</usage>\n            </addressBlock>")
    out = fixed_dir / svd.name
    out.write_text(text, encoding="utf-8")
    return out


def run_one(svd: Path, out: Path, fixed_dir: Path) -> tuple[bool, str]:
    out.mkdir(parents=True, exist_ok=True)
    use_svd = svd
    if "hc32-ddl-data" in str(svd) and "<usage>" not in svd.read_text(encoding="utf-8"):
        use_svd = fix_svd(svd, fixed_dir)
    proc = subprocess.run(
        ["chiptool", "extract-all", "--svd", str(use_svd), "--output", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--series", help="只跑指定 SVD 名（不含 .svd）")
    args = ap.parse_args()

    ok, fail = [], []
    for repo in ("hc32-ddl-data", "hc32-ll-data"):
        svd_dir = WS / repo / "svd"
        for svd in sorted(svd_dir.glob("*.svd")):
            if args.series and svd.stem != args.series:
                continue
            out = TRIAL / repo / svd.stem
            good, msg = run_one(svd, out, TRIAL / "_svd_fixed")
            n = len(list(out.glob("*.yaml"))) if good else 0
            (ok if good else fail).append(f"{repo}/{svd.stem}: {n} 文件")
            if not good:
                print(f"FAIL {repo}/{svd.stem}: {msg[:200]}", file=sys.stderr)
    print(f"成功 {len(ok)}，失败 {len(fail)}")
    for line in ok:
        print(" ", line)
    for line in fail:
        print(" ", line)


if __name__ == "__main__":
    main()
