"""Kafka-derived maintenance releases remain atomic with manual transitions."""

# ruff: noqa: F811
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from keep.correlation.consumer import correlate_record
from test_correlation_configuration import TEST_CAPACITY, create, definition, rules  # noqa: F401
# Host-clock receipts may lead the database clock; wait it out like the consumer.
from test_kafka_first import apply_waiting, receipt
from test_product import browser  # noqa: F401
from test_search_pipeline import real_db  # noqa: F401

pytestmark = pytest.mark.integration
@pytest.fixture(autouse=True)
def pre_cutover_release_compatibility(real_db):
    # Exercise retained pre-0046 history semantics. Current source-driven behavior
    # is covered in test_source_driven_maintenance.py, which does not use this fixture.
    with real_db.transaction() as conn:
        previous = conn.execute("SELECT source_release_since FROM application_scope WHERE singleton").fetchone()["source_release_since"]
        conn.execute("UPDATE application_scope SET source_release_since=clock_timestamp()+interval '100 years'")
    try:
        yield
    finally:
        with real_db.transaction() as conn:
            conn.execute("UPDATE application_scope SET source_release_since=%s WHERE singleton", (previous,))


CAPACITY = {**TEST_CAPACITY, "maintenance_max_work": 10000,
            "maintenance_max_inputs": 100, "maintenance_max_segments": 20,
            "maintenance": {"max_overlap": 20, "max_work_bytes": 1000000}}


@pytest.fixture
def setup(rules, real_db):
    client, _, tenant, _ = rules
    client.app.state.profile = CAPACITY
    with real_db.transaction() as conn:
        conn.execute("UPDATE kafka_pipeline_control SET enabled=true WHERE singleton")
    config = SimpleNamespace(environment="inline-test", global_tenant_id=tenant)
    return rules, client, tenant, config


def rule(setup, expression="true"):
    create(setup[0], {**definition(), "conditions": [
        {"id": str(uuid4()), "label": "Match", "cel": expression}]})


def window(setup, expression="true"):
    _, client, tenant, _ = setup
    now = datetime.now(UTC)
    response = client.post(f"/api/tenants/{tenant}/maintenance", json={
        "command_id": str(uuid4()), "name": "Suppression", "reason": "", "cel": expression,
        "timezone": "UTC", "start_local": (now-timedelta(minutes=1)).replace(tzinfo=None).isoformat(timespec="seconds"),
        "end_local": (now+timedelta(hours=1)).replace(tzinfo=None).isoformat(timespec="seconds")})
    assert response.status_code == 201, response.text


def receive(db, config, payload, offset=0, received=None):
    record = received or receipt(config, payload)
    topic, key, value = record
    changed = apply_waiting(db, topic=topic, key=key, value=value, partition=0,
                           offset=offset, settings=config, profile=CAPACITY)
    assert changed is not None
    return changed


def consume(db, config, changed, offset=0):
    envelope = json.loads(changed)
    message = SimpleNamespace(value=lambda: changed, key=lambda: config.global_tenant_id.bytes,
        topic=lambda: "keep.inline-test.alerts.changed", partition=lambda: 0, offset=lambda: offset)
    correlate_record(db, config, CAPACITY, message)
    return UUID(envelope["alert_id"])


def expire(db, tenant):
    # Move only this isolated fixture's scheduled end, after its received inputs.
    with db.transaction(tenant) as conn:
        at = conn.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
        conn.execute("UPDATE maintenance_segments SET end_at=%s WHERE tenant_id=%s", (at,tenant))
        conn.execute("UPDATE maintenance_windows SET end_at=%s WHERE tenant_id=%s", (at,tenant))
        conn.execute("UPDATE maintenance_fanout_jobs SET due_at=%s WHERE tenant_id=%s", (at,tenant))
    return at


def mutate(db, setup, alert, status=None):
    _, client, tenant, _ = setup
    with db.transaction(tenant) as conn:
        revision = conn.execute("SELECT revision FROM alerts WHERE tenant_id=%s AND id=%s", (tenant,alert)).fetchone()["revision"]
    body = {"command_id": str(uuid4()), "expected_revision": str(revision)}
    if status:
        path, body = "commands", {**body,"kind":"set_status","value":status}
    else:
        path, body = "notes", {**body,"text":"A manual note"}
    response = client.post(f"/api/tenants/{tenant}/alerts/{alert}/{path}", json=body)
    assert response.status_code == (200 if status else 201), response.text
    return response


def incidents(db, tenant):
    with db.transaction(tenant) as conn:
        return conn.execute("SELECT count(*) AS n FROM incidents WHERE tenant_id=%s", (tenant,)).fetchone()["n"]


