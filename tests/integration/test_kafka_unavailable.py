"""A real broker-client failure must fit admission's budget without PostgreSQL."""

import socket
from time import monotonic
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from keep.config import Settings
from keep.ingestion.kafka_admission import KafkaPublisher
from keep.ingestion.main import create_app


def test_unavailable_broker_has_three_unconfirmed_attempts_without_database(monkeypatch, v1):
    with socket.socket() as reserved:
        # Reserve a loopback port without listening; no remote service is stopped.
        reserved.bind(("127.0.0.1", 0))
        settings = Settings(
            role="ingestion-api",
            deployment_epoch="test",
            global_tenant_id=uuid4(),
            kafka_bootstrap=f"127.0.0.1:{reserved.getsockname()[1]}",
            kafka_admission_enabled=True,
        )
        calls = []

        class CountedPublisher(KafkaPublisher):
            async def publish(self, topic, key, value):
                calls.append((topic, key, value))
                return await super().publish(topic, key, value)

        monkeypatch.setattr("keep.ingestion.main.KafkaPublisher", CountedPublisher)
        monkeypatch.setattr(
            "keep.main.Database", lambda *a, **k: pytest.fail("Ingress opened PG")
        )
        with TestClient(create_app(settings)) as client:
            started = monotonic()
            response = client.post("/api/ingest/v1/alerts", json=v1)
            assert monotonic() - started < 5
            assert response.status_code == 503
            body = response.json()
            assert body["request_id"] == response.headers["x-request-id"]
            assert body["results"][0]["error"]["code"] == "kafka_publish_unconfirmed"
            assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
