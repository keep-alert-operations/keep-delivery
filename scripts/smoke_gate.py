"""Finish gate for Keep sessions: app changes need a smoke run by the smoke-runner subagent.

Claude Code runs this from the workspace's ``.claude/settings.json`` hooks:

    smoke_gate.py session-start | pre-tool-use | stop      (hook payload JSON on stdin)

* ``session-start`` records a content fingerprint of the workspace's app checkouts.
* ``pre-tool-use`` records one for any other app checkout (such as a ``.wt/<task>/<repo>``
  worktree) the first time a tool call names it, before the call runs.
* ``stop`` compares every recorded checkout with its baseline. When app content
  changed, the session may finish only after a smoke run that covers the current
  content, made in this session by the ``smoke-runner`` subagent on Haiku:

  - no covering run: block, naming the subagent and the exact command. The only
    exception is a stop whose last message waits for the user's answer, allowed
    once per code state. After five blocks on one state the gate gives up and lets
    the stop through with a notice, so a broken setup can't loop forever;
  - a covering run that failed, was partial or was blocked: block once with its
    results, then allow;
  - a covering run that passed: allow;
  - a smoke-runner still running, or a live smoke lock: allow without counting.

The fingerprint is ``smoke_state.fingerprint``, the same one the smoke report
records, so a commit of tested content stays covered. Session state lives in
``<workspace>/keep-delivery/.local/smoke/sessions/<session id>.json``. The gate
never fails a hook: on any internal error it lets the call through.

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_state  # noqa: E402

STATE_VERSION = 1
MAX_MISSING_BLOCKS = 5
RUNNING_FRESH_SECONDS = 15 * 60
RUN_ID = re.compile(r"run_id: ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'`|;&<>()]*")
GIT_BASH_PATH = re.compile(r"(?<![\w:])/([a-zA-Z])/[^\s\"'`|;&<>()]*")
WAITING = re.compile(
    r"waiting for (your|the user)|your (answer|approval|go-ahead|decision|input|choice)"
    r"|let me know|should i\b|do you want|would you like|shall i\b|which (option|one)",
    re.IGNORECASE,
)
PATH_KEYS = ("file_path", "notebook_path", "path")


# ------------------------------------------------------------------ state


def workspace_of(payload: dict) -> Path | None:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    if not root:
        return None
    root = Path(root)
    return root if (root / "keep-delivery").is_dir() else None


def state_path(workspace: Path, session: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)
    return workspace / "keep-delivery/.local/smoke/sessions" / f"{safe}.json"


def load(workspace: Path, session: str) -> dict | None:
    data = smoke_state.read_object(state_path(workspace, session))
    return data if data and data.get("version") == STATE_VERSION else None


def save(workspace: Path, session: str, state: dict) -> None:
    path = state_path(workspace, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def new_state(workspace: Path) -> dict:
    return {
        "version": STATE_VERSION,
        "workspace": str(workspace),
        "created_at": time.time(),
        "baselines": {},
        "roots": {},
        "missing_blocks": {},
        "waiting_allowed": [],
        "notified": [],
    }


def baseline(state: dict, checkout: smoke_state.Checkout) -> None:
    if checkout.key in state["baselines"]:
        return
    try:
        fingerprint = smoke_state.fingerprint(checkout)
    except Exception:  # noqa: BLE001 - a checkout git can't read isn't watched
        return
    state["baselines"][checkout.key] = {
        "path": str(checkout.path),
        "repo": checkout.repo,
        "fingerprint": fingerprint,
    }


def baseline_workspace(workspace: Path) -> dict:
    state = new_state(workspace)
    for checkout in smoke_state.workspace_children(workspace):
        baseline(state, checkout)
        state["roots"][checkout.key] = True
    return state


# ------------------------------------------------------------------ session-start, pre-tool-use


def on_session_start(payload: dict, workspace: Path) -> dict | None:
    session = payload.get("session_id")
    if session and load(workspace, session) is None:
        save(workspace, session, baseline_workspace(workspace))
    return None


def named_paths(payload: dict) -> list[str]:
    tool_input = payload.get("tool_input") or {}
    found = [str(tool_input[key]) for key in PATH_KEYS if isinstance(tool_input.get(key), str)]
    if payload.get("tool_name") in ("Bash", "PowerShell"):
        command = str(tool_input.get("command") or "")
        found += WINDOWS_PATH.findall(command)
        found += [
            f"{match.group(1).upper()}:/{match.group(0)[3:]}"
            for match in GIT_BASH_PATH.finditer(command)
        ]
        if payload.get("cwd"):
            found.append(str(payload["cwd"]))
    return found


def on_pre_tool_use(payload: dict, workspace: Path) -> dict | None:
    session = payload.get("session_id")
    if not session:
        return None
    candidates = [Path(path) for path in named_paths(payload)]
    candidates = [path for path in candidates if smoke_state.inside(path, workspace)]
    if not candidates:
        return None
    state = load(workspace, session) or baseline_workspace(workspace)
    changed = False
    for path in candidates:
        root = smoke_state.checkout_root(path)
        if root is None or not smoke_state.inside(root, workspace):
            continue
        key = smoke_state.path_key(root)
        if key in state["roots"]:
            continue
        checkout = smoke_state.describe(root)
        state["roots"][key] = checkout is not None
        if checkout is not None:
            baseline(state, checkout)
        changed = True
    if changed:
        save(workspace, session, state)
    return None


# ------------------------------------------------------------------ transcripts


def read_lines(path: Path) -> list[dict]:
    entries = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    return entries


def tool_result_text(entry: dict) -> str:
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            inner = block.get("content")
            if isinstance(inner, str):
                parts.append(inner)
            elif isinstance(inner, list):
                parts += [str(b.get("text", "")) for b in inner if isinstance(b, dict)]
    return "\n".join(parts)


def subagent_dir(payload: dict) -> Path | None:
    transcript = payload.get("transcript_path")
    if not transcript:
        return None
    transcript = Path(transcript)
    return transcript.parent / transcript.stem / "subagents"


def smoke_runners(payload: dict) -> tuple[list[dict], list[str]]:
    """This session's smoke-runner subagents: id, finished, haiku, run_ids, fresh."""
    directory = subagent_dir(payload)
    runners: list[dict] = []
    problems: list[str] = []
    if directory is None or not directory.is_dir():
        return runners, problems
    now = time.time()
    for meta_path in sorted(directory.glob("agent-*.meta.json")):
        meta = smoke_state.read_object(meta_path)
        if meta is None:
            problems.append(f"{meta_path.name} is unreadable")
            continue
        if meta.get("agentType") != "smoke-runner":
            continue
        transcript = meta_path.with_name(meta_path.name.removesuffix(".meta.json") + ".jsonl")
        runner = {"id": meta_path.name, "finished": meta.get("finished") is True}
        try:
            entries = read_lines(transcript)
            mtime = max(meta_path.stat().st_mtime, transcript.stat().st_mtime)
        except OSError as error:
            if runner["finished"]:
                problems.append(f"{transcript.name}: {error.strerror or error}")
            runners.append(
                {**runner, "fresh": now - meta_path.stat().st_mtime < RUNNING_FRESH_SECONDS}
            )
            continue
        models = {
            str(entry["message"].get("model"))
            for entry in entries
            if entry.get("type") == "assistant" and isinstance(entry.get("message"), dict)
        }
        runner["models"] = sorted(models)
        runner["haiku"] = bool(models) and all("haiku" in model for model in models)
        runner["run_ids"] = sorted(
            {m for e in entries for m in RUN_ID.findall(tool_result_text(e))}
        )
        runner["fresh"] = now - mtime < RUNNING_FRESH_SECONDS
        if runner["finished"] and not models:
            problems.append(f"{transcript.name} has no assistant messages with a model")
        runners.append(runner)
    return runners, problems


