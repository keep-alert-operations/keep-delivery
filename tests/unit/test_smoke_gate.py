"""The smoke finish gate (scripts/smoke_gate.py).

Requirements: smoke-runner plan (.plans/2026-11-18-smoke-runner.md) A10 and A13;
user decisions of 2026-11-18 (a missing run always sends the agent back, a failed
or blocked run sends it back once) and 2026-11-19 (Q1: a stop that waits for the
user's answer is allowed once per code state). The fixtures copy the shapes of
Claude Code's session transcript and ``subagents/agent-*.meta.json`` files.
"""

from __future__ import annotations

import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
gate = importlib.import_module("smoke_gate")
state = importlib.import_module("smoke_state")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def make_repo(root: Path) -> None:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "smoke@example.invalid")
    git(root, "config", "user.name", "Smoke test")
    git(root, "config", "core.autocrlf", "false")
    (root / "src").mkdir()
    (root / "src/app.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "start")


class Session:
    """A workspace with keep-api and a Claude Code session directory."""

    def __init__(self, tmp_path: Path):
        self.ws = tmp_path / "ws"
        (self.ws / "keep-delivery").mkdir(parents=True)
        self.api = self.ws / "keep-api"
        make_repo(self.api)
        self.id = str(uuid4())
        projects = tmp_path / "projects"
        projects.mkdir()
        self.transcript = projects / f"{self.id}.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        self.subagents = projects / self.id / "subagents"
        self.reports = self.ws / "keep-delivery/.local/smoke/reports"

    def payload(self, **extra) -> dict:
        return {
            "session_id": self.id,
            "transcript_path": str(self.transcript),
            "cwd": str(self.ws),
            **extra,
        }

    def start(self) -> None:
        assert gate.on_session_start(self.payload(), self.ws) is None

    def stop(self, **extra):
        return gate.on_stop(self.payload(**extra), self.ws)

    def edit(self, text: str = "value = 2\n", path: Path | None = None) -> None:
        (path or self.api / "src/app.py").write_text(text, encoding="utf-8")

    def checkout(self, path: Path | None = None):
        return state.describe(path or self.api)

    def report(self, result="passed", version=None, checkouts=None) -> str:
        run_id = str(uuid4())
        self.reports.mkdir(parents=True, exist_ok=True)
        checkouts = checkouts or [self.checkout()]
        body = {
            "run_id": run_id,
            "result": result,
            "algorithm_version": version or state.ALGORITHM_VERSION,
            "finished_at": "2026-11-19T09:00:00+00:00",
            "scenarios": {"S1": {"result": result, "reason": "" if result == "passed" else "x"}},
            "stack_includes_change": {"value": "yes", "reason": "test"},
            "checkouts": [{"key": c.key, "fingerprint": state.fingerprint(c)} for c in checkouts],
        }
        (self.reports / f"{run_id}.json").write_text(json.dumps(body), encoding="utf-8")
        return run_id

    def subagent(
        self,
        run_id: str | None,
        *,
        model="claude-haiku-5-5",
        finished=True,
        agent_type="smoke-runner",
        in_tool_result=True,
        transcript=True,
    ) -> None:
        self.subagents.mkdir(parents=True, exist_ok=True)
        name = f"agent-a{uuid4().hex[:16]}"
        meta = {"agentType": agent_type, "isBuiltIn": False, "requestShape": "background"}
        if finished:
            meta["finished"] = True
        (self.subagents / f"{name}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        if not transcript:
            return
        output = f"run_id: {run_id}\nresult: passed" if run_id else ""
        lines = [
            {"type": "user", "message": {"role": "user", "content": "Run: smoke"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "tool_use", "name": "PowerShell", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "content": output if in_tool_result else "result: passed",
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "text", "text": f"run_id: {run_id}"}],
                },
            },
        ]
        (self.subagents / f"{name}.jsonl").write_text(
            "\n".join(json.dumps(line) for line in lines), encoding="utf-8"
        )

    def say(self, text: str = "", tool: dict | None = None) -> None:
        """Appends one user turn and the assistant's last message to the main transcript."""
        content = [{"type": "text", "text": text}] if text else []
        if tool:
            content.append({"type": "tool_use", **tool})
        lines = [
            {"type": "user", "message": {"role": "user", "content": "please do it"}},
            {"type": "assistant", "message": {"role": "assistant", "content": content}},
        ]
        with open(self.transcript, "a", encoding="utf-8") as out:
            out.write("".join(json.dumps(line) + "\n" for line in lines))


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    value = Session(tmp_path)
    value.start()
    return value


