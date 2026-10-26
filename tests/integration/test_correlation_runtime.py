"""Real SQL consumer/ownership transactions with native CEL conditions."""

from uuid import uuid4
from datetime import datetime, UTC, timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest

from appchi.alerts.lifecycle import apply_source
from appchi.alerts.repository import publish, store_alert
from appchi.correlation import runtime
from appchi.shared.transactions import coordinator
from test_correlation_configuration import TEST_CAPACITY, create, definition, rules  # noqa: F401
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def running(request):
    return request.getfixturevalue("rules")


def source(db, tenant, payload, previous=None, capacity=TEST_CAPACITY):
    from appchi.maintenance.runtime import prepare_for_tenant, apply_transition

    catalog = prepare_for_tenant(db, tenant, capacity)
    with db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        runtime.lock_contexts(conn, tenant)
        order = conn.execute(
            "UPDATE tenant_work_state SET admission_order=admission_order+1 WHERE tenant_id=%s RETURNING admission_order",
            (tenant,),
        ).fetchone()["admission_order"]
        item = {
            "id": uuid4(),
            "tenant_id": tenant,
            "version": "v1",
            "key_field": str(uuid4()) if previous is None else previous["key_field"],
            "received_at": now,
            "payload": payload,
        }
        after, changed = apply_source(previous, item)
        store_alert(conn, after, new=previous is None)
        if catalog:
            after, changed, _ = apply_transition(
                conn, previous, after, now, order, "source", now, catalog
            )
            store_alert(conn, after)
        publish(
            conn,
            previous,
            after,
            now,
            "source",
            semantic=changed,
            original_order=order,
            maintenance_input_recorded=catalog is not None,
        )
        return after


def drain(db, tenant, capacity=TEST_CAPACITY):
    for _ in range(30):
        if runtime.worker_turn(db, tenant, capacity) == 0:
            return
    pytest.fail("consumer did not become idle")


def test_rule_runs_without_a_capacity_profile(running, real_db, v1):
    """Requirements: REV-39; correlation rules 6."""
    client, _, tenant, _ = running
    client.app.state.profile = None
    body = {
        **definition(),
        "conditions": [{"id": str(uuid4()), "label": "Match", "cel": "true"}],
    }
    create(running, body)
    source(real_db, tenant, v1, capacity=None)
    assert runtime.worker_turn(real_db, tenant, None) == 1
    with real_db.transaction() as conn:
        assert conn.execute(
            "SELECT count(*) AS n FROM incidents WHERE tenant_id=%s", (tenant,)
        ).fetchone()["n"] == 1