def test_note_materializing_scheduled_end_applies_release_before_commit(setup, real_db, v1):
    _, _, tenant, config = setup
    rule(setup)
    window(setup)
    changed = receive(real_db,config,v1)
    alert = consume(real_db,config,changed)
    assert incidents(real_db,tenant) == 0
    end = expire(real_db,tenant)
    response = mutate(real_db,setup,alert)
    assert incidents(real_db,tenant) == 1
    with real_db.transaction(tenant) as conn:
        revision = conn.execute("SELECT revision FROM alerts WHERE tenant_id=%s AND id=%s", (tenant,alert)).fetchone()["revision"]
        assert response.json()["meta"]["revision"] == str(revision)
        support = conn.execute("SELECT evidence FROM correlation_decisions WHERE tenant_id=%s", (tenant,)).fetchone()["evidence"]["supports"][0]
        assert datetime.fromisoformat(support["start"]) == end
        assert not conn.execute("SELECT 1 FROM maintenance_effect_outbox WHERE tenant_id=%s AND processed_at IS NULL", (tenant,)).fetchone()


def test_source_and_maintenance_release_without_configured_rules(setup, real_db, v1):
    _, _, tenant, config = setup
    window(setup)
    alert = consume(real_db,config,receive(real_db,config,v1))
    expire(real_db,tenant)
    mutate(real_db,setup,alert)
    assert incidents(real_db,tenant) == 0


def test_manual_reopen_inside_maintenance_cannot_manufacture_release_evidence(setup, real_db, v1):
    _, _, tenant, config = setup
    rule(setup)
    window(setup)
    alert = consume(real_db,config,receive(real_db,config,v1))
    mutate(real_db,setup,alert,"resolved")
    mutate(real_db,setup,alert,"firing")
    expire(real_db,tenant)
    mutate(real_db,setup,alert)
    assert incidents(real_db,tenant) == 0
    with real_db.transaction(tenant) as conn:
        assert not conn.execute("SELECT 1 FROM correlation_evidence WHERE tenant_id=%s", (tenant,)).fetchone()
        assert conn.execute("SELECT correlation_allowed FROM maintenance_qualifier_effects WHERE tenant_id=%s", (tenant,)).fetchone()["correlation_allowed"] is False


def test_release_waits_for_later_snapshot_source_then_consumption_applies_it(setup, real_db, v1):
    _, _, tenant, config = setup
    rule(setup, 'alert.message == "later"')
    window(setup)
    alert = consume(real_db,config,receive(real_db,config,v1))
    later = receive(real_db,config,{**v1,"message":"later"},1)
    expire(real_db,tenant)
    mutate(real_db,setup,alert)
    assert incidents(real_db,tenant) == 0
    consume(real_db,config,later,1)
    assert incidents(real_db,tenant) == 1


def test_source_refire_after_manual_resolve_can_qualify_at_end(setup, real_db, v1):
    _, _, tenant, config = setup
    rule(setup)
    window(setup)
    alert = consume(real_db,config,receive(real_db,config,v1))
    mutate(real_db,setup,alert,"resolved")
    consume(real_db,config,receive(real_db,config,v1,1),1)
    expire(real_db,tenant)
    mutate(real_db,setup,alert)
    assert incidents(real_db,tenant) == 1


@pytest.mark.parametrize("initial,expected", [("later",0),("other",1)])
def test_post_reopen_source_requires_normal_condition_transition(setup, real_db, v1, initial, expected):
    _, _, tenant, config = setup
    rule(setup, 'alert.message == "later"')
    window(setup)
    alert = consume(real_db,config,receive(real_db,config,{**v1,"message":initial}))
    mutate(real_db,setup,alert,"resolved")
    mutate(real_db,setup,alert,"firing")
    consume(real_db,config,receive(real_db,config,{**v1,"message":"later"},1),1)
    expire(real_db,tenant)
    mutate(real_db,setup,alert)
    assert incidents(real_db,tenant) == expected


def test_manual_assignment_closing_final_cause_cannot_qualify(setup, real_db, v1):
    context, client, tenant, config = setup
    rule(setup)
    window(setup, "alert.assignee_id == null")
    alert = consume(real_db,config,receive(real_db,config,v1))
    with real_db.transaction(tenant) as conn:
        revision = conn.execute("SELECT revision FROM alerts WHERE tenant_id=%s AND id=%s", (tenant,alert)).fetchone()["revision"]
    response = client.post(f"/api/tenants/{tenant}/alerts/{alert}/commands", json={
        "command_id":str(uuid4()),"expected_revision":str(revision),
        "kind":"set_assignee","value":str(context[1])})
    assert response.status_code == 200, response.text
    assert incidents(real_db,tenant) == 0


def test_source_final_unsuppression_after_manual_reopen_is_genuine_qualifier(setup, real_db, v1):
    _, _, tenant, config = setup
    rule(setup)
    window(setup, 'alert.message == "blocked"')
    alert = consume(real_db,config,receive(real_db,config,{**v1,"message":"blocked"}))
    mutate(real_db,setup,alert,"resolved")
    mutate(real_db,setup,alert,"firing")
    changed = receive(real_db,config,{**v1,"message":"unblocked"},1)
    assert incidents(real_db,tenant) == 0
    consume(real_db,config,changed,1)
    assert incidents(real_db,tenant) == 1


