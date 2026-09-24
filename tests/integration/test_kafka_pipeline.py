"""Opt-in actual Kafka publication/offsets plus actual PostgreSQL receipt authority."""

# ruff: noqa: F811
import os
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4
import pytest
from confluent_kafka import Consumer, Producer, TopicPartition, OFFSET_BEGINNING
from confluent_kafka.admin import AdminClient, NewTopic
from appchi.ingestion.service import Admission
from appchi.ingestion.native import canonical
from appchi.alerts.processor import process_one
from appchi.worker import relay_turn
from appchi.pipeline import record_receipt, consume_once
from appchi.shared.errors import AppError
from test_search_pipeline import real_db  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.compatibility]


def poll_record(client):
    deadline = monotonic() + 15
    while monotonic() < deadline:
        message = client.poll(1)
        if message is not None:
            assert not message.error(), message.error()
            return message
    pytest.fail("Kafka did not return the acknowledged record within 15 seconds")


def test_real_kafka_replay_durable_receipts_and_poison_offset(real_db, cipher, profile, v1):
    bootstrap = os.getenv("APPCHI_TEST_KAFKA_BOOTSTRAP")
    if not bootstrap:
        pytest.skip("Requires a disposable real Kafka broker")
    env = "test-" + uuid4().hex
    settings = SimpleNamespace(environment=env, deployment_epoch=env, kafka_bootstrap=bootstrap)
    topic = f"appchi.{env}.alerts.received"
    admin = AdminClient({"bootstrap.servers": bootstrap})
    admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])[topic].result(15)
    consumer = None
    group = "appchi-test-" + uuid4().hex

    def connection():
        client = Consumer(
            {
                "bootstrap.servers": bootstrap,
                "group.id": group,
                "enable.auto.commit": False,
                "enable.auto.offset.store": False,
                "auto.offset.reset": "earliest",
            }
        )
        client.assign([TopicPartition(topic, 0, OFFSET_BEGINNING)])
        return client

    try:
        Admission(real_db, cipher, profile).admit(canonical(v1), "v1")
        Admission(real_db, cipher, profile).admit(canonical(dict(v1, message="second")), "v1")
        for _ in range(4):
            relay_turn(real_db, settings)
            with real_db.transaction() as conn:
                if (
                    conn.execute(
                        "SELECT count(*) AS n FROM application_outbox WHERE published_at IS NULL"
                    ).fetchone()["n"]
                    == 0
                ):
                    break
        with real_db.transaction() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM application_outbox WHERE published_at IS NOT NULL"
                ).fetchone()["n"]
                == 2
            )
            tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
        while process_one(real_db, tenant):
            pass
        consumer = connection()
        first = poll_record(consumer)
        record_receipt(
            real_db,
            consumer="processor",
            topic=topic,
            partition=first.partition(),
            offset=first.offset(),
            key=first.key(),
            value=first.value(),
            environment=env,
        )
        # Simulate failure after PG commit and before Kafka offset commit.
        consumer.close()
        consumer = connection()
        received = []
        deadline = monotonic() + 15
        while len(received) < 2 and monotonic() < deadline:
            result = consume_once(real_db, settings, "processor", consumer)
            if result:
                received.append(result)
        assert received == [tenant, tenant]
        assert consumer.committed([TopicPartition(topic, 0)], timeout=5)[0].offset == 2
        with real_db.transaction() as conn:
            assert conn.execute("SELECT count(*) AS n FROM pipeline_receipts").fetchone()["n"] == 2
        producer = Producer({"bootstrap.servers": bootstrap, "acks": "all"})
        producer.produce(topic, key=tenant.bytes, value=b'{"bad":"event"}')
        assert producer.flush(5) == 0
        deadline = monotonic() + 15
        failed = False
        while monotonic() < deadline:
            try:
                consume_once(real_db, settings, "processor", consumer)
            except AppError:
                failed = True
                break
        assert failed
        assert consumer.committed([TopicPartition(topic, 0)], timeout=5)[0].offset == 2
        with real_db.transaction() as conn:
            assert (
                conn.execute("SELECT count(*) AS n FROM pipeline_quarantine").fetchone()["n"] == 1
            )
    finally:
        if consumer:
            consumer.close()
        admin.delete_topics([topic], operation_timeout=10)[topic].result(15)
