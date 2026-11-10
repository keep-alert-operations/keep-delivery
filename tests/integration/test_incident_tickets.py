"""ServiceNow tickets on incidents through the real Product API and PostgreSQL (REV-70)."""
# ruff: noqa: F811

from uuid import uuid4

import pytest

from test_incident_note_revision import RestrictedDatabase
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration

SERVICENOW = "https://acme.service-now.com"
LINK = SERVICENOW + "/nav_to.do?uri=task.do%3Fsysparm_query%3Dnumber%3DINC0041235"
OTHER = SERVICENOW + "/incident.do?sys_id=9d385017c611228701d22104cc95c371"


@pytest.fixture
def browser_settings():
    return {"servicenow_url": SERVICENOW + "/"}


@pytest.fixture
def tickets(browser, real_db, tenant):
    client, actor = browser
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, actor),
        )
    original = client.app.state.db
    client.app.state.db = RestrictedDatabase(real_db)
    base = f"/api/tenants/{tenant}"
    created = client.post(
        base + "/incidents",
        json={"command_id": str(uuid4()), "title": "Checkout slow", "severity": "critical"},
    )
    assert created.status_code == 201, created.text
    try:
        yield client, actor, tenant, base, created.json()["data"]["id"]
    finally:
        client.app.state.db = original


def detail(client, base, incident):
    response = client.get(f"{base}/incidents/{incident}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def link(client, base, incident, revision, command=None, **body):
    return client.post(
        f"{base}/incidents/{incident}/ticket",
        json={"command_id": command or str(uuid4()), "expected_revision": revision, **body},
    )


def ticket_activity(client, base, incident):
    response = client.get(f"{base}/incidents/{incident}/activity")
    assert response.status_code == 200, response.text
    return [row for row in response.json()["data"] if row["kind"] == "ticket"]


def test_status_reports_the_configured_servicenow(tickets):
    """Requirements: REV-70; system completion 10."""
    client, _, _, base, _ = tickets
    response = client.get(base + "/servicenow/status")
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"configured": True, "base_url": SERVICENOW}
    assert client.get(f"/api/tenants/{uuid4()}/servicenow/status").status_code == 404


def test_link_change_and_unlink_record_activity(tickets):
    """Requirements: REV-70; incident operations 15, 17 and 18."""
    client, actor, _, base, incident = tickets
    before = detail(client, base, incident)
    assert before["ticket_url"] is None and before["ticket_linked_by_display_name"] is None
    command = str(uuid4())
    linked = link(client, base, incident, before["revision"], command, url=LINK, number="inc0041235")
    assert linked.status_code == 200, linked.text
    revision = linked.json()["data"]["revision"]
    assert int(revision) == int(before["revision"]) + 1
    after = detail(client, base, incident)
    assert (after["ticket_url"], after["ticket_number"]) == (LINK, "INC0041235")
    assert after["ticket_linked_by"] == str(actor)
    assert after["ticket_linked_by_display_name"] == "Synthetic user"
    assert after["ticket_linked_at"]
    # A ticket link is not incident work: last activity does not move.
    assert after["last_activity"] == before["last_activity"]
    # The same command retried returns the first result and records nothing new.
    retried = link(client, base, incident, before["revision"], command, url=LINK, number="inc0041235")
    assert retried.status_code == 200, retried.text
    assert retried.json()["data"]["revision"] == revision
    # Saving the same ticket again is a no-op.
    same = link(client, base, incident, revision, url=LINK, number="INC0041235")
    assert same.status_code == 200 and same.json()["data"]["revision"] == revision
    changed = link(client, base, incident, revision, url=OTHER)
    assert changed.status_code == 200, changed.text
    revision = changed.json()["data"]["revision"]
    assert detail(client, base, incident)["ticket_number"] is None
    unlinked = link(client, base, incident, revision, url=None, number=None)
    assert unlinked.status_code == 200, unlinked.text
    final = detail(client, base, incident)
    assert [final[key] for key in ("ticket_url", "ticket_number", "ticket_linked_at", "ticket_linked_by")] == [None] * 4
    evidence = [
        {key: row["evidence"][key] for key in ("url", "number", "previous_url", "previous_number")}
        for row in reversed(ticket_activity(client, base, incident))
    ]
    assert evidence == [
        {"url": LINK, "number": "INC0041235", "previous_url": None, "previous_number": None},
        {"url": OTHER, "number": None, "previous_url": LINK, "previous_number": "INC0041235"},
        {"url": None, "number": None, "previous_url": OTHER, "previous_number": None},
    ]


