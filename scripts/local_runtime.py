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


def wait_workers():
    from appchi.worker_health import probe as healthy

    records = json.loads((STATE / "workers.json").read_text(encoding="utf-8-sig"))
    for _ in range(60):
        pending = []
        for record in records:
            path = STATE / (record["Role"] + ".health.json")
            if not healthy(path, "ready", 15):
                pending.append(record["Role"])
                continue
            try:
                same_process = json.loads(path.read_text())["pid"] == record["WorkerId"]
            except (OSError, ValueError, KeyError):
                same_process = False
            if not same_process:
                pending.append(record["Role"])
        if not pending:
            print(f"All {len(records)} managed workers completed a healthy turn.")
            return
        time.sleep(1)
    raise RuntimeError("Workers not ready: " + ", ".join(pending))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("probe", "wait-workers"))
    args = parser.parse_args()
    probe() if args.action == "probe" else wait_workers()
