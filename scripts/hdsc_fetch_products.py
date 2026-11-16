#!/usr/bin/env python3
r"""从小华半导体官网 (www.xhsc.com.cn) 拉取 HC32 型号列表为 CSV。

数据链路：
  /产品线            → 4 个产品分类 slug（通用控制/电机与电源控制/超低能耗/汽车电子）
  /产品线/<分类>      → 系列链接（静态 HTML 中以 JSON 转义形式内嵌，如 \/产品线/.../hc32f460）
  /产品线/<分类>/<系列> → 型号表（静态 <table>，含主频/内核/Flash/RAM/GPIO/外设/封装）

输出列与 hc32-data-tools/CSV_SCHEMA.md 的 chips.csv 完全一致（source=website），写入两个
数据仓库的 data/csv/website_chips.csv，供 hc32-data-tools/csv_to_yaml.py 与数据手册提取
的 chips.csv 合并（DS 行优先，website 行补缺）。

用法：
  python3 scripts/hdsc_fetch_products.py                # 写入两个仓库
  python3 scripts/hdsc_fetch_products.py --out foo.csv  # 只写指定文件
  python3 scripts/hdsc_fetch_products.py --list-series  # 只打印发现的系列
"""
from __future__ import annotations

import argparse
import codecs
import csv
import html as html_mod
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://www.xhsc.com.cn"
LANDING = f"{BASE}/%e4%ba%a7%e5%93%81%e7%ba%bf"  # /产品线
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# chips.csv 统一列头（与 CSV_SCHEMA.md 一致）
CHIP_FIELDS = [
    "part_no", "series", "freq_mhz", "flash_kb", "sram_kb", "package",
    "pin_count", "gpio", "vdd_min", "vdd_max", "uart", "spi", "i2c", "i2s",
    "can", "usb", "adc", "dac", "timer", "rtc", "wdt", "touchkey", "lcd",
    "opa", "cmp", "other", "url", "source",
]

PACKAGE_RE = re.compile(
    r"-(LQFP\d+|QFN\d+|TSSOP\d+|SOP\d+|VFBGA\d+|BGA\d+|DIP\d+|CSP\d+|"
    r"WLCSP\d*|PGA\d*)$", re.I)

# 订货型号后缀里的缩写封装前缀（官网表格用 LQ48/QN32TR 等缩写）
_PKG_PREFIX = {
    "LQ": "LQFP", "QN": "QFN", "TS": "TSSOP", "SOP": "SOP",
    "LQFP": "LQFP", "QFN": "QFN", "TSSOP": "TSSOP", "VFBGA": "VFBGA",
    "BGA": "BGA", "DIP": "DIP", "CSP": "CSP", "WLCSP": "WLCSP",
    "PGA": "PGA", "SMD": "SMD", "ZIP": "ZIP",
}
_SUFFIX_RE = re.compile(r"^([A-Za-z]{1,5})(\d{1,3})([A-Za-z]{0,4})$")


def split_package(part_full: str) -> tuple[str, str]:
    """拆出 (part_no, package)：把 -LQ48 / -QFN48TR 等封装后缀从型号中剥离。"""
    if "-" not in part_full:
        return part_full, ""
    base, suffix = part_full.split("-", 1)
    m = _SUFFIX_RE.match(suffix.strip())
    if not m:
        return part_full, ""
    prefix, num, tail = m.groups()
    pkg = _PKG_PREFIX.get(prefix.upper(), prefix.upper()) + num
    return base, pkg


def fetch(url: str, retries: int = 3) -> str:
    """GET 一个页面，返回文本。失败重试。"""
    last_err: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - 网络错误统一重试
            last_err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"fetch failed: {url}: {last_err}")


def decode_json_escapes(s: str) -> str:
    r"""解码页面内嵌 JSON 转义（\/ 与 \uXXXX），不影响普通字符。"""
    s = s.replace("\\/", "/")
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


def _normalized(html: str) -> str:
    """解码 JSON 转义与百分号编码，统一成普通中文路径文本。"""
    h = decode_json_escapes(html)
    return urllib.parse.unquote(h)


def find_category_slugs(html: str) -> list[str]:
    """从 /产品线 页面提取分类 slug。"""
    h = _normalized(html)
    slugs = set()
    for seg in re.findall(r"/产品线/([^/\"\\?]+)", h):
        slug = seg.strip()
        if slug and not slug.startswith("product"):
            slugs.add(slug)
    return sorted(slugs)


def find_series_links(html: str) -> list[str]:
    """从分类页面提取系列路径（/产品线/<分类>/<系列slug>）。"""
    h = _normalized(html)
    paths = set()
    for m in re.finditer(r"/产品线/[^/\"\\?]+/([a-z0-9][a-z0-9-]*)", h):
        paths.add(m.group(0))
    return sorted(paths)


