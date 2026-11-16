import base64
import hashlib
import secrets
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from keep.config import Settings
from keep.main import create_app
from keep.ingestion.service import Admission
from keep.ingestion.native import canonical
from keep.alerts.processor import process_one

pytestmark = pytest.mark.integration


@pytest.fixture
def browser(real_db, cipher, browser_settings):
    principal = uuid4()
    handle = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    hashed = hashlib.sha256(handle.encode()).digest()
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='human_auth'")
        conn.execute(
            "INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible,evidence_generation,verified_at) VALUES(%s,'test',%s,'user','Synthetic user',true,1,clock_timestamp())",
            (principal, str(principal)),
        )
        conn.execute(
            "INSERT INTO auth_sessions(handle_hash,principal_id,csrf_hash,csrf_cipher,auth_time,absolute_at,deployment_epoch) VALUES(%s,%s,%s,%s,clock_timestamp(),clock_timestamp()+interval '1 day','test')",
            (
                hashed,
                principal,
                hashlib.sha256(csrf.encode()).digest(),
                cipher.seal(csrf.encode(), "session-csrf:" + hashed.hex()),
            ),
        )
    settings = Settings(
        database_url="unused",
        wrapping_key=base64.b64encode(b"k" * 32).decode(),
        wrapping_key_id="test",
        deployment_epoch="test",
        public_origin="https://app.test",
        capacity_profile=Path("qualification/development-capacity.json").resolve(),
        # Bound before startup, so the lifespan creates process-owned clients.
        **browser_settings,
    )
    with TestClient(create_app(settings, real_db), base_url="https://app.test") as client:
        client.cookies.set("__Host-keep_session", handle)
        client.headers.update({"Origin": "https://app.test", "X-CSRF-Token": csrf})
        yield client, principal
    with real_db.transaction() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE handle_hash=%s", (hashed,))


def admit_alert(real_db, cipher, profile, v1):
    result = Admission(real_db, cipher, profile).admit(canonical(v1), "v1")[1]
    with real_db.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    while process_one(real_db, tenant):
        pass
    outcome = Admission(real_db, cipher, profile).receipt(
        result["request_id"], result["receipt_token"], "v1"
    )
    return str(tenant), outcome["items"][0]["alert_id"]


def test_alert_command_replay_noop_and_notes(browser, real_db, cipher, profile, v1):
    client, principal = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}/alerts/{alert}"
    body = {
        "command_id": str(uuid4()),
        "expected_revision": "1",
        "kind": "set_assignee",
        "value": str(principal),
    }
    response = client.post(base + "/commands", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["revision"] == "2"
    assert client.post(base + "/commands", json=body).json() == response.json()
    stale = client.post(base + "/commands", json={**body, "command_id": str(uuid4())})
    assert stale.status_code == 409
    note = client.post(
        base + "/notes",
        json={"command_id": str(uuid4()), "expected_revision": "2", "text": "  Investigating  "},
    )
    assert note.status_code == 201, note.text
    assert note.json()["data"]["text"] == "Investigating"
    history = client.get(base + "/history?limit=1")
    assert history.status_code == 200, history.text
    assert history.json()["next_cursor"]
    assert (
        client.get(
            base + "/history", params={"limit": 1, "cursor": history.json()["next_cursor"]}
        ).status_code
        == 200
    )
    assert client.get(base + "/source-events").json()["data"][0].get("raw_json") is None


def test_csrf_and_tenant_isolation(browser, real_db, cipher, profile, v1, tenant):
    client, _ = browser
    global_id, alert = admit_alert(real_db, cipher, profile, v1)
    hidden = client.get(f"/api/tenants/{tenant}/alerts/{alert}")
    assert hidden.status_code == 404
    bad = client.post(
        f"/api/tenants/{global_id}/alerts/{alert}/commands",
        headers={"X-CSRF-Token": "bad"},
        json={
            "command_id": str(uuid4()),
            "expected_revision": "1",
            "kind": "set_status",
            "value": "resolved",
        },
    )
    assert bad.status_code == 403
    assert "Investigating" not in hidden.text


def test_incident_link_conflict_and_resolution(browser, real_db, cipher, profile, v1):
    client, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}/incidents"
    created = client.post(
        base,
        json={
            "command_id": str(uuid4()),
            "title": "Incident",
            "severity": "high",
            "alert_ids": [alert],
        },
    )
    assert created.status_code == 201, created.text
    incident = created.json()["data"]["id"]
    conflict = client.post(
        base,
        json={
            "command_id": str(uuid4()),
            "title": "Other",
            "severity": "low",
            "alert_ids": [alert],
        },
    )
    assert conflict.status_code == 409, conflict.text
    detail = client.get(base + "/" + incident)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["total_linked"] == "1"
    result = client.post(
        base + "/" + incident + "/status",
        json={"command_id": str(uuid4()), "expected_revision": "1", "status": "resolved"},
    )
    assert result.status_code == 200, result.text
    assert client.get(f"/api/tenants/{tenant}/alerts/{alert}").json()["data"]["status"] == "firing"


def test_strict_http_rejects_duplicates(browser):
    client, _ = browser
    result = client.post(
        "/api/ingest/v1/alerts",
        content=b'{"severity":"error","severity":"clear"}',
        headers={"Content-Type": "application/json"},
    )
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "duplicate_key"


