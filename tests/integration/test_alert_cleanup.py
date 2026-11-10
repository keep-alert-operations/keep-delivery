"""Protected alert erasure with real SQL and explicit consumer release fixtures."""

# ruff: noqa: F811
from pathlib import Path
from uuid import uuid4, UUID
import pytest

from keep.recovery.retirement import retire_alert
from keep.recovery.alert_cleanup import cleanup_alert
from keep.pipeline import record_receipt
from keep.shared.errors import AppError
from test_product import browser, admit_alert  # noqa: F401
from test_search_pipeline import real_db as isolated_db
from test_incident_retirement import Store

pytestmark = pytest.mark.integration


@pytest.fixture
def real_db():
    for db in isolated_db.__wrapped__():
        draft = Path("migrations/0026_alert_command_retirement.sql.draft")
        if draft.exists():
            with db.transaction() as conn:
                conn.execute(draft.read_text(encoding="utf-8-sig"))
        yield db


@pytest.fixture
def retired(browser, real_db, cipher, profile, v1):
    api, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}/alerts/{alert}"
    note = {"command_id": str(uuid4()), "expected_revision": "1", "text": "Expired note content"}
    assert api.post(base + "/notes", json=note).status_code == 201
    resolve = {
        "command_id": str(uuid4()),
        "expected_revision": "2",
        "kind": "set_status",
        "value": "resolved",
    }
    assert api.post(base + "/commands", json=resolve).status_code == 200
    with real_db.transaction(tenant) as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
        conn.execute(
            "UPDATE alerts SET retention_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (alert,),
        )
    store = Store()
    assert retire_alert(real_db, store, tenant, alert)
    return api, tenant, alert, base, note, resolve, store


def release_consumers(db, tenant):
    with db.transaction(tenant) as conn:
        events = conn.execute(
            "SELECT * FROM application_outbox WHERE tenant_id=%s ORDER BY stream,stream_order",
            (tenant,),
        ).fetchall()
        conn.execute(
            "UPDATE application_outbox SET published_at=clock_timestamp() WHERE tenant_id=%s",
            (tenant,),
        )
        for index, event in enumerate(events):
            consumers = (
                ["processor"]
                if event["stream"] == "received"
                else ["projection", "correlation-wakeup"]
            )
            for consumer in consumers:
                conn.execute(
                    "INSERT INTO pipeline_receipts(consumer_name,topic,partition,broker_offset,tenant_id,outbox_id) VALUES(%s,%s,0,%s,%s,%s)",
                    (
                        consumer,
                        "keep.development.alerts." + event["stream"],
                        index,
                        tenant,
                        event["id"],
                    ),
                )
        conn.execute(
            "UPDATE tenant_view_state v SET searchable_seq=w.projection_seq FROM tenant_work_state w WHERE w.tenant_id=v.tenant_id AND v.tenant_id=%s",
            (tenant,),
        )
        conn.execute(
            "INSERT INTO correlation_consumer_state(tenant_id,change_order) SELECT tenant_id,change_order FROM tenant_work_state WHERE tenant_id=%s ON CONFLICT(tenant_id) DO UPDATE SET change_order=excluded.change_order",
            (tenant,),
        )
    return events


