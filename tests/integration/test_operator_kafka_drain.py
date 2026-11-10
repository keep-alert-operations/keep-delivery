# ruff: noqa: F811 -- pytest fixture imports
"""Real durable deletion boundaries with controlled Kafka offset observations."""

from datetime import UTC, datetime
import json
from types import SimpleNamespace
from uuid import uuid4

from confluent_kafka import TopicPartition
import pytest

from keep.operators.kafka_drain import require_drain
from keep.shared.errors import AppError
from test_operator_repository import operators, create  # noqa: F401
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


def test_request_lifetime_and_every_partition_must_cross_the_frozen_barrier(
    operators, real_db, tmp_path
):
    operator, _, _ = create(operators)
    health = tmp_path / "clock.json"
    health.write_text(
        json.dumps(
            {
                "measured_at": datetime.now(UTC).isoformat(),
                "offset_seconds": 0,
                "synchronized": True,
            }
        )
    )
    settings = SimpleNamespace(
        environment="drain-" + uuid4().hex, kafka_bootstrap="fixture:9092", clock_health_file=health
    )
    topic = f"keep.{settings.environment}.alerts.received"
    work = SimpleNamespace(
        operator_id=operator["id"],
        tenant_id=operators[2],
        policy_generation=1,
        policy_document='{"enabled":false}',
    )
    high = {0: 5, 1: 2}
    low = {0: 0, 1: 0}
    positions = {0: 4, 1: 2}
    calls = []

    class Kafka:
        def __init__(self, options):
            calls.append(options)

        def list_topics(self, requested, timeout):
            assert requested == topic
            return SimpleNamespace(
                topics={topic: SimpleNamespace(error=None, partitions={0: None, 1: None})}
            )

        def get_watermark_offsets(self, partition, timeout):
            return low[partition.partition], high[partition.partition]

        def committed(self, partitions, timeout):
            return [TopicPartition(topic, p.partition, positions[p.partition]) for p in partitions]

        def close(self):
            pass

    def drain():
        require_drain(real_db, settings, work, lambda: None, consumer_factory=Kafka)

    with real_db.transaction() as conn:
        original = conn.execute(
            "SELECT enabled,received_partitions FROM kafka_pipeline_control WHERE singleton"
        ).fetchone()
        conn.execute(
            "UPDATE kafka_pipeline_control SET enabled=true,received_partitions=2 WHERE singleton"
        )
    try:
        with pytest.raises(AppError, match="operator_kafka_drain_pending"):
            drain()
        assert calls == []
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE operator_kafka_drains SET denied_at=clock_timestamp()-interval '16 seconds' WHERE operator_id=%s",
                (work.operator_id,),
            )
        with pytest.raises(AppError, match="operator_kafka_drain_pending"):
            drain()
        with real_db.transaction() as conn:
            barrier = conn.execute(
                "SELECT offsets FROM operator_kafka_drains WHERE operator_id=%s",
                (work.operator_id,),
            ).fetchone()["offsets"]
        assert barrier["partitions"] == {"0": 5, "1": 2}
        high[0], positions[0] = 9, 5
        drain()  # Other operators' later traffic does not move this fence.
        low[0] = 6
        with pytest.raises(AppError, match="operator_kafka_drain_unverified"):
            drain()
        low[0] = 0
        work.policy_generation = 2
        before = len(calls)
        with pytest.raises(AppError, match="operator_kafka_drain_pending"):
            drain()
        assert len(calls) == before  # A new denial generation starts its own wait.
    finally:
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE kafka_pipeline_control SET enabled=%s,received_partitions=%s WHERE singleton",
                (original["enabled"], original["received_partitions"]),
            )
