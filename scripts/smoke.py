"""Run the smoke baseline S1-S4 against the running local stack and write a report.

    <stack delivery>/.venv/Scripts/python.exe <stack delivery>/scripts/smoke.py run --workspace <workspace>

The whole run executes from the *stack checkouts*: the main ``keep-delivery`` that
holds the stack's ``.local`` state and the main ``keep-frontend`` whose Playwright
specs drive the browser. ``--workspace`` only selects where the report goes and
which checkouts are fingerprinted. The development runbook, "Repeatable
verification workflow", defines S1-S4; this command runs them in order:

* S1 identity and access: ``playwright.local-identity.config.ts``.
* S2 native v1 and alert work: ``playwright.local-runtime.config.ts``.
* S3 native v2 isolation: ``local_v2_verify.py verify`` and ``send``, then
  ``playwright.local-v2.config.ts``.
* S4 incident lifecycle: ``playwright.local-incident.config.ts``.

A failed S1 blocks S2-S4. A failed S2 still runs S3, and S4 then sends its own
alert. A failed S3 still runs S4. A skipped test makes its scenario ``partial``,
which is not a pass. A stack that isn't ready makes the run ``blocked`` without
running any scenario. Runs are serialized by a lock in the stack's
``.local/smoke``; each run first repairs what an interrupted run left (the local
viewer's group membership and unfinished S4 incident cleanup) and repairs again
at the end. The total budget, lock wait included, is 540 seconds.

Exit status: 0 passed, 1 failed or partial, 2 blocked. The report is
``<workspace>/keep-delivery/.local/smoke/reports/<run_id>.json`` (and
``latest.json``); the run ledger is ``<stack delivery>/.local/verification/<run_id>/``.
Nothing secret is printed or stored: no passwords, cookies or keys.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_state  # noqa: E402

BUDGET_SECONDS = 540
LOCK_WAIT_SECONDS = 120
FINAL_REPAIR_RESERVE = 60
SCENARIOS = ("S1", "S2", "S3", "S4")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
OPT_INS = (
    "KEEP_LOCAL_IDENTITY_TEST",
    "KEEP_LOCAL_RUNTIME_TEST",
    "KEEP_LOCAL_V2_BROWSER_TEST",
    "KEEP_LOCAL_INCIDENT_TEST",
    "KEEP_SMOKE_S4_CLEANUP",
    "KEEP_SMOKE_S2_ALERT",
    "KEEP_SMOKE_S2_TENANT",
    "KEEP_SMOKE_S3_RUN",
    "KEEP_SMOKE_LEDGER",
    "KEEP_SMOKE_RUN",
)
# Services that load each repository's code, by role in the process records.
# None: whether the stack runs the change can't be told (migrations need the job).
REPO_ROLES: dict[str, list[str] | None] = {
    "keep-api": ["Api"],
    "keep-ingestion": ["Ingestion"],
    "keep-processor": ["source"],
    "keep-indexer": ["indexer"],
    "keep-correlation": ["correlation"],
    "keep-domain": ["Api", "source", "indexer", "correlation"],
    "keep-contracts": ["Api", "Ingestion", "source", "indexer", "correlation"],
    "keep-frontend": [],  # the Next.js dev server reloads changed files
    "keep-delivery": [],  # tooling runs per command
    "keep-migrations": None,
}


@dataclass
class Scenario:
    result: str = "not run"  # passed | failed | partial | blocked | not run
    reason: str = ""
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    seconds: float = 0.0
    evidence: list[str] = field(default_factory=list)
    ids: dict = field(default_factory=dict)


@dataclass
class Paths:
    workspace: Path
    stack_delivery: Path
    stack_frontend: Path

    @property
    def python(self) -> Path:
        windows = self.stack_delivery / ".venv/Scripts/python.exe"
        return windows if windows.exists() else self.stack_delivery / ".venv/bin/python"

    @property
    def reports(self) -> Path:
        return self.workspace / "keep-delivery/.local/smoke"

    @property
    def lock(self) -> Path:
        return self.stack_delivery / ".local/smoke/lock"

    @property
    def verification(self) -> Path:
        return self.stack_delivery / ".local/verification"

    @property
    def playwright(self) -> Path:
        return self.stack_frontend / "node_modules/@playwright/test/cli.js"


# --------------------------------------------------------------------- helpers


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value) -> float | None:
    """Epoch seconds from an ISO time (any fraction length, ``Z`` allowed)."""
    if not isinstance(value, str):
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", value.strip()).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def plain(text: str, limit: int = 300) -> str:
    text = ANSI.sub("", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def last_line(log: Path) -> str:
    try:
        lines = [
            line
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        ]
    except OSError:
        return ""
    return plain(lines[-1]) if lines else ""


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)
    else:  # pragma: no cover - the stack is Windows-only today
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def run_process(
    command: list[str], cwd: Path, env: dict, timeout: float, log: Path
) -> tuple[int | None, bool]:
    """Runs a command with its output in ``log``; kills the whole tree on timeout."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8", errors="replace") as out:
        process = subprocess.Popen(
            command, cwd=str(cwd), env=env, stdout=out, stderr=subprocess.STDOUT
        )
        try:
            return process.wait(timeout=max(timeout, 1)), False
        except subprocess.TimeoutExpired:
            kill_tree(process.pid)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            return None, True