def test_delayed_qualifier_replays_changed_fields_and_original_window(running, real_db, v1):
    client, _, tenant, _ = running
    capacity = {
        **TEST_CAPACITY,
        "maintenance_max_work": 10000,
        "maintenance_max_inputs": 100,
        "maintenance_max_segments": 20,
        "maintenance": {"max_overlap": 20, "max_work_bytes": 1000000},
    }
    client.app.state.profile = capacity
    create(
        running,
        {
            **definition(),
            "conditions": [
                {"id": str(uuid4()), "label": "Match", "cel": 'alert.message == "match"'}
            ],
        },
    )
    now = datetime.now(UTC)
    response = client.post(
        f"/api/tenants/{tenant}/maintenance",
        json={
            "command_id": str(uuid4()),
            "name": "Replay maintenance",
            "reason": "",
            "cel": 'alert.message == "blocked"',
            "timezone": "UTC",
            "start_local": (now - timedelta(minutes=1))
            .replace(tzinfo=None)
            .isoformat(timespec="seconds"),
            "end_local": (now + timedelta(hours=1))
            .replace(tzinfo=None)
            .isoformat(timespec="seconds"),
        },
    )
    assert response.status_code == 201, response.text
    initial = source(real_db, tenant, {**v1, "message": "blocked"}, capacity=capacity)
    assert initial["suppressed"]
    original = source(real_db, tenant, {**v1, "message": "match"}, initial, capacity)
    assert not original["suppressed"]
    lost = source(real_db, tenant, {**v1, "message": "other"}, original, capacity)
    regained = source(real_db, tenant, {**v1, "message": "match"}, lost, capacity)
    drain(real_db, tenant, capacity)
    with real_db.transaction() as conn:
        decision = conn.execute(
            "SELECT * FROM correlation_decisions WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert decision is not None
        support = decision["evidence"]["supports"][0]
        assert datetime.fromisoformat(support["start"]) == regained["timestamp"]
        assert datetime.fromisoformat(support["start"]) > original["timestamp"]
        assert (
            conn.execute(
                "SELECT health FROM correlation_consumer_state WHERE tenant_id=%s", (tenant,)
            ).fetchone()["health"]
            == "healthy"
        )


def test_arbitration_complete_union_and_transaction_retry(running, real_db, v1, monkeypatch):
    _, _, tenant, _ = running
    body = definition("Earlier")
    body["conditions"][0]["cel"] = 'alert.message == "A"'
    body["conditions"][1]["cel"] = 'alert.message == "B"'
    first, _ = create(running, body)
    second, _ = create(
        running,
        {
            **definition("Later"),
            "conditions": [{"id": str(uuid4()), "label": "Any", "cel": "true"}],
        },
    )
    # First input alone qualifies the later rule, so exercise two-condition first
    # rule with both rows' final state before processing; priority is per ready turn.
    one = source(real_db, tenant, {**v1, "message": "A"})
    two = source(real_db, tenant, {**v1, "message": "B"})
    # Disable later intent so complete multi-condition union is the first winner.
    with real_db.transaction() as conn:
        conn.execute("UPDATE correlation_rules SET enabled=false WHERE id=%s", (second["id"],))
    assert runtime.worker_turn(real_db, tenant, TEST_CAPACITY) == 1
    original = runtime._commit_decision

    def fail_after_write(*args):
        original(*args)
        raise RuntimeError("injected crash before commit")

    monkeypatch.setattr(runtime, "_commit_decision", fail_after_write)
    with pytest.raises(RuntimeError):
        runtime.worker_turn(real_db, tenant, TEST_CAPACITY)
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM incidents WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM correlation_evidence WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 1
        )
    monkeypatch.setattr(runtime, "_commit_decision", original)
    drain(real_db, tenant)
    with real_db.transaction() as conn:
        links = conn.execute(
            "SELECT * FROM alert_active_ownership WHERE tenant_id=%s", (tenant,)
        ).fetchall()
        assert {r["alert_id"] for r in links} == {one["id"], two["id"]}
        assert len({r["incident_id"] for r in links}) == 1
        decisions = conn.execute(
            "SELECT * FROM correlation_decisions WHERE tenant_id=%s", (tenant,)
        ).fetchall()
        assert len(decisions) == 1 and str(decisions[0]["rule_id"]) == first["id"]
        assert len(decisions[0]["evidence"]["supports"]) == 2
    drain(real_db, tenant)


def test_parallel_consumers_keep_earliest_rule_and_one_context(running, real_db, v1):
    _, _, tenant, _ = running
    first, _ = create(
        running,
        {
            **definition("First"),
            "conditions": [{"id": str(uuid4()), "label": "Any", "cel": "true"}],
        },
    )
    create(
        running,
        {
            **definition("Second"),
            "conditions": [{"id": str(uuid4()), "label": "Any", "cel": "true"}],
        },
    )
    source(real_db, tenant, v1)
    source(real_db, tenant, v1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(lambda _: runtime.worker_turn(real_db, tenant, TEST_CAPACITY), range(2))
        )
    assert outcomes == [1, 1]
    drain(real_db, tenant)
    with real_db.transaction() as conn:
        rows = conn.execute(
            "SELECT rule_id,incident_id FROM correlation_decisions WHERE tenant_id=%s", (tenant,)
        ).fetchall()
        assert len(rows) == 2 and {str(row["rule_id"]) for row in rows} == {first["id"]}
        assert len({row["incident_id"] for row in rows}) == 1


def test_runtime_block_during_compilation_is_not_resurrected(running, real_db, v1, monkeypatch):
    _, _, tenant, _ = running
    rule, _ = create(
        running,
        {**definition(), "conditions": [{"id": str(uuid4()), "label": "Any", "cel": "true"}]},
    )
    source(real_db, tenant, v1)
    original = runtime.prepare_tenant

    def concurrent_block(*args):
        prepared = original(*args)
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE correlation_rules SET health='blocked',blocked_reason='condition_evaluation_failed' WHERE id=%s",
                (rule["id"],),
            )
        return prepared

    monkeypatch.setattr(runtime, "prepare_tenant", concurrent_block)
    assert runtime.worker_turn(real_db, tenant, TEST_CAPACITY) == 1
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT health FROM correlation_rules WHERE id=%s", (rule["id"],)
            ).fetchone()["health"]
            == "blocked"
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM incidents WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )


def test_automatic_complete_set_exceeds_manual_selection_limit(running, real_db, v1):
    client, _, tenant, _ = running
    capacity = {
        **TEST_CAPACITY,
        "correlation": {
            **TEST_CAPACITY["correlation"],
            "live_evidence_per_rule": 250,
            "live_evidence_per_tenant": 500,
        },
    }
    client.app.state.profile = capacity
    body = definition()
    body["conditions"][0]["cel"] = 'alert.message == "A"'
    body["conditions"][1]["cel"] = 'alert.message == "B"'
    create(running, body)
    for _ in range(101):
        source(real_db, tenant, {**v1, "message": "A"})
    source(real_db, tenant, {**v1, "message": "B"})
    for _ in range(105):
        if not runtime.worker_turn(real_db, tenant, capacity):
            break
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM alert_active_ownership WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 102
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM correlation_decisions WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 1
        )


def test_resolution_repeat_barrier_reopen_and_unlink(running, real_db, v1):
    client, _, tenant, base = running
    rule, _ = create(
        running,
        {**definition(), "conditions": [{"id": str(uuid4()), "label": "Any", "cel": "true"}]},
    )
    alert = source(real_db, tenant, v1)
    drain(real_db, tenant)
    with real_db.transaction() as conn:
        incident = conn.execute("SELECT * FROM incidents WHERE tenant_id=%s", (tenant,)).fetchone()
    url = f"/api/tenants/{tenant}/incidents/{incident['id']}"
    response = client.post(
        url + "/status",
        json={
            "command_id": str(uuid4()),
            "expected_revision": str(incident["revision"]),
            "status": "resolved",
        },
    )
    assert response.status_code == 200, response.text
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT active_incident_id FROM correlation_contexts WHERE tenant_id=%s", (tenant,)
            ).fetchone()["active_incident_id"]
            is None
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM correlation_evidence WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )
        current = conn.execute("SELECT * FROM alerts WHERE id=%s", (alert["id"],)).fetchone()
    source(real_db, tenant, v1, current)
    drain(real_db, tenant)
    response = client.post(
        url + "/status",
        json={
            "command_id": str(uuid4()),
            "expected_revision": response.json()["data"]["revision"],
            "status": "open",
        },
    )
    assert response.status_code == 200, response.text
    unlinked = client.post(
        url + "/alerts/unlink",
        json={
            "command_id": str(uuid4()),
            "expected_revision": response.json()["data"]["revision"],
            "alert_ids": [str(alert["id"])],
        },
    )
    assert unlinked.status_code == 200, unlinked.text
    drain(real_db, tenant)
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM alert_active_ownership WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM incidents WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 1
        )


def test_protected_rule_delete_preserves_existing_incident_origin(running, real_db, v1):
    from test_correlation_deletion import Store

    client, _, tenant, base = running
    client.app.state.recovery_store = Store()
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
    try:
        rule, _ = create(
            running,
            {**definition(), "conditions": [{"id": str(uuid4()), "label": "Any", "cel": "true"}]},
        )
        source(real_db, tenant, v1)
        drain(real_db, tenant)
        with real_db.transaction() as conn:
            incident = conn.execute(
                "SELECT * FROM incidents WHERE tenant_id=%s", (tenant,)
            ).fetchone()
        response = client.delete(
            base + "/" + rule["id"], headers={"Idempotency-Key": str(uuid4()), "If-Match": '"1"'}
        )
        assert response.status_code == 200, response.text
        with real_db.transaction() as conn:
            assert (
                conn.execute("SELECT * FROM incidents WHERE id=%s", (incident["id"],)).fetchone()
                == incident
            )
            assert (
                conn.execute(
                    "SELECT active_incident_id FROM correlation_contexts WHERE id=%s",
                    (incident["context_id"],),
                ).fetchone()["active_incident_id"]
                == incident["id"]
            )
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM correlation_evidence WHERE tenant_id=%s", (tenant,)
                ).fetchone()["n"]
                == 0
            )
        url = f"/api/tenants/{tenant}/incidents/{incident['id']}/status"
        closed = client.post(
            url,
            json={
                "command_id": str(uuid4()),
                "expected_revision": str(incident["revision"]),
                "status": "resolved",
            },
        )
        assert closed.status_code == 200, closed.text
        reopened = client.post(
            url,
            json={
                "command_id": str(uuid4()),
                "expected_revision": closed.json()["data"]["revision"],
                "status": "open",
            },
        )
        assert reopened.status_code == 200, reopened.text
    finally:
        client.app.state.recovery_store = None
        with real_db.transaction() as conn:
            conn.execute("UPDATE capability_gates SET ready=false WHERE capability='recovery'")