def is_block(result) -> bool:
    return isinstance(result, dict) and result.get("decision") == "block"


# ------------------------------------------------------------- no change


def test_no_app_change_allows(session):
    assert session.stop() is None


def test_a_docs_only_change_allows(session):
    (session.api / "README.md").write_text("notes\n", encoding="utf-8")
    (session.api / "docs").mkdir()
    (session.api / "docs/x.py").write_text("x = 1\n", encoding="utf-8")
    assert session.stop() is None


def test_a_missing_state_file_baselines_and_allows(session):
    gate.state_path(session.ws, session.id).unlink()
    session.edit()
    result = session.stop()
    assert not is_block(result) and "started late" in result["systemMessage"]
    assert session.stop() is None  # the edit predates the new baseline


def test_reverting_the_change_allows(session):
    session.edit()
    session.edit("value = 1\n")
    assert session.stop() is None


# ------------------------------------------------------------- missing run


def test_an_app_change_without_a_run_blocks_with_the_subagent_and_command(session):
    session.edit()
    result = session.stop()
    assert is_block(result)
    reason = result["reason"]
    assert 'subagent_type "smoke-runner"' in reason
    assert f'scripts{os.sep}smoke.py" run --workspace "{session.ws}"' in reason
    assert str(session.api) in reason


def test_a_missing_run_blocks_every_stop_up_to_the_safety_limit(session):
    session.edit()
    for _ in range(gate.MAX_MISSING_BLOCKS):
        assert is_block(session.stop(stop_hook_active=True))
    result = session.stop(stop_hook_active=True)
    assert not is_block(result) and "reminders" in result["systemMessage"]


def test_an_edit_in_a_worktree_named_by_a_tool_counts(session):
    """A10: a root session editing .wt/<task>/<repo> is gated too."""
    worktree = session.ws / ".wt/task/keep-api"
    git(session.api, "worktree", "add", "-q", str(worktree), "-b", "task")
    target = worktree / "src/app.py"
    pre = session.payload(tool_name="Edit", tool_input={"file_path": str(target)})
    assert gate.on_pre_tool_use(pre, session.ws) is None
    session.edit(path=target)
    result = session.stop()
    assert is_block(result) and str(worktree) in result["reason"]


def test_a_shell_command_naming_a_worktree_baselines_it(session):
    worktree = session.ws / ".wt/task/keep-api"
    git(session.api, "worktree", "add", "-q", str(worktree), "-b", "task")
    bash_path = "/" + worktree.as_posix()[0].lower() + worktree.as_posix()[2:]
    for command in (f'cd "{bash_path}" && sed -i s/1/2/ src/app.py', f"git -C {worktree} status"):
        gate.state_path(session.ws, session.id).unlink()
        session.start()
        pre = session.payload(tool_name="Bash", tool_input={"command": command})
        gate.on_pre_tool_use(pre, session.ws)
        saved = json.loads(gate.state_path(session.ws, session.id).read_text())
        assert state.path_key(worktree) in saved["baselines"]


def test_an_untouched_worktree_of_another_session_is_ignored(session):
    """A10: only checkouts this session named are watched besides the main ones."""
    worktree = session.ws / ".wt/other/keep-api"
    git(session.api, "worktree", "add", "-q", str(worktree), "-b", "other")
    session.edit(path=worktree / "src/app.py")
    assert session.stop() is None


