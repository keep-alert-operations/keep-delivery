# ruff: noqa: F811 -- shared isolated database/browser fixtures
"""Expiry seeds commit with maintenance jobs, independently of expiry success."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from test_maintenance_repository import maintenance_db, maintenance_browser, body, create  # noqa: F401
from test_permission_revision import RestrictedDatabase

from appchi.cel import compile_expression
from appchi.maintenance import repository
from appchi.maintenance.models import Create
from appchi.shared.transactions import coordinator
from appchi.visibility import maintenance as producers

pytestmark = [pytest.mark.integration, pytest.mark.skip(
    reason="REV-34: timer-driven expiry visibility is a deferred component, not a current release SLO")]


def visibility_profile():
    return {
        "version": "appchi-visibility-capture-v1",
        "policy_epoch": "synthetic-maintenance",
        "deployment_epoch": "test",
        "qualification": True,
        "inputs": {
            "max_tenant_inputs": 100,
            "max_tenant_candidates": 100,
            "max_tenant_applications": 100,
            "max_tenant_application_bytes": 100000,
            "max_application_bytes": 2000,
            "max_views_per_turn": 10,
        },
        "registry": {
            "lease_seconds": 30,
            "max_active_session_views": 8,
            "max_active_tenant_views": 8,
            "max_tenant_controls": 100,
            "max_descriptor_bytes": 20000,
            "max_request_bytes": 65536,
        },
    }


@pytest.fixture
def capture(maintenance_browser, maintenance_db):
    client, tenant, actor, app = maintenance_browser
    app.state.profile["visibility"] = visibility_profile()
    db, _ = maintenance_db
    app.state.db = RestrictedDatabase(db)
    yield client, tenant, actor, app, db
    app.state.db = db


def seeds(db, tenant):
    with db.transaction(tenant) as conn:
        return conn.execute(
            "SELECT * FROM visibility_inputs WHERE tenant_id=%s ORDER BY recorded_at,id", (tenant,)
        ).fetchall()


def edit(client, tenant, window, **changes):
    response = client.patch(
        f"/api/tenants/{tenant}/maintenance/{window['id']}",
        json={"command_id": str(uuid4()), "expected_revision": window["revision"], **changes},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_create_and_replay_capture_job_original_end_before_processing(capture):
    client, tenant, _, _, db = capture
    request = body()
    window = create(client, tenant, request)
    assert create(client, tenant, request) == window
    captured = seeds(db, tenant)
    assert len(captured) == 1
    original = captured[0]
    with db.transaction(tenant) as conn:
        job = conn.execute(
            "SELECT * FROM maintenance_fanout_jobs WHERE tenant_id=%s AND kind='scheduled_end'",
            (tenant,),
        ).fetchone()
        assert job["state"] == "pending"
        assert original["id"] == job["id"]
        assert original["started_at"] == job["expected_end"] == job["due_at"]
        assert original["deadline_at"] == original["started_at"] + timedelta(seconds=5)
        assert original["original_order"] == 0 and original["cohort_checkpoint"] is None
        assert original["schedule"] == {
            "window_id": window["id"],
            "revision": 1,
            "generation": job["generation"],
        }
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM visibility_applications WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )


def test_metadata_timezone_and_same_value_edits_do_not_restart_expiry(capture):
    client, tenant, _, _, db = capture
    window = create(client, tenant)
    original = seeds(db, tenant)[0]
    window = edit(client, tenant, window, kind="metadata", name="Updated name")
    window = edit(client, tenant, window, kind="timezone", timezone="Asia/Jerusalem")
    unchanged = edit(client, tenant, window, kind="metadata", name=window["name"])
    assert unchanged["revision"] == window["revision"]
    assert seeds(db, tenant) == [original]


@pytest.mark.parametrize("keep_capture", [True, False])
def test_end_extension_disposes_old_generation_even_after_capture_disabled(capture, keep_capture):
    client, tenant, _, app, db = capture
    window = create(client, tenant)
    original = seeds(db, tenant)[0]
    if not keep_capture:
        del app.state.profile["visibility"]
    new_end = (
        (datetime.now(UTC) + timedelta(hours=2)).replace(tzinfo=None, microsecond=0).isoformat()
    )
    window = edit(client, tenant, window, kind="schedule", end_local=new_end)
    captured = seeds(db, tenant)
    assert captured[0]["id"] == original["id"] and captured[0]["disposition"] == "superseded"
    assert captured[0]["started_at"] == original["started_at"]
    with db.transaction(tenant) as conn:
        control = conn.execute(
            "SELECT * FROM maintenance_controls WHERE tenant_id=%s ORDER BY tenant_order DESC LIMIT 1",
            (tenant,),
        ).fetchone()
        assert captured[0]["disposition_proof"]["evidence"] == {
            "generation": original["schedule"]["generation"],
            "reference": "maintenance-control:" + str(control["id"]),
        }
    if keep_capture:
        assert len(captured) == 2 and captured[1]["disposition"] == "pending"
        assert captured[1]["id"] != original["id"]
        assert captured[1]["schedule"]["revision"] == 2
        assert captured[1]["started_at"] == datetime.fromisoformat(window["end_at"])
    else:
        assert len(captured) == 1


def test_disable_enable_and_delete_capture_distinct_lifecycles(capture):
    client, tenant, _, app, db = capture
    window = create(client, tenant)
    path = f"/api/tenants/{tenant}/maintenance/{window['id']}"
    for action, revision in (("disable", "1"), ("enable", "2")):
        response = client.post(
            path + "/" + action, json={"command_id": str(uuid4()), "expected_revision": revision}
        )
        assert response.status_code == 200, response.text
    captured = seeds(db, tenant)
    assert [seed["disposition"] for seed in captured] == ["cancelled", "pending"]
    assert captured[0]["id"] != captured[1]["id"]
    del app.state.profile["visibility"]
    headers = {"Idempotency-Key": str(uuid4()), "If-Match": '"3"'}
    response = client.delete(path, headers=headers)
    assert response.status_code == 200, response.text
    assert client.delete(path, headers=headers).json() == response.json()
    assert [seed["disposition"] for seed in seeds(db, tenant)] == ["cancelled", "cancelled"]


def test_seed_and_schedule_roll_back_together_when_later_write_fails(capture, monkeypatch):
    client, tenant, _, _, db = capture
    original = producers.record_scheduled_end

    def fail_after_seed(*args, **kwargs):
        assert original(*args, **kwargs)["status"] == "recorded"
        raise RuntimeError("Simulated later transaction failure")

    monkeypatch.setattr(producers, "record_scheduled_end", fail_after_seed)
    with pytest.raises(RuntimeError, match="later transaction failure"):
        create(client, tenant)
    with db.transaction(tenant) as conn:
        for table in (
            "maintenance_windows",
            "maintenance_fanout_jobs",
            "visibility_inputs",
            "visibility_input_capacity",
        ):
            assert (
                conn.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE tenant_id=%s", (tenant,)
                ).fetchone()["n"]
                == 0
            )


def test_capture_capacity_gap_preserves_valid_product_schedule(capture):
    client, tenant, _, app, db = capture
    app.state.profile["visibility"]["inputs"]["max_tenant_inputs"] = 1
    create(client, tenant)
    create(client, tenant)
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_fanout_jobs WHERE tenant_id=%s AND kind='scheduled_end'",
                (tenant,),
            ).fetchone()["n"]
            == 2
        )
        capacity = conn.execute(
            "SELECT * FROM visibility_input_capacity WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert capacity["inputs"] == 1 and capacity["seed_gap_attempts"] == 1


@pytest.mark.parametrize("claimed", [False, True])
def test_overdue_expiry_is_not_retroactively_cancelled_by_late_disable(capture, claimed):
    _, tenant, actor, app, db = capture
    # Build a genuine historical schedule/control fixture in one transaction;
    # today's disable must preserve its already-due original visibility boundary.
    request = body()
    request["cel"] = "true"
    program = compile_expression("true", rule=True)
    with db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        then = now - timedelta(hours=3)
        request["start_local"] = (
            (then + timedelta(minutes=10)).replace(tzinfo=None, microsecond=0).isoformat()
        )
        request["end_local"] = (
            (then + timedelta(hours=1)).replace(tzinfo=None, microsecond=0).isoformat()
        )
        principal = conn.execute("SELECT * FROM principals WHERE id=%s", (actor,)).fetchone()
        definition = repository.candidate(Create(**request), then)
        window, _ = repository.persist(
            conn, tenant, principal, then, definition, program, profile=app.state.profile
        )
        original = conn.execute(
            "SELECT * FROM visibility_inputs WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert original["started_at"] < now
    if claimed:
        from appchi.maintenance.processing import claim_due

        assert claim_due(db, tenant).job_id == original["id"]
        with db.transaction(tenant) as conn:
            job = conn.execute(
                "SELECT * FROM maintenance_fanout_jobs WHERE id=%s", (original["id"],)
            ).fetchone()
            assert job["generation"] != original["schedule"]["generation"]
    with db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        window = repository.get_window(conn, tenant, window["id"], lock=True)
        repository.persist(
            conn,
            tenant,
            principal,
            now,
            definition,
            program,
            previous=window,
            enabled=False,
            action="disable",
            profile=None,
        )
        final = conn.execute(
            "SELECT * FROM visibility_inputs WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert final["disposition"] == "pending" and final["disposition_proof"] is None
        assert final["started_at"] == original["started_at"] and final["deadline_at"] < now
