# ruff: noqa: F811
"""Real SQL proof replay; clocks are explicit synthetic effective instants."""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from appchi.cel.catalog import FIELDS, RULE_FIELDS
from appchi.maintenance.processing import (
    claim_due,
    prepare_catalog,
    record_input,
    record_transition,
    reconcile_alert,
    step_job,
)
from appchi.maintenance.temporal import MaintenanceProofError
from appchi.shared.errors import AppError
from appchi.shared.transactions import coordinator

from test_maintenance_repository import body, create, maintenance_browser, maintenance_db  # noqa: F401, F811

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def processing_schema(maintenance_db):
    db, _ = maintenance_db
    draft = Path(__file__).resolve().parents[2] / "migrations/0011_maintenance_processing.sql.draft"
    if draft.exists():
        with db.transaction() as conn:
            if not conn.execute(
                "SELECT to_regclass('maintenance_alert_inputs') AS table"
            ).fetchone()["table"]:
                conn.execute(draft.read_text(), prepare=False)
    draft = (
        Path(__file__).resolve().parents[2] / "migrations/0014_maintenance_effect_outbox.sql.draft"
    )
    if draft.exists():
        with db.transaction() as conn:
            if not conn.execute(
                "SELECT to_regclass('maintenance_effect_outbox') AS name"
            ).fetchone()["name"]:
                conn.execute(draft.read_text(), prepare=False)


def snapshot(tenant, *, message="match", schema="v2"):
    values = {name: None if FIELDS[name].nullable else "value" for name in RULE_FIELDS}
    values.update(
        id=str(uuid4()),
        tenant_id=str(tenant),
        schema_version=schema,
        message=message,
        status="firing",
        severity="high",
        acknowledged=False,
        time_created="2026-09-22T00:00:00+00:00",
        source_suppressed=False,
        suppressed=False,
    )
    return values


def setup(maintenance_browser, maintenance_db, *, active=True):
    client, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    window = create(client, tenant, body(past=active))
    catalog = prepare_catalog(db, tenant, max_work=10000, max_inputs=100, max_segments=20)
    base = catalog.segments[0].activation.at
    return db, client, tenant, window, catalog, base


def apply(db, catalog, before, after, at, order, *, now=None, cause="source"):
    with db.transaction(catalog.tenant_id) as conn:
        coordinator(conn, catalog.tenant_id)
        if before is None:
            conn.execute(
                "INSERT INTO alert_identities(tenant_id,id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (catalog.tenant_id, after["id"]),
            )
        return record_transition(conn, before, after, at, order, cause, now or at, catalog=catalog)


def test_creation_repeat_false_true_and_replay_are_durable(maintenance_browser, maintenance_db):
    db, _, tenant, _, catalog, base = setup(maintenance_browser, maintenance_db)
    alert = snapshot(tenant)
    current, effects = apply(db, catalog, None, alert, base + timedelta(seconds=1), 2)
    assert current["suppressed"] and effects.suppression_changed
    repeated, _ = apply(db, catalog, alert, alert, base + timedelta(seconds=2), 3)
    assert repeated["suppressed"]
    unmatched = dict(alert, message="other")
    current, effects = apply(db, catalog, alert, unmatched, base + timedelta(seconds=3), 4)
    assert not current["suppressed"] and len(effects.qualifiers) == 1
    _, replay = apply(db, catalog, alert, unmatched, base + timedelta(seconds=3), 4)
    assert replay.qualifiers == () and not replay.history_changed
    apply(db, catalog, unmatched, alert, base + timedelta(seconds=4), 5)
    with db.transaction(tenant) as conn:
        causes = conn.execute(
            "SELECT * FROM alert_maintenance_causes WHERE tenant_id=%s ORDER BY opened_at",
            (tenant,),
        ).fetchall()
        assert len(causes) == 2 and causes[0]["closed_at"] == base + timedelta(seconds=3)
        assert causes[1]["closed_at"] is None
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_cause_history WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 3
        )


