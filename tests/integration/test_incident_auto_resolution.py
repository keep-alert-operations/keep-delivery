"""Real PostgreSQL resolution of incidents whose linked alerts have all resolved."""
# ruff: noqa: F811

from uuid import uuid4

import pytest

from test_product import admit_alert, browser  # noqa: F401

pytestmark = pytest.mark.integration


def create_incident(client, tenant, alert_ids):
    created = client.post(
        f"/api/tenants/{tenant}/incidents",
        json={
            "command_id": str(uuid4()),
            "title": "Auto resolution " + str(uuid4()),
            "severity": "high",
            "alert_ids": alert_ids,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


def resolve_alert(client, tenant, alert):
    base = f"/api/tenants/{tenant}/alerts/{alert}"
    current = client.get(base).json()["data"]
    result = client.post(
        base + "/commands",
        json={
            "command_id": str(uuid4()),
            "expected_revision": current["revision"],
            "kind": "set_status",
            "value": "resolved",
        },
    )
    assert result.status_code == 200, result.text


def incident_state(real_db, incident):
    with real_db.transaction() as conn:
        row = conn.execute(
            "SELECT i.status,i.resolved_at IS NOT NULL AS has_resolved_at,i.retention_at IS NOT NULL AS has_retention,c.cause FROM incidents i JOIN incident_cycles c ON c.incident_id=i.id AND c.id=i.cycle_id WHERE i.id=%s",
            (incident,),
        ).fetchone()
        owners = conn.execute(
            "SELECT count(*) AS n FROM alert_active_ownership WHERE incident_id=%s", (incident,)
        ).fetchone()["n"]
        kinds = [
            r["kind"]
            for r in conn.execute(
                "SELECT kind FROM incident_activity WHERE incident_id=%s ORDER BY recorded_at",
                (incident,),
            )
        ]
    return row, owners, kinds


def test_manual_resolution_of_last_firing_alert_resolves_incident(
    browser, real_db, cipher, profile, v1
):
    """Requirements: REV-46, I2, I3; incident operations 4 and 5."""
    client, _ = browser
    tenant, first = admit_alert(real_db, cipher, profile, v1)
    _, second = admit_alert(real_db, cipher, profile, {**v1, "application": "test-" + str(uuid4())})
    incident = create_incident(client, tenant, [first, second])

    resolve_alert(client, tenant, first)
    row, owners, _ = incident_state(real_db, incident)
    assert row["status"] == "open"
    assert owners == 2

    resolve_alert(client, tenant, second)
    row, owners, kinds = incident_state(real_db, incident)
    assert row["status"] == "resolved"
    assert row["cause"] == "alerts_resolved"
    assert row["has_resolved_at"] and row["has_retention"]
    assert owners == 0
    assert kinds[-1] == "alerts-resolved"
    detail = client.get(f"/api/tenants/{tenant}/incidents/{incident}").json()["data"]
    assert detail["status"] == "resolved"
    assert detail["total_linked"] == "2"
    history = client.get(f"/api/tenants/{tenant}/alerts/{second}/history").json()["data"]
    assert any(entry["kind"] == "incident-alerts-resolved" for entry in history)


def test_source_clear_resolves_incident_and_refire_leaves_it_resolved(
    browser, real_db, cipher, profile, v1
):
    """Requirements: REV-46, I2; incident operations 4; correlation rules 15."""
    client, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    incident = create_incident(client, tenant, [alert])

    admit_alert(real_db, cipher, profile, {**v1, "severity": "clear", "message": "clear"})
    assert client.get(f"/api/tenants/{tenant}/alerts/{alert}").json()["data"]["status"] == (
        "resolved"
    )
    row, owners, kinds = incident_state(real_db, incident)
    assert row["status"] == "resolved"
    assert row["cause"] == "alerts_resolved"
    assert owners == 0
    assert kinds[-2:] == ["linked-alert-status", "alerts-resolved"]

    admit_alert(real_db, cipher, profile, {**v1, "message": "firing again"})
    assert client.get(f"/api/tenants/{tenant}/alerts/{alert}").json()["data"]["status"] == "firing"
    row, owners, _ = incident_state(real_db, incident)
    assert row["status"] == "resolved"
    assert owners == 0


def test_unlink_and_empty_incidents_do_not_resolve(browser, real_db, cipher, profile, v1):
    """Requirements: REV-46, I3; incident operations 5."""
    client, _ = browser
    tenant, resolved = admit_alert(real_db, cipher, profile, v1)
    _, firing = admit_alert(real_db, cipher, profile, {**v1, "application": "test-" + str(uuid4())})
    resolve_alert(client, tenant, resolved)
    incident = create_incident(client, tenant, [resolved, firing])
    revision = client.get(f"/api/tenants/{tenant}/incidents/{incident}").json()["data"]["revision"]
    unlinked = client.post(
        f"/api/tenants/{tenant}/incidents/{incident}/alerts/unlink",
        json={"command_id": str(uuid4()), "expected_revision": revision, "alert_ids": [firing]},
    )
    assert unlinked.status_code == 200, unlinked.text
    assert incident_state(real_db, incident)[0]["status"] == "open"

    empty = create_incident(client, tenant, [])
    assert incident_state(real_db, empty)[0]["status"] == "open"


def test_bulk_resolution_resolves_each_fully_resolved_incident(
    browser, real_db, cipher, profile, v1
):
    """Requirements: REV-46, I2; alert operations 27."""
    client, _ = browser
    tenant, first = admit_alert(real_db, cipher, profile, v1)
    _, second = admit_alert(real_db, cipher, profile, {**v1, "application": "test-" + str(uuid4())})
    _, other = admit_alert(real_db, cipher, profile, {**v1, "application": "test-" + str(uuid4())})
    both = create_incident(client, tenant, [first, second])
    partial = create_incident(client, tenant, [other])
    items = [
        {
            "alert_id": key,
            "expected_revision": client.get(f"/api/tenants/{tenant}/alerts/{key}").json()["data"][
                "revision"
            ],
        }
        for key in (first, second)
    ]
    result = client.post(
        f"/api/tenants/{tenant}/alerts/bulk-commands",
        json={
            "command_id": str(uuid4()),
            "kind": "set_status",
            "value": "resolved",
            "items": items,
        },
    )
    assert result.status_code == 200, result.text
    assert [row["revision"] for row in result.json()["data"]] == [
        client.get(f"/api/tenants/{tenant}/alerts/{key}").json()["data"]["revision"]
        for key in (first, second)
    ]
    assert incident_state(real_db, both)[0]["status"] == "resolved"
    assert incident_state(real_db, partial)[0]["status"] == "open"
