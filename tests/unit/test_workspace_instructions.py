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


def load_requirements():
    script = Path(__file__).resolve().parents[2] / "scripts/workspace_requirements.py"
    spec = importlib.util.spec_from_file_location("workspace_requirements", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def editable_install(module, root, monkeypatch, *, pip=True, requires=None):
    """Run install with a fake environment; return the commands and pinned requirements.

    The fake environment resolves through the real workspace_requirements.resolve
    and records each pinned package as installed, so the install loop converges.
    """
    resolver = load_requirements()
    sibling = root / "appchi-api"
    sibling.mkdir(exist_ok=True)
    (sibling / "pyproject.toml").write_text("[project]\nname='appchi-api'\n")
    environment = root / "env"
    requires = requires or {}
    installed = set()
    calls, pins = [], []

    def run(args, cwd, *, capture=False):
        calls.append((args, cwd))
        if args[:3] == ["poetry", "env", "info"]:
            return str(environment)
        if args[1:2] == [str(module.REQUIREMENTS)]:
            request = module.json.loads(args[2])
            locks = {path: module.tomllib.loads(Path(path).read_text()).get("package", [])
                     for path in request["locks"]}
            result = resolver.resolve(
                request["distributions"], locks, lambda name: requires.get(name, ()), installed
            )
            installed.update(resolver.key(pin["name"]) for pin in result["pins"])
            return module.json.dumps(result)
        if "-r" in args:
            pins.append(Path(args[args.index("-r") + 1]).read_text())

    monkeypatch.setattr(module, "repository", lambda root, name: root / name)
    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *a, **k: module.subprocess.CompletedProcess(a, 0 if pip else 1),
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
    assert calls[4][0][:2] == [python, str(module.REQUIREMENTS)]
    assert module.json.loads(calls[4][0][2])["distributions"] == {"appchi-api": None}
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
        '[[package]]\nname = "httptools"\nversion = "0.8.0"\ngroups = ["main"]\n'
        'files = [{file = "a.whl", hash = "sha256:aa"}, {file = "b.tar.gz", hash = "sha256:bb"}]\n'
    )
    calls, pins, _, python = editable_install(
        module, root, monkeypatch, requires={"appchi-api": ["httptools>=0.8,<1"]}
    )
    assert pins == ["httptools==0.8.0 --hash=sha256:aa --hash=sha256:bb\n"]
    install = next(args for args, _ in calls if "-r" in args)
    assert install[:6] == [python, "-m", "pip", "install", "--no-deps", "--require-hashes"]


def test_requirement_pinned_by_no_lock_stops_install(workspace, monkeypatch):
    module, root, _ = workspace
    with pytest.raises(ValueError, match="No sibling lock pins"):
        editable_install(module, root, monkeypatch, requires={"appchi-api": ["unlocked-package"]})


def test_declaring_source_lock_wins_over_conflicting_sibling_lock():
    resolver = load_requirements()
    locks = {
        # Fallback order puts the other sibling first; its pin must not win.
        "other.lock": [{"name": "httptools", "version": "0.6.0", "groups": ["main"]}],
        "api.lock": [{"name": "httptools", "version": "0.8.0", "groups": ["main"]}],
    }
    result = resolver.resolve(
        {"appchi-api": "api.lock"}, locks, lambda name: ["httptools>=0.6,<1"], set()
    )
    assert [(pin["version"], pin["lock"]) for pin in result["pins"]] == [("0.8.0", "api.lock")]
    assert result["unresolved"] == []


def test_lock_entries_outside_main_group_platform_or_specifier_are_rejected():
    resolver = load_requirements()
    never = 'sys_platform == "never-a-platform"'
    locks = {"api.lock": [
        {"name": "dev-only", "version": "1.0", "groups": ["dev"]},
        {"name": "other-platform", "version": "1.0", "groups": ["main"], "markers": never},
        {"name": "group-marker", "version": "1.0", "groups": ["main", "dev"],
         "markers": {"main": never}},
        {"name": "too-old", "version": "0.5", "groups": ["main"]},
    ]}
    requires = ["dev-only", "other-platform", "group-marker", "too-old>=0.8"]
    result = resolver.resolve({"appchi-api": "api.lock"}, locks, lambda name: requires, set())
    assert result["pins"] == []
    assert len(result["unresolved"]) == 4


def test_second_declarer_outside_the_chosen_version_is_unresolved():
    resolver = load_requirements()
    locks = {"api.lock": [{"name": "httptools", "version": "0.8.0", "groups": ["main"]}]}
    requires = {"appchi-api": ["httptools>=0.8"], "appchi-ingestion": ["httptools<0.7"]}
    result = resolver.resolve(
        {"appchi-api": "api.lock", "appchi-ingestion": "api.lock"}, locks,
        lambda name: requires[name], set(),
    )
    assert [pin["version"] for pin in result["pins"]] == ["0.8.0"]
    assert result["unresolved"] == ["httptools<0.7 (required by appchi-ingestion; 0.8.0 chosen)"]


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