def test_replay_privacy_and_protected_bounded_erasure(retired, real_db):
    api, tenant, alert, base, note, resolve, store = retired
    for endpoint, body in (("/notes", note), ("/commands", resolve)):
        response = api.post(base + endpoint, json=body)
        assert (
            response.status_code == 409
            and response.json()["error"]["code"] == "command_result_expired"
        )
        assert "Expired note content" not in response.text
    # Proof and consumers independently block physical deletion.
    store.fail, store.fail_kind = "final", "command"
    with pytest.raises(AppError):
        cleanup_alert(real_db, store, tenant, alert)
    store.fail = None
    assert not cleanup_alert(real_db, store, tenant, alert)
    events = release_consumers(real_db, tenant)
    for _ in range(40):
        if cleanup_alert(real_db, store, tenant, alert, limit=2):
            break
    else:
        pytest.fail("Bounded cleanup failed to complete")
    with real_db.transaction(tenant) as conn:
        for table in ("alerts", "alert_notes", "alert_source_events", "alert_activity"):
            column = "id" if table == "alerts" else "alert_id"
            assert (
                conn.execute(f"SELECT 1 FROM {table} WHERE {column}=%s", (alert,)).fetchone()
                is None
            )
        marker = conn.execute(
            "SELECT * FROM projection_work WHERE alert_id=%s", (alert,)
        ).fetchall()
        assert len(marker) == 1 and marker[0]["snapshot"]["live"] is False
        assert (
            conn.execute(
                "SELECT final_version FROM alert_identities WHERE id=%s", (alert,)
            ).fetchone()["final_version"]
            == 4
        )
        assert (
            conn.execute(
                "SELECT 1 FROM ingest_items WHERE alert_id=%s AND (key_field<>'' OR length(identity_material)>0)",
                (alert,),
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT result FROM command_receipts WHERE command_id=%s", (note["command_id"],)
            ).fetchone()["result"]
            is None
        )
    # Already consumed immutable envelopes remain recognizable at later offsets
    # after their bodies expire, without recreating domain work.
    for index, event in enumerate(events):
        consumer = "processor" if event["stream"] == "received" else "projection"
        assert record_receipt(
            real_db,
            consumer=consumer,
            topic="keep.development.alerts." + event["stream"],
            partition=0,
            offset=1000 + index,
            key=UUID(tenant).bytes,
            value=bytes(event["envelope"]),
            environment="development",
        ) == UUID(tenant)
    assert api.get(base).status_code == 404
    assert retire_alert(real_db, store, tenant, alert)


def test_active_rebuild_holds_alert_bodies_and_preserves_incident(retired, real_db):
    api, tenant, alert, base, _, _, store = retired
    # Create a counterpart before cleanup through retained identity references;
    # the public API correctly rejects attaching an already retired alert.
    incident = uuid4()
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO incident_identities(id,tenant_id) VALUES(%s,%s)", (incident, tenant)
        )
        conn.execute(
            "INSERT INTO incident_alert_links(tenant_id,incident_id,alert_id,linked_at) VALUES(%s,%s,%s,clock_timestamp())",
            (tenant, incident, alert),
        )
        conn.execute(
            "INSERT INTO search_rebuild_jobs(id,source_index,target_index,source_epoch,target_epoch,binding_digest,state) VALUES(%s,'old','test-target','old','test-epoch','test','registered')",
            (uuid4(),),
        )
    release_consumers(real_db, tenant)
    assert not cleanup_alert(real_db, store, tenant, alert)
    with real_db.transaction(tenant) as conn:
        assert conn.execute("SELECT 1 FROM alerts WHERE id=%s", (alert,)).fetchone()
        conn.execute("UPDATE search_rebuild_jobs SET hold_active=false,state='cancelled'")
    for _ in range(20):
        if cleanup_alert(real_db, store, tenant, alert):
            break
    with real_db.transaction(tenant) as conn:
        assert conn.execute(
            "SELECT 1 FROM incident_alert_links WHERE alert_id=%s", (alert,)
        ).fetchone()
        assert (
            conn.execute(
                "SELECT retired_at FROM incident_identities WHERE id=%s", (incident,)
            ).fetchone()["retired_at"]
            is None
        )


