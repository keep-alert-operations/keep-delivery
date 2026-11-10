"""An eligibility race must not merge separate alert retirement attempts."""

from uuid import uuid4

import pytest

from keep.recovery.retirement import retire_alert
from test_incident_retirement import Store, real_db, incident  # noqa: F401
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


def test_same_revision_alert_attempts_have_distinct_protection(request):
    client, tenant, _, alert, *_ = request.getfixturevalue("incident")
    database = request.getfixturevalue("real_db")
    path = f"/api/tenants/{tenant}/alerts/{alert}"
    revision = client.get(path).json()["data"]["revision"]
    response = client.post(
        path + "/commands",
        json={
            "command_id": str(uuid4()),
            "expected_revision": revision,
            "kind": "set_status",
            "value": "resolved",
        },
    )
    assert response.status_code == 200, response.text
    with database.transaction(tenant) as conn:
        conn.execute(
            "UPDATE alerts SET retention_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (alert,),
        )
    store = Store()

    def deadline_changed():
        # Model a deadline correction while independent intent protection is in flight.
        with database.transaction(tenant) as conn:
            conn.execute(
                "UPDATE alerts SET retention_at=clock_timestamp()+interval '1 day' WHERE id=%s",
                (alert,),
            )

    store.on_intent = deadline_changed
    assert not retire_alert(database, store, tenant, alert)
    assert client.get(path).status_code == 200
    with database.transaction(tenant) as conn:
        conn.execute(
            "UPDATE alerts SET retention_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (alert,),
        )
    assert retire_alert(database, store, tenant, alert)
    first, cancel, second, final = store.events
    assert [event.state for event in store.events] == ["intent", "cancel", "intent", "final"]
    assert first.revision == second.revision
    assert first.digest == cancel.digest
    assert second.digest == final.digest
    assert first.digest != second.digest
    assert client.get(path).status_code == 404
