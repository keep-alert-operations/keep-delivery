"""Real PostgreSQL erasure, dependency and stale-worker fences."""
# ruff: noqa: F811

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from appchi.operators.cleanup import cleanup_operation, cleanup_turn
from appchi.operators.executor_ledger import PostgresLedger
from appchi.operators.worker import Work, VerifiedResult, complete
from appchi.recovery.command_retention import compact_ordinary_command
from appchi.shared.errors import AppError
from appchi.shared.transactions import coordinator
from test_command_retention import Store, seed as receipt_seed
from test_maintenance_repository import maintenance_db  # noqa: F401
from test_operator_repository import operators  # noqa: F401
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_db(maintenance_db):
    db, _ = maintenance_db
    with db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
    return db


def seed(
    db, tenant, *, operator=None, kind="setup", state="completed", days=91, progress=None, steps=0
):
    identifier = uuid4()
    with db.transaction() as conn:
        if operator is None:
            operator = uuid4()
            conn.execute(
                "INSERT INTO operators(id,tenant_id,name,name_key,lifecycle) VALUES(%s,%s,%s,%s,'ready')",
                (operator, tenant, str(operator), str(operator)),
            )
        conn.execute(
            """INSERT INTO operator_operations(id,tenant_id,operator_id,kind,generation,state,
            actor_id,actor_label,progress,attempts,error_code,completed_at)
            VALUES(%s,%s,%s,%s,1,%s,%s,'Sensitive actor',%s,9,'diagnostic-marker',
              CASE WHEN %s::integer IS NULL THEN NULL ELSE clock_timestamp()-%s*interval '24 hours' END)""",
            (
                identifier,
                tenant,
                operator,
                kind,
                state,
                uuid4(),
                Jsonb(progress or {"executor": {"labels": [{"uid": "journal-marker"}]}}),
                days,
                days,
            ),
        )
        for _ in range(steps):
            conn.execute(
                """INSERT INTO operator_steps(id,tenant_id,operator_id,operation_id,generation,
                step,outcome,recorded_at) VALUES(%s,%s,%s,%s,1,'remote','diagnostic',clock_timestamp())""",
                (uuid4(), tenant, operator, identifier),
            )
    return operator, identifier


def row(db, identifier):
    with db.transaction() as conn:
        return conn.execute(
            "SELECT * FROM operator_operations WHERE id=%s", (identifier,)
        ).fetchone()


class RoleDB:
    def __init__(self, db, role):
        self.db, self.role = db, role

    @contextmanager
    def transaction(self, tenant=None):
        with self.db.transaction(tenant) as conn:
            conn.execute(
                psycopg.sql.SQL("SET LOCAL ROLE {}").format(psycopg.sql.Identifier(self.role))
            )
            yield conn


