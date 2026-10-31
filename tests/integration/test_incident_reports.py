"""Real PostgreSQL incident report through the Product API (REV-53)."""
# ruff: noqa: F811

import hashlib
import os
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_correlation_configuration import TEST_CAPACITY, definition
from test_incident_note_revision import RestrictedDatabase
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


def wire_time(value):
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@pytest.fixture
def reports(browser, real_db, tenant):
    client, actor = browser
    client.app.state.profile = TEST_CAPACITY
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, actor),
        )
    original = client.app.state.db
    client.app.state.db = RestrictedDatabase(real_db)
    try:
        yield client, actor, tenant, f"/api/tenants/{tenant}/reports/incidents"
    finally:
        client.app.state.db = original


def report(client, path, start, end, **params):
    response = client.get(
        path,
        params={"from": wire_time(start), "to": wire_time(end), "time_zone": "UTC", **params},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def db_now(real_db):
    with real_db.transaction() as conn:
        return conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]


def seed(conn, tenant, severity, cycles, *, origin="manual", context=None, events=(), assignee=None, retired=False):
    """One incident with explicit cycles (opened, investigating, resolved, cause) and activity."""
    incident = uuid4()
    first, last = cycles[0], cycles[-1]
    ids = [uuid4() for _ in cycles]
    conn.execute(
        "INSERT INTO incident_identities(id,tenant_id,retired_at) VALUES(%s,%s,%s)",
        (incident, tenant, last[0] if retired else None),
    )
    status = "resolved" if last[2] else "investigating" if last[1] else "open"
    conn.execute(
        "INSERT INTO incidents(id,tenant_id,title,description,severity,status,assignee_id,origin,context_id,cycle_id,"
        "created_at,updated_at,last_activity,resolved_at) VALUES(%s,%s,'Seeded','',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (incident, tenant, severity, status, assignee, origin, context, ids[-1], first[0], last[0], last[0], last[2]),
    )
    for identifier, (opened, investigating, resolved, cause) in zip(ids, cycles, strict=True):
        conn.execute(
            "INSERT INTO incident_cycles(tenant_id,incident_id,id,opened_at,investigating_at,resolved_at,cause) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (tenant, incident, identifier, opened, investigating, resolved, cause),
        )
    for recorded, kind, value in events:
        evidence = {"assignee_id": value} if kind == "assignment" else {"after_assignee_id": None}
        conn.execute(
            "INSERT INTO incident_activity(tenant_id,incident_id,id,kind,recorded_at,evidence) VALUES(%s,%s,%s,%s,%s,%s)",
            (tenant, incident, uuid4(), kind, recorded, Jsonb(evidence)),
        )
    return incident


def context(conn, tenant, rule):
    identifier = uuid4()
    conn.execute(
        "INSERT INTO correlation_contexts(id,tenant_id,rule_id,grouping_generation,group_digest,group_key,created_at) "
        "VALUES(%s,%s,%s,1,%s,%s,clock_timestamp())",
        (identifier, tenant, rule, hashlib.sha256(identifier.bytes).digest(), b"group"),
    )
    return identifier