def _clean_cell(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text.replace("²", "2")).strip()


def parse_table_grid(table_html: str) -> list[list[str]]:
    """把 <table> 解析为二维网格，正确处理 colspan/rowspan。

    每一行都展平成完整的物理列数（colspan 复制填充），保证表头与数据行
    按列号对齐。
    """
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S | re.I)
    occupied: dict[tuple[int, int], str] = {}  # (row, col) -> 文本
    grid: list[list[str]] = []
    for r, tr in enumerate(trs):
        out: list[str] = []
        c = 0
        cells = re.findall(r"(<t[dh][^>]*>)(.*?)</t[dh]>", tr, re.S | re.I)
        for open_tag, inner in cells:
            while (r, c) in occupied:
                out.append(occupied.pop((r, c)))
                c += 1
            cs_m = re.search(r'colspan\s*=\s*"?(\d+)', open_tag, re.I)
            rs_m = re.search(r'rowspan\s*=\s*"?(\d+)', open_tag, re.I)
            colspan = int(cs_m.group(1)) if cs_m else 1
            rowspan = int(rs_m.group(1)) if rs_m else 1
            text = _clean_cell(inner)
            out.extend([text] * colspan)
            for i in range(1, rowspan):
                for j in range(colspan):
                    occupied[(r + i, c + j)] = text
            c += colspan
        while (r, c) in occupied:
            out.append(occupied.pop((r, c)))
            c += 1
        grid.append(out)
    return grid


def parse_series_page(html: str) -> tuple[list[str], list[list[str]], list[str]]:
    """从系列页解析型号表，返回 (表头, 数据行, 每行的 /product/NNN.html 链接)。"""
    tables = re.findall(r"<table[^>]*>.*?</table>", html, re.S | re.I)
    for tb in tables:
        if "产品型号" not in tb:
            continue
        grid = parse_table_grid(tb)
        if len(grid) < 3:
            continue
        hdr_idx = next(i for i, r in enumerate(grid)
                       if any("产品型号" in c for c in r))
        hdr = grid[hdr_idx]
        sub = grid[hdr_idx + 1] if hdr_idx + 1 < len(grid) else []
        if sub and re.match(r"^HC\d{2}", sub[0]):
            sub = []  # 只有一行表头
        # 两行表头合并：子表头（Timer/RTC/USART/…）优先，父表头（通信接口等）兜底
        header = []
        for i, a in enumerate(hdr):
            b = sub[i].strip() if i < len(sub) else ""
            header.append(b if b and b != "-" else a)
        data = [r for r in grid[hdr_idx + (2 if sub else 1):]
                if r and re.match(r"^HC\d{2}", r[0])]
        if not data:
            continue
        links = re.findall(r'href="(/product/\d+\.html)"', tb)
        # 链接按行序对齐：每行首格均有详情链接
        row_links = links[:len(data)] + [""] * max(0, len(data) - len(links))
        return header, data, row_links
    return [], [], []


def cell(header: list[str], row: list[str], *names: str) -> str:
    """按表头名取数据格（模糊匹配第一个命中列）。"""
    for name in names:
        for i, h in enumerate(header):
            if name in h and i < len(row):
                v = row[i].strip()
                if v and v != "-":
                    return v
    return ""


def split_vdd(v: str) -> tuple[str, str]:
    nums = re.findall(r"\d+\.?\d*", v)
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], nums[0]
    return "", ""


