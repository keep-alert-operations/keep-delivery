"""Workspace entrypoint drift must fail before documentation qualification."""
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / 'qualification' / 'verify_documents.py'
SPEC = importlib.util.spec_from_file_location('context_document_check', SCRIPT)
DOCUMENTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCUMENTS)


def install(workspace):
    canonical = workspace / 'appchi-delivery' / 'workspace'
    canonical.mkdir(parents=True)
    for name, text in (('AGENTS.md', '# Guidance\n'), ('CLAUDE.md', '@AGENTS.md\n')):
        (canonical / name).write_text(text, encoding='utf-8')
        (workspace / name).write_text(text, encoding='utf-8')
    return canonical


def test_entrypoints_match(tmp_path):
    install(tmp_path)
    assert DOCUMENTS.entrypoint_errors(tmp_path) == []


def test_entrypoint_drift_and_missing_import_are_errors(tmp_path):
    canonical = install(tmp_path)
    (tmp_path / 'AGENTS.md').write_text('Local edit', encoding='utf-8')
    (tmp_path / 'CLAUDE.md').write_text('No import', encoding='utf-8')
    (canonical / 'CLAUDE.md').write_text('No import', encoding='utf-8')
    errors = DOCUMENTS.entrypoint_errors(tmp_path)
    assert [name for name, _ in errors] == ['AGENTS.md', 'CLAUDE.md']


def test_missing_entrypoint_is_reported(tmp_path):
    install(tmp_path)
    (tmp_path / 'AGENTS.md').unlink()
    assert DOCUMENTS.entrypoint_errors(tmp_path)[0][0] == 'AGENTS.md'
