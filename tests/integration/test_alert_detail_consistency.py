"""Alert detail cannot mix a captured alert with a newer maintenance timeline."""
# ruff: noqa: F811

from uuid import uuid4

import pytest

from appchi.alerts import routes
from test_maintenance_repository import maintenance_browser, maintenance_db  # noqa: F401
from test_maintenance_processing import setup, publish_source

pytestmark = pytest.mark.integration


def current_alert(maintenance_browser, maintenance_db):
    db, client, tenant, window, catalog, _ = setup(maintenance_browser, maintenance_db)
    client.app.include_router(routes.router)
    alert, _, _ = publish_source(db, tenant, None, catalog)
    return db, client, tenant, window, alert


def test_detail_uses_one_snapshot_during_concurrent_disable(
    maintenance_browser, maintenance_db, monkeypatch
):
    db, client, tenant, window, alert = current_alert(maintenance_browser, maintenance_db)
    original = routes.derived_read
    called = False

    def concurrently_disable(conn, captured):
        nonlocal called
        if not called:
            called = True
            response = client.post(
                f"/api/tenants/{tenant}/maintenance/{window['id']}/disable",
                json={"command_id": str(uuid4()), "expected_revision": "1"},
            )
            assert response.status_code == 200, response.text
        return original(conn, captured)

    monkeypatch.setattr(routes, "derived_read", concurrently_disable)
    path = f"/api/tenants/{tenant}/alerts/{alert['id']}"
    first = client.get(path)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["suppressed"] is True
    assert first.json()["data"]["maintenance_updating"] is False
    # REV-34: disable stops new coverage without sweeping existing alerts, and
    # reads use persisted suppression; only a later source input releases it.
    second = client.get(path)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["suppressed"] is True
    assert second.json()["data"]["maintenance_updating"] is False
    with db.transaction(tenant) as conn:
        materialized = conn.execute(
            "SELECT suppressed,revision FROM alerts WHERE tenant_id=%s AND id=%s",
            (tenant, alert["id"]),
        ).fetchone()
        assert materialized["suppressed"] is True and materialized["revision"] == alert["revision"]