def test_delayed_source_after_disable_retains_original_interval(
    maintenance_browser, maintenance_db
):
    db, client, tenant, window, catalog, base = setup(maintenance_browser, maintenance_db)
    disabled = client.post(
        f"/api/tenants/{tenant}/maintenance/{window['id']}/disable",
        json={"command_id": str(uuid4()), "expected_revision": "1"},
    )
    assert disabled.status_code == 200
    catalog = prepare_catalog(db, tenant, max_work=10000, max_inputs=100, max_segments=20)
    boundary = catalog.segments[0].deactivation
    receipt = base + (boundary.at - base) / 2
    alert = snapshot(tenant)
    current, effects = apply(
        db, catalog, None, alert, receipt, 2, now=boundary.at + timedelta(minutes=1)
    )
    assert current["suppressed"] and not effects.qualifiers
    release = boundary.at + timedelta(minutes=2)
    current, effects = apply(db, catalog, alert, alert, release, 3)
    assert not current["suppressed"] and effects.qualifiers[0][1] == release
    with db.transaction(tenant) as conn:
        cause = conn.execute(
            "SELECT * FROM alert_maintenance_causes WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert cause["opened_at"] == receipt and cause["closed_at"] == release
        assert cause["recorded_at"] <= cause["closed_at"]


def test_activation_no_backfill_and_source_overlap(maintenance_browser, maintenance_db):
    db, _, tenant, _, catalog, base = setup(maintenance_browser, maintenance_db)
    alert = snapshot(tenant)
    # Creation predates the control, although this work is processed afterward.
    current, _ = apply(db, catalog, None, alert, base - timedelta(seconds=1), 1, now=base)
    assert not current["suppressed"]
    current, _ = apply(db, catalog, alert, alert, base + timedelta(seconds=1), 2)
    assert not current["suppressed"]
    source_on = dict(alert, source_suppressed=True)
    current, _ = apply(db, catalog, alert, source_on, base + timedelta(seconds=2), 3)
    assert current["suppressed"]
    unmatched = dict(source_on, message="other")
    apply(db, catalog, source_on, unmatched, base + timedelta(seconds=3), 4)
    apply(db, catalog, unmatched, source_on, base + timedelta(seconds=4), 5)
    source_off = dict(alert, source_suppressed=False)
    current, effects = apply(db, catalog, source_on, source_off, base + timedelta(seconds=5), 6)
    assert current["suppressed"] and not effects.qualifiers
    ending = catalog.segments[0].end
    with db.transaction(tenant) as conn:
        coordinator(conn, tenant)
        current, effects = reconcile_alert(
            conn, source_off, ending + timedelta(minutes=10), catalog=catalog
        )
        assert current["suppressed"] and not effects.qualifiers


def test_two_windows_union_closes_only_at_last_end(maintenance_browser, maintenance_db):
    db, client, tenant, _, _, _ = setup(maintenance_browser, maintenance_db)
    second = body(past=True)
    create(client, tenant, second)
    catalog = prepare_catalog(db, tenant, max_work=10000, max_inputs=100, max_segments=20)
    base = max(s.activation.at for s in catalog.segments)
    alert = snapshot(tenant)
    current, _ = apply(db, catalog, None, alert, base + timedelta(seconds=1), 3)
    assert current["suppressed"]
    with db.transaction(tenant) as conn:
        coordinator(conn, tenant)
        _, effects = reconcile_alert(
            conn,
            current,
            max(s.end for s in catalog.segments) + timedelta(minutes=1),
            catalog=catalog,
        )
        assert not effects.qualifiers
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM alert_maintenance_causes WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 2
        )


def test_missing_baseline_and_conflicting_replay_roll_back(maintenance_browser, maintenance_db):
    db, _, tenant, _, catalog, base = setup(maintenance_browser, maintenance_db)
    alert = snapshot(tenant)
    with db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO alert_identities(tenant_id,id) VALUES(%s,%s)", (tenant, alert["id"])
        )
    with pytest.raises(MaintenanceProofError, match="complete_timeline"):
        apply(db, catalog, alert, alert, base + timedelta(seconds=1), 2)
    apply(db, catalog, None, alert, base + timedelta(seconds=1), 2)
    with pytest.raises(MaintenanceProofError, match="conflicting_input"):
        apply(db, catalog, None, dict(alert, message="wrong"), base + timedelta(seconds=1), 2)
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_alert_inputs WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 1
        )


def test_stale_catalog_and_work_limit_do_not_publish_partial_causes(
    maintenance_browser, maintenance_db
):
    db, client, tenant, _, catalog, base = setup(maintenance_browser, maintenance_db)
    create(client, tenant, body(past=True))
    alert = snapshot(tenant)
    with pytest.raises(AppError, match="maintenance_catalog_changed"):
        apply(db, catalog, None, alert, base + timedelta(seconds=1), 3)
    catalog = prepare_catalog(db, tenant, max_work=1, max_inputs=100, max_segments=20)
    with pytest.raises(MaintenanceProofError, match="allocation"):
        apply(db, catalog, None, alert, base + timedelta(seconds=1), 3)
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_alert_inputs WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )


def test_journal_baseline_works_while_capability_is_disabled(maintenance_browser, maintenance_db):
    _, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    alert = snapshot(tenant)
    with db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='maintenance'")
        conn.execute(
            "INSERT INTO alert_identities(tenant_id,id) VALUES(%s,%s)", (tenant, alert["id"])
        )
        identity, digest = record_input(conn, None, alert, now, 1, "source", now)
        assert record_input(conn, None, dict(alert, suppressed=True), now, 1, "source", now) == (
            identity,
            digest,
        )
    catalog = prepare_catalog(db, tenant, max_work=100, max_inputs=100, max_segments=10)
    with db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        current, _ = reconcile_alert(conn, alert, now, catalog=catalog)
        assert not current["suppressed"]


@pytest.mark.skip(reason="REV-34: deferred scheduler prototype; current source-driven release is tested separately")
def test_job_lease_fencing_atomic_publish_and_cursor(maintenance_browser, maintenance_db):
    db, client, tenant, window, catalog, base = setup(maintenance_browser, maintenance_db)
    alert = snapshot(tenant)
    current, _ = apply(db, catalog, None, alert, base + timedelta(microseconds=1), 2)
    assert current["suppressed"]
    response = client.post(
        f"/api/tenants/{tenant}/maintenance/{window['id']}/disable",
        json={"command_id": str(uuid4()), "expected_revision": "1"},
    )
    assert response.status_code == 200
    lease = claim_due(db, tenant)
    assert lease is not None and claim_due(db, tenant) is None
    catalog = prepare_catalog(db, tenant, max_work=1000, max_inputs=100, max_segments=10)

    def lock(conn, tenant_id, alert_id):
        assert str(alert_id) == current["id"]
        return current

    def fail(conn, before, after, effects, now):
        assert not after["suppressed"] and effects.qualifiers
        raise RuntimeError("controlled publication failure")

    with pytest.raises(RuntimeError, match="controlled"):
        step_job(db, lease, catalog, lock_alert=lock, publish=fail)
    with db.transaction(tenant) as conn:
        job = conn.execute(
            "SELECT * FROM maintenance_fanout_jobs WHERE id=%s", (lease.job_id,)
        ).fetchone()
        assert job["cursor_alert_id"] is None
        assert (
            conn.execute(
                "SELECT closed_at FROM alert_maintenance_causes WHERE tenant_id=%s", (tenant,)
            ).fetchone()["closed_at"]
            is None
        )
        conn.execute(
            "UPDATE maintenance_fanout_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
            (lease.job_id,),
        )
    replacement = claim_due(db, tenant)
    assert replacement.generation == lease.generation + 1
    assert step_job(db, lease, catalog, lock_alert=lock, publish=fail) == "stale"
    published = []

    def publish(conn, before, after, effects, now):
        published.extend(effects.qualifiers)
        conn.execute(
            "UPDATE tenant_work_state SET projection_seq=projection_seq+1 WHERE tenant_id=%s",
            (tenant,),
        )

    assert step_job(db, replacement, catalog, lock_alert=lock, publish=publish) == "processed"
    assert step_job(db, replacement, catalog, lock_alert=lock, publish=publish) == "complete"
    assert step_job(db, replacement, catalog, lock_alert=lock, publish=fail) == "stale"
    assert len(published) == 1
    with db.transaction(tenant) as conn:
        job = conn.execute(
            "SELECT * FROM maintenance_fanout_jobs WHERE id=%s", (lease.job_id,)
        ).fetchone()
        assert job["required_projection_seq"] == 1 and job["lease_until"] is None


@pytest.mark.skip(reason="REV-34: deferred scheduler prototype; current source-driven release is tested separately")
def test_scheduled_end_publishes_one_generation_and_obsolete_job_is_fenced(
    maintenance_browser, maintenance_db
):
    db, _, tenant, _, catalog, base = setup(maintenance_browser, maintenance_db)
    segment = catalog.segments[0]
    # A dedicated test fixture makes the accepted end overdue; no sleep or runtime clock bypass.
    with db.transaction(tenant) as conn:
        end = base - timedelta(microseconds=1)
        conn.execute(
            "UPDATE maintenance_segments SET end_at=%s WHERE tenant_id=%s AND id=%s",
            (end, tenant, segment.id),
        )
        conn.execute(
            "UPDATE maintenance_fanout_jobs SET expected_end=%s,due_at=%s WHERE tenant_id=%s AND kind='scheduled_end'",
            (end, end, tenant),
        )
    lease = claim_due(db, tenant)
    assert lease is not None
    with db.transaction(tenant) as conn:
        first = conn.execute(
            "SELECT * FROM maintenance_controls WHERE tenant_id=%s AND kind='scheduled_end'",
            (tenant,),
        ).fetchall()
        assert len(first) == 1 and first[0]["effective_at"] == end
        conn.execute(
            "UPDATE maintenance_fanout_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
            (lease.job_id,),
        )
    assert claim_due(db, tenant).generation == lease.generation + 1
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_controls WHERE tenant_id=%s AND kind='scheduled_end'",
                (tenant,),
            ).fetchone()["n"]
            == 1
        )


