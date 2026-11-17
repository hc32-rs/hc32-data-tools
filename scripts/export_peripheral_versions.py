#!/usr/bin/env python3
"""Export the reviewed peripheral names, without changing PAC input data.

Assignments live in each data repository's docs/peripheral-version-map.json.
This exporter never assigns numbers. Unknown layouts, changed inputs and collisions
require review of that map. --check is read-only and checks committed outputs too.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import yaml

WS = Path(__file__).resolve().parents[2]
FIELDS = ('series', 'instance', 'kind', 'current_version', 'proposed_version',
          'target_file', 'status', 'notes')
BEGIN = '<!-- peripheral-versions:begin -->'
END = '<!-- peripheral-versions:end -->'
EXCLUDED = frozenset('DCU MAU FMAC HASH SKE USBFS USBHS ETH MDIO NFC SDIOC DVP QSPI '
                     'SMC DMC MPU KEYSCAN OTS VREF HRPWM HALL PLA TMR6 ERMU'.split())
VERSION = re.compile(r'v([1-9]\d*)(?:_([1-9]\d*))?\Z')
TOPICS = {
    'analog': '模拟', 'clock-reset': '时钟、复位与校准',
    'power-wdt': '电源与看门狗', 'gpio-int': 'GPIO、中断与调试',
    'serial': '串口', 'spi-i2c-i2s': 'SPI、I2C 与 I2S',
    'timer-adv': '高级定时器与刹车', 'timer-gen': '通用与低功耗定时器',
    'pca-bt-pcnt-aos': 'PCA、BT、PCNT、AOS 与 EAU', 'dma': 'DMA',
    'memory': '存储控制', 'crypto-comm': '校验、加密、RTC 与通信',
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def version_key(version: str) -> tuple[int, ...]:
    return tuple(map(int, re.findall(r'\d+', version)))


def is_excluded(side: str, series: str, instance: str) -> bool:
    if side != 'll':
        return False
    if series == 'HC32M458' and instance == 'DFM':
        return True
    return any(re.fullmatch(prefix + r'(?:\d+|_COMMON)?', instance)
               for prefix in EXCLUDED)


def target_file(side: str, layout: dict) -> str:
    if layout['status'] == 'excluded':
        return ''
    return f"{layout['kind']}_{side}_{layout['version']}.rs"


def read_svd_instances(repo: Path, series: str) -> set[str]:
    tree = ET.parse(repo / 'svd' / f'{series}.svd')
    # Ignore an optional XML namespace. Only direct peripheral children count.
    for element in tree.iter():
        element.tag = element.tag.rsplit('}', 1)[-1]
    return {p.findtext('name', '').strip()
            for p in tree.findall('./peripherals/peripheral')}


def without_descriptions(value):
    if isinstance(value, dict):
        return {k: without_descriptions(v) for k, v in value.items() if k != 'description'}
    if isinstance(value, list):
        return [without_descriptions(v) for v in value]
    return value


def register_shapes(ir: dict) -> dict:
    """Use register names/coordinates and their actual fields, not instance node IDs."""
    result = {}
    for name, block in ir.items():
        if not name.startswith('block/'):
            continue
        for item in block.get('items', []):
            shape = without_descriptions(item)
            if 'fieldset' in shape:
                shape['fieldset'] = without_descriptions(ir['fieldset/' + item['fieldset']])
            result[item['name']] = shape
    return result


def difference_note(current: dict, base: dict, base_name: str) -> str:
    a, b = register_shapes(current), register_shapes(base)
    added = sorted(a.keys() - b.keys())
    removed = sorted(b.keys() - a.keys())
    changed = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])
    parts = []
    for label, names in [('增加', added), ('缺少', removed), ('定义差异', changed)]:
        if names:
            parts.append(label + ' ' + '/'.join(names[:5]) + (f' 等 {len(names)} 项' if len(names) > 5 else ''))
    if not parts:
        # Do not claim hardware equivalence: enum references and IR node names
        # can still differ, and original reset values may not be in this IR.
        parts.append('寄存器坐标/内联字段未见差异；保留独立 IR 节点/枚举/描述来源，不据此合并')
    return '相对 ' + base_name + '：' + '；'.join(parts)


def load_review(repo: Path, side: str) -> tuple[list[dict], list[dict]]:
    doc = json.loads((repo / 'docs/peripheral-version-map.json').read_text())
    require(doc['schema_version'] == 1 and doc['side'] == side,
            f'{repo}: unexpected proposal schema/side')
    require(doc['stage'] in ('proposal-awaiting-confirmation', 'active'),
            f'{repo}: proposal stage must be explicit')
    layouts = doc['layouts']
    with (repo / 'data/csv/register_versions.csv').open(newline='') as stream:
        source_rows = list(csv.DictReader(stream))
    source_index = {(r['series'], r['instance']): r for r in source_rows}
    require(len(source_index) == len(source_rows), 'duplicate source instance')
    assigned = {}
    targets = set()
    tiers = defaultdict(set)
    versions = defaultdict(set)
    digest_cache = {}
    ir_cache = {}
    svd_cache = {}
    used_sources = set()
    for layout in layouts:
        source = layout['source']
        require(re.fullmatch(r'[a-z][a-z0-9_]*_(?:(?:ddl|ll)_v[1-9]\d*(?:_[1-9]\d*)?|v\d+|common)', source) is not None,
                f'unsafe source identifier: {source}')
        require(re.fullmatch(r'[a-z][a-z0-9_]*', layout['kind']) is not None,
                f'invalid target kind: {layout["kind"]}')
        status, version = layout['status'], layout['version']
        require(status in ('excluded', 'proposed', 'active'), f'{source}: invalid status')
        if status != 'excluded':
            expected_status = 'active' if doc['stage'] == 'active' else 'proposed'
            require(status == expected_status, f'{source}: status disagrees with map stage')
        if status == 'active':
            require(source == f"{layout['kind']}_{side}_{version}", f'{source}: noncanonical filename')
            require(layout['block'] == layout['kind'].upper(), f'{source}: noncanonical block')
        require(bool(layout['rationale']), f'{source}: missing rationale')
        if status == 'excluded':
            require(version == '' and layout['tier'] == 0 and layout['topic'] is None,
                    f'{source}: excluded layout must not have an assignment')
        else:
            match = VERSION.fullmatch(version)
            require(match is not None, f'{source}: invalid version {version}')
            require(int(match[1]) == layout['tier'], f'{source}: tier/version disagree')
            require(layout['topic'] in TOPICS, f'{source}: unknown topic')
            target = target_file(side, layout)
            require(target not in targets, f'duplicate target: {target}')
            targets.add(target)
            tiers[layout['kind']].add(layout['tier'])
            versions[(layout['kind'], layout['tier'])].add(int(match[2] or 0))
        path = repo / 'data/registers' / f'{source}.yaml'
        if source not in digest_cache:
            digest_cache[source] = hashlib.sha256(path.read_bytes()).hexdigest()
        require(digest_cache[source] == layout['source_sha256'],
                f'{path}: source changed; review its assignment before exporting')
        if status in ('proposed', 'active'):
            if source not in ir_cache:
                ir_cache[source] = yaml.load(path.read_text(), Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))
            ir = ir_cache[source]
            if status == 'active':
                require(f"block/{layout['block']}" in ir, f'{source}: missing canonical block')
            registers = sum(len(v.get('items', [])) for k, v in ir.items() if k.startswith('block/'))
            fields = sum(len(v.get('fields', [])) for k, v in ir.items() if k.startswith('fieldset/'))
            require((registers, fields) == (layout['register_count'], layout['field_count']),
                    f'{source}: recorded register/field counts disagree with input')
        require(bool(layout['instances']), f'{source}: empty coverage')
        used_sources.add(source)
        for series, instances in layout['instances'].items():
            require(bool(instances), f'{source}/{series}: empty instance list')
            require(re.fullmatch(r'HC32[A-Za-z0-9]+', series) is not None,
                    f'invalid series: {series}')
            if series not in svd_cache:
                svd_cache[series] = read_svd_instances(repo, series)
            for instance in instances:
                key = (series, instance)
                require(key not in assigned, f'duplicate assignment: {key}')
                require(key in source_index, f'unknown instance: {key}')
                row = source_index[key]
                require(f"{row['kind']}_{row['version']}" == source,
                        f'{key}: source mapping changed')
                if 'block' in layout:
                    require(row['block'] == layout['block'], f'{key}: block reference changed')
                require(instance in svd_cache[series], f'{key}: missing from SVD')
                require(is_excluded(side, series, instance) == (status == 'excluded'),
                        f'{key}: exclusion scope mismatch')
                assigned[key] = layout
    require(set(assigned) == set(source_index),
            f'unassigned instances: {sorted(set(source_index) - set(assigned))}')
    actual_sources = {p.stem for p in (repo / 'data/registers').glob('*.yaml')}
    require(used_sources == actual_sources,
            f'unmapped/orphan register files: {sorted(actual_sources ^ used_sources)}')
    for kind, numbers in tiers.items():
        require(numbers == set(range(1, max(numbers) + 1)), f'{kind}: non-contiguous tiers')
    for key, numbers in versions.items():
        require(numbers == set(range(max(numbers) + 1)), f'{key}: non-contiguous variants')
    base_layouts = {(e['kind'], e['tier']): e for e in layouts
                    if e['status'] in ('proposed', 'active') and '_' not in e['version']}
    for layout in layouts:
        if layout['status'] == 'excluded':
            layout['detail'] = ''
            continue
        base = base_layouts[(layout['kind'], layout['tier'])]
        layout['detail'] = ('本档位对照基准；完整定义见来源 YAML' if layout is base else
                            difference_note(ir_cache[layout['source']], ir_cache[base['source']],
                                            target_file(side, base).removesuffix('.rs')))
    rows = []
    for key, old in source_index.items():
        layout = assigned[key]
        notes = f"source=data/registers/{layout['source']}.yaml; target_kind={layout['kind']}; {layout['rationale']}"
        if layout['status'] in ('proposed', 'active'):
            notes += f"; {layout['detail']}; evidence=docs/explore/{layout['topic']}.md"
        if old['instance'] == 'EMB_CTL1':
            notes += '; SVD 零基址附属节点，非有效硬件实例确认'
        rows.append(dict(zip(FIELDS, (old['series'], old['instance'], old['kind'],
                                     old['version'], layout['version'], target_file(side, layout),
                                     layout['status'], notes))))
    rows.sort(key=lambda r: (r['kind'], version_key(r['proposed_version']),
                             r['target_file'], r['series'], r['instance']))
    return layouts, rows


def csv_text(rows: list[dict]) -> str:
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def coverage(layout: dict) -> str:
    # Group series sharing exactly the same instance set. No implied wildcards.
    by_instances = defaultdict(list)
    for series, instances in layout['instances'].items():
        by_instances[tuple(instances)].append(series.removeprefix('HC32'))
    return '；'.join('/'.join(series) + ': ' + ', '.join(instances)
                    for instances, series in by_instances.items())


def topic_table(side: str, layouts: list[dict], topic: str) -> str:
    groups = defaultdict(list)
    for layout in layouts:
        if layout['topic'] == topic:
            groups[(layout['kind'], layout['tier'])].append(layout)
    lines = ['## 版本与系列对照', '', '以下名称与生成输入保持一致。系列名前缀 `HC32` 省略。', '']
    for (kind, number), entries in sorted(groups.items()):
        entries.sort(key=lambda e: version_key(e['version']))
        lines += [f'### {kind}：v{number}', '', '；'.join(dict.fromkeys(e['rationale'] for e in entries)) + '。', '',
                  '| 目标文件 | 当前来源 | 系列：实例 | 寄存器 / 字段条目 | 变体差异提示 |', '|---|---|---|---:|---|']
        for e in entries:
            lines.append(f"| `{target_file(side, e)}` | [{e['source']}](../../data/registers/{e['source']}.yaml) | "
                         f"{coverage(e)} | {e['register_count']} / {e['field_count']} | {e['detail']} |")
        lines.append('')
    return '\n'.join(lines).rstrip()


def summary_table(side: str, layouts: list[dict], rows: list[dict]) -> str:
    included = [e for e in layouts if e['status'] in ('proposed', 'active')]
    groups = defaultdict(list)
    for e in included:
        groups[e['kind']].append(e)
    excluded_rows = sum(r['status'] == 'excluded' for r in rows)
    lines = ['## 覆盖统计', '',
             f"共 {len(rows)} 个系列/实例映射：{len(rows)-excluded_rows} 个已分配、{excluded_rows} 个排除。",
             f"纳入 {len(groups)} 个目标外设名、{len(included)} 个独立生成布局。", '',
             '| 外设 | 主档位 | 布局数 | 功能依据 |', '|---|---|---:|---|']
    for kind, es in sorted(groups.items()):
        ns = sorted({e['tier'] for e in es})
        tiers = f'v{ns[0]}' if len(ns) == 1 else f'v{ns[0]}–v{ns[-1]}'
        reasons = '；'.join(dict.fromkeys(e['rationale'] for e in es))
        lines.append(f"| [{kind}](explore/{es[0]['topic']}.md) | {tiers} | {len(es)} | {reasons} |")
    excluded = [e for e in layouts if e['status'] == 'excluded']
    if excluded:
        lines += ['', '## 排除项对照', '', '| 原生成布局 | 系列：实例 |', '|---|---|']
        for e in excluded:
            lines.append(f"| [{e['source']}](../data/registers/{e['source']}.yaml) | {coverage(e)} |")
    return '\n'.join(lines)


def replace_block(text: str, generated: str) -> str:
    require(text.count(BEGIN) == 1 and text.count(END) == 1,
            'expected exactly one generated block')
    prefix, rest = text.split(BEGIN)
    _, suffix = rest.split(END)
    return f'{prefix}{BEGIN}\n{generated}\n{END}{suffix}'


def validate_docs(repo: Path, side: str, layouts: list[dict], outputs: dict[Path, str]) -> None:
    targets = {target_file(side, e).removesuffix('.rs') for e in layouts if e['status'] in ('proposed', 'active')}
    docs = [repo / 'README.md', *sorted((repo / 'docs').rglob('*.md'))]
    for path in docs:
        text = outputs.get(path, path.read_text())
        for match in re.finditer(r'\b([a-z][a-z0-9_]*_(?:ddl|ll)_v\d+(?:_\d+)?)\b', text):
            require(match[1] in targets, f'{path}: unknown proposed name {match[1]}')
        prose = re.sub(re.escape(BEGIN) + r'.*?' + re.escape(END), '', text, flags=re.S)
        for name in re.findall(r'\b[a-z][a-z0-9_]*_v[0-9][a-z0-9_]*\b', prose):
            require(name in targets, f'{path}: obsolete or unknown version in prose: {name}')
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
            if re.match(r'[a-z]+://|#', link):
                continue
            target = (path.parent / link.split('#', 1)[0]).resolve()
            require(target.exists() or target in outputs, f'{path}: broken link {link}')


def render(repo: Path, side: str) -> tuple[dict[Path, str], dict]:
    layouts, rows = load_review(repo, side)
    outputs = {repo / 'docs/peripheral-versions.csv': csv_text(rows)}
    path = repo / 'docs/layout-versions.md'
    outputs[path] = replace_block(path.read_text(), summary_table(side, layouts, rows))
    for topic in TOPICS:
        path = repo / f'docs/explore/{topic}.md'
        outputs[path] = replace_block(path.read_text(), topic_table(side, layouts, topic))
    validate_docs(repo, side, layouts, outputs)
    return outputs, {'side': side, 'instances': len(rows), 'layouts': len(layouts),
                     'active': sum(r['status'] == 'active' for r in rows),
                     'proposed': sum(r['status'] == 'proposed' for r in rows),
                     'excluded': sum(r['status'] == 'excluded' for r in rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='validate without writing')
    parser.add_argument('--workspace', type=Path, default=WS)
    args = parser.parse_args()
    # Validate both repositories before writing any output.
    outputs = {}
    stats = []
    for side in ('ddl', 'll'):
        rendered, stat = render(args.workspace / f'hc32-{side}-data', side)
        outputs.update(rendered)
        stats.append(stat)
    stale = [path for path, content in outputs.items()
             if not path.exists() or path.read_bytes() != content.encode('utf-8')]
    if args.check:
        require(not stale, 'stale exports: ' + ', '.join(str(p) for p in stale))
    else:
        for path in stale:
            path.write_bytes(outputs[path].encode('utf-8'))
    print(json.dumps({'mode': 'check' if args.check else 'export', 'repositories': stats,
                      'changed_files': len(stale)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, ET.ParseError) as error:
        print(f'error: {error}', file=sys.stderr)
        sys.exit(1)
