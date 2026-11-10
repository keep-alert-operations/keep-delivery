"""Probe the actual local dependencies and managed application workers."""

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/runtime"
RECOVERY = "docs/runbooks/kafka-first.md#failure-and-recovery"


def incarnation_problems(partitions):
    """Return why a broker partition no longer holds the history PostgreSQL recorded.

    Each item carries the broker ``low``/``high`` watermarks, ``required``: the
    highest next offset PostgreSQL observed as committed, and optionally
    ``recorded``: the highest (offset, delivery UUID) source result with
    ``broker_event_id`` read at that offset. A recreated topic restarts its offsets,
    so it either ends below that observation or holds another delivery there.
    """
    problems = []
    for item in partitions:
        name = f"{item['topic']}:{item['partition']}"
        if item["high"] < item["required"]:
            problems.append(
                f"{name} ends at offset {item['high']}, below recorded offset {item['required']}"
            )
            continue
        recorded = item.get("recorded")
        if recorded and recorded[0] >= item["low"] and item.get("broker_event_id") != recorded[1]:
            problems.append(f"{name} offset {recorded[0]} holds a different delivery")
    return problems


def recorded_history(conn, topics):
    """PostgreSQL's highest observed committed offsets and source results per partition."""
    positions = {
        (row["topic"], row["partition"]): row["next_offset"]
        for row in conn.execute(
            "SELECT topic,partition,max(next_offset) AS next_offset FROM kafka_consumer_positions WHERE topic=ANY(%s) GROUP BY topic,partition",
            (topics,),
        )
    }
    recorded = {
        (row["topic"], row["partition"]): (row["input_offset"], str(row["delivery_id"]))
        for row in conn.execute(
            """SELECT DISTINCT ON (topic,partition) topic,partition,input_offset,delivery_id
            FROM kafka_source_results WHERE topic=ANY(%s) ORDER BY topic,partition,input_offset DESC""",
            (topics,),
        )
    }
    return positions, recorded


def broker_event_id(consumer, topic, partition, offset):
    """Read one record's delivery UUID through assignment, never committing offsets."""
    from confluent_kafka import TopicPartition

    consumer.assign([TopicPartition(topic, partition, offset)])
    try:
        for _ in range(10):
            message = consumer.poll(1.0)
            if message is not None and not message.error():
                return json.loads(message.value()).get("event_id") if message.offset() == offset else None
        return None
    finally:
        consumer.unassign()


def require_topic_history(settings, kafka, topics, positions, recorded):
    """Refuse workers on a topic whose broker log restarted under PostgreSQL proof."""
    from uuid import uuid4
    from confluent_kafka import Consumer, TopicPartition

    metadata = kafka.list_topics(timeout=5)
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap,
            "group.id": f"keep-local-probe-{uuid4()}",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
        }
    )
    partitions = []
    try:
        for topic in topics:
            for partition in sorted(metadata.topics[topic].partitions):
                low, high = consumer.get_watermark_offsets(
                    TopicPartition(topic, partition), timeout=5
                )
                key = (topic, partition)
                item = {
                    "topic": topic,
                    "partition": partition,
                    "low": low,
                    "high": high,
                    "required": max(
                        positions.get(key, 0), recorded[key][0] + 1 if key in recorded else 0
                    ),
                }
                if key in recorded:
                    item["recorded"] = recorded[key]
                    if low <= recorded[key][0] < high:
                        item["broker_event_id"] = broker_event_id(
                            consumer, topic, partition, recorded[key][0]
                        )
                partitions.append(item)
    finally:
        consumer.close()
    problems = incarnation_problems(partitions)
    if problems:
        raise RuntimeError(
            "Kafka topic history no longer matches PostgreSQL replay proof ("
            + "; ".join(problems)
            + f"). Workers were not started; follow {RECOVERY}."
        )


def probe():
    from keep.config import Settings
    from keep.db import Database
    from confluent_kafka import KafkaException
    from confluent_kafka.admin import AdminClient, NewTopic
    from elasticsearch import Elasticsearch
    from redis import Redis

    settings = Settings()
    if settings.environment != "development":
        raise RuntimeError("This launcher is development only")
    db = Database(
        settings.database_url.get_secret_value(),
        size=1,
        deployment_epoch=settings.deployment_epoch,
    )
    try:
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT enabled FROM kafka_pipeline_control WHERE singleton"
            ).fetchone()
            cutover = bool(row and row["enabled"])
        with Elasticsearch(
            settings.elasticsearch_url, request_timeout=5, max_retries=0
        ) as client:
            version = client.info()["version"]["number"]
        with Redis.from_url(
            settings.redis_url.get_secret_value(), socket_timeout=3
        ) as redis:
            if not redis.ping():
                raise RuntimeError("Redis did not respond")
        kafka = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
        topics = [f"keep.development.alerts.{name}" for name in ("received", "changed")]
        for _ in range(30):
            try:
                metadata = kafka.list_topics(timeout=3)
                break
            except KafkaException:
                time.sleep(1)
        else:
            raise RuntimeError("Local Kafka did not become ready")
        missing = [
            NewTopic(name, num_partitions=1, replication_factor=1)
            for name in topics
            if name not in metadata.topics
        ]
        if missing and cutover:
            # Recreating a topic restarts its offsets under existing replay proof.
            raise RuntimeError(
                "Kafka application topics are missing after the recorded cutover: "
                + ", ".join(topic.topic for topic in missing)
                + f". Broker storage was lost; follow {RECOVERY}."
            )
        if missing:
            for future in kafka.create_topics(missing).values():
                future.result(timeout=15)
        if cutover:
            with db.transaction() as conn:
                positions, recorded = recorded_history(conn, topics)
            require_topic_history(settings, kafka, topics, positions, recorded)
    finally:
        db.close()
    print(
        f"PostgreSQL, Elasticsearch {version}, Redis and Kafka responded; application topics exist"
        + (" and match recorded offsets." if cutover else ".")
    )