PROFILE = {
    "qualified": True,
    "maintenance_max_work": 10000,
    "maintenance_max_inputs": 100,
    "maintenance_max_segments": 20,
}


def publish_source(db, tenant, before, catalog, *, message="match", key=None):
    from appchi.alerts.lifecycle import apply_source
    from appchi.alerts.repository import publish, store_alert
    from appchi.maintenance.runtime import apply_transition

    with db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        order = conn.execute(
            "UPDATE tenant_work_state SET admission_order=admission_order+1 WHERE tenant_id=%s RETURNING admission_order",
            (tenant,),
        ).fetchone()["admission_order"]
        item = {
            "id": uuid4(),
            "tenant_id": tenant,
            "version": "v2",
            "key_field": before["key_field"] if before else key or str(uuid4()),
            "operator_id": uuid4(),
            "received_at": now,
            "payload": {
                "application": "app",
                "component": "comp",
                "message": message,
                "severity": "high",
                "status": "firing",
                "time_created": "22-09-2026 00:00:00",
            },
        }
        after, _ = apply_source(before, item)
        if before is None:
            store_alert(conn, after, new=True)
        after, changed, effects = apply_transition(
            conn, before, after, now, order, "source", now, catalog
        )
        store_alert(conn, after)
        publish(conn, before, after, now, "source", semantic=changed, original_order=order)
        return after, changed, effects


def test_runtime_source_repeat_revisions_effect_queue_and_withdrawal(
    maintenance_browser, maintenance_db
):
    from appchi.maintenance.processing import Effects
    from appchi.maintenance.runtime import persist_effects, prepare_for_tenant

    db, _, tenant, _, _, _ = setup(maintenance_browser, maintenance_db)
    catalog = prepare_for_tenant(db, tenant, PROFILE)
    first, _, _ = publish_source(db, tenant, None, catalog)
    assert first["suppressed"] and first["revision"] == 1
    repeat, changed, effects = publish_source(db, tenant, first, catalog)
    assert (
        repeat["revision"] == 1
        and repeat["projection_version"] == 2
        and not changed
        and not effects.history_changed
    )
    after, changed, effects = publish_source(db, tenant, repeat, catalog, message="other")
    assert after["revision"] == 2 and changed and len(effects.qualifiers) == 1
    with db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        event = conn.execute(
            "SELECT * FROM maintenance_effect_outbox WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert event["action"] == "activate" and event["before_snapshot"]["suppressed"]
        assert event["after_snapshot"]["message"] == "other" and not event["scheduled"]
        persist_effects(conn, after, effects, now)
        withdrawal = Effects(False, True, (), (event["qualifier_id"],), b"corrected-proof")
        persist_effects(conn, after, withdrawal, now)
        persist_effects(conn, after, withdrawal, now)
        events = conn.execute(
            "SELECT * FROM maintenance_effect_outbox WHERE tenant_id=%s ORDER BY change_order",
            (tenant,),
        ).fetchall()
        assert len(events) == 2 and events[1]["corrects_id"] == event["id"]
        assert events[1]["effective_at"] == event["effective_at"]
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_alert_inputs WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 3
        )


def test_runtime_keeps_suppression_until_source_update(maintenance_browser, maintenance_db):
    from appchi.maintenance.processing import derive_current
    from appchi.maintenance.runtime import prepare_for_tenant, worker_turn

    db, client, tenant, window, _, _ = setup(maintenance_browser, maintenance_db)
    catalog = prepare_for_tenant(db, tenant, PROFILE)
    before, _, _ = publish_source(db, tenant, None, catalog)
    response = client.post(f"/api/tenants/{tenant}/maintenance/{window['id']}/disable",
        json={"command_id": str(uuid4()), "expected_revision": "1"})
    assert response.status_code == 200
    with db.transaction(tenant) as conn:
        now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        assert derive_current(conn, before, now) == (True, False)
    assert worker_turn(db, tenant, PROFILE) == 0
    catalog = prepare_for_tenant(db, tenant, PROFILE)
    after, _, _ = publish_source(db, tenant, before, catalog)
    assert not after["suppressed"]
    assert after["revision"] == 2 and after["projection_version"] == 2


