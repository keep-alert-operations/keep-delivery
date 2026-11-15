"""Read-only schedule preview uses the same temporal transition as real writes."""
# ruff: noqa: F811

from uuid import uuid4

import pytest

from keep.maintenance import routes
from test_maintenance_repository import maintenance_db, maintenance_browser, create, body  # noqa: F401

pytestmark = pytest.mark.integration


def edit(kind="timezone", **changes):
    return {"command_id": str(uuid4()), "expected_revision": "1", "kind": kind, **changes}


def test_timezone_preview_preserves_utc_and_makes_no_writes(maintenance_browser, maintenance_db):
    client, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    window = create(client, tenant)
    url = f"/api/tenants/{tenant}/maintenance/{window['id']}/validate"
    with db.transaction() as conn:
        before = conn.execute(
            "SELECT admission_order,last_clock FROM tenant_work_state WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        activity = conn.execute(
            "SELECT revoked_at,absolute_at FROM auth_sessions WHERE principal_id=%s", (maintenance_browser[2],)
        ).fetchone()
        conn.execute("UPDATE tenant_grants SET role='view' WHERE tenant_id=%s", (tenant,))
    command = edit(timezone="Asia/Jerusalem")
    response = client.post(url, json=command)
    assert response.status_code == 200, response.text
    preview = response.json()["data"]
    assert preview["valid"]
    assert preview["start_at"] == window["start_at"]
    assert preview["end_at"] == window["end_at"]
    assert preview["schedule"] == window["schedule_evidence"]
    assert preview["start_display"]["timezone"] == "Asia/Jerusalem"
    assert preview["start_display"]["original_timezone"] == "UTC"
    with db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT admission_order,last_clock FROM tenant_work_state WHERE tenant_id=%s",
                (tenant,),
            ).fetchone()
            == before
        )
        assert (
            conn.execute(
                "SELECT revoked_at,absolute_at FROM auth_sessions WHERE principal_id=%s",
                (maintenance_browser[2],),
            ).fetchone()
            == activity
        )
        assert (
            conn.execute(
                "SELECT 1 FROM command_receipts WHERE command_id=%s", (command["command_id"],)
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_revisions WHERE window_id=%s",
                (window["id"],),
            ).fetchone()["n"]
            == 1
        )
    assert client.post(url, json={**command, "expected_revision": "2"}).status_code == 409
    assert client.post(url, json=command, headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_preview_enforces_started_and_ended_boundaries(maintenance_browser, maintenance_db):
    client, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    window = create(client, tenant, body(past=True))
    url = f"/api/tenants/{tenant}/maintenance/{window['id']}/validate"
    locked = client.post(url, json=edit("schedule", cel="false"))
    assert locked.status_code == 200
    assert locked.json()["data"]["diagnostics"][0]["code"] == "maintenance_start_locked"
    with db.transaction() as conn:
        conn.execute(
            "UPDATE maintenance_windows SET end_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (window["id"],),
        )
    ended = client.post(url, json=edit("schedule", end_local="2030-01-01T00:00:00"))
    assert ended.json()["data"]["diagnostics"][0]["code"] == "maintenance_ended"
    assert client.post(url, json=edit("metadata", name="Ended but retained")).json()["data"][
        "valid"
    ]


def test_edit_preview_dst_gap_fold_and_unchanged_endpoint(maintenance_browser):
    client, tenant, _, _ = maintenance_browser
    definition = {
        **body(),
        "start_local": "2030-01-01T00:00:00",
        "end_local": "2030-12-01T00:00:00",
    }
    window = create(client, tenant, definition)
    url = f"/api/tenants/{tenant}/maintenance/{window['id']}/validate"
    gap = client.post(
        url, json=edit("schedule", timezone="America/New_York", end_local="2030-03-10T02:30:00")
    )
    assert gap.json()["data"]["diagnostics"][0]["code"] == "nonexistent_local_time"
    fold_body = edit("schedule", timezone="America/New_York", end_local="2030-11-03T01:30:00")
    fold = client.post(url, json=fold_body)
    assert fold.json()["data"]["diagnostics"][0]["code"] == "ambiguous_local_time"
    chosen = client.post(url, json={**fold_body, "end_occurrence": "second"}).json()["data"]
    assert chosen["valid"]
    assert chosen["start_at"] == window["start_at"]
    assert chosen["schedule"]["start"] == window["schedule_evidence"]["start"]
    assert chosen["end_at"] == "2030-11-03T06:30:00.000000Z"


@pytest.mark.parametrize("change", ["revision", "access"])
def test_edit_preview_rechecks_after_compilation(
    maintenance_browser, maintenance_db, monkeypatch, change
):
    client, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    window = create(client, tenant)
    original = routes.compiled

    def compile_and_change(source):
        with db.transaction() as conn:
            # Compilation holds neither the identity guard nor a resource lock.
            conn.execute(
                "SELECT scope FROM authorization_guards WHERE scope='identity' FOR UPDATE NOWAIT"
            )
            if change == "revision":
                conn.execute(
                    "UPDATE maintenance_windows SET revision=revision+1 WHERE id=%s",
                    (window["id"],),
                )
            else:
                conn.execute("DELETE FROM tenant_grants WHERE tenant_id=%s", (tenant,))
        return original(source)

    monkeypatch.setattr(routes, "compiled", compile_and_change)
    response = client.post(
        f"/api/tenants/{tenant}/maintenance/{window['id']}/validate", json=edit(timezone="UTC")
    )
    assert response.status_code == (409 if change == "revision" else 404), response.text