def require_kafka_cutover():
    from keep.config import Settings
    from keep.db import Database
    settings=Settings()
    db=Database(settings.database_url.get_secret_value(),1,deployment_epoch=settings.deployment_epoch)
    try:
        with db.transaction() as conn:
            row=conn.execute("SELECT * FROM kafka_pipeline_control WHERE singleton").fetchone()
            if not row or not row["enabled"]:
                raise RuntimeError("Offline Kafka protocol cutover required; follow docs/runbooks/kafka-first.md before starting workers")
            if not row.get("received_topic_id"):
                # The source refuses until the cutover's topic incarnation is recorded (REV-41).
                raise RuntimeError(
                    "Kafka topic identity is not recorded; with workers stopped, apply migration 0054 and run "
                    f"python -m keep.kafka_cutover --record-topic-identity (see {RECOVERY})"
                )
    finally:
        db.close()


def wait_workers():
    from keep.worker_health import probe as healthy

    records = json.loads((STATE / "workers.json").read_text(encoding="utf-8-sig"))
    for _ in range(60):
        pending = []
        optional_pending = []
        for record in records:
            path = STATE / (record["Role"] + ".health.json")
            if not healthy(path, "ready", 15):
                (optional_pending if record["Role"].endswith("-hints") else pending).append(
                    record["Role"]
                )
                continue
            try:
                same_process = json.loads(path.read_text())["pid"] == record["WorkerId"]
            except (OSError, ValueError, KeyError):
                same_process = False
            if not same_process:
                (optional_pending if record["Role"].endswith("-hints") else pending).append(
                    record["Role"]
                )
        if not pending:
            print(f"All {len(records) - sum(r['Role'].endswith('-hints') for r in records)} required workers completed a healthy turn.")
            if optional_pending:
                print("Optional Kafka hint workers degraded: " + ", ".join(optional_pending))
            return
        time.sleep(1)
    raise RuntimeError("Workers not ready: " + ", ".join(pending))


def migrate_search():
    """Copy the existing local serving projection into the Compose Elasticsearch volume."""
    from keep.config import Settings
    from elasticsearch import Elasticsearch, helpers

    settings = Settings()
    index_alias = settings.elasticsearch_index
    source_url = "http://127.0.0.1:59200"
    target_url = settings.elasticsearch_url
    if not target_url:
        raise RuntimeError("Compose Elasticsearch URL is required")
    with Elasticsearch(source_url, request_timeout=10, max_retries=0) as source, Elasticsearch(
        target_url, request_timeout=10, max_retries=0
    ) as target:
        for _ in range(600):
            if target.ping():
                break
            time.sleep(1)
        else:
            raise RuntimeError("Compose Elasticsearch did not become ready")
        if target.indices.exists_alias(name=index_alias):
            print("Compose Elasticsearch already has the Keep serving alias.")
            return
        if not source.ping():
            if (ROOT / ".local/runtime/keep-local-migration.dump").exists():
                raise RuntimeError("The existing local Elasticsearch projection is unavailable")
            print("No previous local Elasticsearch projection to migrate.")
            return
        aliases = source.indices.get_alias(name=index_alias, allow_no_indices=True)
        if not aliases:
            print("No previous local Elasticsearch projection to migrate.")
            return
        if len(aliases) != 1:
            raise RuntimeError("Expected exactly one local serving index")
        source_index = next(iter(aliases))
        if source_index != index_alias + "-v1":
            raise RuntimeError("The existing local serving index has an unexpected name")
        if target.indices.exists(index=source_index):
            raise RuntimeError("Compose Elasticsearch has a conflicting local index")

        mapping = source.indices.get_mapping(index=source_index)[source_index]["mappings"]
        expected = source.count(index=index_alias)["count"]
        target.indices.create(
            index=source_index,
            mappings=mapping,
            settings={"number_of_shards": 1, "number_of_replicas": 0},
            aliases={index_alias: {}},
        )

        def documents():
            for hit in helpers.scan(
                source,
                index=source_index,
                query={"query": {"match_all": {}}},
                preserve_order=False,
            ):
                action = {
                    "_op_type": "index",
                    "_index": source_index,
                    "_id": hit["_id"],
                    "_source": hit["_source"],
                }
                if "_routing" in hit:
                    action["_routing"] = hit["_routing"]
                yield action

        try:
            helpers.bulk(target, documents(), raise_on_error=True)
            target.indices.refresh(index=index_alias)
            copied = target.count(index=index_alias)["count"]
            if copied != expected:
                raise RuntimeError("The local Elasticsearch document count did not match")
        except Exception:
            target.indices.delete(index=source_index, ignore_unavailable=True)
            raise
    print(f"Migrated {expected} local Elasticsearch documents into the Compose volume.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("probe", "wait-workers", "migrate-search", "require-kafka-cutover"))
    args = parser.parse_args()
    if args.action == "probe":
        probe()
    elif args.action == "require-kafka-cutover":
        require_kafka_cutover()
    elif args.action == "wait-workers":
        wait_workers()
    else:
        migrate_search()