def test_stale_revisions_change_nothing(tickets):
    """Requirements: REV-70; incident operations 17 and 18."""
    client, _, _, base, incident = tickets
    revision = detail(client, base, incident)["revision"]
    assert link(client, base, incident, revision, url=LINK).status_code == 200
    stale = link(client, base, incident, revision, url=OTHER)
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "revision_conflict"
    assert detail(client, base, incident)["ticket_url"] == LINK
    assert len(ticket_activity(client, base, incident)) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"url": "https://acme-dev.service-now.com/incident.do"},
        {"url": "https://acme.service-now.com:8443/incident.do"},
        {"url": "http://acme.service-now.com/incident.do"},
        {"url": "https://acme.service-now.com.evil.example/incident.do"},
        {"url": "https://user:pw@acme.service-now.com/incident.do"},
        {"url": "https://acme.service-now.com/a b"},
        {"url": SERVICENOW + "/" + "a" * 2048},
        {"url": "javascript:alert(1)"},
        {"url": LINK, "number": "INC 41235"},
        {"number": "INC0041235"},
    ],
)
def test_invalid_links_are_rejected(tickets, body):
    """Requirements: REV-70; incident operations 18."""
    client, _, _, base, incident = tickets
    revision = detail(client, base, incident)["revision"]
    response = link(client, base, incident, revision, **body)
    assert response.status_code == 422, response.text
    assert detail(client, base, incident)["ticket_url"] is None


def test_other_hosts_get_the_ticket_error_code(tickets):
    """Requirements: REV-70; incident operations 18."""
    client, _, _, base, incident = tickets
    revision = detail(client, base, incident)["revision"]
    response = link(client, base, incident, revision, url="https://acme-dev.service-now.com/x")
    assert response.json()["error"]["code"] == "ticket_url_invalid"


def test_resolved_incidents_keep_and_take_links(tickets):
    """Requirements: REV-70; incident operations 4 and 15."""
    client, _, _, base, incident = tickets
    revision = detail(client, base, incident)["revision"]
    revision = link(client, base, incident, revision, url=LINK).json()["data"]["revision"]
    resolved = client.post(
        f"{base}/incidents/{incident}/status",
        json={"command_id": str(uuid4()), "expected_revision": revision, "status": "resolved"},
    )
    assert resolved.status_code == 200, resolved.text
    revision = resolved.json()["data"]["revision"]
    assert detail(client, base, incident)["ticket_url"] == LINK
    changed = link(client, base, incident, revision, url=OTHER)
    assert changed.status_code == 200, changed.text
    reopened = client.post(
        f"{base}/incidents/{incident}/status",
        json={
            "command_id": str(uuid4()),
            "expected_revision": changed.json()["data"]["revision"],
            "status": "open",
        },
    )
    assert reopened.status_code == 200, reopened.text
    assert detail(client, base, incident)["ticket_url"] == OTHER


def test_viewers_read_but_cannot_link(tickets, real_db):
    """Requirements: REV-70; incident operations 18 and 19."""
    client, actor, tenant, base, incident = tickets
    revision = detail(client, base, incident)["revision"]
    revision = link(client, base, incident, revision, url=LINK).json()["data"]["revision"]
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, actor),
        )
    assert detail(client, base, incident)["ticket_url"] == LINK
    assert client.get(base + "/servicenow/status").status_code == 200
    denied = link(client, base, incident, revision, url=None)
    assert denied.status_code == 403, denied.text
    assert detail(client, base, incident)["ticket_url"] == LINK


def test_without_servicenow_linking_is_refused_but_unlinking_works(tickets):
    """Requirements: REV-70; incident operations 15 and 18."""
    client, _, _, base, incident = tickets
    revision = detail(client, base, incident)["revision"]
    revision = link(client, base, incident, revision, url=LINK).json()["data"]["revision"]
    configured = client.app.state.servicenow
    client.app.state.servicenow = None
    try:
        assert client.get(base + "/servicenow/status").json()["data"] == {
            "configured": False,
            "base_url": None,
        }
        refused = link(client, base, incident, revision, url=OTHER)
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "servicenow_not_configured"
        # Saving the stored link unchanged is still a no-op.
        same = link(client, base, incident, revision, url=LINK)
        assert same.status_code == 200 and same.json()["data"]["revision"] == revision
        unlinked = link(client, base, incident, revision, url=None)
        assert unlinked.status_code == 200, unlinked.text
        assert detail(client, base, incident)["ticket_url"] is None
    finally:
        client.app.state.servicenow = configured


def test_list_rows_carry_the_ticket(tickets):
    """Requirements: REV-70; system completion 10."""
    client, _, _, base, incident = tickets
    revision = detail(client, base, incident)["revision"]
    assert link(client, base, incident, revision, url=LINK, number="INC0041235").status_code == 200
    rows = client.get(base + "/incidents").json()["data"]
    (row,) = [row for row in rows if row["id"] == incident]
    assert (row["ticket_url"], row["ticket_number"]) == (LINK, "INC0041235")
