"""The smoke runner (scripts/smoke.py) and its shared state (scripts/smoke_state.py).

Requirements: smoke baseline S1-S4 (development runbook, "Repeatable verification
workflow" and "Critical smoke scenarios and expected results"); smoke-runner plan
(.plans/2026-11-18-smoke-runner.md) A2 and A5-A8.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
smoke = importlib.import_module("smoke")
state = importlib.import_module("smoke_state")


# ------------------------------------------------------------------- fixtures


class Clock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make_paths(tmp_path: Path, playwright: bool = True):
    workspace = tmp_path / "ws"
    delivery = workspace / "keep-delivery"
    frontend = workspace / "keep-frontend"
    (delivery / ".venv/Scripts").mkdir(parents=True)
    (delivery / ".venv/Scripts/python.exe").write_text("", encoding="utf-8")
    (delivery / ".local/identity").mkdir(parents=True)
    (delivery / ".local/identity/credentials.json").write_text("{}", encoding="utf-8")
    if playwright:
        cli = frontend / "node_modules/@playwright/test/cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("", encoding="utf-8")
    else:
        frontend.mkdir(parents=True)
    return smoke.Paths(workspace=workspace, stack_delivery=delivery, stack_frontend=frontend)


def make_runner(tmp_path: Path, clock: Clock | None = None, **kwargs):
    clock = clock or Clock()
    runner = smoke.Runner(
        make_paths(tmp_path, kwargs.pop("playwright", True)),
        api_url=kwargs.pop("api_url", "http://127.0.0.1:1"),
        ingestion_url="http://127.0.0.1:1",
        ui_url="https://127.0.0.1:1",
        adapi_url="http://127.0.0.1:1",
        clock=clock,
        wall=clock,
        sleep=clock.sleep,
        **kwargs,
    )
    runner.checkouts = lambda: []
    return runner


def passed(**ids):
    return smoke.Scenario(result="passed", passed=1, ids=ids)


def failed(reason: str = "broken"):
    return smoke.Scenario(result="failed", failed=1, reason=reason)


def stub_scenarios(runner, results: dict, calls: list) -> None:
    runner.check_readiness = lambda: []
    runner.repair = lambda stage: calls.append(f"repair-{stage}") or {}

    def step(name):
        def run(*args):
            calls.append((name, args))
            return results[name]

        return run

    runner.run_s1 = step("S1")
    runner.run_s2 = step("S2")
    runner.run_s3 = step("S3")
    runner.run_s4 = step("S4")


# ------------------------------------------------------- A2: skips aren't passes


def report(expected=0, unexpected=0, skipped=0, flaky=0, suites=None, errors=None):
    return {
        "stats": {
            "expected": expected,
            "unexpected": unexpected,
            "skipped": skipped,
            "flaky": flaky,
        },
        "suites": suites or [],
        "errors": errors or [],
    }


def test_all_expected_tests_passing_is_a_pass():
    """A2: every test ran and passed."""
    assert smoke.classify(report(expected=7), 0, False).result == "passed"


def test_a_skipped_test_makes_the_scenario_partial():
    """A2: a skip is partial coverage, which is not a pass (runbook S1 limits)."""
    scenario = smoke.classify(report(expected=6, skipped=1), 0, False)
    assert (scenario.result, scenario.skipped) == ("partial", 1)


def test_all_tests_skipped_is_partial_not_passed():
    """A2: an opt-in variable that didn't reach the spec skips everything."""
    assert smoke.classify(report(skipped=7), 0, False).result == "partial"