def test_source_received_before_manual_resolve_cannot_revive_at_later_end(setup, real_db, v1):
    _, _, tenant, config = setup
    rule(setup)
    window(setup)
    alert = consume(real_db,config,receive(real_db,config,v1))
    queued = receipt(config,v1)
    mutate(real_db,setup,alert,"resolved")
    consume(real_db,config,receive(real_db,config,v1,1,queued),1)
    expire(real_db,tenant)
    mutate(real_db,setup,alert)
    assert incidents(real_db,tenant) == 0


def test_bulk_finishes_all_mutations_before_inline_incident_additions(setup, real_db, v1):
    context, client, tenant, config = setup
    rule(setup)
    window(setup)
    first = consume(real_db,config,receive(real_db,config,v1))
    second = consume(real_db,config,receive(real_db,config,{**v1,"application":str(uuid4())},1),1)
    expire(real_db,tenant)
    with real_db.transaction(tenant) as conn:
        items = [{"alert_id":str(row["id"]),"expected_revision":str(row["revision"])} for row in
            conn.execute("SELECT id,revision FROM alerts WHERE tenant_id=%s AND id=ANY(%s::uuid[])", (tenant,[first,second]))]
    command = {"command_id":str(uuid4()),"items":items,"kind":"set_assignee","value":str(context[1])}
    response = client.post(f"/api/tenants/{tenant}/alerts/bulk-commands", json=command)
    assert response.status_code == 200, response.text
    with real_db.transaction(tenant) as conn:
        rows = conn.execute("SELECT revision,assignee_id FROM alerts WHERE tenant_id=%s", (tenant,)).fetchall()
        assert len(rows) == 2 and all(row["revision"] == 3 and row["assignee_id"] == context[1] for row in rows)
        assert all(row["revision"] == "3" for row in response.json()["data"])
        assert conn.execute("SELECT count(*) AS n FROM alert_active_ownership WHERE tenant_id=%s", (tenant,)).fetchone()["n"] == 2
    assert incidents(real_db,tenant) == 1
    replay = client.post(f"/api/tenants/{tenant}/alerts/bulk-commands", json=command)
    assert replay.status_code == 200 and replay.json() == response.json()


def test_late_applied_source_updates_existing_end_provenance(setup, real_db, v1):
    _, _, tenant, config = setup
    rule(setup, 'alert.message == "later"')
    window(setup)
    alert = consume(real_db,config,receive(real_db,config,v1))
    queued = receipt(config,{**v1,"message":"later"})
    expire(real_db,tenant)
    mutate(real_db,setup,alert)
    assert incidents(real_db,tenant) == 0
    later = receive(real_db,config,v1,1,queued)
    assert incidents(real_db,tenant) == 0
    consume(real_db,config,later,1)
    assert incidents(real_db,tenant) == 1


def test_unchanged_source_consumption_unblocks_release_without_new_journal(setup, real_db, v1):
    _, _, tenant, config = setup
    rule(setup)
    window(setup)
    alert = consume(real_db,config,receive(real_db,config,v1))
    repeated = receive(real_db,config,v1,1)
    expire(real_db,tenant)
    mutate(real_db,setup,alert)
    assert incidents(real_db,tenant) == 0
    consume(real_db,config,repeated,1)
    assert incidents(real_db,tenant) == 1


def test_note_does_not_promote_evidence_from_stale_source_snapshot(setup, real_db, v1):
    _, _, tenant, config = setup
    rule(setup, 'alert.message == "original"')
    older = receive(real_db,config,{**v1,"message":"original"})
    receive(real_db,config,{**v1,"message":"newer"},1)
    alert = consume(real_db,config,older)
    assert incidents(real_db,tenant) == 0
    mutate(real_db,setup,alert)
    unrelated = receive(real_db,config,{**v1,"application":str(uuid4()),"message":"other"},2)
    consume(real_db,config,unrelated,2)
    assert incidents(real_db,tenant) == 0


@pytest.mark.parametrize("back_received_before_end", [False, True])
def test_away_and_back_after_end_waits_for_all_applied_sources_and_keeps_new_start(setup, real_db, v1, back_received_before_end):
    _, _, tenant, config = setup
    rule(setup, 'alert.message == "original"')
    window(setup)
    consume(real_db,config,receive(real_db,config,{**v1,"message":"original"}))
    queued = receipt(config,{**v1,"message":"original"}) if back_received_before_end else None
    end = expire(real_db,tenant)
    away = receive(real_db,config,{**v1,"message":"other"},1)
    back = receive(real_db,config,{**v1,"message":"original"},2,queued)
    assert incidents(real_db,tenant) == 0
    consume(real_db,config,away,1)
    assert incidents(real_db,tenant) == 0
    consume(real_db,config,back,2)
    assert incidents(real_db,tenant) == 1
    with real_db.transaction(tenant) as conn:
        membership = conn.execute("SELECT start_at FROM correlation_evidence WHERE tenant_id=%s", (tenant,)).fetchone()
        expected = end if back_received_before_end else datetime.fromisoformat(json.loads(back)["effective_at"])
        assert membership["start_at"] == expected
        assert membership["start_at"] >= end
