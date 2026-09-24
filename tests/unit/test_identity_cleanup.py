# ruff: noqa: F811 -- imported pytest fixtures are injected by name.
"""Real PostgreSQL accepted-access cleanup and protected audit expiry tests."""

from pathlib import Path
from uuid import uuid4

import pytest

from appchi.identity.cleanup import (
    assignment_proof,
    cleanup_assignment,
    cleanup_assignments,
    hold_audit,
    purge_audit_batch,
    release_audit_hold,
    retire_audit,
    visible_assignee,
)
from appchi.maintenance.runtime import prepare_for_tenant
from appchi.shared.errors import AppError

from test_maintenance_repository import Store, maintenance_browser, maintenance_db  # noqa: F401
from test_maintenance_processing import PROFILE, publish_source

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def cleanup_schema(maintenance_db):
    db, _ = maintenance_db
    draft = Path(__file__).resolve().parents[3] / "migrations/0019_identity_cleanup.sql.draft"
    if draft.exists():
        with db.transaction() as conn:
            if not conn.execute("SELECT to_regclass('audit_retirement_jobs') AS name").fetchone()[
                "name"
            ]:
                conn.execute(draft.read_text(), prepare=False)


def assigned(maintenance_browser, maintenance_db):
    from appchi.alerts.routes import router as alert_router
    from appchi.incidents.routes import router as incident_router

    client, tenant, user, app = maintenance_browser
    db, _ = maintenance_db
    app.include_router(alert_router)
    app.include_router(incident_router)
    app.state.profile.update(PROFILE)
    catalog = prepare_for_tenant(db, tenant, PROFILE)
    alert, _, _ = publish_source(db, tenant, None, catalog)
    response = client.post(
        f"/api/tenants/{tenant}/alerts/{alert['id']}/commands",
        json={
            "command_id": str(uuid4()),
            "expected_revision": "1",
            "kind": "set_assignee",
            "value": str(user),
        },
    )
    assert response.status_code == 200, response.text
    incident = client.post(
        f"/api/tenants/{tenant}/incidents",
        json={
            "command_id": str(uuid4()),
            "title": "Cleanup",
            "severity": "medium",
            "alert_ids": [str(alert["id"])],
        },
    )
    assert incident.status_code == 201, incident.text
    incident_id = incident.json()["data"]["id"]
    response = client.post(
        f"/api/tenants/{tenant}/incidents/{incident_id}/assignment",
        json={"command_id": str(uuid4()), "expected_revision": "1", "assignee_id": str(user)},
    )
    assert response.status_code == 200, response.text
    return db, tenant, user, alert["id"], incident_id


def test_lost_access_cleanup_is_atomic_idempotent_and_preserves_inactivity(
    maintenance_browser, maintenance_db
):
    db, tenant, user, alert_id, incident_id = assigned(maintenance_browser, maintenance_db)
    with db.transaction(tenant) as conn:
        before_alert = conn.execute("SELECT * FROM alerts WHERE id=%s", (alert_id,)).fetchone()
        before_incident = conn.execute(
            "SELECT * FROM incidents WHERE id=%s", (incident_id,)
        ).fetchone()
        conn.execute(
            "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, user)
        )
        assert visible_assignee(conn, tenant, user) == (None, True)
    assert cleanup_assignment(db, tenant, "alert", alert_id, user, profile=PROFILE)
    assert cleanup_assignment(db, tenant, "incident", incident_id, user, profile=PROFILE)
    assert not cleanup_assignment(db, tenant, "alert", alert_id, user, profile=PROFILE)
    assert not cleanup_assignment(db, tenant, "incident", incident_id, user, profile=PROFILE)
    with db.transaction(tenant) as conn:
        alert = conn.execute("SELECT * FROM alerts WHERE id=%s", (alert_id,)).fetchone()
        incident = conn.execute("SELECT * FROM incidents WHERE id=%s", (incident_id,)).fetchone()
        assert alert["assignee_id"] is None and incident["assignee_id"] is None
        assert (
            alert["revision"] == before_alert["revision"] + 1
            and incident["revision"] == before_incident["revision"] + 1
        )
        assert alert["last_source_activity"] == before_alert["last_source_activity"]
        assert incident["last_activity"] == before_incident["last_activity"]
        event = conn.execute(
            "SELECT * FROM alert_activity WHERE alert_id=%s AND kind='eligibility_lost'",
            (alert_id,),
        ).fetchall()
        assert (
            len(event) == 1
            and event[0]["evidence"]["context"]["before_assignee_label"] == "Test editor"
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM incident_activity WHERE incident_id=%s AND kind='eligibility_lost'",
                (incident_id,),
            ).fetchone()["n"]
            == 1
        )