def waiting_for_user(payload: dict) -> bool:
    """True when the last turn ends by asking the user something."""
    transcript = payload.get("transcript_path")
    if not transcript:
        return False
    try:
        entries = read_lines(Path(transcript))[-400:]
    except OSError:
        return False
    texts: list[str] = []
    for entry in reversed(entries):
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if entry.get("type") == "user":
            if isinstance(content, str) or (
                isinstance(content, list)
                and any(isinstance(b, dict) and b.get("type") == "text" for b in content)
            ):
                break  # the user's own message starts this turn
            continue
        if entry.get("type") != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                tool_input = block.get("input") or {}
                if name == "AskUserQuestion":
                    return True
                if name.endswith("wrap_up") and (
                    tool_input.get("blocked") or tool_input.get("ask")
                ):
                    return True
            elif block.get("type") == "text" and not texts:
                texts.append(str(block.get("text") or ""))
    final = texts[0].strip() if texts else ""
    return final.endswith("?") or bool(WAITING.search(final[-600:]))


# ------------------------------------------------------------------ stop


def stack_delivery(workspace: Path) -> Path:
    """The main keep-delivery checkout whose .local holds the running stack."""
    delivery = workspace / "keep-delivery"
    checkout = smoke_state.describe(delivery)
    if checkout is None or checkout.main:
        return delivery
    common = smoke_state.git(delivery, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common.strip()).parent


