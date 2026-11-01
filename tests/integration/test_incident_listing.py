"""Real PostgreSQL incident filters, query-bound live pages, link counts and current access."""
# ruff: noqa: F811

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from test_product import admit_alert, browser  # noqa: F401
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
    """Requirements: I5; incident operations 14."""
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
    """Requirements: I5; incident operations 14."""
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
    """Requirements: I5, REV-34; incident operations 3 and 14."""
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
    """Requirements: I5; incident operations 14."""
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
    """Requirements: I5; incident operations 14 and 19."""
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
    """Requirements: I5; incident operations 14."""
    client, path, _, _, _ = incident_list
    assert client.get(path, params=filters).status_code in (400, 422)


def test_list_rows_carry_the_detail_link_counts(browser, real_db, cipher, profile, v1):
    """Requirements: I5, REV-56; incident operations 7, 14 and 16; system completion 8 and 15."""
    client, _ = browser
    alerts = []
    for index in range(4):
        tenant, alert = admit_alert(
            real_db, cipher, profile, {**v1, "application": f"{v1['application']}-{index}"}
        )
        alerts.append(alert)
    plain, dismissed, unproven, released = alerts
    path = f"/api/tenants/{tenant}/incidents"

    def create(title, alert_ids):
        body = {"command_id": str(uuid4()), "title": title, "severity": "high"}
        response = client.post(
            path, json={**body, **({"alert_ids": alert_ids} if alert_ids else {})}
        )
        assert response.status_code == 201, response.text
        return response.json()["data"]

    mixed = create("Mixed links", [plain, dismissed])["id"]
    no_proof = create("Without proof", [unproven])["id"]
    resolved = create("Resolved", [released])
    empty = create("Empty", [])["id"]
    status = client.post(
        f"{path}/{resolved['id']}/status",
        json={
            "command_id": str(uuid4()),
            "expected_revision": resolved["revision"],
            "status": "resolved",
        },
    )
    assert status.status_code == 200, status.text
    gone, unlinked = uuid4(), uuid4()
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE alerts SET dismissed_at=clock_timestamp(),"
            "dismissed_until=clock_timestamp()+interval '1 hour' WHERE tenant_id=%s AND id=%s",
            (tenant, dismissed),
        )
        conn.execute(
            "DELETE FROM maintenance_alert_checkpoints WHERE tenant_id=%s AND alert_id=%s",
            (tenant, unproven),
        )
        for alert in (gone, unlinked):
            conn.execute(
                "INSERT INTO alert_identities(tenant_id,id) VALUES(%s,%s)", (tenant, alert)
            )
        conn.execute(
            "INSERT INTO incident_alert_links(tenant_id,incident_id,alert_id,linked_at) VALUES(%s,%s,%s,clock_timestamp())",
            (tenant, mixed, gone),
        )
        conn.execute(
            "INSERT INTO incident_alert_links(tenant_id,incident_id,alert_id,linked_at,unlinked_at) VALUES(%s,%s,%s,clock_timestamp(),clock_timestamp())",
            (tenant, mixed, unlinked),
        )
    original = client.app.state.db
    client.app.state.db = RestrictedDatabase(real_db)
    try:
        rows = {row["id"]: row for row in result(client, path, view="all", limit=200)["data"]}
        counts = {
            key: {name: rows[key][name] for name in ("total_linked", "suppressed", "unavailable")}
            for key in (mixed, no_proof, resolved["id"], empty)
        }
        assert counts == {
            mixed: {"total_linked": "3", "suppressed": "1", "unavailable": "1"},
            no_proof: {"total_linked": "1", "suppressed": None, "unavailable": "0"},
            resolved["id"]: {"total_linked": "1", "suppressed": "0", "unavailable": "0"},
            empty: {"total_linked": "0", "suppressed": "0", "unavailable": "0"},
        }
        for key in (mixed, resolved["id"], empty):
            detail = client.get(f"{path}/{key}")
            assert detail.status_code == 200, detail.text
            data = detail.json()["data"]
            assert {name: data[name] for name in counts[key]} == counts[key]
        detail = client.get(f"{path}/{no_proof}")
        assert detail.status_code == 503, detail.text
        assert "maintenance_proof_unavailable" in detail.text
    finally:
        client.app.state.db = original


