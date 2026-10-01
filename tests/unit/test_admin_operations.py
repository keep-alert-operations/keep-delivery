# ruff: noqa: F811
"""Real PostgreSQL repair probes; shares the maintenance suite's isolated fixtures."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from appchi.admin_operations import router
from appchi.alerts.processor import process_one, record_failure
from appchi.ingestion.native import canonical
from appchi.ingestion.service import Admission

from test_maintenance_repository import maintenance_browser  # noqa: F401
from test_maintenance_repository import maintenance_db as database_fixture

pytestmark = pytest.mark.integration


@pytest.fixture
def maintenance_db():
    # Each case owns a database, including its fixed Global native-v1 destination.
    yield from database_fixture.__wrapped__()


@pytest.fixture
def admin(maintenance_browser, maintenance_db):
    client, _, principal, app = maintenance_browser
    db, _ = maintenance_db
    if not any(getattr(route, "name", "") == "retry" for route in app.routes):
        app.include_router(router)
    with db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (principal,))
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='maintenance'")
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    allocation = {
        "id": "repair-test",
        "global_items": 10000,
        "tenant_items": 10000,
        "global_bytes": 10**8,
        "tenant_bytes": 10**8,
        "byte_multiplier": 4,
        "item_overhead": 2048,
    }
    service = Admission(db, app.state.settings.cipher(), allocation)
    payload = {
        "application": "source-" + str(uuid4()),
        "object": "cpu",
        "node_name": "host",
        "message": "sensitive-source-body",
        "operator": "legacy",
        "time_created": "22-09-2026 00:00:00",
        "severity": "error",
    }
    status, _ = service.admit(canonical(payload), "v1")
    assert status == 202
    with db.transaction(tenant) as conn:
        head = conn.execute(
            "SELECT * FROM ingest_items WHERE tenant_id=%s AND disposition='pending'", (tenant,)
        ).fetchone()
    for _ in range(10):
        record_failure(db, tenant, head["id"])
    return client, db, str(tenant), principal, head, app


def command(generation="1", reason="Capacity proof repaired and reviewed"):
    return {"command_id": str(uuid4()), "expected_block_generation": generation, "reason": reason}


def path(tenant):
    return f"/api/admin/tenants/{tenant}/processing"


def durable_snapshot(db, tenant):
    with db.transaction(tenant) as conn:
        return {
            "items": conn.execute(
                "SELECT * FROM ingest_items WHERE tenant_id=%s ORDER BY id", (tenant,)
            ).fetchall(),
            "reservations": conn.execute(
                "SELECT * FROM admission_reservations WHERE tenant_id=%s ORDER BY item_id",
                (tenant,),
            ).fetchall(),
            "capacity": conn.execute("SELECT * FROM admission_capacity ORDER BY scope").fetchall(),
            "alerts": conn.execute(
                "SELECT * FROM alerts WHERE tenant_id=%s ORDER BY id", (tenant,)
            ).fetchall(),
            "outbox": conn.execute(
                "SELECT * FROM application_outbox WHERE tenant_id=%s ORDER BY id", (tenant,)
            ).fetchall(),
            "view": conn.execute(
                "SELECT * FROM tenant_view_state WHERE tenant_id=%s", (tenant,)
            ).fetchone(),
            "progress": conn.execute(
                "SELECT admission_order,change_order,projection_seq FROM tenant_work_state WHERE tenant_id=%s",
                (tenant,),
            ).fetchone(),
        }


def test_success_proves_real_reducer_but_does_not_apply_head_or_release_capacity(admin):
    client, db, tenant, _, head, _ = admin
    before = durable_snapshot(db, tenant)
    body = command()
    result = client.post(path(tenant) + "/retry", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["data"]["head"]["id"] == str(head["id"])
    assert durable_snapshot(db, tenant) == before
    assert client.post(path(tenant) + "/retry", json=body).json() == result.json()
    with db.transaction(tenant) as conn:
        state = conn.execute(
            "SELECT * FROM tenant_work_state WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert not state["blocked"] and state["failures"] == 0 and state["block_generation"] == 1
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM audit_events WHERE kind='source-processing-retry'"
            ).fetchone()["n"]
            == 1
        )
        for table in (
            "alert_source_events",
            "correlation_journal",
            "maintenance_alert_inputs",
            "projection_work",
        ):
            assert (
                conn.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE tenant_id=%s", (tenant,)
                ).fetchone()["n"]
                == 0
            )
    assert process_one(db, tenant)
    with db.transaction(tenant) as conn:
        stored = conn.execute("SELECT * FROM ingest_items WHERE id=%s", (head["id"],)).fetchone()
        assert (
            stored["disposition"] == "applied"
            and stored["admission_order"] == head["admission_order"]
            and stored["received_at"] == head["received_at"]
        )


def test_failed_capacity_proof_keeps_head_blocked_and_receipt_audited(admin):
    client, db, tenant, _, head, _ = admin
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE admission_reservations SET released=true WHERE item_id=%s", (head["id"],)
        )
    before = durable_snapshot(db, tenant)
    body = command()
    result = client.post(path(tenant) + "/retry", json=body)
    assert result.status_code == 409 and result.json()["error"]["code"] == "repair_required"
    assert durable_snapshot(db, tenant) == before
    with db.transaction(tenant) as conn:
        state = conn.execute(
            "SELECT * FROM tenant_work_state WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert state["blocked"] and state["failures"] == 10
        event = conn.execute(
            "SELECT * FROM audit_events WHERE kind='source-processing-retry'"
        ).fetchone()
        assert (
            event["tenant_id"] is None
            and event["evidence"]["proof_code"] == "capacity_accounting_mismatch"
        )
        conn.execute(
            "UPDATE admission_reservations SET released=false WHERE item_id=%s", (head["id"],)
        )
    # Same committed failed command remains the same result after repair.
    assert client.post(path(tenant) + "/retry", json=body).status_code == 409
    assert client.post(path(tenant) + "/retry", json=command()).status_code == 200


def test_current_privilege_is_required_for_replay_and_health(admin):
    client, db, tenant, principal, _, _ = admin
    body = command()
    assert client.post(path(tenant) + "/retry", json=body).status_code == 200
    with db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=false WHERE id=%s", (principal,))
    assert client.post(path(tenant) + "/retry", json=body).status_code == 403
    assert client.get(path(tenant)).status_code == 403


def test_stale_generation_and_concurrent_retries_do_not_apply_or_skip(admin):
    client, db, tenant, _, _, _ = admin
    before = durable_snapshot(db, tenant)
    stale = client.post(path(tenant) + "/retry", json=command("0"))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "block_generation_conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: client.post(path(tenant) + "/retry", json=command()).status_code, range(2)
            )
        )
    assert sorted(results) == [200, 409]
    assert durable_snapshot(db, tenant) == before


def test_reason_validation_health_sanitization_and_command_fingerprint(admin):
    client, db, tenant, _, _, _ = admin
    for value in ("", "   ", "x" * 1001, "secret\x00token", "spoof\u202eheader"):
        assert client.post(path(tenant) + "/retry", json=command(reason=value)).status_code == 422
    reason = "Restored proof <script>text only</script>\nOperator reviewed"
    body = command(reason=reason)
    assert client.post(path(tenant) + "/retry", json=body).status_code == 200
    assert (
        client.post(path(tenant) + "/retry", json={**body, "reason": "changed"}).status_code == 409
    )
    result = client.get(path(tenant))
    assert (
        result.status_code == 200 and result.json()["data"]["last_repair"]["outcome"] == "resumed"
    )
    assert "sensitive-source-body" not in result.text and "<script>" not in result.text
    with db.transaction() as conn:
        event = conn.execute(
            "SELECT evidence FROM audit_events WHERE kind='source-processing-retry'"
        ).fetchone()
        assert event["evidence"]["reason"] == reason


def test_closed_maintenance_timeline_does_not_allow_blind_unblock(admin):
    client, db, tenant, _, _, app = admin
    with db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='maintenance'")
    app.state.profile = None
    response = client.post(path(tenant) + "/retry", json=command())
    assert response.status_code == 409 and response.json()["error"]["code"] == "repair_required"
    assert client.get(path(tenant)).json()["data"]["blocked"]
