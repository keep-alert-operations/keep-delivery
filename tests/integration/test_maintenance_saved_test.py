"""Saved-window tests preserve persisted scheduling and perform no commands."""
# ruff: noqa: F811

from uuid import uuid4

import pytest

from appchi.maintenance import routes
from test_maintenance_repository import maintenance_db, maintenance_browser  # noqa: F401
from test_maintenance_processing import setup, publish_source

pytestmark = pytest.mark.integration


def selected(maintenance_browser, maintenance_db):
    db, client, tenant, window, catalog, _ = setup(maintenance_browser, maintenance_db)
    alert, _, _ = publish_source(db, tenant, None, catalog)
    return db, client, tenant, window, alert


def test_saved_ended_window_viewer_test_is_read_only(maintenance_browser, maintenance_db):
    db, client, tenant, window, alert = selected(maintenance_browser, maintenance_db)
    path = f"/api/tenants/{tenant}/maintenance/{window['id']}/test"
    body = {"alert_id": str(alert["id"]), "expected_revision": window["revision"]}
    with db.transaction() as conn:
        conn.execute("UPDATE tenant_grants SET role='view' WHERE tenant_id=%s", (tenant,))
        conn.execute(
            "UPDATE maintenance_windows SET end_at=clock_timestamp()-interval '1 second',enabled=false WHERE id=%s",
            (window["id"],),
        )
        before = conn.execute(
            "SELECT admission_order,last_clock FROM tenant_work_state WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        activity = conn.execute(
            "SELECT activity_at FROM auth_sessions WHERE principal_id=%s", (maintenance_browser[2],)
        ).fetchone()
        revisions = conn.execute(
            "SELECT count(*) AS n FROM maintenance_revisions WHERE window_id=%s", (window["id"],)
        ).fetchone()
        commands = conn.execute("SELECT count(*) AS n FROM command_receipts").fetchone()
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["matches"] and data["firing"]
    assert not data["covers_selected_time"] and not data["enabled"]
    assert not data["qualification_proven"]
    assert response.json()["meta"]["revision"] == window["revision"]
    assert response.json()["meta"]["alert_revision"] == str(alert["revision"])
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
                "SELECT activity_at FROM auth_sessions WHERE principal_id=%s",
                (maintenance_browser[2],),
            ).fetchone()
            == activity
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_revisions WHERE window_id=%s",
                (window["id"],),
            ).fetchone()
            == revisions
        )
        assert conn.execute("SELECT count(*) AS n FROM command_receipts").fetchone() == commands
    assert client.post(path, json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post(path, json={**body, "expected_revision": "999"}).status_code == 409
    assert client.post(path, json={**body, "alert_id": str(uuid4())}).status_code == 404


@pytest.mark.parametrize("change,expected", [("alert", 200), ("revision", 409), ("access", 401)])
def test_saved_window_test_rechecks_after_compilation(
    maintenance_browser, maintenance_db, monkeypatch, change, expected
):
    db, client, tenant, window, alert = selected(maintenance_browser, maintenance_db)
    original = routes.compiled

    def compile_and_change(source):
        with db.transaction() as conn:
            if change == "alert":
                conn.execute(
                    "UPDATE alerts SET message='no longer matches',revision=revision+1 WHERE id=%s",
                    (alert["id"],),
                )
            elif change == "revision":
                conn.execute(
                    "UPDATE maintenance_windows SET revision=revision+1 WHERE id=%s",
                    (window["id"],),
                )
            else:
                conn.execute(
                    "UPDATE principals SET eligible=false WHERE id=%s", (maintenance_browser[2],)
                )
        return original(source)

    monkeypatch.setattr(routes, "compiled", compile_and_change)
    response = client.post(
        f"/api/tenants/{tenant}/maintenance/{window['id']}/test",
        json={"expected_revision": window["revision"], "alert_id": str(alert["id"])},
    )
    assert response.status_code == expected, response.text
    if change == "alert":
        assert not response.json()["data"]["matches"]
        assert response.json()["meta"]["alert_revision"] == str(alert["revision"] + 1)