def test_bounded_resume_keeps_minimal_fence_and_rejects_late_workers(real_db, tenant):
    operator, operation = seed(real_db, tenant, steps=3)
    with real_db.transaction() as conn:
        credential = uuid4()
        conn.execute(
            """INSERT INTO operator_credentials(id,tenant_id,operator_id,state,verifier,ciphertext)
            VALUES(%s,%s,%s,'revoked',%s,NULL)""",
            (credential, tenant, operator, uuid4().bytes),
        )
        conn.execute(
            "UPDATE operator_operations SET candidate_id=%s WHERE id=%s", (credential, operation)
        )
        conn.execute(
            """INSERT INTO operator_activity(id,tenant_id,operator_id,actor_label,kind,evidence)
            VALUES(%s,%s,%s,'Audit actor','audit-marker','{}')""",
            (uuid4(), tenant, operator),
        )
        conn.execute(
            """INSERT INTO operator_remote_bindings(tenant_id,operator_id,kind,remote_id,generation,verified_at)
            VALUES(%s,%s,'receiver','binding-marker',1,clock_timestamp())""",
            (tenant, operator),
        )
    before = row(real_db, operation)
    worker = RoleDB(real_db, "appchi_worker")
    assert not cleanup_operation(worker, tenant, operator, operation, limit=1)
    assert not cleanup_operation(worker, tenant, operator, operation, limit=1)
    assert cleanup_operation(worker, tenant, operator, operation, limit=1)
    after = row(real_db, operation)
    assert after["progress"] == {} and after["actor_id"] is None and after["actor_label"] is None
    assert (
        after["error_code"] is None and after["attempts"] == 0 and after["diagnostics_retired_at"]
    )
    for key in (
        "id",
        "tenant_id",
        "operator_id",
        "kind",
        "generation",
        "candidate_id",
        "completed_at",
    ):
        assert after[key] == before[key]
    assert cleanup_operation(worker, tenant, operator, operation)
    with real_db.transaction() as conn:
        assert conn.execute(
            "SELECT 1 FROM operator_credentials WHERE id=%s", (credential,)
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM operator_activity WHERE operator_id=%s", (operator,)
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM operator_remote_bindings WHERE operator_id=%s", (operator,)
        ).fetchone()
    assert not complete(worker, before, VerifiedResult())
    work = Work(
        str(operation), str(operator), str(tenant), 1, "setup", "name", None, None, None, False
    )
    with pytest.raises(AppError, match="stale_provisioning_generation"):
        PostgresLedger(worker).begin(work, "synthetic-profile")
    with pytest.raises(psycopg.errors.CheckViolation), worker.transaction(tenant) as conn:
        conn.execute(
            "UPDATE operator_operations SET completed_at=NULL,state='pending' WHERE id=%s",
            (operation,),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege), worker.transaction(tenant) as conn:
        conn.execute("DELETE FROM operator_operations WHERE id=%s", (operation,))
    with pytest.raises(psycopg.errors.CheckViolation), worker.transaction(tenant) as conn:
        conn.execute(
            """INSERT INTO operator_steps(id,tenant_id,operator_id,operation_id,generation,step,outcome,recorded_at)
            VALUES(%s,%s,%s,%s,1,'late','invalid',clock_timestamp())""",
            (uuid4(), tenant, operator, operation),
        )


@pytest.mark.parametrize(
    "state,days", [("completed", 89), ("pending", 100), ("running", None), ("needs_attention", 200)]
)
def test_unfinished_or_young_diagnostics_never_age_away(real_db, tenant, state, days):
    operator, operation = seed(real_db, tenant, state=state, days=days)
    assert not cleanup_operation(real_db, tenant, operator, operation)
    assert row(real_db, operation)["progress"]


@pytest.mark.parametrize("classified", [False, True])
def test_command_result_dependency_released_only_after_protected_compaction(
    real_db, tenant, classified
):
    operator, operation = seed(real_db, tenant)
    key = receipt_seed(
        real_db, tenant, "operator-create", result={"data": {"operation": {"id": str(operation)}}}
    )
    if classified:
        with real_db.transaction() as conn:
            conn.execute(
                "INSERT INTO command_retention(scope,command_id,tenant_id,family,operator_operation_id) VALUES(%s,%s,%s,'operator',%s)",
                (*key[:2], tenant, operation),
            )
    assert not cleanup_operation(real_db, tenant, operator, operation)
    assert compact_ordinary_command(real_db, Store(), *key[:2])
    assert cleanup_operation(real_db, tenant, operator, operation)


@pytest.mark.parametrize(
    "kind,progress,pending_name",
    [
        ("cancel", {"cancel_kind": "setup"}, False),
        ("cleanup", {}, True),
        ("create_key", {"restore_cancel": True, "cancel_kind": "setup"}, False),
        ("cancel", {"cancel_kind": "setup", "explicit": True}, False),
    ],
)
def test_pending_executor_predecessor_dependencies(real_db, tenant, kind, progress, pending_name):
    operator, operation = seed(real_db, tenant)
    progress = dict(progress)
    if progress.pop("explicit", False):
        progress["cancel_operation_id"] = str(operation)
    _, pending = seed(
        real_db, tenant, operator=operator, kind=kind, state="pending", days=None, progress=progress
    )
    if pending_name:
        with real_db.transaction() as conn:
            conn.execute("UPDATE operators SET pending_name='reserved' WHERE id=%s", (operator,))
    assert not cleanup_operation(real_db, tenant, operator, operation)
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE operator_operations SET state='completed',completed_at=clock_timestamp() WHERE id=%s",
            (pending,),
        )
        conn.execute("UPDATE operators SET pending_name=NULL WHERE id=%s", (operator,))
    assert cleanup_operation(real_db, tenant, operator, operation)


