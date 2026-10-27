"""Validate tracked workspace Markdown links, fences and source ownership targets."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote

WORKSPACE = Path(__file__).resolve().parents[2]
IGNORED = {'.git', '.venv', 'node_modules', '.next', 'dist', 'legacy', '.local'}


def entrypoint_errors(workspace):
    """Installed entrypoints must reflect their canonical delivery sources."""
    errors = []
    for name in ('AGENTS.md', 'CLAUDE.md'):
        canonical = workspace / 'appchi-delivery' / 'workspace' / name
        installed = workspace / name
        if not canonical.is_file() or not installed.is_file():
            errors.append([name, 'Missing canonical or installed workspace entrypoint; run bootstrap'])
        # Git may check the canonical copy out with CRLF; line endings alone are not drift.
        elif (canonical.read_bytes().replace(b'\r\n', b'\n')
              != installed.read_bytes().replace(b'\r\n', b'\n')):
            errors.append([name, 'Installed entrypoint differs from delivery canonical copy; reconcile before bootstrap'])
    claude = workspace / 'CLAUDE.md'
    if claude.is_file() and '@AGENTS.md' not in claude.read_text(encoding='utf-8-sig').splitlines():
        errors.append(['CLAUDE.md', 'Expected a standalone @AGENTS.md import of shared guidance'])
    return errors


def main():
    errors, documents = entrypoint_errors(WORKSPACE), 0
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
    if ids != set(range(1, 46)):
        errors.append(['design-register.md', 'Expected REV-01 through REV-45'])
    print(json.dumps({'documents': documents, 'source_entries': len(inventory['files']),
                      'review_ids': 'REV-01..REV-45', 'errors': errors}, indent=2))
    if errors:
        raise SystemExit(1)
    subprocess.run(
        [sys.executable, str(WORKSPACE / 'appchi-delivery/qualification/check_context.py')],
        cwd=WORKSPACE / 'appchi-delivery', check=True,
    )


if __name__ == '__main__':
    main()
