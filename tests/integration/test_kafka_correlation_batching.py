"""Batched correlation offsets: a crash before the offset commit replays idempotently."""

# ruff: noqa: F811
import os
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest

from keep.correlation.consumer import correlate_record
from test_correlation_configuration import rules  # noqa: F401
from test_kafka_maintenance_inline import CAPACITY, consume, receive, rule, setup  # noqa: F401
from test_product import browser  # noqa: F401
from test_search_pipeline import real_db  # noqa: F401

pytestmark = pytest.mark.integration


def consumption(db, tenant):
    with db.transaction(tenant) as conn:
        return conn.execute(
            """SELECT (SELECT count(*) FROM kafka_correlation_events WHERE tenant_id=%s) AS events,
            (SELECT kafka_sequence FROM correlation_consumer_state WHERE tenant_id=%s) AS sequence,
            (SELECT count(*) FROM correlation_evidence WHERE tenant_id=%s) AS evidence,
            (SELECT count(*) FROM correlation_decisions WHERE tenant_id=%s) AS decisions,
            (SELECT count(*) FROM incidents WHERE tenant_id=%s) AS incidents""",
            (tenant,) * 5,
        ).fetchone()


def payload(v1):
    return {**v1, "application": "batch-" + uuid4().hex}


def test_replayed_changed_event_after_postgres_commit_changes_nothing(setup, real_db, v1):
    _, _, tenant, config = setup
    rule(setup)
    changed = receive(real_db, config, payload(v1))
    consume(real_db, config, changed)
    first = consumption(real_db, tenant)
    assert first["events"] == 1 and first["sequence"] >= 1
    # The offset commit was lost: the same record is delivered again.
    consume(real_db, config, changed)
    assert consumption(real_db, tenant) == first


def test_real_broker_batches_commits_and_replays_uncommitted_inputs(setup, real_db, v1):
    from confluent_kafka import Producer, TopicPartition
    from confluent_kafka.admin import AdminClient, NewTopic
    from keep.kafka_changed import ChangedConsumer

    bootstrap = os.getenv("KEEP_TEST_KAFKA_BOOTSTRAP")
    if not bootstrap:
        pytest.skip("Requires isolated Kafka")
    _, _, tenant, inline = setup
    rule(setup)
    config = SimpleNamespace(
        environment="batch-" + uuid4().hex, global_tenant_id=tenant, kafka_bootstrap=bootstrap
    )
    topic = f"keep.{config.environment}.alerts.changed"
    admin = AdminClient({"bootstrap.servers": bootstrap})
    for future in admin.create_topics([NewTopic(topic, 1, 1)]).values():
        future.result(15)
    with real_db.transaction() as conn:
        conn.execute("UPDATE kafka_pipeline_control SET changed_partitions=1 WHERE singleton")
    producer = Producer({"bootstrap.servers": bootstrap, "acks": "all"})
    for offset in range(3):
        changed = receive(real_db, inline, payload(v1), offset=offset)
        producer.produce(topic, key=tenant.bytes, value=changed)
    assert producer.flush(10) == 0
    point = TopicPartition(topic, 0)

    def drain(worker, count):
        handled, deadline = 0, monotonic() + 60
        while handled < count and monotonic() < deadline:
            handled += worker.turn()
        assert handled == count

    worker = ChangedConsumer(
        real_db, config, CAPACITY, "correlation", correlate_record, commit_interval=3600
    )
    try:
        drain(worker, 3)
        # Durable PostgreSQL consumption precedes any offset commit.
        assert consumption(real_db, tenant)["events"] == 3
        assert worker.client.committed([point], timeout=5)[0].offset < 0
        first = consumption(real_db, tenant)
        # Crash before the batched commit: the in-memory progress is lost.
        worker.uncommitted.clear()
    finally:
        worker.close()
    # The interval is far above the replay's duration, so only an idle turn can
    # commit the replayed offsets.
    worker = ChangedConsumer(
        real_db, config, CAPACITY, "correlation", correlate_record, commit_interval=10
    )
    try:
        drain(worker, 3)
        assert consumption(real_db, tenant) == first
        assert worker.client.committed([point], timeout=5)[0].offset < 0
        idle, deadline = 0, monotonic() + 30
        while monotonic() < deadline and worker.client.committed([point], timeout=5)[0].offset != 3:
            assert worker.turn() == 0
            idle += 1
        assert worker.client.committed([point], timeout=5)[0].offset == 3
        assert idle > 0
    finally:
        worker.close()
    with real_db.transaction() as conn:
        assert conn.execute(
            "SELECT next_offset FROM kafka_consumer_positions WHERE consumer='correlation' AND topic=%s",
            (topic,),
        ).fetchone() == {"next_offset": 3}