def test_paths_outside_the_workspace_are_never_watched(session, tmp_path):
    outside = tmp_path / "elsewhere/keep-api"
    make_repo(outside)
    pre = session.payload(tool_name="Write", tool_input={"file_path": str(outside / "src/a.py")})
    gate.on_pre_tool_use(pre, session.ws)
    saved = json.loads(gate.state_path(session.ws, session.id).read_text())
    assert state.path_key(outside) not in saved["baselines"]


# ------------------------------------------------------------- running


def test_a_running_smoke_runner_allows_without_counting(session):
    session.edit()
    session.subagent(None, finished=False)
    assert session.stop() is None
    saved = json.loads(gate.state_path(session.ws, session.id).read_text())
    assert saved["missing_blocks"] == {}


def test_a_live_smoke_lock_allows(session):
    session.edit()
    lock = session.ws / "keep-delivery/.local/smoke/lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "started_at": __import__("time").time()}))
    assert session.stop() is None


# ------------------------------------------------------------- covering run


def test_a_passed_run_by_a_haiku_smoke_runner_allows(session):
    session.edit()
    session.subagent(session.report())
    assert session.stop() is None


def test_a_run_by_another_model_does_not_count(session):
    session.edit()
    session.subagent(session.report(), model="claude-opus-5-5")
    result = session.stop()
    assert is_block(result) and "other than Haiku" in result["reason"]


def test_a_run_id_the_subagent_never_saw_from_the_command_does_not_count(session):
    session.edit()
    session.subagent(session.report(), in_tool_result=False)
    assert is_block(session.stop())


def test_a_report_from_another_agent_type_does_not_count(session):
    session.edit()
    session.subagent(session.report(), agent_type="general-purpose")
    assert is_block(session.stop())


def test_an_edit_after_the_run_needs_another_run(session):
    session.edit()
    session.subagent(session.report())
    session.edit("value = 3\n")
    result = session.stop()
    assert is_block(result) and "covered older code" in result["reason"]


def test_a_commit_after_the_run_stays_covered(session):
    session.edit()
    session.subagent(session.report())
    git(session.api, "commit", "-q", "-am", "tested change")
    assert session.stop() is None


def test_a_report_from_older_smoke_tooling_needs_another_run(session):
    session.edit()
    session.subagent(session.report(version=state.ALGORITHM_VERSION + 1))
    result = session.stop()
    assert is_block(result) and "smoke tooling changed" in result["reason"]


@pytest.mark.parametrize("result", ["failed", "partial", "blocked"])
def test_a_covering_run_that_did_not_pass_blocks_once(session, result):
    session.edit()
    run_id = session.report(result=result)
    session.subagent(run_id)
    first = session.stop()
    assert is_block(first)
    assert f"Smoke run {run_id} {result}" in first["reason"]
    assert session.stop() is None


def test_unreadable_subagent_files_block_once_with_the_reason(session):
    session.edit()
    session.subagent(session.report(), transcript=False)
    first = session.stop()
    assert is_block(first) and "can't read" in first["reason"]
    assert session.stop() is None


# ------------------------------------------------------------- Q1: waiting for the user


def test_a_question_to_the_user_is_allowed_once_per_code_state(session):
    session.edit()
    session.say("Should I also update the guide?")
    assert session.stop() is None
    assert is_block(session.stop())
    session.edit("value = 4\n")
    assert session.stop() is None


def test_a_blocked_wrap_up_counts_as_waiting(session):
    session.edit()
    session.say("Recap.", tool={"name": "mcp__ccd_turn__wrap_up", "input": {"blocked": True}})
    assert session.stop() is None


def test_a_plain_finish_is_not_waiting(session):
    session.edit()
    session.say("Done. All tests pass.")
    assert is_block(session.stop())


# ------------------------------------------------------------- entry point and A13


def test_bad_input_never_fails_a_hook(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert gate.main(["smoke_gate.py", "stop"]) == 0
    assert capsys.readouterr().out == ""


def test_a_workspace_without_keep_delivery_is_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    payload = {"session_id": "s", "cwd": str(tmp_path)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert gate.main(["smoke_gate.py", "stop"]) == 0