def create_rule(client, tenant, name):
    response = client.post(
        f"/api/tenants/{tenant}/correlation-rules",
        json={"command_id": str(uuid4()), **definition(name)},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def command(client, path, revision, **body):
    response = client.post(path, json={"command_id": str(uuid4()), "expected_revision": revision, **body})
    assert response.status_code == 200, response.text
    return response.json()["data"]["revision"]


def test_report_follows_the_real_incident_lifecycle(reports, real_db):
    """Requirements: REV-53, I2, I5; incident operations 4, 20."""
    client, actor, tenant, path = reports
    base = f"/api/tenants/{tenant}/incidents"
    created = {}
    for severity in ("critical", "low"):
        response = client.post(
            base, json={"command_id": str(uuid4()), "title": severity, "severity": severity}
        )
        assert response.status_code == 201, response.text
        created[severity] = response.json()["data"]
    first, second = created["critical"], created["low"]
    one = base + "/" + first["id"]
    revision = command(client, one + "/assignment", first["revision"], assignee_id=str(actor))
    revision = command(client, one + "/status", revision, status="investigating")
    revision = command(client, one + "/status", revision, status="resolved")
    command(client, one + "/status", revision, status="open")  # Reopens with the assignee kept.
    two = base + "/" + second["id"]
    revision = command(client, two + "/status", second["revision"], status="investigating")
    command(client, two + "/status", revision, status="resolved")

    now = db_now(real_db)
    data = report(client, path, now - timedelta(hours=24), now)
    summary = data["summary"]
    assert data["period"]["bin"] == "hour"
    assert len(data["series"]) == 24
    assert (summary["opened"], summary["opened_new"], summary["opened_reopened"]) == ("3", "2", "1")
    assert summary["resolved"] == "2"
    assert summary["resolved_by_cause"] == {"manual": "2", "alerts_resolved": "0", "inactivity": "0"}
    assert summary["reopened"] == "1"
    # The reopened cycle kept its assignee, which is not a new response.
    assert (summary["first_response"]["responded"], summary["first_response"]["waiting"]) == ("2", "1")
    assert sum(int(item["opened"]) for item in data["series"]) == 3
    assert data["previous_summary"]["opened"] == "0"
    severity = {row["severity"]: row for row in data["breakdowns"]["severity"]}
    assert (severity["critical"]["opened"], severity["low"]["opened"]) == ("2", "1")
    people = {row["assignee_id"]: row for row in data["breakdowns"]["assignee"]["rows"]}
    assert people[str(actor)]["display_name"] == "Synthetic user"
    assert (people[str(actor)]["opened"], people[str(actor)]["resolved"]) == ("2", "1")
    assert (people[None]["opened"], people[None]["resolved"]) == ("1", "1")
    assert data["unavailable"] == []


def test_report_figures_match_seeded_history(reports, real_db):
    """Requirements: REV-53, I5; incident operations 4, 13, 20; correlation rules."""
    client, actor, tenant, path = reports
    kept, deleted = create_rule(client, tenant, "Checkout errors"), create_rule(client, tenant, "TLS expiry")
    now = db_now(real_db)
    start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=6)
    t = now.replace(minute=0, second=0, microsecond=0) - timedelta(days=1)
    minute, hour, day = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
    other = uuid4()
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
            (other, str(other), str(other)),
        )
        first, second = context(conn, tenant, kept), context(conn, tenant, deleted)
        # Assigned after 10 minutes, resolved, then reopened and still open with its assignee.
        seed(
            conn, tenant, "critical",
            [(t - 3 * day, t - 3 * day + 30 * minute, t - 3 * day + 2 * hour, "manual"), (t - 2 * day, None, None, None)],
            origin="correlation", context=first, assignee=actor,
            events=[(t - 3 * day + 10 * minute, "assignment", str(actor))],
        )
        seed(
            conn, tenant, "high", [(t - day, t - day + 20 * minute, t - day + 4 * hour, "alerts_resolved")],
            origin="correlation", context=second,
        )
        # Assigned, unassigned again, then resolved: unassigned at resolution.
        seed(
            conn, tenant, "low", [(t - day, None, t - day + 6 * hour, "manual")],
            events=[(t - day + 5 * minute, "assignment", str(uuid4())), (t - day + hour, "assignment", None)],
        )
        # Assigned, then the assignee lost eligibility before resolution.
        seed(
            conn, tenant, "high", [(t - 2 * day, None, t - 2 * day + 3 * hour, "manual")],
            events=[(t - 2 * day + 15 * minute, "assignment", str(uuid4())), (t - 2 * day + hour, "eligibility_lost", None)],
        )
        # Previous period only.
        seed(conn, tenant, "medium", [(start - 3 * day, None, start - 3 * day + hour, "manual")])
        # Never counted: a retired incident and another tenant's incident.
        seed(conn, tenant, "high", [(t - day, None, t - day + hour, "manual")], retired=True)
        seed(conn, other, "high", [(t - day, None, t - day + hour, "manual")])
        conn.execute(
            "UPDATE correlation_rules SET deleted_at=clock_timestamp(),enabled=false WHERE tenant_id=%s AND id=%s",
            (tenant, deleted),
        )
        conn.execute(
            "UPDATE correlation_rule_identities SET deleted_at=clock_timestamp(),final_revision=1 WHERE tenant_id=%s AND id=%s",
            (tenant, deleted),
        )

    data = report(client, path, start, now)
    summary = data["summary"]
    assert data["period"]["bin"] == "day"
    assert len(data["series"]) == 7
    assert data["previous"] == {"from": wire_time(start - 7 * day), "to": wire_time(start)}
    assert (summary["opened"], summary["opened_new"], summary["opened_reopened"]) == ("5", "4", "1")
    assert summary["resolved"] == "4"
    assert summary["resolved_by_cause"] == {"manual": "3", "alerts_resolved": "1", "inactivity": "0"}
    assert summary["reopened"] == "1"
    assert summary["first_response"] == {
        "median_seconds": "750", "p90_seconds": "1110", "responded": "4", "waiting": "1"
    }
    assert summary["time_to_resolve"] == {"median_seconds": "12600", "p90_seconds": "19440"}
    assert (data["previous_summary"]["opened"], data["previous_summary"]["resolved"]) == ("1", "1")
    assert sum(int(item["resolved"]) for item in data["series"]) == 4
    groups = data["breakdowns"]
    assert [(row["severity"], row["opened"], row["resolved"]) for row in groups["severity"]] == [
        ("critical", "2", "1"), ("high", "2", "2"), ("medium", "0", "0"), ("low", "1", "1")
    ]
    assert [(row["origin"], row["opened"]) for row in groups["origin"]] == [("correlation", "3"), ("manual", "2")]
    rules = {row["rule_id"]: row for row in groups["rule"]["rows"]}
    assert (rules[kept]["name"], rules[kept]["deleted"], rules[kept]["opened"]) == ("Checkout errors", False, "2")
    # Its incident references its context, so the deleted rule's history and name are retained.
    assert (rules[deleted]["name"], rules[deleted]["deleted"]) == ("TLS expiry", True)
    people = {row["assignee_id"]: (row["opened"], row["resolved"]) for row in groups["assignee"]["rows"]}
    assert people == {str(actor): ("2", "1"), None: ("3", "3")}