def test_alternate_grant_stale_directory_and_replacement_are_preserved(
    maintenance_browser, maintenance_db
):
    db, tenant, user, alert_id, _ = assigned(maintenance_browser, maintenance_db)
    team = uuid4()
    with db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible) VALUES(%s,'synthetic',%s,'team','Alternate',true)",
            (team, str(team)),
        )
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, team),
        )
        conn.execute(
            "INSERT INTO directory_memberships(team_id,user_id) VALUES(%s,%s)", (team, user)
        )
        conn.execute(
            "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, user)
        )
        conn.execute(
            "UPDATE principals SET verified_at=clock_timestamp()-interval '400 days' WHERE id=%s",
            (user,),
        )
        assert assignment_proof(conn, tenant, user)["qualifies"]
    assert not cleanup_assignment(db, tenant, "alert", alert_id, user, profile=PROFILE)
    assert not cleanup_assignment(db, tenant, "alert", alert_id, uuid4(), profile=PROFILE)
    with db.transaction(tenant) as conn:
        conn.execute("UPDATE principals SET eligible=false WHERE id=%s", (user,))
    assert cleanup_assignment(db, tenant, "alert", alert_id, user, profile=PROFILE)


def test_assignment_sweep_is_bounded_and_regained_access_is_not_cleared(
    maintenance_browser, maintenance_db
):
    db, tenant, user, _, _ = assigned(maintenance_browser, maintenance_db)
    first = cleanup_assignments(db, profile=PROFILE, limit=1, tenant_id=tenant)
    assert first.scanned == 1 and first.cleared == 0 and first.cursor is not None
    second = cleanup_assignments(
        db, profile=PROFILE, limit=1, cursor=first.cursor, tenant_id=tenant
    )
    assert second.scanned == 1 and second.cleared == 0 and second.cursor is None
    with db.transaction(tenant) as conn:
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (user,))
        conn.execute(
            "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, user)
        )
    assert cleanup_assignments(db, profile=PROFILE, tenant_id=tenant).cleared == 0


def audit(db, tenant, *, hours=8784):
    event = uuid4()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE capability_gates SET ready=true WHERE capability IN ('audit_retention','recovery')"
        )
        conn.execute(
            "INSERT INTO audit_events(id,tenant_id,actor_label,kind,evidence,recorded_at) VALUES(%s,%s,'system','test-event','{}',clock_timestamp()-make_interval(hours=>%s))",
            (event, tenant, hours),
        )
    return event


def test_audit_elapsed_deadline_hold_and_minimal_fence(maintenance_browser, maintenance_db):
    _, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    store = Store()
    fresh = audit(db, tenant, hours=8759)
    expired = audit(db, tenant)
    with db.transaction() as conn:
        hold_audit(conn, expired, "reference", "retained-reference")
    assert not retire_audit(db, store, fresh)
    assert not retire_audit(db, store, expired)
    assert store.events == []
    with db.transaction() as conn:
        release_audit_hold(conn, expired, "reference", "retained-reference")
    assert retire_audit(db, store, expired)
    assert retire_audit(db, store, expired)
    assert [event.state for event in store.events] == ["intent", "final"]
    assert store.events[0].id != store.events[1].id
    with db.transaction() as conn:
        assert not conn.execute("SELECT 1 FROM audit_events WHERE id=%s", (expired,)).fetchone()
        assert conn.execute("SELECT 1 FROM audit_events WHERE id=%s", (fresh,)).fetchone()
        fence = conn.execute(
            "SELECT * FROM audit_expiry_fences WHERE event_id=%s", (expired,)
        ).fetchone()
        assert "evidence" not in fence and "actor_label" not in fence


