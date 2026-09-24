"""Real SQL retention/holds; independent evidence is a controlled local fixture."""
# ruff: noqa: F811

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb
import pytest

from appchi.ingestion.native import canonical
from appchi.recovery.command_retention import (
    expired,
    compact_ordinary_command,
    command_retention_turn,
)
from appchi.recovery.evidence import DurableAck
from appchi.shared.errors import AppError, unavailable
from appchi.shared.transactions import command_result
from test_search_pipeline import real_db as isolated_db
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def real_db():
    for db in isolated_db.__wrapped__():
        draft = Path(".local/0029_command_retention.sql.draft")
        if draft.exists():
            with db.transaction() as conn:
                conn.execute(draft.read_text(encoding="utf-8"))
        with db.transaction() as conn:
            conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
        yield db


class Store:
    def __init__(self):
        self.events = []
        self.callback = None
        self.fail = None
        self.bad_ack = False

    def append(self, evidence):
        self.events.append(evidence)
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        if evidence.state == self.fail:
            raise unavailable("controlled_evidence_outage")
        return DurableAck(
            evidence.id, "fixture", "checkpoint", "0" * 64 if self.bad_ack else evidence.digest
        )


def seed(db, tenant, family="presets:create", *, result=None, days=100, status=200):
    scope, command = f"{tenant}:{uuid4()}:{family}", str(uuid4())
    payload = {"command_id": command, "name": "private command request"}
    result = result or {"data": {"id": str(uuid4()), "name": "private response marker"}}
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO command_receipts(scope,command_id,fingerprint,result,status,completed_at)
                     VALUES(%s,%s,%s,%s,%s,clock_timestamp()-%s*interval '1 day')""",
            (scope, command, sha256(canonical(payload)).digest(), Jsonb(result), status, days),
        )
    return scope, command, payload


def receipt(db, key):
    with db.transaction() as conn:
        return conn.execute(
            "SELECT * FROM command_receipts WHERE scope=%s AND command_id=%s", key[:2]
        ).fetchone()


def test_age_boundary_unknown_and_parent_owned_results(real_db, tenant):
    young = seed(real_db, tenant, days=89)
    old = seed(real_db, tenant, days=90)
    unknown = seed(real_db, tenant, "unregistered-family")
    parent = seed(real_db, tenant, "incident-create")
    misleading = seed(real_db, tenant)
    with real_db.transaction() as conn:
        incident = uuid4()
        conn.execute(
            "INSERT INTO incident_identities(id,tenant_id) VALUES(%s,%s)", (incident, tenant)
        )
        conn.execute(
            "INSERT INTO command_resources(scope,command_id,tenant_id,resource_kind,resource_id) VALUES(%s,%s,%s,'incident',%s)",
            (*misleading[:2], tenant, incident),
        )
    for key, expected in (
        (young, False),
        (old, True),
        (unknown, False),
        (parent, False),
        (misleading, False),
    ):
        row = receipt(real_db, key)
        with real_db.transaction() as conn:
            assert expired(conn, row) is expected
    assert not compact_ordinary_command(real_db, Store(), *parent[:2])


def test_operator_pending_and_original_terminal_clock(real_db, tenant):
    operator, operation = uuid4(), uuid4()
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO operators(id,tenant_id,name,name_key,lifecycle) VALUES(%s,%s,%s,%s,'ready')",
            (operator, tenant, str(operator), str(operator)),
        )
        conn.execute(
            "INSERT INTO operator_operations(id,tenant_id,operator_id,kind,generation,actor_label) VALUES(%s,%s,%s,'setup',1,'test')",
            (operation, tenant, operator),
        )
    key = seed(
        real_db,
        tenant,
        "operator-create",
        status=202,
        result={"data": {"operation": {"id": str(operation)}}},
    )
    with real_db.transaction() as conn:
        assert not expired(conn, receipt(real_db, key))
        conn.execute(
            "UPDATE operator_operations SET state='completed',completed_at=clock_timestamp()-interval '89 days' WHERE id=%s",
            (operation,),
        )
    assert not compact_ordinary_command(real_db, Store(), *key[:2])
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE operator_operations SET completed_at=clock_timestamp()-interval '91 days' WHERE id=%s",
            (operation,),
        )
        conn.execute(
            "INSERT INTO operator_operations(id,tenant_id,operator_id,kind,generation,actor_label) VALUES(%s,%s,%s,'rotate',2,'test')",
            (uuid4(), tenant, operator),
        )
    assert compact_ordinary_command(real_db, Store(), *key[:2])
    with real_db.transaction() as conn:
        # The permanent command fence must not retain an entire terminal job.
        assert (
            conn.execute("DELETE FROM operator_operations WHERE id=%s", (operation,)).rowcount == 1
        )


@pytest.mark.parametrize("mode", ["intent", "final", "bad_ack"])
def test_failed_evidence_keeps_body_and_stable_retry_identity(real_db, tenant, mode):
    key, store = seed(real_db, tenant), Store()
    store.fail, store.bad_ack = (mode, False) if mode != "bad_ack" else (None, True)
    with pytest.raises(AppError):
        compact_ordinary_command(real_db, store, *key[:2])
    assert receipt(real_db, key)["result"] is not None
    first_intent = store.events[0]
    store.fail, store.bad_ack = None, False
    assert compact_ordinary_command(real_db, store, *key[:2])
    assert store.events[-2] == first_intent
    assert receipt(real_db, key)["result"] is None


def test_hold_added_during_remote_protection_prevents_erasure(real_db, tenant):
    key, store = seed(real_db, tenant), Store()

    def add_hold():
        with real_db.transaction() as conn:
            conn.execute(
                "INSERT INTO command_retention_holds(scope,command_id,kind,reference) VALUES(%s,%s,'recovery','drill')",
                key[:2],
            )

    store.callback = add_hold
    assert not compact_ordinary_command(real_db, store, *key[:2])
    assert receipt(real_db, key)["result"] is not None
    with real_db.transaction() as conn:
        assert not expired(conn, receipt(real_db, key))
        conn.execute(
            "UPDATE command_retention_holds SET released_at=clock_timestamp() WHERE scope=%s AND command_id=%s",
            key[:2],
        )
    assert compact_ordinary_command(real_db, store, *key[:2])
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        add_hold()


def test_recovery_gate_changed_during_protection_keeps_body(real_db, tenant):
    key, store = seed(real_db, tenant), Store()

    def gate():
        with real_db.transaction() as conn:
            conn.execute("UPDATE capability_gates SET ready=false WHERE capability='recovery'")

    store.callback = gate
    with pytest.raises(AppError):
        compact_ordinary_command(real_db, store, *key[:2])
    assert receipt(real_db, key)["result"] is not None


def test_concurrent_compactors_share_fence_and_permanent_receipt(real_db, tenant):
    key, store = seed(real_db, tenant), Store()
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(lambda _: compact_ordinary_command(real_db, store, *key[:2]), range(2))
        )
    assert any(results)
    with real_db.transaction() as conn:
        jobs = conn.execute(
            "SELECT * FROM command_content_retirements WHERE scope=%s AND command_id=%s", key[:2]
        ).fetchall()
        assert len(jobs) == 1 and jobs[0]["state"] == "cleaned"
    assert receipt(real_db, key)["result"] is None
    with pytest.raises(AppError, match="command_result_expired"):
        with real_db.transaction() as conn:
            command_result(conn, *key)
    with pytest.raises(AppError, match="idempotency_conflict"):
        with real_db.transaction() as conn:
            command_result(conn, *key[:2], {**key[2], "name": "changed"})


def test_worker_role_bounded_scan(real_db, tenant):
    keys = [seed(real_db, tenant) for _ in range(3)]
    original = real_db.transaction

    @contextmanager
    def role(tenant_id=None):
        with original(tenant_id) as conn:
            conn.execute("SET LOCAL ROLE appchi_worker")
            yield conn

    real_db.transaction = role
    try:
        first = command_retention_turn(real_db, Store(), limit=1)
        assert first.attempted == first.completed == 1 and first.cursor
        while first.cursor:
            first = command_retention_turn(real_db, Store(), cursor=first.cursor, limit=1)
    finally:
        real_db.transaction = original
    assert all(receipt(real_db, key)["result"] is None for key in keys)


def test_delete_pending_proof_and_copied_receipt_erasure(real_db, tenant):
    key = seed(real_db, tenant, "dashboards:delete")
    job = uuid4()
    row = receipt(real_db, key)
    with real_db.transaction() as conn:
        conn.execute(
            """INSERT INTO configuration_delete_jobs(id,tenant_id,resource_kind,resource_id,scope,command_id,
        fingerprint,expected_revision,actor_id,actor_label,intent_id,final_id,cancel_id,digest,state,receipt,created_at,updated_at)
        VALUES(%s,%s,'dashboard',%s,%s,%s,%s,1,%s,'test',%s,%s,%s,%s,'deleted',%s,clock_timestamp()-interval '100 days',clock_timestamp()-interval '100 days')""",
            (
                job,
                tenant,
                uuid4(),
                *key[:2],
                row["fingerprint"],
                uuid4(),
                uuid4(),
                uuid4(),
                uuid4(),
                "a" * 64,
                Jsonb(row["result"]),
            ),
        )
    assert not compact_ordinary_command(real_db, Store(), *key[:2])
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE configuration_delete_jobs SET state='final_protected',final_ack=%s WHERE id=%s",
            (Jsonb({"proof": "controlled"}), job),
        )
    assert compact_ordinary_command(real_db, Store(), *key[:2])
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT receipt FROM configuration_delete_jobs WHERE id=%s", (job,)
            ).fetchone()["receipt"]
            is None
        )


def test_api_role_expiry_before_cleanup_and_conflict_precedence(real_db, tenant):
    key = seed(real_db, tenant)
    with pytest.raises(AppError, match="command_result_expired"):
        with real_db.transaction(tenant) as conn:
            conn.execute("SET LOCAL ROLE appchi_api")
            command_result(conn, *key)
    assert receipt(real_db, key)["result"] is not None
    with pytest.raises(AppError, match="idempotency_conflict"):
        with real_db.transaction(tenant) as conn:
            conn.execute("SET LOCAL ROLE appchi_api")
            command_result(conn, *key[:2], {**key[2], "name": "different"})


def test_http_preset_save_registers_and_expired_retry_cannot_reexecute(browser, real_db):
    client, _ = browser
    with real_db.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    body = {
        "command_id": str(uuid4()),
        "name": "Completed preset",
        "cel": "true",
        "sort": [{"field": "timestamp", "direction": "desc"}],
        "columns": ["message"],
    }
    original = real_db.transaction

    @contextmanager
    def api_role(tenant_id=None):
        with original(tenant_id) as conn:
            conn.execute("SET LOCAL ROLE appchi_api")
            yield conn

    real_db.transaction = api_role
    try:
        first = client.post(f"/api/tenants/{tenant}/presets", json=body)
        assert first.status_code == 201, first.text
    finally:
        real_db.transaction = original
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT family FROM command_retention WHERE command_id=%s", (body["command_id"],)
            ).fetchone()["family"]
            == "preset"
        )
        conn.execute(
            "UPDATE command_receipts SET completed_at=clock_timestamp()-interval '91 days' WHERE command_id=%s",
            (body["command_id"],),
        )
    response = client.post(f"/api/tenants/{tenant}/presets", json=body)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "command_result_expired"
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM presets WHERE id=%s", (first.json()["data"]["id"],)
            ).fetchone()["n"]
            == 1
        )
