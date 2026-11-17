#!/usr/bin/env python3
"""Activate approved filenames and block names, retaining hardware definitions."""
import csv
import hashlib
import json
import re
from pathlib import Path

from export_peripheral_versions import WS, load_review, target_file


def main():
    plans = []
    for side in ('ddl', 'll'):
        repo = WS / f'hc32-{side}-data'
        path = repo / 'docs/peripheral-version-map.json'
        doc = json.loads(path.read_text())
        load_review(repo, side)
        with (repo / 'data/csv/register_versions.csv').open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        index = {(r['series'], r['instance']): r for r in rows}
        moves = []
        for entry in doc['layouts']:
            entry.setdefault('legacy_source', entry['source'])
            members = [(s, i) for s, names in entry['instances'].items() for i in names]
            blocks = {index[key]['block'] for key in members}
            if len(blocks) != 1:
                raise ValueError(f"ambiguous block: {entry['source']}")
            old_block = blocks.pop()
            entry['block'] = old_block
            if entry['status'] == 'excluded':
                continue
            stem = target_file(side, entry).removesuffix('.rs')
            old = repo / f"data/registers/{entry['source']}.yaml"
            new = repo / f'data/registers/{stem}.yaml'
            if old != new and new.exists():
                raise ValueError(f'target already exists: {new}')
            content = old.read_text()
            new_block = entry['kind'].upper()
            content = content.replace(f'block/{old_block}:', f'block/{new_block}:', 1)
            content = re.sub(r'(?m)^(\s+(?:block|extends): )' + re.escape(old_block) + r'$',
                             lambda m: m[1] + new_block, content)
            moves.append((old, new, content))
            entry['block'] = new_block
            entry['source_sha256'] = hashlib.sha256(content.encode()).hexdigest()
            entry.update(source=stem, status='active')
            for key in members:
                index[key].update(kind=entry['kind'], version=f"{side}_{entry['version']}", block=new_block)
        doc['stage'] = 'active'
        plans.append((repo, path, doc, rows, moves))
    for repo, path, doc, rows, moves in plans:
        for old, new, content in moves:
            if old != new:
                old.rename(new)
            new.write_text(content)
        with (repo / 'data/csv/register_versions.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=('series', 'instance', 'kind', 'version', 'block'), lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
        print(f'{repo.name}: activated {len(moves)} names and canonical block references')


if __name__ == '__main__':
    main()
