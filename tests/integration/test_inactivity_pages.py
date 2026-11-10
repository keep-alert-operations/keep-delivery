# ruff: noqa: F811 -- imported pytest fixture is injected by name
from uuid import uuid4
from datetime import timedelta
import pytest
from keep.inactivity import expire_alert, expire_incident
from test_product import browser, admit_alert  # noqa: F401

pytestmark = pytest.mark.integration


def test_incident_inactivity_releases_claim_without_resolving_alert(
    browser, real_db, cipher, profile, v1
):
    """Requirements: I4, REV-34; incident operations 12."""
    client, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}"
    response = client.post(
        base + "/incidents",
        json={
            "command_id": str(uuid4()),
            "title": "Expiry",
            "severity": "medium",
            "description": "",
            "alert_ids": [alert],
        },
    )
    assert response.status_code == 201, response.text
    incident = response.json()["data"]["id"]
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE incidents SET last_activity=clock_timestamp()-interval '31 days' WHERE id=%s",
            (incident,),
        )
    assert expire_incident(real_db, tenant, incident)
    assert not expire_incident(real_db, tenant, incident)
    assert client.get(base + "/alerts/" + alert).json()["data"]["status"] == "firing"
    assert client.get(base + "/incidents/" + incident).json()["data"]["status"] == "resolved"
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM alert_active_ownership WHERE alert_id=%s", (alert,)
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT cause FROM incident_cycles WHERE incident_id=%s", (incident,)
            ).fetchone()["cause"]
            == "inactivity"
        )


def test_alert_inactivity_skips_extended_deadline_then_resolves(
    browser, real_db, cipher, profile, v1
):
    client, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    assert not expire_alert(real_db, tenant, alert)
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE alerts SET last_source_activity=clock_timestamp()-interval '31 days' WHERE id=%s",
            (alert,),
        )
    assert expire_alert(real_db, tenant, alert)
    assert not expire_alert(real_db, tenant, alert)
    row = client.get(f"/api/tenants/{tenant}/alerts/{alert}").json()["data"]
    assert row["status"] == "resolved"
    with real_db.transaction() as conn:
        value = conn.execute("SELECT * FROM alerts WHERE id=%s", (alert,)).fetchone()
        assert value["retention_at"] - value["resolved_at"] == timedelta(days=90)


def test_configuration_and_incident_pages_have_continuations(browser, real_db):
    client, _ = browser
    with real_db.transaction() as conn:
        tenant = str(conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"])
    base = f"/api/tenants/{tenant}"
    prefix = str(uuid4())
    for suffix in ("a", "b", "c"):
        response = client.post(
            base + "/incidents",
            json={
                "command_id": str(uuid4()),
                "title": prefix + " " + suffix,
                "severity": "medium",
                "description": "",
                "alert_ids": [],
            },
        )
        assert response.status_code == 201, response.text
    first = client.get(base + "/incidents", params={"q": prefix, "limit": 1}).json()
    assert first["next_cursor"]
    second = client.get(
        base + "/incidents", params={"q": prefix, "limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert first["data"][0]["id"] != second["data"][0]["id"]
    client.post(
        base + "/incidents",
        json={
            "command_id": str(uuid4()),
            "title": prefix + " d",
            "severity": "medium",
            "description": "",
            "alert_ids": [],
        },
    )
    assert (
        client.get(
            base + "/incidents", params={"q": prefix, "limit": 1, "cursor": first["next_cursor"]}
        ).status_code
        == 200
    )


def test_access_audit_requires_admin_and_excludes_expired_records(browser, real_db):
    client, principal = browser
    with real_db.transaction() as conn:
        tenant = str(conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"])
        marker = str(uuid4())
        conn.execute(
            "INSERT INTO audit_events(tenant_id,actor_label,kind,evidence) VALUES(%s,'test',%s,'{}')",
            (tenant, marker),
        )
        conn.execute(
            "INSERT INTO audit_events(tenant_id,actor_label,kind,evidence,recorded_at) VALUES(%s,'test',%s,'{}',clock_timestamp()-interval '366 days')",
            (tenant, marker + "-old"),
        )
    assert client.get(f"/api/tenants/{tenant}/audit").status_code == 403
    assert client.get("/api/admin/access-audit").status_code == 403
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (principal,))
    body = client.get(f"/api/tenants/{tenant}/audit?limit=200").json()
    kinds = {event["kind"] for event in body["data"]}
    assert marker in kinds and marker + "-old" not in kinds
    assert client.get("/api/admin/access-audit").status_code == 200


def test_unknown_and_ambiguous_queries_rejected(browser, real_db):
    client, _ = browser
    with real_db.transaction() as conn:
        tenant = str(conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"])
    assert client.get(f"/api/tenants/{tenant}/incidents?unrecognized=true").status_code == 422
    assert client.get(f"/api/tenants/{tenant}/incidents?limit=1&limit=2").status_code == 400
