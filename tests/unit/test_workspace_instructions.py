"""Bootstrap installs shared agent guidance without losing local instruction edits."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts/workspace.py"
    spec = importlib.util.spec_from_file_location("workspace_setup", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    delivery = tmp_path / "appchi-delivery"
    canonical = delivery / "workspace"
    canonical.mkdir(parents=True)
    (canonical / "AGENTS.md").write_text("Shared guidance\n", encoding="utf-8")
    (canonical / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    monkeypatch.setattr(module, "DELIVERY", delivery)
    return module, tmp_path, {"repositories": {}}


def test_installs_both_files_and_repeated_bootstrap_needs_no_backup(workspace):
    module, root, manifest = workspace
    module.bootstrap(root, manifest)
    module.bootstrap(root, manifest)
    for name in ("AGENTS.md", "CLAUDE.md"):
        assert (root / name).read_bytes() == (module.DELIVERY / "workspace" / name).read_bytes()
        assert not (root / f"{name}.before-bootstrap").exists()


def test_saves_exact_local_instruction_bytes(workspace):
    module, root, manifest = workspace
    original = b"Manual instructions\r\nPreserve me.\r\n"
    for name in ("AGENTS.md", "CLAUDE.md"):
        (root / name).write_bytes(original)
    module.bootstrap(root, manifest)
    for name in ("AGENTS.md", "CLAUDE.md"):
        assert (root / f"{name}.before-bootstrap").read_bytes() == original
        assert (root / name).read_bytes() == (module.DELIVERY / "workspace" / name).read_bytes()


@pytest.mark.parametrize("conflict", ["AGENTS.md", "CLAUDE.md"])
def test_existing_backup_conflict_preserves_both_root_files(workspace, conflict):
    module, root, manifest = workspace
    original = b"Unreconciled manual guidance\r\n"
    for name in ("AGENTS.md", "CLAUDE.md"):
        (root / name).write_bytes(original)
    backup = root / f"{conflict}.before-bootstrap"
    backup.write_bytes(b"Earlier backup")
    with pytest.raises(ValueError, match=f"Root {conflict} differs"):
        module.bootstrap(root, manifest)
    assert backup.read_bytes() == b"Earlier backup"
    for name in ("AGENTS.md", "CLAUDE.md"):
        assert (root / name).read_bytes() == original


def test_install_validates_lock_then_syncs_before_editable_overrides(workspace, monkeypatch):
    module, root, _ = workspace
    sibling = root / "appchi-api"
    sibling.mkdir()
    (sibling / "pyproject.toml").write_text("[project]\nname='appchi-api'\n")
    calls = []
    monkeypatch.setattr(module, "repository", lambda root, name: root / name)
    monkeypatch.setattr(module, "run", lambda args, cwd: calls.append((args, cwd)))
    module.install(root, {"repositories": {"appchi-api": {}}}, editable=True)
    assert calls[0] == (["poetry", "check", "--lock"], module.DELIVERY)
    assert calls[1] == (["poetry", "sync", "--all-extras"], module.DELIVERY)
    executable = module.DELIVERY / (
        ".venv/Scripts/python.exe" if module.os.name == "nt" else ".venv/bin/python"
    )
    assert calls[2] == ([str(executable), "-m", "pip", "install", "--no-deps",
                         "--editable", str(sibling)], module.DELIVERY)
    assert calls[3][0][-1] == "ci"
    assert calls[3][1] == root / "appchi-frontend"


def test_invalid_lock_stops_before_sync_or_editable_changes(workspace, monkeypatch):
    module, root, manifest = workspace
    calls = []

    def invalid(args, cwd):
        calls.append(args)
        raise ValueError("lock does not match project")

    monkeypatch.setattr(module, "run", invalid)
    with pytest.raises(ValueError, match="lock"):
        module.install(root, manifest, editable=True)
    assert calls == [["poetry", "check", "--lock"]]
