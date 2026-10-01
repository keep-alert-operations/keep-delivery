"""Validate tracked workspace Markdown links, fences and source ownership targets."""
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote

WORKSPACE = Path(__file__).resolve().parents[2]
IGNORED = {'.git', '.venv', 'node_modules', '.next', 'dist', 'legacy', '.local'}


def main():
    errors, documents = [], 0
    for repository in WORKSPACE.glob('appchi-*'):
        for directory, children, files in os.walk(repository):
            children[:] = [name for name in children if name not in IGNORED]
            for name in files:
                if not name.endswith('.md'):
                    continue
                path = Path(directory) / name
                text = path.read_text(encoding='utf-8-sig')
                documents += 1
                fences = re.findall(r'^\s*(```+|~~~+)', text, re.M)
                if len(fences) % 2:
                    errors.append([str(path.relative_to(WORKSPACE)), 'Unbalanced fences'])
                prose = re.sub(r'^\s*(```|~~~).*?^\s*\1\s*$', '', text, flags=re.M | re.S)
                for match in re.finditer(r'\]\(([^\n)]+)\)', prose):
                    link = match.group(1).strip().strip('<>')
                    if re.match(r'https://github.com/appchi-alert-operations/appchi-[^/]+/(?:blob|tree)/main/', link):
                        parts = link.split('/', 7)
                        target = WORKSPACE / parts[4] / unquote(parts[7].split('#')[0])
                    elif re.match(r'[a-z]+:', link) or link.startswith('#'):
                        continue
                    else:
                        target = path.parent / unquote(link.split('#')[0])
                    if not target.exists():
                        errors.append([str(path.relative_to(WORKSPACE)), link])
    inventory = json.loads((WORKSPACE / 'appchi-delivery/workspace/source-inventory.json').read_text())
    for source, item in inventory['files'].items():
        if not (WORKSPACE / item['repository'] / item['path']).exists():
            errors.append([source, 'Missing assigned destination'])
    register = (WORKSPACE / 'appchi-design/decisions/design-register.md').read_text(encoding='utf-8')
    ids = {int(value) for value in re.findall(r'^(?:\|\s*|##\s*)REV-(\d+)', register, re.M)}
    if ids != set(range(1, 38)):
        errors.append(['design-register.md', 'Expected REV-01 through REV-37'])
    print(json.dumps({'documents': documents, 'source_entries': len(inventory['files']),
                      'review_ids': 'REV-01..REV-37', 'errors': errors}, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