def test_a_failing_test_fails_the_scenario_with_its_title_and_message():
    suites = [
        {
            "specs": [
                {
                    "title": "wrong password",
                    "tests": [
                        {
                            "results": [
                                {
                                    "status": "failed",
                                    "error": {"message": "\x1b[31mExpected 401\x1b[39m\nmore"},
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    ]
    scenario = smoke.classify(report(expected=6, unexpected=1, suites=suites), 1, False)
    assert scenario.result == "failed"
    assert scenario.reason == "wrong password: Expected 401"


@pytest.mark.parametrize(
    ("data", "code", "timed_out", "reason"),
    [
        (None, 1, False, "no Playwright report (exit 1)"),
        (report(), 0, False, "no tests ran"),
        (report(expected=1), 0, True, "timed out"),
        (report(errors=[{"message": "config error"}]), 1, False, "config error"),
        (report(expected=1), 3, False, "Playwright exit 3"),
    ],
)
def test_missing_empty_timed_out_or_broken_runs_fail(data, code, timed_out, reason):
    scenario = smoke.classify(data, code, timed_out)
    assert (scenario.result, scenario.reason) == ("failed", reason)


# ------------------------------------------------------- A6: failure order


def test_a_failed_s1_blocks_s2_to_s4(tmp_path):
    """A6: identity must pass before the dependent scenarios (runbook: stop on failure)."""
    runner, calls = make_runner(tmp_path), []
    stub_scenarios(runner, {"S1": failed()}, calls)
    result = runner.run()
    assert [c[0] for c in calls if isinstance(c, tuple)] == ["S1"]
    for name in ("S2", "S3", "S4"):
        assert result["scenarios"][name]["result"] == "blocked"
        assert "S1 failed" in result["scenarios"][name]["reason"]
    assert result["result"] == "failed"


def test_a_failed_s2_still_runs_s3_and_s4_uses_its_own_alert(tmp_path):
    """A6/A5: S4 gets the failed S2, so it sends its own alert instead."""
    runner, calls = make_runner(tmp_path), []
    s2 = failed()
    stub_scenarios(runner, {"S1": passed(), "S2": s2, "S3": passed(), "S4": passed()}, calls)
    result = runner.run()
    steps = [c for c in calls if isinstance(c, tuple)]
    assert [c[0] for c in steps] == ["S1", "S2", "S3", "S4"]
    assert steps[-1][1] == (s2,)
    assert result["result"] == "failed"


def test_a_failed_s3_still_runs_s4(tmp_path):
    runner, calls = make_runner(tmp_path), []
    stub_scenarios(runner, {"S1": passed(), "S2": passed(), "S3": failed(), "S4": passed()}, calls)
    result = runner.run()
    assert result["scenarios"]["S4"]["result"] == "passed"
    assert result["result"] == "failed"


def test_all_scenarios_passing_writes_the_report_ledger_and_latest(tmp_path):
    """A1: the report, its per-run copy and the run ledger."""
    runner, calls = make_runner(tmp_path), []
    stub_scenarios(runner, {"S1": passed(), "S2": passed(), "S3": passed(), "S4": passed()}, calls)
    result = runner.run()
    assert result["result"] == "passed"
    assert result["algorithm_version"] == state.ALGORITHM_VERSION
    reports = runner.paths.reports
    assert json.loads((reports / "latest.json").read_text())["run_id"] == runner.run_id
    assert (reports / "reports" / f"{runner.run_id}.json").exists()
    assert json.loads((runner.ledger / "ledger.json").read_text())["result"] == "passed"
    assert calls[0] == "repair-start" and calls[-1] == "repair-end"
    assert not runner.paths.lock.exists()


def test_the_summary_names_the_run_id_and_each_scenario(tmp_path):
    runner, calls = make_runner(tmp_path), []
    stub_scenarios(runner, {"S1": passed(), "S2": passed(), "S3": passed(), "S4": passed()}, calls)
    text = smoke.summary(runner.run(), 0)
    assert text.splitlines()[0] == f"run_id: {runner.run_id}"
    assert all(f"| {name} | passed |" in text for name in smoke.SCENARIOS)


def test_s2_without_evidence_for_this_run_is_not_a_pass(tmp_path):
    """S2 hands its alert to S4 only through evidence written by this run."""
    runner = make_runner(tmp_path)
    runner.playwright = lambda *args, **kwargs: passed()
    evidence = runner.paths.stack_delivery / ".local/runtime/browser-evidence.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps({"smoke_run": "older", "alert": "a", "tenant": "t"}))
    assert runner.run_s2().result == "failed"
    evidence.write_text(json.dumps({"smoke_run": runner.run_id, "alert": "a", "tenant": "t"}))
    assert runner.run_s2().ids == {"tenant": "t", "alert": "a"}


def test_s4_reports_blocked_when_its_ledger_says_so(tmp_path):
    """A5: every candidate alert already belongs to an incident."""
    runner = make_runner(tmp_path)
    runner.ledger.mkdir(parents=True)
    (runner.ledger / "s4.json").write_text(
        json.dumps({"outcome": "blocked", "reason": "alert x already belongs to incident y"})
    )
    runner.playwright = lambda *args, **kwargs: failed("S4 blocked")
    scenario = runner.run_s4(failed())
    assert (scenario.result, scenario.reason) == (
        "blocked",
        "alert x already belongs to incident y",
    )


# ------------------------------------------------------- A7: readiness


def test_an_unreachable_api_blocks_the_run_without_running_anything(tmp_path):
    """A7: --api-url http://127.0.0.1:1 reports blocked and runs no browser or sender."""
    executed = []
    runner = make_runner(tmp_path, execute=lambda *args: executed.append(args) or (0, False))
    runner.repair = lambda stage: {}
    probed = []

    def status(url, timeout=5):
        # Probe the API for real; Windows takes seconds to refuse each loopback port.
        probed.append(url)
        return smoke.Runner.http_status(url, timeout) if len(probed) == 1 else None

    runner.http_status = status
    result = runner.run()
    assert probed[0] == "http://127.0.0.1:1/livez"
    assert result["result"] == "blocked"
    assert all(s["result"] == "blocked" for s in result["scenarios"].values())
    assert "api is not ready at http://127.0.0.1:1/livez" in result["scenarios"]["S1"]["reason"]
    assert executed == []
    assert smoke.summary(result, 2).splitlines()[1].startswith("result: blocked (exit 2)")


def test_a_frontend_without_playwright_names_npm_ci(tmp_path):
    runner = make_runner(tmp_path, playwright=False)
    runner.http_status = lambda url, timeout=5: 200
    problems = runner.check_readiness()
    assert any("run npm ci there" in problem for problem in problems)


# ------------------------------------------------------- A8: lock and budget


def test_a_live_lock_holder_blocks_after_the_wait(tmp_path, monkeypatch):
    """A8: one run at a time; the second waits up to 120 s, then reports blocked."""
    clock = Clock()
    runner = make_runner(tmp_path, clock)
    runner.paths.lock.parent.mkdir(parents=True)
    runner.paths.lock.write_text(
        json.dumps({"pid": 4242, "started_at": clock.now, "run_id": "other"})
    )
    monkeypatch.setattr(state, "pid_alive", lambda pid: True)
    reason = runner.acquire_lock()
    assert reason == "another smoke run is in progress (run other, pid 4242)"
    assert clock.now - 1000.0 >= smoke.LOCK_WAIT_SECONDS
    assert json.loads(runner.paths.lock.read_text())["run_id"] == "other"


@pytest.mark.parametrize(("alive", "age"), [(False, 10), (True, state.LOCK_STALE_SECONDS + 1)])
def test_a_dead_or_stale_lock_holder_is_taken_over(tmp_path, monkeypatch, alive, age):
    """A8: a killed run's lock doesn't block the next run."""
    clock = Clock()
    runner = make_runner(tmp_path, clock)
    runner.paths.lock.parent.mkdir(parents=True)
    runner.paths.lock.write_text(
        json.dumps({"pid": 4242, "started_at": clock.now - age, "run_id": "old"})
    )
    monkeypatch.setattr(state, "pid_alive", lambda pid: alive)
    assert runner.acquire_lock() is None
    assert json.loads(runner.paths.lock.read_text())["run_id"] == runner.run_id
    runner.release_lock()
    assert not runner.paths.lock.exists()


def test_scenarios_past_the_budget_are_blocked(tmp_path):
    """A8: the whole run fits 540 s; what doesn't fit is blocked, not skipped."""
    clock = Clock()
    runner, calls = make_runner(tmp_path, clock), []
    stub_scenarios(runner, {"S1": passed()}, calls)

    def slow_s1():
        clock.now += smoke.BUDGET_SECONDS - smoke.FINAL_REPAIR_RESERVE - 10
        return passed()

    runner.run_s1 = slow_s1
    result = runner.run()
    for name in ("S2", "S3", "S4"):
        assert result["scenarios"][name]["result"] == "blocked"
        assert result["scenarios"][name]["reason"] == "the smoke time budget ran out"


def write_s4(root: Path, folder: str, **fields) -> Path:
    run = folder
    path = root / folder / "s4.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = {
        "run": run,
        "title": f"smoke-{run} incident",
        "tenant_id": "t",
        "incident_id": f"i-{run}",
        "cleaned": False,
        **fields,
    }
    path.write_text(json.dumps(ledger), encoding="utf-8")
    return path


def test_only_unfinished_smoke_ledgers_are_cleaned_up(tmp_path):
    """A5/A8: cleanup touches only an incident its own ledger recorded."""
    pending = write_s4(tmp_path, "r1")
    write_s4(tmp_path, "r2", cleaned=True)
    write_s4(tmp_path, "r3", incident_id=None)
    write_s4(tmp_path, "r4", title="Production outage")
    write_s4(tmp_path, "r5", run="another-run")
    write_s4(tmp_path, "r6")
    found = smoke.pending_s4_cleanups(tmp_path)
    assert found == [pending, tmp_path / "r6" / "s4.json"]


def test_start_repair_takes_earlier_ledgers_and_end_repair_only_this_runs(tmp_path):
    runner = make_runner(tmp_path)
    earlier = write_s4(runner.paths.verification, "earlier")
    own = write_s4(runner.paths.verification, runner.run_id)
    seen = []
    runner.repair_membership = lambda: "ok"
    runner.repair_s4 = lambda ledgers: seen.append(ledgers) or []
    runner.repair("start")
    runner.repair("end")
    assert seen == [[earlier], [own]]


def test_membership_is_checked_at_the_end_only_after_s1_ran(tmp_path):
    runner = make_runner(tmp_path)
    runner.repair_membership = lambda: "restored"
    assert runner.repair("end") == {}
    runner.s1_attempted = True
    assert runner.repair("end") == {"membership": "restored"}


# ------------------------------------------------------- stack_includes_change


def checkout(path="C:/ws/keep-api", repo="keep-api", stack=True, active=True, newest=200.0):
    return {
        "path": path,
        "repo": repo,
        "stack": stack,
        "work_in_progress": {"active": active, "newest_change": newest},
    }


def editable_from(path):
    return lambda repo: state.path_key(path)


def test_no_work_in_progress_is_covered():
    value, _ = smoke.stack_includes_change([checkout(active=False)], {}, editable_from("x"))
    assert value == "yes"


def test_work_in_a_worktree_is_not_in_the_stack():
    rows = [checkout(path="C:/ws/.wt/task/keep-api", stack=False)]
    value, reason = smoke.stack_includes_change(rows, {"Api": 300.0}, editable_from("x"))
    assert value == "no" and "C:/ws/.wt/task/keep-api" in reason


def test_a_worktree_with_the_stack_checkouts_content_is_in_the_stack():
    """A branch checked out in the stack, with its worktree detached at the same content."""
    main = checkout(path="C:/ws/keep-frontend", repo="keep-frontend") | {"fingerprint": "v1:a"}
    worktree = main | {"path": "C:/ws/.wt/task/keep-frontend", "stack": False}
    assert smoke.stack_includes_change([main, worktree], {}, editable_from("x"))[0] == "yes"
    changed = worktree | {"fingerprint": "v1:b"}
    value, reason = smoke.stack_includes_change([main, changed], {}, editable_from("x"))
    assert value == "no" and "C:/ws/.wt/task/keep-frontend" in reason


def test_a_change_after_the_service_started_needs_a_restart():
    value, reason = smoke.stack_includes_change(
        [checkout(newest=400.0)], {"Api": 300.0}, editable_from("C:/ws/keep-api")
    )
    assert value == "no" and "restart" in reason


def test_a_service_started_after_the_change_runs_it():
    value, _ = smoke.stack_includes_change(
        [checkout(newest=200.0)], {"Api": 300.0}, editable_from("C:/ws/keep-api")
    )
    assert value == "yes"


def test_a_pinned_install_does_not_run_the_checkout():
    value, reason = smoke.stack_includes_change(
        [checkout()], {"Api": 300.0}, editable_from("C:/elsewhere/keep-api")
    )
    assert value == "no" and "not installed from" in reason


def test_migrations_and_unknown_start_times_are_unknown():
    rows = [checkout(path="C:/ws/keep-migrations", repo="keep-migrations")]
    assert smoke.stack_includes_change(rows, {}, editable_from("x"))[0] == "unknown"
    assert (
        smoke.stack_includes_change([checkout()], {}, editable_from("C:/ws/keep-api"))[0]
        == "unknown"
    )


def test_frontend_changes_are_live_in_the_dev_server():
    rows = [checkout(path="C:/ws/keep-frontend", repo="keep-frontend")]
    assert smoke.stack_includes_change(rows, {}, editable_from("x"))[0] == "yes"


def test_process_start_times_parse_seven_fraction_digits(tmp_path):
    runtime = tmp_path / ".local/runtime"
    runtime.mkdir(parents=True)
    (runtime / "workers.json").write_text(
        json.dumps([{"Role": "source", "StartTime": "2026-11-19T08:30:16.0431922Z"}])
    )
    starts = smoke.process_starts(tmp_path)
    assert starts["source"] == pytest.approx(smoke.parse_time("2026-11-19T08:30:16.043192+00:00"))


# ------------------------------------------------------- fingerprints


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "keep-api"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "smoke@example.invalid")
    git(root, "config", "user.name", "Smoke test")
    git(root, "config", "core.autocrlf", "false")
    (root / "src").mkdir()
    (root / "src/app.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "start")
    return state.describe(root)


def test_the_fingerprint_keeps_through_a_commit_of_the_same_content(repo):
    """A10: a commit after a covering run doesn't require another run."""
    (repo.path / "src/app.py").write_text("value = 2\n", encoding="utf-8")
    (repo.path / "src/new.py").write_text("x = 1\n", encoding="utf-8")
    dirty = state.fingerprint(repo)
    git(repo.path, "add", ".")
    assert state.fingerprint(repo) == dirty
    git(repo.path, "commit", "-q", "-m", "change")
    assert state.fingerprint(repo) == dirty


def test_the_fingerprint_changes_with_app_content_and_deletions(repo):
    before = state.fingerprint(repo)
    (repo.path / "src/app.py").write_text("value = 3\n", encoding="utf-8")
    edited = state.fingerprint(repo)
    assert edited != before
    (repo.path / "src/app.py").unlink()
    assert state.fingerprint(repo) not in (before, edited)


def test_docs_and_agent_files_do_not_change_the_fingerprint(repo):
    before = state.fingerprint(repo)
    (repo.path / "README.md").write_text("notes\n", encoding="utf-8")
    (repo.path / "docs").mkdir()
    (repo.path / "docs/guide.txt").write_text("guide\n", encoding="utf-8")
    (repo.path / ".claude").mkdir()
    (repo.path / ".claude/checks.json").write_text("{}\n", encoding="utf-8")
    assert state.fingerprint(repo) == before
    assert state.fingerprint(repo).startswith(f"v{state.ALGORITHM_VERSION}:")


@pytest.mark.parametrize(
    ("repo_name", "path", "app"),
    [
        ("keep-api", "src/keep/app.py", True),
        ("keep-api", "README.md", False),
        ("keep-api", "docs/a.py", False),
        ("keep-api", ".github/workflows/ci.yml", False),
        ("keep-api", "tests/.claude/x.json", False),
        ("keep-delivery", "workspace/manifest.json", False),
        ("keep-delivery", "status/implementation-status.md", False),
        ("keep-delivery", "qualification/check_context.py", False),
        ("keep-delivery", "scripts/smoke.py", True),
        ("keep-api", "workspace/manifest.json", True),
    ],
)
def test_app_paths(repo_name, path, app):
    assert state.is_app_path(repo_name, path) is app


def test_describe_names_worktrees_by_their_main_repository(repo, tmp_path):
    worktree = tmp_path / ".wt/task/keep-api"
    git(repo.path, "worktree", "add", "-q", str(worktree), "-b", "task")
    described = state.describe(worktree)
    assert (described.repo, described.main) == ("keep-api", False)
    assert state.describe(repo.path).main is True
    design = tmp_path / "keep-design"
    design.mkdir()
    git(design, "init", "-q")
    assert state.describe(design) is None


def test_the_s4_playwright_report_never_overwrites_the_s4_ledger(tmp_path):
    """Windows paths ignore case: S4's report must not be ``s4.json``."""
    seen = {}

    def execute(command, cwd, env, timeout, log):
        seen.update(env)
        return 0, False

    runner = make_runner(tmp_path, execute=execute)
    runner.run_s4(failed())
    report = state.path_key(seen["PLAYWRIGHT_JSON_OUTPUT_NAME"])
    assert report != state.path_key(seen["KEEP_SMOKE_LEDGER"])