def test_preset_dashboard_saved_reference(browser, real_db):
    client, _ = browser
    with real_db.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    base = f"/api/tenants/{tenant}"
    result = client.post(
        base + "/presets",
        json={
            "command_id": str(uuid4()),
            "name": "Preset " + str(uuid4()),
            "cel": "true",
            "sort": [{"field": "timestamp", "direction": "desc"}],
            "columns": ["message", "status"],
        },
    )
    assert result.status_code == 201, result.text
    preset = result.json()["data"]["id"]
    result = client.post(
        base + "/dashboards",
        json={
            "command_id": str(uuid4()),
            "name": "Dashboard " + str(uuid4()),
            "panels": [
                {
                    "id": str(uuid4()),
                    "preset_id": preset,
                    "title": None,
                    "x": 0,
                    "y": 0,
                    "w": 3,
                    "h": 2,
                    "thresholds": [],
                }
            ],
        },
    )
    assert result.status_code == 201, result.text
    assert result.json()["data"]["panels"][0]["x"] == 0


def test_bulk_lifecycle_refreshes_incident_activity(browser, real_db, cipher, profile, v1):
    client, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    created = client.post(
        f"/api/tenants/{tenant}/incidents",
        json={
            "command_id": str(uuid4()),
            "title": "Bulk link",
            "severity": "high",
            "alert_ids": [alert],
        },
    )
    incident = created.json()["data"]["id"]
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE incidents SET last_activity=clock_timestamp()-interval '10 days' WHERE id=%s",
            (incident,),
        )
    current = client.get(f"/api/tenants/{tenant}/alerts/{alert}").json()["data"]
    result = client.post(
        f"/api/tenants/{tenant}/alerts/bulk-commands",
        json={
            "command_id": str(uuid4()),
            "kind": "set_status",
            "value": "resolved",
            "items": [{"alert_id": alert, "expected_revision": current["revision"]}],
        },
    )
    assert result.status_code == 200, result.text
    with real_db.transaction() as conn:
        row = conn.execute(
            "SELECT last_activity>clock_timestamp()-interval '1 minute' AS recent,revision,status FROM incidents WHERE id=%s",
            (incident,),
        ).fetchone()
        assert row["recent"]
        # Its only linked alert resolved, so the incident resolved too (REV-46).
        assert row["revision"] == 3
        assert row["status"] == "resolved"


def test_incident_move_notes_and_search(browser, real_db, cipher, profile, v1):
    client, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}/incidents"
    first = client.post(
        base,
        json={
            "command_id": str(uuid4()),
            "title": "Source",
            "severity": "high",
            "alert_ids": [alert],
        },
    ).json()["data"]
    second = client.post(
        base, json={"command_id": str(uuid4()), "title": "Destination", "severity": "low"}
    ).json()["data"]
    moved = client.post(
        base + "/move-alerts",
        json={
            "command_id": str(uuid4()),
            "source_id": first["id"],
            "destination_id": second["id"],
            "source_revision": "1",
            "destination_revision": "1",
            "alert_ids": [alert],
        },
    )
    assert moved.status_code == 200, moved.text
    note = client.post(
        base + "/" + second["id"] + "/notes",
        json={
            "command_id": str(uuid4()),
            "expected_revision": "2",
            "text": "Unique searchable Zürich",
        },
    )
    assert note.status_code == 201, note.text
    results = client.get(base, params={"q": "ZÜRICH"}).json()["data"]
    assert any(item["id"] == second["id"] for item in results)
    assert client.get(base + "/" + first["id"] + "/alerts").json()["data"] == []


def test_retired_counterpart_never_exposes_content(browser, real_db, cipher, profile, v1):
    client, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}/incidents"
    incident = client.post(
        base,
        json={
            "command_id": str(uuid4()),
            "title": "Retained incident",
            "severity": "high",
            "alert_ids": [alert],
        },
    ).json()["data"]["id"]
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE alert_identities SET retired_at=clock_timestamp() WHERE id=%s", (alert,)
        )
    linked = client.get(base + "/" + incident + "/alerts").json()["data"][0]
    assert linked["reference_id"] == alert
    assert linked["message"] is None
    assert client.get(base + "/" + incident).json()["data"]["unavailable"] == "1"
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE incident_identities SET retired_at=clock_timestamp() WHERE id=%s", (incident,)
        )
    assert all(row["id"] != incident for row in client.get(base).json()["data"])


def test_invalid_cel_and_revision_are_validation_failures(browser, real_db):
    client, _ = browser
    with real_db.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    result = client.post(
        f"/api/tenants/{tenant}/presets",
        json={
            "command_id": str(uuid4()),
            "name": "Invalid",
            "cel": "alert.no_such_field == 2",
            "sort": [{"field": "timestamp", "direction": "desc"}],
            "columns": ["message"],
        },
    )
    assert result.status_code == 422, result.text
    result = client.post(
        f"/api/tenants/{tenant}/incidents/move-alerts",
        json={
            "command_id": str(uuid4()),
            "source_id": str(uuid4()),
            "destination_id": str(uuid4()),
            "source_revision": "bad",
            "destination_revision": "-5",
            "alert_ids": [str(uuid4())],
        },
    )
    assert result.status_code == 422
