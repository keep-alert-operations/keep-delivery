"""Offline review reproductions; no services, migrations or source modifications.

Run from delivery: .venv/Scripts/python.exe -B qualification/scalability-review-2026-10-06.py
SQL/context spies verify control flow and operation counts, not database capacity.
"""

import ast
import asyncio
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import runpy
import re
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from appchi.maintenance.processing import Catalog, reconcile_alert
from appchi.maintenance.temporal import MaintenanceProofError
from appchi.search.projection import Projection, build_document, canonical
from appchi.search.writer import ProjectionWriter
from appchi.shared.errors import AppError

WORKSPACE = Path(__file__).resolve().parents[2]


def maintenance_cap():
    class Cursor:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return self.value

        def fetchall(self):
            return self.value

    class Connection:
        def execute(self, query, params):
            if "maintenance_generation FROM tenant_view_state" in query:
                return Cursor({"maintenance_generation": 1})
            if "FROM maintenance_alert_inputs" in query:
                return Cursor([{}] * 101)
            raise AssertionError(query)

    tenant, alert = uuid4(), uuid4()
    try:
        reconcile_alert(
            Connection(), {"tenant_id": tenant, "id": alert}, datetime.now(UTC),
            catalog=Catalog(tenant, 1, (), 10000, 100),
        )
    except MaintenanceProofError as error:
        assert str(error) == "maintenance_input_allocation_exceeded"
    else:
        raise AssertionError("Expected maintenance cap rejection")
    return {"receipts": 101, "cap": 100, "segments": 0,
            "error": "maintenance_input_allocation_exceeded"}


def projection_cap():
    fixture = runpy.run_path(str(WORKSPACE / "appchi-indexer/tests/unit/test_search.py"))["snapshot"]
    notes = [" ".join(f"n{i:03d}_{j:04d}" for j in range(900)) for i in range(120)]
    item = Projection("tenant", 1, "a", 1, fixture(notes=notes))
    document_bytes = len(canonical(build_document(item)))
    client = Mock()
    try:
        ProjectionWriter(client, "index", "epoch", max_document_bytes=1048576).publish(
            [item], guard=lambda *args: None, covered_by_newer=lambda *args: False,
        )
    except AppError as error:
        assert error.code == "projection_document_capacity"
    else:
        raise AssertionError("Expected projection cap rejection")
    client.bulk.assert_not_called()
    return {"notes": len(notes), "max_note_codepoints": max(map(len, notes)),
            "document_bytes": document_bytes, "error": "projection_document_capacity"}


def evidence_rewrite():
    path = WORKSPACE / "appchi-domain/src/appchi/correlation/runtime.py"
    node = next(n for n in ast.parse(path.read_text(encoding="utf-8")).body
                if isinstance(n, ast.FunctionDef) and n.name == "_persist")
    repo = SimpleNamespace(ensure_context=Mock(return_value="context"))
    namespace = {"repo": repo}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    rule = SimpleNamespace(id="r", epoch=1, health="healthy")
    members = [SimpleNamespace(context=SimpleNamespace(group="g"), condition_id="c",
                               alert_id=f"a{i}", input_id=f"i{i}", start=0, expiry=1,
                               observed_revision=1) for i in range(1000)]
    prepared = SimpleNamespace(tenant="t", rows=[dict(id="r", blocked_reason="", health="healthy")],
                               capacity={"digest_bucket": 1})
    conn = Mock()
    namespace["_persist"](conn, prepared,
                          [SimpleNamespace(rule=rule, memberships=members, blocked_reason="")])
    assert conn.execute.call_count == 1001
    assert repo.ensure_context.call_count == 1000
    return {"memberships": len(members), "direct_sql": conn.execute.call_count,
            "context_lookups": repo.ensure_context.call_count}


def migration_checksums():
    directory = WORKSPACE / "appchi-migrations/src/appchi/migration_sql"
    ledger = json.loads((directory / "checksums.json").read_text())
    files = {path.name: path for path in directory.glob("*.sql")}
    assert set(files) == set(ledger)
    for name, expected in ledger.items():
        assert hashlib.sha256(files[name].read_bytes()).hexdigest() == expected, name
    return {"sql_files": len(files), "exact_ledger_membership": True, "checksums_match": True}


def live_lock():
    path = WORKSPACE / "appchi-api/src/appchi/live.py"
    node = next(n for n in ast.parse(path.read_text(encoding="utf-8")).body
                if isinstance(n, ast.ClassDef) and n.name == "LiveRegistry")
    namespace = {"asyncio": asyncio, "RLock": threading.RLock, "time": time}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    registry = namespace["LiveRegistry"]()
    registry.register("tenant")
    started = threading.Event()

    def slow_load():
        started.set()
        time.sleep(0.25)
        return {"view_generation": 1}

    thread = threading.Thread(target=lambda: registry.read("tenant", slow_load))
    thread.start()
    assert started.wait(2)
    began = time.perf_counter()
    registry.invalidate("tenant")
    elapsed = time.perf_counter() - began
    thread.join()
    assert elapsed >= 0.20
    return {"simulated_load_seconds": 0.25, "blocked_seconds": round(elapsed, 3)}


def activity_timer():
    ui = (WORKSPACE / "appchi-frontend/src/components/workspace.tsx").read_text(encoding="utf-8")
    auth = (WORKSPACE / "appchi-domain/src/appchi/identity/authentication.py").read_text(encoding="utf-8")
    poll_ms = int(re.search(r"setInterval\(check,\s*(\d+)\)", ui).group(1))
    activity = re.search(r'void request\("/auth/activity"[\s\S]*?\},\s*(\d+)\);'
                         r"\s*return[^\n]*\n\s*\},\s*\[session, onDenied\]\)", ui)
    assert activity and "setSession(result.data)" in ui
    activity_ms = int(activity.group(1))
    idle_minutes = int(re.search(r"s\.activity_at>clock_timestamp\(\)-interval '(\d+) minutes'", auth).group(1))
    next_poll, next_activity, callbacks = poll_ms, activity_ms, 0
    while min(next_poll, next_activity) <= idle_minutes * 60_000:
        if next_poll <= next_activity:
            next_activity = next_poll + activity_ms
            next_poll += poll_ms
        else:
            callbacks += 1
            next_activity += activity_ms
    assert callbacks == 0
    return {"poll_ms": poll_ms, "activity_ms": activity_ms,
            "modeled_minutes": idle_minutes, "activity_callbacks": callbacks}


if __name__ == "__main__":
    print(json.dumps({"maintenance": maintenance_cap(), "projection": projection_cap(),
                      "correlation": evidence_rewrite(), "migrations": migration_checksums(),
                      "live_lock": live_lock(), "activity_timer": activity_timer()}, indent=2))