def smoke_command(workspace: Path) -> str:
    return smoke_state.smoke_command(workspace, stack_delivery(workspace))


def load_report(workspace: Path, run_id: str) -> dict | None:
    report = smoke_state.read_object(
        workspace / "keep-delivery/.local/smoke/reports" / f"{run_id}.json"
    )
    return report if report and report.get("run_id") == run_id else None


def covers(report: dict, changed: dict[str, str]) -> bool:
    if report.get("algorithm_version") != smoke_state.ALGORITHM_VERSION:
        return False
    recorded = {row.get("key"): row.get("fingerprint") for row in report.get("checkouts") or []}
    return all(recorded.get(key) == fingerprint for key, fingerprint in changed.items())


def results_line(report: dict) -> str:
    parts = []
    for name, scenario in (report.get("scenarios") or {}).items():
        reason = f" ({scenario.get('reason')})" if scenario.get("reason") else ""
        parts.append(f"{name} {scenario.get('result')}{reason}")
    return "; ".join(parts)


def missing_message(workspace: Path, changed_paths: list[str], note: str) -> str:
    lines = [
        "Smoke run required before you finish: app code changed in "
        + ", ".join(changed_paths)
        + ".",
    ]
    if note:
        lines.append(note)
    lines += [
        'Run the smoke-runner subagent (Agent tool, subagent_type "smoke-runner") with this prompt:',
        f"Run: {smoke_command(workspace)}",
        "It runs S1-S4 on the running local stack in about 3 minutes and returns a table with run_id. "
        "You may stop while it runs; finish after its result. It never starts services: if the stack "
        "is down, start it first (keep-delivery/docs/runbooks/development.md, "
        '"Environment, startup and readiness").',
        "Then handle the result: fix what your change broke and run it again, or say in your reply why "
        "a failure can't be fixed now. If you are stopping only to ask the user something, say in your "
        "last message that you are waiting for their answer.",
    ]
    return "\n".join(lines)