def test_protected_hold_cancels_retirement_and_cannot_be_added_late(
    browser, real_db, cipher, profile, v1
):
    import psycopg

    api, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    response = api.post(
        f"/api/tenants/{tenant}/alerts/{alert}/commands",
        json={
            "command_id": str(uuid4()),
            "expected_revision": "1",
            "kind": "set_status",
            "value": "resolved",
        },
    )
    assert response.status_code == 200
    with real_db.transaction(tenant) as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
        conn.execute(
            "UPDATE alerts SET retention_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (alert,),
        )
    store = Store()

    def hold():
        with real_db.transaction(tenant) as conn:
            conn.execute(
                "INSERT INTO alert_retention_holds(tenant_id,alert_id,kind,reference) VALUES(%s,%s,'recovery','late')",
                (tenant, alert),
            )

    store.on_intent = hold
    assert not retire_alert(real_db, store, tenant, alert)
    assert [event.state for event in store.events] == ["intent", "cancel"]
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE alert_retention_holds SET released_at=clock_timestamp() WHERE alert_id=%s",
            (alert,),
        )
    assert retire_alert(real_db, store, tenant, alert)
    with pytest.raises(psycopg.errors.CheckViolation):
        with real_db.transaction(tenant) as conn:
            conn.execute(
                "INSERT INTO alert_retention_holds(tenant_id,alert_id,kind,reference) VALUES(%s,%s,'recovery','too-late')",
                (tenant, alert),
            )


def test_consumed_offset_cannot_change_its_event_after_cleanup(
    retired, real_db, cipher, profile, v1
):
    _, tenant, alert, _, _, _, store = retired
    # Kafka-first publishes changed events to Kafka, not this outbox; only the
    # legacy received stream still has outbox rows, so pin a second one.
    admit_alert(real_db, cipher, profile, {**v1, "application": v1["application"] + "-other"})
    events = release_consumers(real_db, tenant)
    for _ in range(20):
        if cleanup_alert(real_db, store, tenant, alert):
            break
    received = [event for event in events if event["stream"] == "received"]
    assert len(received) >= 2
    with real_db.transaction() as conn:
        first = conn.execute(
            "SELECT * FROM pipeline_receipts WHERE outbox_id=%s AND consumer_name='processor'",
            (received[0]["id"],),
        ).fetchone()
    with pytest.raises(AppError, match="pipeline_authoritative_proof_missing"):
        record_receipt(
            real_db,
            consumer="processor",
            topic=first["topic"],
            partition=first["partition"],
            offset=first["broker_offset"],
            key=UUID(tenant).bytes,
            value=bytes(received[1]["envelope"]),
            environment="development",
        )
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT outbox_id FROM pipeline_receipts WHERE consumer_name=%s AND topic=%s AND partition=%s AND broker_offset=%s",
                ("processor", first["topic"], first["partition"], first["broker_offset"]),
            ).fetchone()["outbox_id"]
            == received[0]["id"]
        )


def test_recovery_gate_closing_during_intent_prevents_logical_retirement(
    browser, real_db, cipher, profile, v1
):
    api, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}/alerts/{alert}"
    result = api.post(
        base + "/commands",
        json={
            "command_id": str(uuid4()),
            "expected_revision": "1",
            "kind": "set_status",
            "value": "resolved",
        },
    )
    assert result.status_code == 200
    with real_db.transaction(tenant) as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
        conn.execute(
            "UPDATE alerts SET retention_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (alert,),
        )
    store = Store()

    def close_gate():
        with real_db.transaction() as conn:
            conn.execute("UPDATE capability_gates SET ready=false WHERE capability='recovery'")

    store.on_intent = close_gate
    with pytest.raises(AppError) as failure:
        retire_alert(real_db, store, tenant, alert)
    assert failure.value.code == "recovery_not_ready"
    assert api.get(base).status_code == 200
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT retired_at FROM alert_identities WHERE id=%s", (alert,)
            ).fetchone()["retired_at"]
            is None
        )
        assert conn.execute("SELECT 1 FROM alert_key_claims WHERE alert_id=%s", (alert,)).fetchone()
        assert (
            conn.execute(
                "SELECT 1 FROM projection_work WHERE alert_id=%s AND snapshot->>'live'='false'",
                (alert,),
            ).fetchone()
            is None
        )
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
    assert retire_alert(real_db, store, tenant, alert)
    assert len({event.id for event in store.events if event.state == "intent"}) == 1
