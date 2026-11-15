"""Maintenance test reads current authorized alert state after compilation."""
# ruff: noqa: F811

import pytest

from keep.maintenance import routes
from test_maintenance_repository import maintenance_db, body  # noqa: F401
from test_product import browser, admit_alert  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_db(maintenance_db):
    return maintenance_db[0]


def candidate(alert):
    value = body()
    value.pop("command_id")
    return {
        **value,
        "alert_id": alert,
        "cel": f'alert.id == "{alert}" && alert.message == "changed during compilation"',
    }


def test_selected_alert_is_fresh_without_mutating_state(
    browser, real_db, cipher, profile, v1, monkeypatch
):
    client, actor = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    original = routes.compiled
    with real_db.transaction() as conn:
        activity = conn.execute(
            "SELECT revoked_at,absolute_at FROM auth_sessions WHERE principal_id=%s", (actor,)
        ).fetchone()
        before = conn.execute("SELECT revision FROM alerts WHERE id=%s", (alert,)).fetchone()

    def compile_and_change(source):
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE alerts SET message='changed during compilation' WHERE id=%s", (alert,)
            )
        return original(source)

    monkeypatch.setattr(routes, "compiled", compile_and_change)
    response = client.post(f"/api/tenants/{tenant}/maintenance/test", json=candidate(alert))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["matches"] is True
    assert response.json()["data"]["qualification_proven"] is False
    with real_db.transaction() as conn:
        assert (
            conn.execute("SELECT revision FROM alerts WHERE id=%s", (alert,)).fetchone() == before
        )
        assert (
            conn.execute(
                "SELECT revoked_at,absolute_at FROM auth_sessions WHERE principal_id=%s", (actor,)
            ).fetchone()
            == activity
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_controls WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )


def test_selected_alert_test_reauthorizes_after_compilation(
    browser, real_db, cipher, profile, v1, monkeypatch
):
    client, actor = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    original = routes.compiled

    def compile_and_revoke(source):
        with real_db.transaction() as conn:
            conn.execute(
                "SELECT scope FROM authorization_guards WHERE scope='identity' FOR UPDATE NOWAIT"
            )
            conn.execute("UPDATE principals SET eligible=false WHERE id=%s", (actor,))
        return original(source)

    monkeypatch.setattr(routes, "compiled", compile_and_revoke)
    response = client.post(f"/api/tenants/{tenant}/maintenance/test", json=candidate(alert))
    assert response.status_code == 401, response.text