def on_stop(payload: dict, workspace: Path) -> dict | None:
    session = payload.get("session_id")
    if not session:
        return None
    state = load(workspace, session)
    if state is None:
        save(workspace, session, baseline_workspace(workspace))
        return {
            "systemMessage": "Smoke gate started late in this session; earlier changes are not checked."
        }
    changed: dict[str, str] = {}
    paths: list[str] = []
    for key, base in state["baselines"].items():
        checkout = smoke_state.Checkout(Path(base["path"]), base["repo"], False)
        if not checkout.path.is_dir():
            continue
        try:
            current = smoke_state.fingerprint(checkout)
        except Exception:  # noqa: BLE001
            continue
        if current != base["fingerprint"]:
            changed[key] = current
            paths.append(base["path"])
    if not changed:
        return None
    digest = hashlib.sha256(json.dumps(sorted(changed.items())).encode()).hexdigest()[:16]

    runners, problems = smoke_runners(payload)
    lock = stack_delivery(workspace) / ".local/smoke/lock"
    if any(not r["finished"] and r.get("fresh") for r in runners) or smoke_state.lock_holder(
        lock, time.time()
    ):
        return None
    if any("smoke" in json.dumps(task).lower() for task in payload.get("background_tasks") or []):
        return None

    reports = []
    for runner in runners:
        if runner["finished"] and runner.get("haiku"):
            for run_id in runner.get("run_ids") or []:
                report = load_report(workspace, run_id)
                if report:
                    reports.append(report)
    covering = [report for report in reports if covers(report, changed)]
    if covering:
        latest = max(covering, key=lambda r: str(r.get("finished_at") or ""))
        if latest.get("result") == "passed":
            return None
        key = f"result:{digest}:{latest['run_id']}"
        if key in state["notified"]:
            return None
        state["notified"].append(key)
        save(workspace, session, state)
        change = latest.get("stack_includes_change") or {}
        return {
            "decision": "block",
            "reason": (
                f"Smoke run {latest['run_id']} {latest.get('result')}: {results_line(latest)}.\n"
                f"stack_includes_change: {change.get('value')} ({change.get('reason')}).\n"
                "Fix what your change broke and run the smoke-runner again, or say in your reply why "
                "it can't be fixed now (for example a pre-existing failure or a stack problem). "
                "This message is shown once; the next stop is allowed."
            ),
        }

    if problems:
        key = f"format:{digest}"
        if key not in state["notified"]:
            state["notified"].append(key)
            save(workspace, session, state)
            return {
                "decision": "block",
                "reason": "Smoke gate can't read this session's subagent files ("
                + "; ".join(problems[:3])
                + "), so it can't confirm a smoke run. Tell the user; the next stop is allowed.",
            }
        return None

    if waiting_for_user(payload) and digest not in state["waiting_allowed"]:
        state["waiting_allowed"].append(digest)
        save(workspace, session, state)
        return None

    count = state["missing_blocks"].get(digest, 0) + 1
    state["missing_blocks"][digest] = count
    save(workspace, session, state)
    if count > MAX_MISSING_BLOCKS:
        return {
            "systemMessage": f"Smoke gate: no smoke run after {MAX_MISSING_BLOCKS} reminders; "
            "this stop was let through. Ask the agent for the smoke results."
        }
    note = ""
    if any(r["finished"] and not r.get("haiku") for r in runners):
        note = "A smoke-runner ran on a model other than Haiku; its run doesn't count."
    elif reports:
        note = "Your last smoke run covered older code: the code changed after it, or the smoke tooling changed."
    return {"decision": "block", "reason": missing_message(workspace, paths, note)}


# ------------------------------------------------------------------ entry point

HANDLERS = {
    "session-start": on_session_start,
    "pre-tool-use": on_pre_tool_use,
    "stop": on_stop,
}


def main(argv: list[str]) -> int:
    try:
        handler = HANDLERS[argv[1]]
        payload = json.loads(sys.stdin.read() or "{}")
        workspace = workspace_of(payload)
        if workspace is None:
            return 0
        result = handler(payload, workspace)
    except Exception as error:  # noqa: BLE001 - never fail a hook
        print(f"smoke gate: {type(error).__name__}: {error}", file=sys.stderr)
        return 0
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