def test_runtime_requires_qualified_profile_and_context_lock_hook(
    maintenance_browser, maintenance_db
):
    from appchi.maintenance.runtime import prepare_for_tenant, worker_turn

    db, _, tenant, _, _, _ = setup(maintenance_browser, maintenance_db)
    with pytest.raises(AppError, match="capacity_profile_required"):
        prepare_for_tenant(db, tenant, {**PROFILE, "qualified": False})
    with pytest.raises(AppError, match="capacity_profile_required"):
        prepare_for_tenant(db, tenant, {**PROFILE, "maintenance_max_work": True})
    with db.transaction(tenant) as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='correlation'")
    assert worker_turn(db, tenant, PROFILE) == 0
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE capability_gates SET ready=false WHERE capability IN ('maintenance','correlation')"
        )
    assert prepare_for_tenant(db, tenant, None) is None


def test_enabled_alert_api_assignment_notes_noop_and_inactivity(
    maintenance_browser, maintenance_db
):
    from appchi.alerts.routes import router as alert_router
    from appchi.inactivity import expire_alert
    from appchi.maintenance.runtime import prepare_for_tenant

    client, tenant, editor, app = maintenance_browser
    db, _ = maintenance_db
    app.include_router(alert_router)
    app.state.profile.update(PROFILE)
    definition = body(past=True)
    definition["cel"] = "alert.assignee_id == null && alert.status == 'firing'"
    create(client, tenant, definition)
    catalog = prepare_for_tenant(db, tenant, PROFILE)
    current, _, _ = publish_source(db, tenant, None, catalog)
    base = f"/api/tenants/{tenant}/alerts/{current['id']}"
    assert client.get(base).json()["data"]["suppressed"]
    command = {
        "command_id": str(uuid4()),
        "expected_revision": "1",
        "kind": "set_assignee",
        "value": str(editor),
    }
    response = client.post(base + "/commands", json=command)
    assert response.status_code == 200, response.text
    assert not response.json()["data"]["suppressed"] and response.json()["data"]["revision"] == "2"
    assert client.post(base + "/commands", json=command).json() == response.json()
    noop = client.post(
        base + "/commands", json={**command, "command_id": str(uuid4()), "expected_revision": "2"}
    )
    assert noop.status_code == 200 and noop.json()["data"]["revision"] == "2"
    note = client.post(
        base + "/notes",
        json={
            "command_id": str(uuid4()),
            "expected_revision": "2",
            "text": "Retain explicit note revision",
        },
    )
    assert note.status_code == 201 and note.json()["data"]["revision"] == "3"
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_alert_inputs WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 2
        )
        conn.execute(
            "UPDATE alerts SET last_source_activity=clock_timestamp()-interval '31 days' WHERE tenant_id=%s",
            (tenant,),
        )
    assert expire_alert(db, tenant, current["id"], profile=PROFILE)
    assert client.get(base).json()["data"]["revision"] == "4"
    assert client.get(base).json()["data"]["status"] == "resolved"


def test_enabled_source_processor_admission_and_repeat_revision(
    maintenance_browser, maintenance_db
):
    from appchi.alerts.processor import process_one
    from appchi.ingestion.native import canonical
    from appchi.ingestion.service import Admission

    client, _, principal, app = maintenance_browser
    db, _ = maintenance_db
    with db.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, principal),
        )
    app.state.profile.update(PROFILE)
    create(client, str(tenant), body(past=True))
    allocation = {
        **PROFILE,
        "id": "controlled-admission",
        "global_items": 10000,
        "tenant_items": 10000,
        "global_bytes": 10**8,
        "tenant_bytes": 10**8,
        "byte_multiplier": 4,
        "item_overhead": 2048,
    }
    service = Admission(db, app.state.settings.cipher(), allocation)
    payload = {
        "application": "test-" + str(uuid4()),
        "object": "cpu",
        "node_name": "n1",
        "message": "match",
        "operator": "legacy",
        "time_created": "22-09-2026 00:00:00",
        "severity": "error",
    }
    for _ in range(2):
        status, _ = service.admit(canonical(payload), "v1")
        assert status == 202
        assert process_one(db, tenant, profile=allocation)
    with db.transaction(tenant) as conn:
        row = conn.execute(
            "SELECT * FROM alerts WHERE tenant_id=%s AND application=%s",
            (tenant, payload["application"]),
        ).fetchone()
        assert row["suppressed"] and row["revision"] == 1 and row["projection_version"] == 2
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_alert_inputs WHERE alert_id=%s", (row["id"],)
            ).fetchone()["n"]
            == 2
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM alert_source_events WHERE alert_id=%s", (row["id"],)
            ).fetchone()["n"]
            == 2
        )