def _first_failure(suites: list) -> str:
    for suite in suites or []:
        for spec in suite.get("specs") or []:
            for test in spec.get("tests") or []:
                for result in test.get("results") or []:
                    if result.get("status") in ("failed", "timedOut", "interrupted"):
                        error = (result.get("error") or {}).get("message") or result.get("status")
                        first = plain(str(error)).splitlines()[0] if error else ""
                        return plain(f"{spec.get('title')}: {first}")
        found = _first_failure(suite.get("suites") or [])
        if found:
            return found
    return ""


def classify(report, code: int | None, timed_out: bool) -> Scenario:
    """A scenario's result from a Playwright JSON report (A2: a skip is not a pass)."""
    scenario = Scenario()
    if timed_out:
        scenario.result, scenario.reason = "failed", "timed out"
        return scenario
    if not isinstance(report, dict) or not isinstance(report.get("stats"), dict):
        scenario.result, scenario.reason = "failed", f"no Playwright report (exit {code})"
        return scenario
    stats = report["stats"]
    scenario.passed = int(stats.get("expected") or 0)
    scenario.failed = int(stats.get("unexpected") or 0) + int(stats.get("flaky") or 0)
    scenario.skipped = int(stats.get("skipped") or 0)
    errors = report.get("errors") or []
    if scenario.failed or errors or code != 0:
        scenario.result = "failed"
        scenario.reason = (
            _first_failure(report.get("suites") or [])
            or (
                plain(str(errors[0].get("message")))
                if errors and isinstance(errors[0], dict)
                else ""
            )
            or f"Playwright exit {code}"
        )
    elif scenario.skipped:
        scenario.result, scenario.reason = "partial", f"{scenario.skipped} test(s) skipped"
    elif not scenario.passed:
        scenario.result, scenario.reason = "failed", "no tests ran"
    else:
        scenario.result = "passed"
    return scenario


def pending_s4_cleanups(verification: Path, exclude_run: str | None = None) -> list[Path]:
    """S4 ledgers whose incident still needs cleanup.

    Only a smoke ledger qualifies: its folder is its run, and its incident title
    is the one that run created. Any other incident is never touched.
    """
    found = []
    for path in sorted(Path(verification).glob("*/s4.json")):
        ledger = smoke_state.read_object(path)
        if not isinstance(ledger, dict):
            continue
        run = ledger.get("run")
        if not run or run != path.parent.name or run == exclude_run:
            continue
        if ledger.get("cleaned") is True:
            continue
        if not ledger.get("incident_id") or not ledger.get("tenant_id"):
            continue
        if ledger.get("title") != f"smoke-{run} incident":
            continue
        found.append(path)
    return found