def row_to_chip(header: list[str], row: list[str], series: str,
                detail_url: str) -> dict[str, str]:
    part_full = row[0].strip()
    part, package = split_package(part_full)
    if not package:  # 从封装形式列取
        pm = re.search(r"(LQFP\d+|QFN\d+|TSSOP\d+|SOP\d+|VFBGA\d+|BGA\d+|CSP\d+)",
                       cell(header, row, "封装形式"), re.I)
        package = pm.group(1).upper() if pm else ""
    pin_m = re.search(r"(\d+)", package)
    vdd_min, vdd_max = split_vdd(cell(header, row, "电压"))

    other_parts = []
    for name in ("DMA", "QSPI", "SDIO", "AES", "TRNG", "Hash", "工作温度"):
        v = cell(header, row, name)
        if v:
            other_parts.append(f"{name}={v}")
    adc_u = cell(header, row, "ADC12bit（unit）", "ADC")
    adc_ch = cell(header, row, "ADC12bit（ch）")
    if adc_ch and adc_ch != adc_u:
        other_parts.append(f"ADC_ch={adc_ch}")

    return {
        "part_no": part,
        "series": series,
        "freq_mhz": cell(header, row, "主频").replace("MHz", ""),
        "flash_kb": cell(header, row, "Flash"),
        "sram_kb": cell(header, row, "RAM"),
        "package": package,
        "pin_count": pin_m.group(1) if pin_m else "",
        "gpio": cell(header, row, "GPIO"),
        "vdd_min": vdd_min,
        "vdd_max": vdd_max,
        "uart": cell(header, row, "USART", "UART"),
        "spi": cell(header, row, "SPI"),
        "i2c": cell(header, row, "I2C"),
        "i2s": cell(header, row, "I2S"),
        "can": cell(header, row, "CAN"),
        "usb": cell(header, row, "USB"),
        "adc": adc_u,
        "dac": cell(header, row, "DAC"),
        "timer": cell(header, row, "定时及计数", "Timer"),
        "rtc": cell(header, row, "RTC"),
        "wdt": "",
        "touchkey": "",
        "lcd": "",
        "opa": cell(header, row, "OPA", "PGA"),
        "cmp": cell(header, row, "Vcomp", "CMP"),
        "other": "|".join(other_parts),
        "url": urllib.parse.urljoin(BASE, detail_url) if detail_url else "",
        "source": "website",
    }


def discover_series() -> list[tuple[str, str]]:
    """[(系列名, 系列页路径)]，从 /产品线 与各分类页面爬出。"""
    landing = fetch(LANDING)
    cats = find_category_slugs(landing)
    if not cats:
        raise RuntimeError("未能从 /产品线 提取分类 slug，页面结构可能已变化")
    print(f"分类: {', '.join(cats)}", file=sys.stderr)
    series: dict[str, str] = {}
    for cat in cats:
        cat_path = urllib.parse.quote("/产品线/" + cat, safe="/")
        try:
            html = fetch(BASE + cat_path)
        except RuntimeError as e:
            print(f"warn: 分类页失败 {cat}: {e}", file=sys.stderr)
            continue
        for path in find_series_links(html):
            slug = path.rsplit("/", 1)[-1]
            name = re.sub(r"(-\d+)?-copy$", "", slug, flags=re.I)
            name = re.sub(r"-\d+$", "", name).upper()
            if (name.startswith("HC") and "COPY" not in name
                    and "TEST" not in name and name not in series):
                series[name] = path
        time.sleep(0.4)
    return sorted(series.items())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", action="append", default=[],
                    help="输出 CSV 路径（可多次）；默认写入两个数据仓库")
    ap.add_argument("--cache-dir", default="/tmp/hdsc_products_cache",
                    help="页面缓存目录，便于断点重跑")
    ap.add_argument("--list-series", action="store_true",
                    help="只打印发现的系列，不抓型号")
    args = ap.parse_args()

    ws = Path(__file__).resolve().parents[2]
    default_out = [
        ws / "hc32-ddl-data/data/csv/website_chips.csv",
        ws / "hc32-ll-data/data/csv/website_chips.csv",
    ]
    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    series_list = discover_series()
    print(f"发现 {len(series_list)} 个系列", file=sys.stderr)
    if args.list_series:
        for name, path in series_list:
            print(f"  {name}: {path}")
        return

    outs = [Path(p) for p in args.out] or default_out
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for name, path in series_list:
        page_url = BASE + urllib.parse.quote(path, safe="/")
        cache_file = cache / f"{name}.html"
        if cache_file.exists():
            html = cache_file.read_text(encoding="utf-8")
        else:
            try:
                html = fetch(page_url)
            except RuntimeError as e:
                print(f"warn: 系列页失败 {name}: {e}", file=sys.stderr)
                continue
            cache_file.write_text(html, encoding="utf-8")
            time.sleep(0.4)
        header, data, links = parse_series_page(html)
        if not data:
            print(f"warn: {name} 无型号表（{page_url}）", file=sys.stderr)
            continue
        n_new = 0
        for row, link in zip(data, links):
            chip = row_to_chip(header, row, name, link)
            if chip["part_no"] in seen:
                continue
            seen.add(chip["part_no"])
            rows.append(chip)
            n_new += 1
        print(f"{name}: {len(data)} 行（新增 {n_new}）", file=sys.stderr)

    rows.sort(key=lambda r: (r["series"], r["part_no"]))
    for out in outs:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CHIP_FIELDS)
            w.writeheader()
            w.writerows(rows)
    print(f"共 {len(rows)} 个型号 → {', '.join(str(o) for o in outs)}", file=sys.stderr)


if __name__ == "__main__":
    main()
