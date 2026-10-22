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


def editable_install(module, root, monkeypatch, *, pip=True, unmet=()):
    """Run install with a fake environment; return the commands and pinned requirements."""
    sibling = root / "appchi-api"
    sibling.mkdir(exist_ok=True)
    (sibling / "pyproject.toml").write_text("[project]\nname='appchi-api'\n")
    environment = root / "env"
    calls, pins, pending = [], [], list(unmet)

    def run(args, cwd, *, capture=False):
        calls.append((args, cwd))
        if args[:3] == ["poetry", "env", "info"]:
            return str(environment)
        if args[1:2] == ["-c"]:
            found, pending[:] = list(pending), []
            return module.json.dumps(found)
        if "-r" in args:
            pins.append(Path(args[args.index("-r") + 1]).read_text())

    monkeypatch.setattr(module, "repository", lambda root, name: root / name)
    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **k: module.subprocess.CompletedProcess(a, 0 if pip else 1)
    )
    module.install(root, {"repositories": {"appchi-api": {}}}, editable=True)
    python = environment / ("Scripts/python.exe" if module.os.name == "nt" else "bin/python")
    return calls, pins, sibling, str(python)


def test_install_validates_lock_then_syncs_before_editable_overrides(workspace, monkeypatch):
    module, root, _ = workspace
    calls, pins, sibling, python = editable_install(module, root, monkeypatch)
    assert calls[0] == (["poetry", "check", "--lock"], module.DELIVERY)
    assert calls[1] == (["poetry", "sync", "--all-extras"], module.DELIVERY)
    assert calls[2] == (["poetry", "env", "info", "--path"], module.DELIVERY)
    assert calls[3] == ([python, "-m", "pip", "install", "--no-deps",
                         "--editable", str(sibling)], module.DELIVERY)
    assert calls[4][0][:2] == [python, "-c"] and calls[4][0][3:] == ["appchi-api"]
    assert calls[5][0][-1] == "ci"
    assert calls[5][1] == root / "appchi-frontend"
    assert pins == []


def test_install_restores_pip_in_an_environment_without_it(workspace, monkeypatch):
    module, root, _ = workspace
    calls, _, _, python = editable_install(module, root, monkeypatch, pip=False)
    assert calls[3] == ([python, "-m", "ensurepip", "--default-pip"], module.DELIVERY)
    assert calls[4][0][1:4] == ["-m", "pip", "install"]


def test_requirement_added_by_local_source_installs_from_its_lock(workspace, monkeypatch):
    module, root, _ = workspace
    (root / "appchi-api").mkdir()
    (root / "appchi-api" / "poetry.lock").write_text(
        '[[package]]\nname = "httptools"\nversion = "0.8.0"\n'
        'files = [{file = "a.whl", hash = "sha256:aa"}, {file = "b.tar.gz", hash = "sha256:bb"}]\n'
    )
    calls, pins, _, python = editable_install(module, root, monkeypatch, unmet=["httptools"])
    assert pins == ["httptools==0.8.0 --hash=sha256:aa --hash=sha256:bb\n"]
    install = next(args for args, _ in calls if "-r" in args)
    assert install[:6] == [python, "-m", "pip", "install", "--no-deps", "--require-hashes"]


def test_requirement_pinned_by_no_lock_stops_install(workspace, monkeypatch):
    module, root, _ = workspace
    with pytest.raises(ValueError, match="pinned by no sibling lock"):
        editable_install(module, root, monkeypatch, unmet=["unlocked-package"])


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