def process_starts(stack_delivery: Path) -> dict[str, float]:
    starts: dict[str, float] = {}
    for record_file in (".local/identity/processes.json", ".local/runtime/workers.json"):
        records = smoke_state.read_json(stack_delivery / record_file)
        for record in records if isinstance(records, list) else []:
            if isinstance(record, dict) and record.get("Role"):
                started = parse_time(record.get("StartTime"))
                if started:
                    starts[str(record["Role"])] = started
    return starts


def editable_source(stack_delivery: Path, repo: str) -> str | None:
    """The checkout an editable install of ``repo`` points at, if any."""
    distribution = repo.replace("-", "_")
    for site in (
        stack_delivery / ".venv/Lib/site-packages",
        *(stack_delivery / ".venv/lib").glob("python*/site-packages"),
    ):
        for info in site.glob(f"{distribution}-*.dist-info/direct_url.json"):
            data = smoke_state.read_object(info) or {}
            if (data.get("dir_info") or {}).get("editable"):
                path = urllib.parse.unquote(urllib.parse.urlparse(str(data.get("url") or "")).path)
                if re.match(r"^/[A-Za-z]:", path):  # file:///C:/... on Windows
                    path = path[1:]
                return smoke_state.path_key(path)
    return None


def stack_includes_change(
    checkouts: list[dict], starts: dict[str, float], editable: Callable[[str], str | None]
) -> tuple[str, str]:
    """Whether the running stack holds the work in progress: yes, no or unknown."""
    active = [c for c in checkouts if c["work_in_progress"].get("active")]
    if not active:
        return "yes", "no work in progress outside origin/main in any app checkout"
    # A worktree with the same content as its stack checkout carries no other work.
    in_stack = {c["repo"]: c.get("fingerprint") for c in checkouts if c["stack"]}
    outside = [
        c
        for c in active
        if not c["stack"]
        and (c.get("fingerprint") is None or c["fingerprint"] != in_stack.get(c["repo"]))
    ]
    if outside:
        return "no", "work in progress outside the stack checkouts: " + ", ".join(
            c["path"] for c in outside
        )
    stale: list[str] = []
    unknown: list[str] = []
    for checkout in active:
        repo = checkout["repo"]
        roles = REPO_ROLES.get(repo, None)
        if roles is None:
            unknown.append(f"{repo} needs the migration job; not checked")
            continue
        if roles and editable(repo) != smoke_state.path_key(checkout["path"]):
            stale.append(f"{repo} is not installed from {checkout['path']}")
            continue
        newest = checkout["work_in_progress"].get("newest_change") or 0
        for role in roles:
            started = starts.get(role)
            if started is None:
                unknown.append(f"no start time for {role}")
            elif newest > started:
                stale.append(f"{repo} changed after {role} started; restart it")
    if stale:
        return "no", "; ".join(stale)
    if unknown:
        return "unknown", "; ".join(unknown)
    return "yes", "work in progress is in the stack checkouts and the processes started after it"


# --------------------------------------------------------------------- runner