def test_report_is_partial_when_assignment_history_is_out_of_time(reports, real_db, monkeypatch):
    """Requirements: REV-53, N3; incident operations 20; reliability operations 5."""
    import appchi.reports.incidents as module

    client, _, tenant, path = reports
    now = db_now(real_db)
    with real_db.transaction() as conn:
        seed(conn, tenant, "high", [(now - timedelta(hours=3), None, now - timedelta(hours=1), "manual")])
    # The pause runs first, whether or not the history query returns rows.
    slow = f"SELECT q.* FROM (SELECT pg_sleep(0.5)) pause CROSS JOIN LATERAL ({module.ASSIGNMENTS}) q"
    monkeypatch.setattr(module, "ASSIGNMENTS", slow)
    monkeypatch.setattr(module, "ASSIGNMENTS_TIMEOUT_MS", 100)
    data = report(client, path, now - timedelta(hours=24), now)
    assert data["unavailable"] == ["first_response", "assignee"]
    assert data["summary"]["first_response"] is None
    assert data["breakdowns"]["assignee"] is None
    assert (data["summary"]["opened"], data["summary"]["resolved"]) == ("1", "1")


def test_report_requires_tenant_read_access_and_valid_periods(reports, real_db):
    """Requirements: REV-53, A2; incident operations 20; tenant access 8; system completion 6."""
    client, actor, tenant, path = reports
    now = db_now(real_db)
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s", (tenant, actor)
        )
    assert report(client, path, now - timedelta(days=1), now)["summary"]["opened"] == "0"
    for params in (
        {"from": "2026-10-01T00:00:00", "to": wire_time(now)},
        {"from": wire_time(now), "to": wire_time(now - timedelta(days=1))},
        {"from": wire_time(now - timedelta(days=1)), "to": wire_time(now), "time_zone": "Mars/Base"},
        {"from": wire_time(now - timedelta(days=1)), "to": wire_time(now), "group": "x"},
    ):
        assert client.get(path, params=params).status_code == 422, params
    with real_db.transaction() as conn:
        conn.execute("DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, actor))
    response = client.get(path, params={"from": wire_time(now - timedelta(days=1)), "to": wire_time(now)})
    assert response.status_code == 404


def test_ninety_day_report_on_twenty_thousand_cycles_is_within_budget(reports, real_db):
    """Requirements: REV-53, N3; incident operations 20; reliability operations 5, 6."""
    client, _, tenant, path = reports
    now = db_now(real_db)
    with real_db.transaction() as conn:
        rows = (
            "SELECT n,md5(%s||n)::uuid AS incident,md5(%s||'c'||n)::uuid AS cycle,"
            "clock_timestamp()-((n%%85)*interval '1 day')-((n%%23)*interval '1 hour') AS opened "
            "FROM generate_series(1,20000) n"
        )
        args = (str(tenant), str(tenant))
        conn.execute(f"INSERT INTO incident_identities(id,tenant_id) SELECT incident,%s FROM ({rows}) s", (tenant, *args))
        conn.execute(
            "INSERT INTO incidents(id,tenant_id,title,description,severity,status,origin,cycle_id,created_at,updated_at,last_activity,resolved_at) "
            "SELECT incident,%s,'Load','',(ARRAY['critical','high','medium','low'])[1+n%%4],"
            "CASE WHEN n%%5=0 THEN 'open' ELSE 'resolved' END,'manual',cycle,opened,opened,opened,"
            f"CASE WHEN n%%5=0 THEN NULL ELSE opened+interval '2 hours' END FROM ({rows}) s",
            (tenant, *args),
        )
        conn.execute(
            "INSERT INTO incident_cycles(tenant_id,incident_id,id,opened_at,investigating_at,resolved_at,cause) "
            "SELECT %s,incident,cycle,opened,opened+interval '20 minutes',"
            "CASE WHEN n%%5=0 THEN NULL ELSE opened+interval '2 hours' END,CASE WHEN n%%5=0 THEN NULL ELSE 'manual' END "
            f"FROM ({rows}) s",
            (tenant, *args),
        )
        # Ten activity rows per incident (one assignment), plus five incidents with 2,000 each.
        conn.execute(
            "INSERT INTO incident_activity(tenant_id,incident_id,id,kind,recorded_at,evidence) "
            "SELECT %s,incident,gen_random_uuid(),CASE WHEN k=1 THEN 'assignment' ELSE 'link' END,opened+k*interval '1 minute',"
            f"CASE WHEN k=1 THEN jsonb_build_object('assignee_id',NULL) ELSE '{{}}'::jsonb END FROM ({rows}) s,generate_series(1,10) k",
            (tenant, *args),
        )
        conn.execute(
            "INSERT INTO incident_activity(tenant_id,incident_id,id,kind,recorded_at,evidence) "
            "SELECT %s,incident,gen_random_uuid(),'linked-alert-status',opened+k*interval '1 second','{}'::jsonb "
            f"FROM ({rows}) s,generate_series(1,2000) k WHERE n<=5",
            (tenant, *args),
        )
    # Production history grows gradually and autovacuum keeps visibility current;
    # measure the report, not the first read after a bulk load.
    with psycopg.connect(os.environ["APPCHI_TEST_DATABASE_URL"], autocommit=True) as conn:
        for table in ("incident_cycles", "incident_activity", "incidents", "incident_identities"):
            conn.execute(f"VACUUM ANALYZE {table}")
    now = db_now(real_db)  # After seeding, so every seeded opening is inside the period.
    start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=89)
    report(client, path, start, now)  # Warm the connection and plan caches once.
    began = time.perf_counter()
    data = report(client, path, start, now)
    elapsed = time.perf_counter() - began
    print(f"90-day incident report over 20,000 cycles: {elapsed:.3f} s")
    assert data["unavailable"] == []
    assert int(data["summary"]["opened"]) == 20000
    assert data["period"]["bin"] == "week"
    assert elapsed < 2.0
