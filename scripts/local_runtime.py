"""Probe the actual local dependencies and managed application workers."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/runtime"
sys.path.insert(0, str(ROOT / "backend"))


def probe():
    from appchi.config import Settings
    from appchi.db import Database
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
            conn.execute("SELECT 1")
    finally:
        db.close()
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
    topics = [f"appchi.development.alerts.{name}" for name in ("received", "changed")]
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
    if missing:
        for future in kafka.create_topics(missing).values():
            future.result(timeout=15)
    print(
        f"PostgreSQL, Elasticsearch {version}, Redis and Kafka responded; application topics exist."
    )


def require_kafka_cutover():
    from appchi.config import Settings
    from appchi.db import Database
    settings=Settings()
    db=Database(settings.database_url.get_secret_value(),1,deployment_epoch=settings.deployment_epoch)
    try:
        with db.transaction() as conn:
            row=conn.execute("SELECT enabled FROM kafka_pipeline_control WHERE singleton").fetchone()
            if not row or not row["enabled"]:
                raise RuntimeError("Offline Kafka protocol cutover required; follow docs/runbooks/kafka-first.md before starting workers")
    finally:
        db.close()


def wait_workers():
    from appchi.worker_health import probe as healthy

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
    from appchi.config import Settings
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
            print("Compose Elasticsearch already has the Appchi serving alias.")
            return
        if not source.ping():
            if (ROOT / ".local/runtime/appchi-local-migration.dump").exists():
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
