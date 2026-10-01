"""Kafka-first receipt/replay correctness against isolated PostgreSQL and Kafka."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from appchi.ingestion.kafka_admission import KafkaAdmission, KafkaPublisher
from appchi.ingestion.native import canonical
from appchi.kafka_source import apply_record, SourceConsumer

pytestmark = pytest.mark.integration


class Capture:
    def __init__(self):
        self.records = []

    async def publish(self, topic, key, value):
        self.records.append((topic, key, value))


def settings(db):
    with db.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    clock = Path(".local/kafka-first-clock.json")
    clock.write_text(
        json.dumps(
            {
                "synchronized": True,
                "offset_seconds": 0,
                "measured_at": datetime.now(UTC).isoformat(),
            }
        )
    )
    return SimpleNamespace(
        clock_health_file=clock,
        environment="test-" + uuid4().hex,
        global_tenant_id=tenant,
        kafka_bootstrap=os.getenv("APPCHI_TEST_KAFKA_BOOTSTRAP"),
    )


def receipt(config, payload, *, publisher=None, at=None):
    publisher = publisher or Capture()
    admission = KafkaAdmission(
        publisher, environment=config.environment, global_tenant_id=config.global_tenant_id
    )
    status, body = asyncio.run(admission.admit(canonical(payload), "v1", received_at=at))
    assert status == 202, body
    return publisher.records[-1] if isinstance(publisher, Capture) else body


def apply(db, config, record, offset=0):
    topic, key, value = record
    return apply_record(
        db,
        topic=topic,
        key=key,
        value=value,
        partition=0,
        offset=offset,
        settings=config,
        profile=None,
    )


def test_pg_commit_replay_is_immutable_and_duplicate_offset_has_no_second_event(real_db, v1):
    config = settings(real_db)
    record = receipt(config, v1)
    changed = apply(real_db, config, record)
    alert_id = UUID(json.loads(changed)["alert_id"])
    # A committed manual change cannot rewrite the retained source publication.
    with real_db.transaction(config.global_tenant_id) as conn:
        conn.execute(
            "UPDATE alerts SET message='intervening manual state',revision=revision+1 WHERE id=%s",
            (alert_id,),
        )
    assert apply(real_db, config, record) == changed
    assert apply(real_db, config, record, 1) is None
    with real_db.transaction(config.global_tenant_id) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM alert_source_events WHERE alert_id=%s", (alert_id,)
            ).fetchone()["n"]
            == 1
        )
        assert (
            conn.execute("SELECT message FROM alerts WHERE id=%s", (alert_id,)).fetchone()[
                "message"
            ]
            == "intervening manual state"
        )


def test_resubmission_is_new_delivery_but_unchanged_repeat_not_semantic(real_db, v1):
    config = settings(real_db)
    first = receipt(config, v1)
    second = receipt(config, v1)
    assert json.loads(first[2])["event_id"] != json.loads(second[2])["event_id"]
    initial = json.loads(apply(real_db, config, first))
    repeated = json.loads(apply(real_db, config, second, 1))
    assert initial["change_order"] == repeated["change_order"]
    assert int(repeated["projection_version"]) == int(initial["projection_version"]) + 1


def test_terminal_post_acceptance_identity_conflict_advances_application(real_db, v1):
    config = settings(real_db)
    apply(real_db, config, receipt(config, v1))
    conflicting = receipt(config, dict(v1, severity="warning"))
    assert apply(real_db, config, conflicting, 1) is None
    with real_db.transaction(config.global_tenant_id) as conn:
        result = conn.execute(
            "SELECT outcome,error_code FROM kafka_source_results WHERE delivery_id=%s",
            (json.loads(conflicting[2])["event_id"],),
        ).fetchone()
        assert result["outcome"] == "rejected"
        assert result["error_code"]


def test_application_order_preserved_when_receive_timestamps_reverse(real_db, v1):
    config = settings(real_db)
    now = datetime.now(UTC) - timedelta(seconds=2)
    apply(real_db, config, receipt(config, v1, at=now))
    changed = apply(
        real_db,
        config,
        receipt(config, dict(v1, message="append second"), at=now - timedelta(seconds=1)),
        1,
    )
    with real_db.transaction(config.global_tenant_id) as conn:
        row = conn.execute(
            "SELECT message FROM alerts WHERE id=%s", (json.loads(changed)["alert_id"],)
        ).fetchone()
        assert row["message"] == "append second"


def test_actual_broker_ack_and_transactional_output_offset(real_db, v1):
    from confluent_kafka import Consumer, TopicPartition, KafkaException
    from confluent_kafka.admin import AdminClient, NewTopic

    config = settings(real_db)
    if not config.kafka_bootstrap:
        pytest.skip("Requires isolated Kafka")
    topics = [f"appchi.{config.environment}.alerts.{name}" for name in ("received", "changed")]
    admin = AdminClient({"bootstrap.servers": config.kafka_bootstrap})
    for future in admin.create_topics([NewTopic(t, 1, 1) for t in topics]).values():
        future.result(15)
    publisher = KafkaPublisher(config)
    worker = None
    output = Consumer(
        {
            "bootstrap.servers": config.kafka_bootstrap,
            "group.id": uuid4().hex,
            "isolation.level": "read_committed",
            "enable.auto.commit": False,
        }
    )
    try:
        # No database is available to the admission object.
        receipt(config, v1, publisher=publisher)
        output.assign([TopicPartition(topics[0], 0, 0)])
        incoming = output.poll(5)
        assert incoming is not None and not incoming.error()
        # Simulate death immediately after PG commit and before begin_transaction.
        original = apply_record(
            real_db,
            topic=incoming.topic(),
            partition=0,
            offset=incoming.offset(),
            key=incoming.key(),
            value=incoming.value(),
            settings=config,
            profile=None,
        )
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE kafka_pipeline_control SET enabled=true,received_partitions=1,changed_partitions=1 WHERE singleton"
            )
        worker = SourceConsumer(real_db, config, None)
        deadline = monotonic() + 60
        while monotonic() < deadline:
            try:
                if worker.turn():
                    break
            except KafkaException:
                worker.close()
                worker = SourceConsumer(real_db, config, None)
        output.assign([TopicPartition(topics[1], 0, 0)])
        deadline = monotonic() + 15
        message = None
        while monotonic() < deadline and message is None:
            message = output.poll(0.2)
        assert message is not None and not message.error()
        assert message.value() == original
        assert message.key() == config.global_tenant_id.bytes
        assert worker.client.committed([TopicPartition(topics[0], 0)], timeout=5)[0].offset == 1
        with real_db.transaction() as conn:
            alert_id = json.loads(original)["alert_id"]
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM alert_source_events WHERE alert_id=%s", (alert_id,)
                ).fetchone()["n"]
                == 1
            )
            # The broker commit succeeded but its PG confirmation was lost.
            conn.execute(
                "UPDATE kafka_source_results SET kafka_confirmed=false WHERE topic=%s", (topics[0],)
            )
        worker.close()
        worker = SourceConsumer(real_db, config, None)
        deadline = monotonic() + 15
        while monotonic() < deadline and not worker.producers:
            assert worker.turn() == 0
        with real_db.transaction() as conn:
            assert conn.execute(
                "SELECT kafka_confirmed FROM kafka_source_results WHERE topic=%s", (topics[0],)
            ).fetchone()["kafka_confirmed"]
        assert output.poll(0.5) is None
    finally:
        publisher.close()
        if worker:
            worker.close()
        output.close()


def test_lost_manual_hint_reconciles_current_authority_and_watermark(real_db, v1, monkeypatch):
    from elasticsearch import Elasticsearch
    from appchi.search.projection import index_mapping
    from appchi.search.reconciliation import reconcile_page
    from appchi.alerts.repository import store_alert, publish
    from appchi.alerts.lifecycle import apply_command
    from appchi import alert_notifications

    endpoint = os.getenv("APPCHI_TEST_ELASTICSEARCH_URL")
    if not endpoint:
        pytest.skip("Requires isolated Elasticsearch")
    monkeypatch.setattr(alert_notifications, "_publisher", None)
    config = settings(real_db)
    changed = json.loads(apply(real_db, config, receipt(config, v1)))
    tenant, alert_id = config.global_tenant_id, UUID(changed["alert_id"])
    index = "appchi-kafka-test-" + uuid4().hex
    epoch = uuid4().hex
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE tenant_view_state SET index_epoch=%s WHERE tenant_id=%s", (epoch, tenant)
        )
    with Elasticsearch(endpoint, request_timeout=5, max_retries=0) as es:
        es.indices.create(
            index=index,
            mappings=index_mapping(),
            settings={"number_of_shards": 1, "number_of_replicas": 0},
        )
        try:
            assert not reconcile_page(real_db, es, index, tenant, epoch)
            # A later manual mutation is deliberately not delivered to Kafka.
            with real_db.transaction(tenant) as conn:
                row = conn.execute(
                    "SELECT * FROM alerts WHERE id=%s FOR UPDATE", (alert_id,)
                ).fetchone()
                now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
                updated, semantic = apply_command(row, "set_status", "resolved", None, now)
                store_alert(conn, updated)
                publish(conn, row, updated, now, "set_status", semantic=semantic)
                boundary = conn.execute(
                    "SELECT projection_seq FROM tenant_work_state WHERE tenant_id=%s", (tenant,)
                ).fetchone()["projection_seq"]
            for _ in range(100):
                if reconcile_page(real_db, es, index, tenant, epoch):
                    break
            with real_db.transaction() as conn:
                assert (
                    conn.execute(
                        "SELECT searchable_seq FROM tenant_view_state WHERE tenant_id=%s", (tenant,)
                    ).fetchone()["searchable_seq"]
                    < boundary
                )
            # Next pass repairs the write that occurred behind the first cursor.
            for _ in range(100):
                if reconcile_page(real_db, es, index, tenant, epoch):
                    break
            document = es.get(index=index, id=str(alert_id))["_source"]
            assert document["status"] == "resolved"
            with real_db.transaction() as conn:
                assert (
                    conn.execute(
                        "SELECT searchable_seq FROM tenant_view_state WHERE tenant_id=%s", (tenant,)
                    ).fetchone()["searchable_seq"]
                    >= boundary
                )
                assert not conn.execute(
                    "SELECT 1 FROM application_outbox WHERE tenant_id=%s AND stream='changed' AND convert_from(envelope,'UTF8')::jsonb->>'alert_id'=%s",
                    (tenant, str(alert_id)),
                ).fetchone()
        finally:
            es.indices.delete(index=index)


def test_real_broker_low_watermark_gap_is_recorded_and_fenced(real_db, v1):
    from confluent_kafka import Consumer, TopicPartition
    from confluent_kafka.admin import AdminClient, NewTopic
    from appchi.shared.errors import AppError

    config = settings(real_db)
    if not config.kafka_bootstrap:
        pytest.skip("Requires isolated Kafka")
    topic = f"appchi.{config.environment}.alerts.received"
    admin = AdminClient({"bootstrap.servers": config.kafka_bootstrap})
    for future in admin.create_topics([NewTopic(topic, 1, 1)]).values():
        future.result(15)
    publisher = KafkaPublisher(config)
    try:
        receipt(config, v1, publisher=publisher)
    finally:
        publisher.close()
    group = Consumer(
        {
            "bootstrap.servers": config.kafka_bootstrap,
            "group.id": f"appchi.{config.environment}.source-v2",
        }
    )
    try:
        group.commit(offsets=[TopicPartition(topic, 0, 0)], asynchronous=False)
    finally:
        group.close()
    # This topic was created by this test and contains only its synthetic input.
    for future in admin.delete_records(
        [TopicPartition(topic, 0, 1)], operation_timeout=5, request_timeout=10
    ).values():
        assert future.result(12).low_watermark == 1
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE kafka_pipeline_control SET enabled=true,received_partitions=1,changed_partitions=1 WHERE singleton"
        )
    worker = SourceConsumer(real_db, config, None)
    try:
        with pytest.raises(AppError, match="kafka_retention_gap"):
            worker._assigned(worker.client, [TopicPartition(topic, 0)])
        with real_db.transaction() as conn:
            fault = conn.execute(
                "SELECT reason,input_offset FROM kafka_pipeline_faults WHERE consumer='source' AND topic=%s",
                (topic,),
            ).fetchone()
            assert fault == {"reason": "retention_gap", "input_offset": 0}
            assert not conn.execute(
                "SELECT 1 FROM kafka_source_results WHERE topic=%s", (topic,)
            ).fetchone()
    finally:
        worker.close()