def test_pending_name_preserves_rename_journal_before_future_cancel(real_db, tenant):
    operator, setup = seed(real_db, tenant)
    _, rename = seed(real_db, tenant, operator=operator, kind="rename", state="superseded")
    _, deleted = seed(real_db, tenant, operator=operator, kind="delete", state="pending", days=None)
    with real_db.transaction() as conn:
        conn.execute("UPDATE operators SET pending_name='reserved' WHERE id=%s", (operator,))
    for operation in (setup, rename):
        assert not cleanup_operation(real_db, tenant, operator, operation)
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE operator_operations SET state='cancelled',completed_at=clock_timestamp() WHERE id=%s",
            (deleted,),
        )
        conn.execute("UPDATE operators SET pending_name=NULL WHERE id=%s", (operator,))
    assert cleanup_operation(real_db, tenant, operator, setup)
    assert cleanup_operation(real_db, tenant, operator, rename)


def test_api_hides_expired_terminal_diagnostics_but_keeps_unfinished(operators, real_db):
    client, base, tenant = operators
    operator, expired = seed(real_db, tenant)
    _, pending = seed(real_db, tenant, operator=operator, state="needs_attention", days=100)
    path = f"{base}/{operator}/operations/"
    assert client.get(path + str(expired)).status_code == 404
    assert client.get(path + str(pending)).status_code == 200
    api = RoleDB(real_db, "appchi_api")
    assert cleanup_operation(real_db, tenant, operator, expired)
    with pytest.raises(psycopg.errors.CheckViolation), api.transaction(tenant) as conn:
        conn.execute(
            "UPDATE operator_operations SET actor_label='restored' WHERE id=%s", (expired,)
        )
    with api.transaction(uuid4()) as conn:
        assert (
            conn.execute("SELECT id FROM operator_operations WHERE id=%s", (expired,)).fetchone()
            is None
        )


def test_coordinator_serializes_new_predecessor_reference_with_cleanup(real_db, tenant):
    operator, operation = seed(real_db, tenant)
    started = Event()

    def erase():
        started.set()
        return cleanup_operation(real_db, tenant, operator, operation)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with real_db.transaction(tenant) as conn:
            coordinator(conn, tenant)
            conn.execute("SELECT id FROM operators WHERE id=%s FOR UPDATE", (operator,))
            future = pool.submit(erase)
            assert started.wait(1)
            conn.execute(
                """INSERT INTO operator_operations(id,tenant_id,operator_id,kind,generation,actor_label,progress)
                VALUES(%s,%s,%s,'cancel',1,'Actor',%s)""",
                (
                    uuid4(),
                    tenant,
                    operator,
                    Jsonb({"cancel_kind": "setup", "cancel_operation_id": str(operation)}),
                ),
            )
        assert not future.result(timeout=5)
    assert row(real_db, operation)["diagnostics_retired_at"] is None


def test_scan_is_bounded_and_resumable(real_db, tenant):
    seeded = [seed(real_db, tenant) for _ in range(3)]
    cursor, seen = None, 0
    for _ in range(100):
        result = cleanup_turn(real_db, cursor=cursor, limit=1, step_limit=1)
        assert result.attempted <= 1
        seen += result.completed
        cursor = result.cursor
        if cursor is None:
            break
    assert seen >= 3
    assert all(row(real_db, operation)["diagnostics_retired_at"] for _, operation in seeded)


def test_unready_recovery_prevents_diagnostics_erasure(real_db, tenant):
    operator, operation = seed(real_db, tenant)
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='recovery'")
    try:
        with pytest.raises(AppError):
            cleanup_operation(real_db, tenant, operator, operation)
    finally:
        with real_db.transaction() as conn:
            conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
    assert row(real_db, operation)["progress"]