class Runner:
    def __init__(
        self,
        paths: Paths,
        *,
        api_url: str,
        ingestion_url: str,
        ui_url: str,
        adapi_url: str,
        budget: float = BUDGET_SECONDS,
        run_id: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        execute=run_process,
    ):
        self.paths = paths
        self.api_url, self.ingestion_url, self.ui_url, self.adapi_url = (
            api_url.rstrip("/"),
            ingestion_url.rstrip("/"),
            ui_url.rstrip("/"),
            adapi_url.rstrip("/"),
        )
        self.budget = budget
        self.run_id = run_id or str(uuid4())
        self.clock, self.wall, self.sleep, self.execute = clock, wall, sleep, execute
        self.start = clock()
        self.started_wall = wall()
        self.ledger = paths.verification / self.run_id
        self.scenarios = {name: Scenario() for name in SCENARIOS}
        self.repairs: dict = {}
        self.readiness: dict = {}
        self.s1_attempted = False
        self.lock_held = False

    # ---- time and lock

    def remaining(self) -> float:
        return self.budget - (self.clock() - self.start)

    def scenario_time(self) -> float:
        return self.remaining() - FINAL_REPAIR_RESERVE

    def acquire_lock(self, wait: float = LOCK_WAIT_SECONDS) -> str | None:
        """Takes the run lock. Returns None, or why the run is blocked."""
        path = self.paths.lock
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = self.clock() + min(wait, max(self.scenario_time(), 0))
        while True:
            try:
                handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = smoke_state.lock_holder(path, self.wall())
                if holder is None:
                    try:  # stale: a killed run or a reused PID
                        path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if self.clock() >= deadline:
                    return f"another smoke run is in progress (run {holder.get('run_id')}, pid {holder.get('pid')})"
                self.sleep(2)
                continue
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(
                    {"pid": os.getpid(), "started_at": self.wall(), "run_id": self.run_id}, out
                )
            self.lock_held = True
            return None

    def release_lock(self) -> None:
        if not self.lock_held:
            return
        holder = smoke_state.read_object(self.paths.lock)
        if isinstance(holder, dict) and holder.get("run_id") == self.run_id:
            try:
                self.paths.lock.unlink()
            except FileNotFoundError:
                pass
        self.lock_held = False

    # ---- environment and HTTP

    def env(self, extra: dict[str, str]) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in OPT_INS and key != "FORCE_COLOR"
        }
        env.update(
            {
                "KEEP_LOCAL_DELIVERY_DIR": str(self.paths.stack_delivery),
                "KEEP_LOCAL_INGESTION_URL": self.ingestion_url,
                "KEEP_LOCAL_ADAPI_URL": self.adapi_url,
                "NO_COLOR": "1",
            }
        )
        env.update(extra)
        return env

    @staticmethod
    def http_status(url: str, timeout: float = 5) -> int | None:
        context = ssl.create_default_context()
        if url.startswith("https://localhost") or url.startswith("https://127.0.0.1"):
            # The local stack's generated development certificate; loopback only.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=context) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code
        except (urllib.error.URLError, OSError, ValueError):
            return None

    # ---- readiness (A7)

    def check_readiness(self) -> list[str]:
        problems: list[str] = []
        if not shutil.which("node"):
            problems.append("node is not on PATH")
        if not self.paths.playwright.exists():
            problems.append(
                f"Playwright is not installed in {self.paths.stack_frontend}; run npm ci there"
            )
        if not self.paths.python.exists():
            problems.append(f"no Python environment at {self.paths.python}")
        if not (self.paths.stack_delivery / ".local/identity/credentials.json").exists():
            problems.append("no local identity credentials; start the stack first")
        checks = {
            "api": (f"{self.api_url}/livez", {200}),
            "ingestion": (f"{self.ingestion_url}/livez", {200}),
            "ui": (f"{self.ui_url}/sign-in", {200}),
            "adapi": (f"{self.adapi_url}/", None),  # any HTTP answer means it is up
        }
        for name, (url, expected) in checks.items():
            status = self.http_status(url)
            self.readiness[name] = status
            if status is None or (expected is not None and status not in expected):
                problems.append(f"{name} is not ready at {url} (status {status})")
        if not problems:
            code, timed_out = self.execute(
                [str(self.paths.python), "scripts/local_runtime.py", "wait-workers"],
                self.paths.stack_delivery,
                self.env({}),
                min(60, max(self.scenario_time(), 1)),
                self.ledger / "wait-workers.log",
            )
            self.readiness["workers"] = "ready" if code == 0 and not timed_out else "not ready"
            if code != 0 or timed_out:
                problems.append(
                    "workers are not ready: "
                    + (last_line(self.ledger / "wait-workers.log") or "wait-workers failed")
                )
        return problems

    # ---- Playwright

    def playwright(
        self, label: str, config: str, extra: dict[str, str], timeout: float | None = None
    ) -> Scenario:
        report = self.ledger / f"{label}-playwright.json"  # s4.json is the S4 ledger
        command = [
            shutil.which("node") or "node",
            str(self.paths.playwright),
            "test",
            "--config",
            config,
            "--reporter=list,json",
            f"--output={self.ledger / (label + '-artifacts')}",
        ]
        env = self.env(
            {
                **extra,
                "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report),
                "PLAYWRIGHT_JSON_OUTPUT_FILE": str(report),
            }
        )
        started = self.clock()
        try:
            report.unlink()
        except FileNotFoundError:
            pass
        limit = timeout if timeout is not None else self.scenario_time()
        code, timed_out = self.execute(
            command, self.paths.stack_frontend, env, limit, self.ledger / f"{label}.log"
        )
        scenario = classify(smoke_state.read_json(report), code, timed_out)
        if timed_out:
            scenario.reason = f"timed out after {int(limit)} s"
        scenario.seconds = round(self.clock() - started, 1)
        scenario.evidence.append(str(self.ledger / f"{label}.log"))
        return scenario

    def command(self, label: str, args: list[str], timeout: float = 120) -> tuple[bool, str]:
        log = self.ledger / f"{label}.log"
        code, timed_out = self.execute(
            [str(self.paths.python), *args],
            self.paths.stack_delivery,
            self.env({}),
            min(timeout, max(self.scenario_time(), 1)),
            log,
        )
        if timed_out:
            return False, f"{label} timed out"
        return code == 0, last_line(log)

    # ---- scenarios

    def run_s1(self) -> Scenario:
        self.s1_attempted = True
        return self.playwright(
            "S1", "playwright.local-identity.config.ts", {"KEEP_LOCAL_IDENTITY_TEST": "1"}
        )

    def run_s2(self) -> Scenario:
        scenario = self.playwright(
            "S2",
            "playwright.local-runtime.config.ts",
            {"KEEP_LOCAL_RUNTIME_TEST": "1", "KEEP_SMOKE_RUN": self.run_id},
        )
        evidence_path = self.paths.stack_delivery / ".local/runtime/browser-evidence.json"
        evidence = smoke_state.read_object(evidence_path)
        if scenario.result == "passed":
            if not isinstance(evidence, dict) or evidence.get("smoke_run") != self.run_id:
                scenario.result, scenario.reason = "failed", "S2 wrote no evidence for this run"
            else:
                scenario.ids = {"tenant": evidence.get("tenant"), "alert": evidence.get("alert")}
                scenario.evidence.append(str(evidence_path))
        return scenario

    def run_s3(self) -> Scenario:
        started = self.clock()
        operators = self.paths.stack_delivery / ".local/operators"
        for step in ("verify", "send"):
            ok, detail = self.command(f"S3-{step}", ["scripts/local_v2_verify.py", step])
            if not ok:
                return Scenario(
                    result="failed",
                    reason=f"local_v2_verify.py {step} failed: {detail}",
                    seconds=round(self.clock() - started, 1),
                    evidence=[str(self.ledger / f"S3-{step}.log")],
                )
        evidence = smoke_state.read_object(operators / "alerts-evidence.json") or {}
        verified = parse_time(evidence.get("verified_at"))
        if not verified or verified < self.started_wall - 5 or not evidence.get("run"):
            return Scenario(
                result="failed",
                reason="send left no fresh alerts-evidence.json",
                seconds=round(self.clock() - started, 1),
            )
        scenario = self.playwright(
            "S3",
            "playwright.local-v2.config.ts",
            {"KEEP_LOCAL_V2_BROWSER_TEST": "1", "KEEP_SMOKE_S3_RUN": str(evidence["run"])},
        )
        scenario.ids = {
            "tenant": evidence.get("tenant_id"),
            "alerts": evidence.get("alert_ids"),
            "sender_run": evidence.get("run"),
        }
        scenario.evidence = [
            str(operators / "verification.json"),
            str(operators / "alerts-evidence.json"),
            *scenario.evidence,
        ]
        scenario.seconds = round(self.clock() - started, 1)
        return scenario

    def run_s4(self, s2: Scenario) -> Scenario:
        ledger = self.ledger / "s4.json"
        extra = {
            "KEEP_LOCAL_INCIDENT_TEST": "1",
            "KEEP_SMOKE_RUN": self.run_id,
            "KEEP_SMOKE_LEDGER": str(ledger),
        }
        if s2.result == "passed" and s2.ids.get("alert"):
            extra["KEEP_SMOKE_S2_ALERT"] = str(s2.ids["alert"])
            extra["KEEP_SMOKE_S2_TENANT"] = str(s2.ids["tenant"])
        scenario = self.playwright("S4", "playwright.local-incident.config.ts", extra)
        record = smoke_state.read_object(ledger)
        if isinstance(record, dict):
            scenario.ids = {
                key: record.get(key)
                for key in (
                    "tenant_id",
                    "incident_id",
                    "alert_id",
                    "alert_source",
                    "s2_alert_owner",
                    "cleaned",
                    "cleanup",
                )
            }
            if record.get("outcome") == "blocked":
                scenario.result, scenario.reason = (
                    "blocked",
                    str(record.get("reason") or "S4 blocked"),
                )
        scenario.evidence.append(str(ledger))
        return scenario

    # ---- repair (A8)

    def viewer_sees_local_demo(self) -> bool:
        """Signs in as local-viewer over HTTPS, reads the session, signs out."""
        users = (
            smoke_state.read_object(self.paths.stack_delivery / ".local/identity/credentials.json")
            or {}
        ).get("users", {})
        password = users["local-viewer"]["password"]
        context = ssl.create_default_context()
        context.check_hostname = False  # the local development certificate, loopback only
        context.verify_mode = ssl.CERT_NONE
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.HTTPSHandler(context=context),
        )
        headers = {
            "Origin": self.ui_url,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = json.dumps(
            {"username": "local-viewer", "password": password, "return_to": None}
        ).encode()
        opener.open(
            urllib.request.Request(f"{self.ui_url}/auth/login", body, headers, method="POST"),
            timeout=15,
        ).read()
        with opener.open(
            urllib.request.Request(f"{self.ui_url}/auth/session", headers=headers), timeout=15
        ) as response:
            session = json.loads(response.read()).get("data") or {}
        try:
            opener.open(
                urllib.request.Request(
                    f"{self.ui_url}/auth/logout",
                    b"{}",
                    {**headers, "X-CSRF-Token": str(session.get("csrf_token") or "")},
                    method="POST",
                ),
                timeout=15,
            ).read()
        except urllib.error.HTTPError:
            pass
        return any(tenant.get("name") == "Local Demo" for tenant in session.get("tenants") or [])

    def restore_membership(self) -> None:
        body = json.dumps(
            {"add_member": {"group": "keep-observers", "member": "local-viewer"}}
        ).encode()
        request = urllib.request.Request(
            f"{self.adapi_url}/__set", body, {"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(request, timeout=15).read()
        settings = (
            smoke_state.read_object(
                self.paths.stack_delivery / ".local/identity/app-environment.json"
            )
            or {}
        )
        env = {**self.env({}), **{key: str(value) for key, value in settings.items()}}
        env["KEEP_DATABASE_URL"] = str(settings.get("KEEP_LOCAL_WORKER_DATABASE_URL", ""))
        env["KEEP_DIRECTORY_BOOTSTRAP_QUALIFIED"] = "false"
        code, timed_out = self.execute(
            [str(self.paths.python), "-m", "keep.worker", "directory", "--once"],
            self.paths.stack_delivery,
            env,
            90,
            self.ledger / "directory-refresh.log",
        )
        if code != 0 or timed_out:
            raise RuntimeError(
                "directory refresh failed: " + last_line(self.ledger / "directory-refresh.log")
            )

    def repair_membership(self) -> str:
        try:
            if self.viewer_sees_local_demo():
                return "ok"
            self.restore_membership()
            if self.viewer_sees_local_demo():
                return "restored"
            return "local-viewer can't see Local Demo after a restore (S1 creates the tenant when it is missing)"
        except Exception as error:  # noqa: BLE001 - reported, never raised past the run
            return f"unknown: {plain(str(error), 200)}"

    def repair_s4(self, ledgers: list[Path]) -> list[dict]:
        results = []
        for path in ledgers:
            record = smoke_state.read_object(path) or {}
            scenario = self.playwright(
                f"repair-{path.parent.name}",
                "playwright.local-incident.config.ts",
                {
                    "KEEP_LOCAL_INCIDENT_TEST": "1",
                    "KEEP_SMOKE_S4_CLEANUP": "1",
                    "KEEP_SMOKE_LEDGER": str(path),
                    "KEEP_SMOKE_RUN": str(record.get("run")),
                },
                timeout=max(min(120, self.remaining() - 5), 5),
            )
            results.append(
                {
                    "ledger": str(path),
                    "incident": record.get("incident_id"),
                    "result": scenario.result,
                    "reason": scenario.reason,
                }
            )
        return results

    def repair(self, stage: str) -> dict:
        """At start: earlier runs' leftovers. At the end: this run's own."""
        out: dict = {}
        if stage == "start" or self.s1_attempted:
            out["membership"] = self.repair_membership()
        ledgers = [
            path
            for path in pending_s4_cleanups(self.paths.verification)
            if (path.parent.name == self.run_id) == (stage == "end")
        ]
        if ledgers:
            out["s4_cleanup"] = self.repair_s4(ledgers)
        return out

    # ---- report

    def checkouts(self) -> list[dict]:
        stack_workspace = self.paths.stack_delivery.parent
        stack_keys = {c.key for c in smoke_state.main_checkouts(stack_workspace)}
        seen: dict[str, smoke_state.Checkout] = {}
        for checkout in smoke_state.all_checkouts(stack_workspace) + smoke_state.workspace_children(
            self.paths.workspace
        ):
            seen.setdefault(checkout.key, checkout)
        rows = []
        for checkout in seen.values():
            try:
                rows.append(
                    {
                        "path": str(checkout.path),
                        "key": checkout.key,
                        "repo": checkout.repo,
                        "main": checkout.main,
                        "stack": checkout.key in stack_keys,
                        **smoke_state.head(checkout),
                        "fingerprint": smoke_state.fingerprint(checkout),
                        "work_in_progress": smoke_state.work_in_progress(checkout),
                    }
                )
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                rows.append(
                    {
                        "path": str(checkout.path),
                        "key": checkout.key,
                        "repo": checkout.repo,
                        "error": plain(str(error)),
                    }
                )
        return rows

    def write_ledger(self, report: dict) -> None:
        self.ledger.mkdir(parents=True, exist_ok=True)
        (self.ledger / "ledger.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    def report(
        self, result: str, checkouts: list[dict], change: tuple[str, str], finished: bool
    ) -> dict:
        return {
            "run_id": self.run_id,
            "result": result,
            "algorithm_version": smoke_state.ALGORITHM_VERSION,
            "started_at": datetime.fromtimestamp(self.started_wall, timezone.utc).isoformat(
                timespec="seconds"
            ),
            "finished_at": now_iso() if finished else None,
            "duration_seconds": round(self.clock() - self.start, 1),
            "workspace": str(self.paths.workspace),
            "stack": {
                "delivery": str(self.paths.stack_delivery),
                "frontend": str(self.paths.stack_frontend),
            },
            "scenarios": {name: asdict(scenario) for name, scenario in self.scenarios.items()},
            "readiness": self.readiness,
            "repairs": self.repairs,
            "stack_includes_change": {"value": change[0], "reason": change[1]},
            "checkouts": [c for c in checkouts if "fingerprint" in c],
            "checkout_errors": [c for c in checkouts if "error" in c],
            "ledger": str(self.ledger),
        }

    # ---- the run

    def block_all(self, reason: str, names=SCENARIOS) -> None:
        for name in names:
            if self.scenarios[name].result == "not run":
                self.scenarios[name] = Scenario(result="blocked", reason=reason)

    def run(self) -> dict:
        self.ledger.mkdir(parents=True, exist_ok=True)
        checkouts = self.checkouts()
        change = stack_includes_change(
            checkouts,
            process_starts(self.paths.stack_delivery),
            lambda repo: editable_source(self.paths.stack_delivery, repo),
        )
        self.write_ledger(self.report("running", checkouts, change, finished=False))
        blocked = self.acquire_lock()
        try:
            if blocked:
                self.block_all(blocked)
            else:
                problems = self.check_readiness()
                if problems:
                    self.block_all("stack not ready: " + "; ".join(problems))
                else:
                    self.repairs["start"] = self.repair("start")
                    self.run_scenarios()
        finally:
            if self.lock_held:
                if not any(
                    s.result == "blocked" and s.reason.startswith("stack not ready")
                    for s in self.scenarios.values()
                ):
                    self.repairs["end"] = self.repair("end")
                self.release_lock()
        results = [s.result for s in self.scenarios.values()]
        if all(r == "passed" for r in results):
            result = "passed"
        elif all(r in ("blocked", "not run") for r in results):
            result = "blocked"
        else:
            result = "failed"
        report = self.report(result, checkouts, change, finished=True)
        self.write_ledger(report)
        self.paths.reports.mkdir(parents=True, exist_ok=True)
        (self.paths.reports / "reports").mkdir(exist_ok=True)
        text = json.dumps(report, indent=2)
        (self.paths.reports / "reports" / f"{self.run_id}.json").write_text(text, encoding="utf-8")
        (self.paths.reports / "latest.json").write_text(text, encoding="utf-8")
        return report

    def run_scenarios(self) -> None:
        order = [("S1", self.run_s1), ("S2", self.run_s2), ("S3", self.run_s3)]
        for name, step in order:
            if name != "S1" and self.scenarios["S1"].result != "passed":
                self.block_all(f"S1 {self.scenarios['S1'].result}; identity must pass first")
                return
            if self.scenario_time() < 20:
                self.block_all("the smoke time budget ran out")
                return
            self.scenarios[name] = step()
        if self.scenario_time() < 20:
            self.block_all("the smoke time budget ran out")
            return
        self.scenarios["S4"] = self.run_s4(self.scenarios["S2"])


# --------------------------------------------------------------------- CLI


def summary(report: dict, exit_code: int) -> str:
    lines = [
        f"run_id: {report['run_id']}",
        f"result: {report['result']} (exit {exit_code}) in {report['duration_seconds']} s",
        "",
        "| Scenario | Result | Passed | Failed | Skipped | Seconds | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, scenario in report["scenarios"].items():
        note = scenario["reason"].replace("|", "/").replace("\n", " ")
        lines.append(
            f"| {name} | {scenario['result']} | {scenario['passed']} | {scenario['failed']} | "
            f"{scenario['skipped']} | {scenario['seconds']} | {note} |"
        )
    change = report["stack_includes_change"]
    lines += [
        "",
        f"stack_includes_change: {change['value']} ({change['reason']})",
        f"repairs: {json.dumps(report['repairs'])}",
        f"report: {Path(report['workspace']) / 'keep-delivery/.local/smoke/reports' / (report['run_id'] + '.json')}",
        f"ledger: {report['ledger']}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="run S1-S4 and write the report")
    run.add_argument(
        "--workspace",
        type=Path,
        default=here.parent,
        help="workspace whose report and checkouts to record",
    )
    run.add_argument(
        "--stack-delivery",
        type=Path,
        default=here,
        help="development only: the stack's keep-delivery",
    )
    run.add_argument(
        "--stack-frontend", type=Path, help="development only: the stack's keep-frontend"
    )
    run.add_argument("--api-url", default="http://127.0.0.1:58000")
    run.add_argument("--ingestion-url", default="http://127.0.0.1:58001")
    run.add_argument("--ui-url", default="https://localhost:3443")
    run.add_argument("--adapi-url", default="http://127.0.0.1:58092")
    run.add_argument("--budget", type=float, default=BUDGET_SECONDS)
    args = parser.parse_args(argv)
    stack_delivery = args.stack_delivery.resolve()
    paths = Paths(
        workspace=args.workspace.resolve(),
        stack_delivery=stack_delivery,
        stack_frontend=(args.stack_frontend or stack_delivery.parent / "keep-frontend").resolve(),
    )
    runner = Runner(
        paths,
        api_url=args.api_url,
        ingestion_url=args.ingestion_url,
        ui_url=args.ui_url,
        adapi_url=args.adapi_url,
        budget=args.budget,
    )
    report = runner.run()
    code = {"passed": 0, "failed": 1}.get(report["result"], 2)
    print(summary(report, code))
    return code


if __name__ == "__main__":
    sys.exit(main())