def test_audit_final_failure_is_resumable_and_platform_scope_is_global(
    maintenance_browser, maintenance_db
):
    db, _ = maintenance_db
    event = audit(db, None)
    store = Store()
    store.fail_final = True
    with pytest.raises(AppError):
        retire_audit(db, store, event)
    with db.transaction() as conn:
        job = conn.execute(
            "SELECT * FROM audit_retirement_jobs WHERE event_id=%s", (event,)
        ).fetchone()
        global_id = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
        assert (
            job["state"] == "retired"
            and job["event_tenant_id"] is None
            and job["scope_tenant_id"] == global_id
        )
        assert conn.execute("SELECT 1 FROM audit_events WHERE id=%s", (event,)).fetchone()
        assert conn.execute(
            "SELECT 1 FROM audit_expiry_fences WHERE event_id=%s", (event,)
        ).fetchone()
        with pytest.raises(AppError, match="not_found"):
            hold_audit(conn, event, "reference", "too-late")
    store.fail_final = False
    assert retire_audit(db, store, event)
    finals = [value.id for value in store.events if value.state == "final"]
    assert finals[0] == finals[1]
    with db.transaction() as conn:
        assert not conn.execute("SELECT 1 FROM audit_events WHERE id=%s", (event,)).fetchone()


def test_audit_new_hold_after_external_intent_cancels_before_delete(
    maintenance_browser, maintenance_db
):
    _, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    event = audit(db, tenant)

    class RacingStore(Store):
        def append(self, evidence):
            if evidence.state == "intent":
                with db.transaction() as conn:
                    hold_audit(conn, event, "recovery", "restore-proof")
            return super().append(evidence)

    store = RacingStore()
    assert not retire_audit(db, store, event)
    assert [value.state for value in store.events] == ["intent", "cancel"]
    with db.transaction() as conn:
        assert conn.execute("SELECT 1 FROM audit_events WHERE id=%s", (event,)).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM audit_expiry_fences WHERE event_id=%s", (event,)
        ).fetchone()


def test_audit_batch_gate_and_missing_store_are_fail_closed(maintenance_browser, maintenance_db):
    _, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    event = audit(db, tenant)
    with pytest.raises(AppError, match="protection_required"):
        retire_audit(db, None, event)
    with db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='audit_retention'")
    with pytest.raises(AppError, match="retention_unready"):
        purge_audit_batch(db, Store())


def test_audit_api_retains_protected_expiry_and_honors_retirement_fence(
    maintenance_browser, maintenance_db
):
    from appchi.tenant_routes import router

    client, tenant, user, app = maintenance_browser
    db, _ = maintenance_db
    app.include_router(router)
    protected, expired = audit(db, tenant), audit(db, tenant)
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='admin' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, user),
        )
        hold_audit(conn, protected, "reference", "history-proof")
    path = f"/api/tenants/{tenant}/audit?period=retained"
    result = client.get(path)
    assert result.status_code == 200, result.text
    ids = {row["id"] for row in result.json()["data"]}
    assert str(protected) in ids and str(expired) not in ids
    with db.transaction(tenant) as conn:
        release_audit_hold(conn, protected, "reference", "history-proof")
    assert retire_audit(db, Store(), protected)
    # Controlled restored content cannot bypass the minimal retirement fence.
    with db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO audit_events(id,tenant_id,actor_label,kind,evidence) VALUES(%s,%s,'system','restored','{}')",
            (protected, tenant),
        )
    assert str(protected) not in {row["id"] for row in client.get(path).json()["data"]}