def test_two_hundred_rows_over_twenty_thousand_links_are_within_budget(
    browser, real_db, maintenance_db, tenant
):
    """Requirements: I5, N3, REV-56; incident operations 14 and 16; reliability operations 5."""
    client, actor = browser
    _, dsn = maintenance_db
    incidents = (
        "SELECT n,md5(%s||'i'||n)::uuid AS incident,md5(%s||'c'||n)::uuid AS cycle "
        "FROM generate_series(1,200) n"
    )
    alerts = "SELECT k,md5(%s||'a'||k)::uuid AS alert FROM generate_series(1,20000) k"
    key = str(tenant)
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, actor),
        )
        conn.execute(
            f"INSERT INTO incident_identities(id,tenant_id) SELECT incident,%s FROM ({incidents}) s",
            (tenant, key, key),
        )
        conn.execute(
            "INSERT INTO incidents(id,tenant_id,title,description,severity,status,origin,cycle_id,"
            "created_at,updated_at,last_activity) "
            "SELECT incident,%s,'Load','','high','open','manual',cycle,clock_timestamp(),clock_timestamp(),"
            f"clock_timestamp()-n*interval '1 second' FROM ({incidents}) s",
            (tenant, key, key),
        )
        conn.execute(
            "INSERT INTO incident_cycles(tenant_id,incident_id,id,opened_at) "
            f"SELECT %s,incident,cycle,clock_timestamp() FROM ({incidents}) s",
            (tenant, key, key),
        )
        conn.execute(
            f"INSERT INTO alert_identities(tenant_id,id) SELECT %s,alert FROM ({alerts}) s",
            (tenant, key),
        )
        # Every tenth alert is dismissed, so it counts as suppressed.
        conn.execute(
            "INSERT INTO alerts(id,tenant_id,schema_version,key_field,operator,application,component,"
            "message,severity,status,time_created,first_received_at,timestamp,history_id,"
            "last_source_activity,dismissed_at,dismissed_until) "
            "SELECT alert,%s,'v2',md5(alert::text),'load','load','load','Load','warning','firing',"
            "clock_timestamp(),clock_timestamp(),clock_timestamp(),gen_random_uuid(),clock_timestamp(),"
            "CASE WHEN k%%10=0 THEN clock_timestamp() END,"
            f"CASE WHEN k%%10=0 THEN clock_timestamp()+interval '1 hour' END FROM ({alerts}) s",
            (tenant, key),
        )
        conn.execute(
            "INSERT INTO maintenance_alert_checkpoints(tenant_id,alert_id,proof_digest,catalog_generation,"
            "current_suppressed,current_cause_ids,union_intervals,complete,checked_at) "
            "SELECT %s,alert,'\\x00'::bytea,v.maintenance_generation,false,'[]','[]',true,clock_timestamp() "
            f"FROM ({alerts}) s,tenant_view_state v WHERE v.tenant_id=%s",
            (tenant, key, tenant),
        )
        # Incident 1 holds 10,000 links; the other 10,000 spread over incidents 2-200.
        conn.execute(
            "INSERT INTO incident_alert_links(tenant_id,incident_id,alert_id,linked_at) "
            "SELECT %s,md5(%s||'i'||CASE WHEN k<=10000 THEN 1 ELSE 2+k%%199 END)::uuid,alert,"
            f"clock_timestamp() FROM ({alerts}) s",
            (tenant, key, key),
        )
    with psycopg.connect(dsn, autocommit=True) as conn:
        for table in ("incident_alert_links", "alerts", "alert_identities", "incidents"):
            conn.execute(f"VACUUM ANALYZE {table}")
        conn.execute("VACUUM ANALYZE maintenance_alert_checkpoints")
    original = client.app.state.db
    client.app.state.db = RestrictedDatabase(real_db)
    path = f"/api/tenants/{tenant}/incidents"
    try:
        result(client, path, limit=200)  # Warm the connection and plan caches once.
        began = time.perf_counter()
        data = result(client, path, limit=200)["data"]
        elapsed = time.perf_counter() - began
    finally:
        client.app.state.db = original
    print(f"200 incident rows over 20,000 links: {elapsed:.3f} s")
    assert len(data) == 200
    assert sum(int(row["total_linked"]) for row in data) == 20000
    assert sum(int(row["suppressed"]) for row in data) == 2000
    assert max(int(row["total_linked"]) for row in data) == 10000
    assert all(row["unavailable"] == "0" for row in data)
    assert elapsed < 2.0
