from uuid import uuid4

import pytest

from keep.alerts.processor import process_one
from keep.ingestion.native import canonical
from keep.ingestion.service import Admission

from test_product import admit_alert, browser  # noqa: F401  (fixture reuse)

pytestmark = pytest.mark.integration


def command(revision, kind, value):
    return {
        "command_id": str(uuid4()),
        "expected_revision": str(revision),
        "kind": kind,
        "value": value,
    }


@pytest.fixture
def dismissing(browser):  # noqa: F811
    client, principal = browser
    settings = client.app.state.settings
    client.app.state.settings = settings.model_copy(update={"alert_dismissal_enabled": True})
    yield client, principal
    client.app.state.settings = settings


def test_dismissal_stays_off_until_enabled(browser, real_db, cipher, profile, v1):  # noqa: F811
    """Requirements: REV-49; alert operations 29."""
    client, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    response = client.post(
        f"/api/tenants/{tenant}/alerts/{alert}/commands",
        json=command(1, "set_dismissal", {"duration_seconds": 60}),
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "dismissal_disabled"


def test_dismiss_change_and_undismiss(dismissing, real_db, cipher, profile, v1):
    """Requirements: REV-49; alert operations 26, 29."""
    client, principal = dismissing
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}/alerts/{alert}"
    body = command(1, "set_dismissal", {"duration_seconds": 3600, "reason": "  Known backlog  "})
    response = client.post(base + "/commands", json=body)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["revision"] == "2" and data["suppressed"] is True
    assert data["dismissal"]["state"] == "active"
    assert data["dismissal"]["dismissed_by"] == str(principal)
    assert data["status"] == "firing" and data["display_status"] == "firing"
    # Replays return the stored result; a stale revision conflicts.
    assert client.post(base + "/commands", json=body).json() == response.json()
    stale = client.post(base + "/commands", json={**body, "command_id": str(uuid4())})
    assert stale.status_code == 409
    notes = client.get(base + "/notes").json()["data"]
    assert [note["text"] for note in notes] == ["Known backlog"]
    detail = client.get(base).json()["data"]
    assert detail["suppressed"] is True and detail["dismissal"]["until"] == data["dismissal"]["until"]
    with real_db.transaction() as conn:
        stored = conn.execute("SELECT suppressed FROM alerts WHERE id=%s", (alert,)).fetchone()
    assert stored["suppressed"] is False  # stored causes are unchanged

    changed = client.post(
        base + "/commands",
        json=command(2, "set_dismissal", {"duration_seconds": 7200}),
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["dismissal"]["until"] > data["dismissal"]["until"]
    ended = client.post(base + "/commands", json=command(3, "set_dismissal", None))
    assert ended.status_code == 200, ended.text
    ended = ended.json()["data"]
    assert ended["dismissal"] is None and ended["suppressed"] is False
    again = client.post(base + "/commands", json=command(4, "set_dismissal", None))
    assert again.status_code == 200 and again.json()["data"]["revision"] == "4"


def test_expiry_is_read_time_and_the_next_source_input_records_it(
    dismissing, real_db, cipher, profile, v1
):
    """Requirements: REV-49; alert operations 29."""
    client, _ = dismissing
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}/alerts/{alert}"
    assert client.post(
        base + "/commands", json=command(1, "set_dismissal", {"duration_seconds": 3600})
    ).status_code == 200
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE alerts SET dismissed_at=clock_timestamp()-interval '2 hours',dismissed_until=clock_timestamp()-interval '1 hour' WHERE id=%s",
            (alert,),
        )
    detail = client.get(base).json()["data"]
    assert detail["suppressed"] is False and detail["dismissal"]["state"] == "expired"
    Admission(real_db, cipher, profile).admit(canonical(v1), "v1")
    while process_one(real_db, tenant):
        pass
    detail = client.get(base).json()["data"]
    assert detail["dismissal"] is None and detail["revision"] == "3"
    history = client.get(base + "/history").json()["data"]
    latest = history[0]
    assert latest["kind"] == "source"
    assert latest["evidence"]["before"]["dismissed_until"] is not None
    assert latest["evidence"]["after"]["dismissed_until"] is None


def test_firing_only_bounds_and_no_bulk(dismissing, real_db, cipher, profile, v1):
    """Requirements: REV-49; alert operations 26, 27, 29."""
    client, _ = dismissing
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}/alerts"
    too_long = client.post(
        f"{base}/{alert}/commands",
        json=command(1, "set_dismissal", {"duration_seconds": 30 * 86400 + 1}),
    )
    assert too_long.status_code == 422
    passed = client.post(
        f"{base}/{alert}/commands",
        json=command(1, "set_dismissal", {"until": "2020-01-01T00:00:00Z"}),
    )
    assert passed.status_code == 422 and passed.json()["error"]["code"] == "dismissal_end_passed"
    assert client.post(
        f"{base}/{alert}/commands", json=command(1, "set_status", "resolved")
    ).status_code == 200
    resolved = client.post(
        f"{base}/{alert}/commands", json=command(2, "set_dismissal", {"duration_seconds": 60})
    )
    assert resolved.status_code == 422 and resolved.json()["error"]["code"] == "alert_not_firing"
    bulk = client.post(
        f"{base}/bulk-commands",
        json={
            "command_id": str(uuid4()),
            "kind": "set_dismissal",
            "value": None,
            "items": [{"alert_id": alert, "expected_revision": "2"}],
        },
    )
    assert bulk.status_code == 422
