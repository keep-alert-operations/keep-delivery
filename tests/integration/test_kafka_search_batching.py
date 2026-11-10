"""Search hints from several busy partitions share one bulk on a real broker."""

# ruff: noqa: F811
import json
import os
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest

from keep.search.projection import index_mapping
from test_kafka_first import apply, receipt
from test_search_pipeline import real_db  # noqa: F401

pytestmark = pytest.mark.integration

PARTITIONS = 4
PER_PARTITION = 10


def test_busy_partitions_share_one_bulk_and_commit_their_own_offsets(real_db, tenant, v1):
    from confluent_kafka import Producer, TopicPartition
    from confluent_kafka.admin import AdminClient, NewTopic
    from keep.kafka_changed import ChangedConsumer

    bootstrap = os.getenv("KEEP_TEST_KAFKA_BOOTSTRAP")
    elasticsearch = os.getenv("KEEP_TEST_ELASTICSEARCH_URL")
    if not bootstrap or not elasticsearch:
        pytest.skip("Requires isolated Kafka and Elasticsearch")
    # Imported here so collection survives an indexer pin from before #11.
    from keep.search import consumer, reconciliation

    if not hasattr(consumer, "index_groups"):
        pytest.skip("requires keep-indexer with index_groups")
    index_batch, index_groups, index_record = (
        consumer.index_batch,
        consumer.index_groups,
        consumer.index_record,
    )
    search = SimpleNamespace(
        environment="development",
        global_tenant_id=tenant,
        elasticsearch_url=elasticsearch,
        elasticsearch_index="keep-hints-" + uuid4().hex,
    )
    kafka = SimpleNamespace(
        environment="hints-" + uuid4().hex, global_tenant_id=tenant, kafka_bootstrap=bootstrap
    )
    topic = f"keep.{kafka.environment}.alerts.changed"
    admin = AdminClient({"bootstrap.servers": bootstrap})
    for future in admin.create_topics([NewTopic(topic, PARTITIONS, 1)]).values():
        future.result(15)
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE kafka_pipeline_control SET enabled=true,changed_partitions=%s WHERE singleton",
            (PARTITIONS,),
        )
    producer = Producer({"bootstrap.servers": bootstrap, "acks": "all"})
    partition_of = {}
    for offset in range(PARTITIONS * PER_PARTITION):
        payload = {**v1, "application": "hints-" + uuid4().hex}
        changed = apply(real_db, search, receipt(search, payload), offset)
        partition_of[json.loads(changed)["alert_id"]] = offset % PARTITIONS
        producer.produce(
            topic, key=tenant.bytes, value=bytes(changed), partition=offset % PARTITIONS
        )
    assert producer.flush(10) == 0

    service = reconciliation.service_for(real_db, search, None)
    service.client.options(request_timeout=30).indices.create(
        index=search.elasticsearch_index,
        mappings=index_mapping(),
        settings={"number_of_shards": 1, "number_of_replicas": 0},
    )
    bulks, original = [], service.client.bulk

    def bulk(**kwargs):
        bulks.append(
            (
                kwargs["refresh"],
                {partition_of[op["index"]["_id"]] for op in kwargs["operations"][::2]},
                len(kwargs["operations"]) // 2,
            )
        )
        return original(**kwargs)

    service.client.bulk = bulk

    def bound(handler):
        return lambda db, settings, profile, value: handler(db, search, profile, value)

    worker = ChangedConsumer(
        real_db,
        kafka,
        None,
        "indexer",
        bound(index_record),
        batch_handler=bound(index_batch),
        groups_handler=bound(index_groups),
        batch_size=reconciliation.BULK_DOCUMENTS,
    )
    points = [TopicPartition(topic, p) for p in range(PARTITIONS)]
    try:
        handled, deadline = 0, monotonic() + 60
        while handled < PARTITIONS * PER_PARTITION and monotonic() < deadline:
            handled += worker.turn()
        assert handled == PARTITIONS * PER_PARTITION
        # Several partitions' hints went through one bulk and one refresh wait.
        assert all(refresh == "wait_for" for refresh, _, _ in bulks)
        assert any(len(partitions) > 1 for _, partitions, _ in bulks)
        assert len(bulks) < PARTITIONS * PER_PARTITION
        assert sum(size for _, _, size in bulks) == PARTITIONS * PER_PARTITION
        visible = service.client.count(index=search.elasticsearch_index)["count"]
        assert visible == PARTITIONS * PER_PARTITION
        deadline = monotonic() + 30
        while monotonic() < deadline and worker.uncommitted:
            assert worker.turn() == 0
        # Every partition committed exactly its own handled offsets.
        committed = {p.partition: p.offset for p in worker.client.committed(points, timeout=5)}
        assert committed == {p: PER_PARTITION for p in range(PARTITIONS)}
    finally:
        worker.close()
        service.client.options(request_timeout=30).indices.delete(
            index=search.elasticsearch_index, ignore_unavailable=True
        )
        service.close()
        real_db._keep_projection_service = None
    with real_db.transaction() as conn:
        positions = conn.execute(
            "SELECT partition,next_offset FROM kafka_consumer_positions WHERE consumer='indexer' AND topic=%s",
            (topic,),
        ).fetchall()
    assert {row["partition"]: row["next_offset"] for row in positions} == {
        p: PER_PARTITION for p in range(PARTITIONS)
    }
