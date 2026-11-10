"""Offline cutover with actual broker group offsets and parsed saved-rule migration."""

# ruff: noqa: F811
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from keep.kafka_cutover import activate, inspect_rules, require_drained
from keep.kafka_source import same_topic, topic_identity
from keep.ingestion.service import Admission
from keep.ingestion.native import canonical
from keep.shared.errors import AppError
from test_correlation_configuration import rules, create, definition, edit  # noqa: F401
from test_product import browser  # noqa: F401
from test_search_pipeline import real_db  # noqa: F401

pytestmark = pytest.mark.integration


def test_offline_cutover_blocks_saved_assignee_and_starts_after_old_records(
    rules, real_db, cipher, profile, v1
):
    """Requirements: REV-33, REV-41; Kafka alert pipeline 8."""
    from confluent_kafka import Consumer, Producer, TopicPartition
    from confluent_kafka.admin import AdminClient, NewTopic

    bootstrap = os.getenv("KEEP_TEST_KAFKA_BOOTSTRAP")
    if not bootstrap:
        pytest.skip("Requires isolated Kafka")
    row, command = create(rules)
    tenant = rules[2]
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE correlation_conditions SET cel='has(assignee_id)' WHERE rule_id=%s",
            (row["id"],),
        )
        conn.execute(
            "UPDATE correlation_rules SET group_by=%s WHERE id=%s",
            (Jsonb(["assignee_id"]), row["id"]),
        )
        # Fixture has synthetic configuration order42, but no alert work. Model
        # the compatible legacy worker's completed configuration-only boundary.
        assert not conn.execute(
            "SELECT 1 FROM correlation_journal WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        conn.execute(
            "UPDATE correlation_consumer_state SET change_order=(SELECT change_order FROM tenant_work_state WHERE tenant_id=%s),pending=false WHERE tenant_id=%s",
            (tenant, tenant),
        )
    assert any(str(value[1]) == row["id"] for value in inspect_rules(real_db))
    env = "cutover-" + uuid4().hex
    config = SimpleNamespace(environment=env, kafka_bootstrap=bootstrap)
    topics = [f"keep.{env}.alerts.{stream}" for stream in ("received", "changed")]
    admin = AdminClient({"bootstrap.servers": bootstrap})
    for future in admin.create_topics([NewTopic(t, 1, 1) for t in topics]).values():
        future.result(15)
    producer = Producer({"bootstrap.servers": bootstrap})
    for topic in topics:
        producer.produce(topic, value=b'{"legacy":true}')
    assert producer.flush(5) == 0
    result = activate(real_db, config)
    assert set(result) == {"source", "correlation", "indexer"}
    with real_db.transaction() as conn:
        recorded = conn.execute(
            "SELECT received_topic_id,changed_topic_id FROM kafka_pipeline_control WHERE singleton"
        ).fetchone()
    for topic, topic_id in zip(topics, recorded.values()):
        assert same_topic(topic_id, topic_identity(admin, topic))
    for role, positions in result.items():
        assert positions[0]["next_offset"] == 1
        consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": f"keep.{env}.{role}-v2"})
        try:
            assert (
                consumer.committed([TopicPartition(positions[0]["topic"], 0)], timeout=5)[0].offset
                == 1
            )
        finally:
            consumer.close()
    with real_db.transaction(tenant) as conn:
        blocked = conn.execute(
            "SELECT kafka_schema_blocked,blocked_reason FROM correlation_rules WHERE id=%s",
            (row["id"],),
        ).fetchone()
        assert blocked == {
            "kafka_schema_blocked": True,
            "blocked_reason": "correlation_field_removed",
        }
    body = {key: command[key] for key in definition()}
    response = edit(rules, row, body)
    assert response.status_code == 200, response.text
    with real_db.transaction(tenant) as conn:
        assert not conn.execute(
            "SELECT kafka_schema_blocked FROM correlation_rules WHERE id=%s", (row["id"],)
        ).fetchone()["kafka_schema_blocked"]
    with pytest.raises(AppError, match="legacy_pipeline_fenced"):
        Admission(real_db, cipher, profile).admit(canonical(v1), "v1")


def test_pending_legacy_input_prevents_cutover(real_db, cipher, profile, v1):
    Admission(real_db, cipher, profile).admit(canonical(v1), "v1")
    with real_db.transaction() as conn:
        with pytest.raises(AppError, match="legacy_pipeline_not_drained"):
            require_drained(conn)


def test_field_name_inside_literal_is_not_a_removed_reference(rules, real_db):
    create(
        rules,
        {
            **definition(),
            "conditions": [
                {"id": str(uuid4()), "label": "Literal", "cel": 'message == "assignee_id"'}
            ],
        },
    )
    assert inspect_rules(real_db) == set()
