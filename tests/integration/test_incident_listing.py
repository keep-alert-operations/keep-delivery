"""Real PostgreSQL incident filters, query-bound live pages and current access."""
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401
from test_maintenance_repository import maintenance_db  # noqa: F401
from test_incident_note_revision import RestrictedDatabase

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_db(maintenance_db):
    db, _ = maintenance_db
    draft = Path(__file__).resolve().parents[2] / ".local/0035_incident_list_indexes.sql"
    if draft.exists():
        with db.transaction() as conn:
            conn.execute(draft.read_text(), prepare=False)
    return db


@pytest.fixture
def incident_list(browser, real_db, tenant):
    client, actor = browser
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, actor),
        )
    original = client.app.state.db
    client.app.state.db = RestrictedDatabase(real_db)
    path = f"/api/tenants/{tenant}/incidents"
    rows = []
    for severity in ("low", "critical", "high", "medium", "high"):
        result = client.post(
            path,
            json={
                "command_id": str(uuid4()),
                "title": "Incident " + severity,
                "description": "Shared description",
                "severity": severity,
            },
        )
        assert result.status_code == 201, result.text
        rows.append(result.json()["data"])
    try:
        yield client, path, rows, actor, tenant
    finally:
        client.app.state.db = original


def result(client, path, **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def page_all(client, path, **params):
    ids, cursor = [], None
    for _ in range(10):
        data = result(client, path, **params, **({"cursor": cursor} if cursor else {}), limit=1)
        ids.extend(row["id"] for row in data["data"])
        cursor = data["next_cursor"]
        if cursor is None:
            return ids
    pytest.fail("Pagination failed to terminate")


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_severity_rank_and_equal_rank_uuid_keysets(incident_list, direction):
    client, path, rows, _, _ = incident_list
    rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    expected = sorted(
        rows, key=lambda row: (rank[row["severity"]] * (1 if direction == "asc" else -1), row["id"])
    )
    assert page_all(client, path, sort="severity", direction=direction) == [
        row["id"] for row in expected
    ]


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_resolution_nulls_last_and_half_open_time_filters(incident_list, real_db, direction):
    client, path, rows, _, tenant = incident_list
    boundary = datetime(2026, 9, 20, tzinfo=UTC)
    with real_db.transaction(tenant) as conn:
        for index, row in enumerate(rows[:3]):
            conn.execute(
                "UPDATE incidents SET status='resolved',resolved_at=%s WHERE tenant_id=%s AND id=%s",
                (boundary + timedelta(hours=index), tenant, row["id"]),
            )
    expected = rows[:3] if direction == "asc" else list(reversed(rows[:3]))
    expected += sorted(rows[3:], key=lambda row: row["id"])
    assert page_all(client, path, view="all", sort="resolved_at", direction=direction) == [
        row["id"] for row in expected
    ]
    assert [
        row["id"]
        for row in result(
            client,
            path,
            view="all",
            resolved_from=boundary.isoformat(),
            resolved_to=(boundary + timedelta(hours=1)).isoformat(),
        )["data"]
    ] == [rows[0]["id"]]
    assert result(client, path, view="active", status="resolved")["data"] == []


def test_current_assignee_filters_match_presentation_before_cleanup(incident_list, real_db):
    client, path, rows, actor, tenant = incident_list
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE incidents SET assignee_id=%s WHERE tenant_id=%s AND id=%s",
            (actor, tenant, rows[0]["id"]),
        )
    assert [r["id"] for r in result(client, path, assignee="me")["data"]] == [rows[0]["id"]]
    assert len(result(client, path, assignee="unassigned")["data"]) == 4
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, actor),
        )
    assert result(client, path, assignee="me")["data"] == []
    data = result(client, path, assignee="unassigned")["data"]
    assert len(data) == 5
    item = next(r for r in data if r["id"] == rows[0]["id"])
    assert item["assignee_id"] is None and item["assignment_cleanup_pending"]


def test_exact_id_origin_rule_and_current_link_filters(incident_list, real_db):
    client, path, rows, _, tenant = incident_list
    rule, context, alert = uuid4(), uuid4(), uuid4()
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO correlation_rule_identities(id,tenant_id) VALUES(%s,%s)", (rule, tenant)
        )
        conn.execute(
            "INSERT INTO correlation_contexts(id,tenant_id,rule_id,grouping_generation,group_digest,group_key,created_at) VALUES(%s,%s,%s,1,%s,%s,clock_timestamp())",
            (context, tenant, rule, b"k" * 32, b"group"),
        )
        conn.execute(
            "UPDATE incidents SET origin='correlation',context_id=%s WHERE tenant_id=%s AND id=%s",
            (context, tenant, rows[0]["id"]),
        )
        conn.execute("INSERT INTO alert_identities(tenant_id,id) VALUES(%s,%s)", (tenant, alert))
        conn.execute(
            "INSERT INTO incident_alert_links(tenant_id,incident_id,alert_id,linked_at) VALUES(%s,%s,%s,clock_timestamp())",
            (tenant, rows[0]["id"], alert),
        )
    for filters in (
        {"incident_id": rows[0]["id"]},
        {"origin": "correlation", "rule_id": str(rule)},
        {"alert_id": str(alert)},
    ):
        assert [r["id"] for r in result(client, path, **filters)["data"]] == [rows[0]["id"]]
    assert result(client, path, origin="manual", rule_id=str(rule))["data"] == []
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE incident_alert_links SET unlinked_at=clock_timestamp() WHERE tenant_id=%s",
            (tenant,),
        )
    assert result(client, path, alert_id=str(alert))["data"] == []
    assert result(client, path, incident_id=str(uuid4()))["data"] == []


def test_text_tokens_ranges_query_binding_and_current_access(incident_list, real_db):
    client, path, rows, actor, tenant = incident_list
    assert len(result(client, path, q="HIGH shared")["data"]) == 2
    first = result(client, path, limit=1)
    assert first["next_cursor"]
    assert (
        client.get(path, params={"cursor": first["next_cursor"], "severity": "high"}).status_code
        == 409
    )
    assert (
        client.get(path, params={"cursor": first["next_cursor"], "direction": "asc"}).status_code
        == 409
    )
    expected = rows[0]["created_at"]
    assert (
        len(result(client, path, created_from=expected, created_to=rows[1]["created_at"])["data"])
        == 1
    )
    assert (
        len(result(client, path, activity_from=expected, activity_to=rows[1]["created_at"])["data"])
        == 1
    )
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, actor)
        )
    assert client.get(path, params={"cursor": first["next_cursor"]}).status_code == 404


@pytest.mark.parametrize(
    "filters",
    [
        {"assignee": "bad"},
        {"incident_id": "bad"},
        {"rule_id": "bad"},
        {"alert_id": "bad"},
        {"created_from": "2026-09-22T00:00:00"},
        {"activity_from": "2026-09-22T00:00:00Z", "activity_to": "2026-09-22T00:00:00Z"},
        {"severity": "unknown"},
        {"sort": "unknown"},
        {"direction": "sideways"},
    ],
)
def test_invalid_filters_fail_explicitly(incident_list, filters):
    client, path, _, _, _ = incident_list
    assert client.get(path, params=filters).status_code in (400, 422)
