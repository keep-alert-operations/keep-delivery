"""Bootstrap installs the smoke-runner agent and the smoke finish gate safely.

Requirements: smoke-runner plan (.plans/2026-11-18-smoke-runner.md) A12 and A13;
user decision 2026-11-19 (Q3: allow the exact smoke command only).
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

DELIVERY = Path(__file__).resolve().parents[2]


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location(
        "workspace_setup", DELIVERY / "scripts/workspace.py"
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def root(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)
    (workspace / ".claude/settings.json").write_text(
        json.dumps({"enabledPlugins": {"example@market": True}}), encoding="utf-8"
    )
    (workspace / ".claude/settings.local.json").write_text(
        json.dumps(
            {
                "permissions": {"allow": ["PowerShell(git status)"]},
                "hooks": {"WorktreeCreate": [{"hooks": [{"type": "command", "command": "x"}]}]},
            }
        ),
        encoding="utf-8",
    )
    return workspace


def install(module, root):
    return module.install_agent_setup(root, DELIVERY, python="C:/Python/python.exe")


def snapshot(root: Path) -> dict:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_installs_the_agent_hooks_and_one_permission_rule(module, root):
    changes = install(module, root)
    assert len(changes) == 3
    agent = root / ".claude/agents/smoke-runner.md"
    assert (
        agent.read_bytes() == (DELIVERY / "workspace/.claude/agents/smoke-runner.md").read_bytes()
    )
    settings = json.loads((root / ".claude/settings.json").read_text())
    assert settings["enabledPlugins"] == {"example@market": True}
    for event in ("SessionStart", "PreToolUse", "Stop"):
        commands = [h["command"] for e in settings["hooks"][event] for h in e["hooks"]]
        assert len(commands) == 1 and "smoke_gate.py" in commands[0]
        assert '"C:/Python/python.exe" -B' in commands[0]
    local = json.loads((root / ".claude/settings.local.json").read_text())
    assert local["hooks"]["WorktreeCreate"][0]["hooks"][0]["command"] == "x"
    assert local["permissions"]["allow"] == [
        "PowerShell(git status)",
        f"PowerShell({module.smoke_command(root)})",
    ]
    assert (root / ".claude/settings.json.before-bootstrap").exists()
    assert (root / ".claude/settings.local.json.before-bootstrap").exists()


def test_a_second_install_changes_nothing(module, root):
    install(module, root)
    before = snapshot(root)
    assert install(module, root) == []
    assert snapshot(root) == before


def test_an_earlier_smoke_gate_entry_is_replaced_not_duplicated(module, root):
    settings = {
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": "python old/smoke_gate.py stop"}]},
                {"hooks": [{"type": "command", "command": "other stop hook"}]},
            ]
        }
    }
    (root / ".claude/settings.json").write_text(json.dumps(settings), encoding="utf-8")
    install(module, root)
    stop = json.loads((root / ".claude/settings.json").read_text())["hooks"]["Stop"]
    commands = [h["command"] for e in stop for h in e["hooks"]]
    assert commands[0] == "other stop hook"
    assert len(commands) == 2 and "old/smoke_gate.py" not in commands[1]


def test_a_locally_edited_agent_is_backed_up_and_an_updated_one_is_not(module, root, tmp_path):
    agent = root / ".claude/agents/smoke-runner.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("my own runner\n", encoding="utf-8")
    install(module, root)
    assert (
        root / ".claude/agents/smoke-runner.md.before-bootstrap"
    ).read_text() == "my own runner\n"
    # A newer canonical agent replaces the installed one without another backup.
    delivery = tmp_path / "delivery"
    shutil.copytree(DELIVERY / "workspace/.claude", delivery / "workspace/.claude")
    (delivery / "workspace/.claude/agents/smoke-runner.md").write_text("v2\n", encoding="utf-8")
    module.install_agent_setup(root, delivery, python="C:/Python/python.exe")
    assert agent.read_text() == "v2\n"
    assert (
        root / ".claude/agents/smoke-runner.md.before-bootstrap"
    ).read_text() == "my own runner\n"
    # Local edits again, with a backup already there: stop and ask to reconcile.
    agent.write_text("edited again\n", encoding="utf-8")
    with pytest.raises(ValueError, match="reconcile"):
        install(module, root)
    assert agent.read_text() == "edited again\n"


def test_without_canonical_agent_files_nothing_is_installed(module, root, tmp_path):
    assert module.install_agent_setup(root, tmp_path / "empty") == []


def test_the_permission_rule_matches_the_gates_command(module, root):
    sys.path.insert(0, str(DELIVERY / "scripts"))
    gate = importlib.import_module("smoke_gate")
    os.environ.pop("CLAUDE_PROJECT_DIR", None)
    (root / "keep-delivery").mkdir()
    assert gate.smoke_command(root) == module.smoke_command(root)


def git_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if not git:
        return None
    # git.exe is in <Git>\cmd from PowerShell and <Git>\mingw64\bin from Git Bash.
    for folder in list(Path(git).resolve().parents)[:3]:
        candidate = folder / "bin/bash.exe"
        if candidate.exists():
            return str(candidate)
    return None


@pytest.mark.skipif(git_bash() is None, reason="Git Bash runs Claude Code hooks on Windows")
@pytest.mark.parametrize("event", ["SessionStart", "PreToolUse", "Stop"])
def test_a_missing_gate_script_never_blocks(tmp_path, event):
    """A13: each hook command exits 0 when keep-delivery/scripts/smoke_gate.py is absent."""
    template = (DELIVERY / "workspace/.claude/settings.hooks.json").read_text(encoding="utf-8")
    hooks = json.loads(template.replace("{python}", Path(sys.executable).as_posix()))["hooks"]
    command = hooks[event][0]["hooks"][0]["command"]
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
    result = subprocess.run(
        [git_bash(), "-c", command], input="{}", capture_output=True, text=True, env=env, timeout=60
    )
    assert (result.returncode, result.stdout) == (0, "")
    # Positive control: with the script present the same command finds it and runs
    # the gate, which records this session's state.
    scripts = tmp_path / "keep-delivery/scripts"
    scripts.mkdir(parents=True)
    for name in ("smoke_gate.py", "smoke_state.py"):
        shutil.copyfile(DELIVERY / "scripts" / name, scripts / name)
    payload = {
        "session_id": f"probe-{event}",
        "cwd": str(tmp_path),
        "transcript_path": str(tmp_path / "transcript.jsonl"),
        "tool_name": "Write",
        "tool_input": {"file_path": str(scripts / "probe.py")},
    }
    result = subprocess.run(
        [git_bash(), "-c", command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / f"keep-delivery/.local/smoke/sessions/probe-{event}.json").is_file()
