import asyncio
from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from keep.ingestion.kafka_admission import KafkaAdmission
from keep.ingestion.keys import received_key
from keep.ingestion.native import canonical, parse_json


class Publisher:
    def __init__(self, failures=None):
        self.failures = failures or {}
        self.records = []
        self.counts = Counter()
        self.active = 0
        self.maximum = 0

    async def publish(self, topic, key, value):
        payload = parse_json(value)["payload"]
        name = payload["application"]
        self.records.append((topic, key, value))
        self.counts[name] += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        await asyncio.sleep(0.001)
        self.active -= 1
        if self.counts[name] <= self.failures.get(name, 0):
            raise RuntimeError("uncertain acknowledgement")


def client(publisher):
    return KafkaAdmission(publisher, environment="development", global_tenant_id=uuid4())


@pytest.mark.asyncio
async def test_concurrent_partial_validation_and_publication(v1):
    publisher = Publisher({"failed": 3})
    admission = client(publisher)
    raw = canonical(
        {
            "alerts": [
                {"payload": dict(v1, application="good")},
                {"payload": dict(v1, application="failed")},
                {"payload": {"bad": "field"}},
            ]
        }
    )
    status, result = await admission.admit(raw, "v1")
    assert status == 207
    assert [r["status"] for r in result["results"]] == [202, 503, 422]
    assert publisher.counts == {"good": 1, "failed": 3}
    assert publisher.maximum == 2
    assert "receipt_token" not in result and "receipt_url" not in result


@pytest.mark.asyncio
async def test_retries_preserve_bytes_time_uuid_and_partition_key(v1):
    publisher = Publisher({v1["application"]: 2})
    admission = client(publisher)
    at = datetime(2026, 9, 30, 1, 2, 3, 456789, tzinfo=UTC)
    status, _ = await admission.admit(canonical(v1), "v1", received_at=at)
    assert status == 202 and len(publisher.records) == 3
    assert publisher.records[0] == publisher.records[1] == publisher.records[2]
    body = parse_json(publisher.records[0][2])
    assert body["received_at"] == "2026-09-30T01:02:03.456789Z"
    assert publisher.records[0][1] == received_key(
        admission.global_tenant_id, v1["application"], body["key_field"]
    )


@pytest.mark.asyncio
async def test_all_publish_failures_are_503(v1):
    publisher = Publisher({v1["application"]: 99})
    status, result = await client(publisher).admit(canonical(v1), "v1")
    assert status == 503 and result["results"][0]["status"] == 503
    assert len(publisher.records) == 3


@pytest.mark.asyncio
async def test_sender_retry_metadata_does_not_deduplicate(v1):
    publisher = Publisher()
    admission = client(publisher)
    raw = canonical({"alerts": [{"event_id": "same", "payload": v1}]})
    await admission.admit(raw, "v1", "same")
    await admission.admit(raw, "v1", "same")
    first, second = [parse_json(record[2]) for record in publisher.records]
    assert first["event_id"] != second["event_id"]
    assert first["received_at"] != second["received_at"]


@pytest.mark.asyncio
async def test_no_ack_before_broker_confirmation(v1):
    entered, ack = asyncio.Event(), asyncio.Event()

    class BlockedPublisher:
        async def publish(self, *args):
            entered.set()
            await ack.wait()

    task = asyncio.create_task(client(BlockedPublisher()).admit(canonical(v1), "v1"))
    await entered.wait()
    assert not task.done()
    ack.set()
    assert (await task)[0] == 202


@pytest.mark.asyncio
async def test_v2_defaults_once_from_ingress_without_database():
    publisher = Publisher()
    gateway = SimpleNamespace(
        verified=True,
        tenant_id=str(uuid4()),
        operator_id=str(uuid4()),
        credential_id=str(uuid4()),
        policy_generation="2",
    )
    payload = {
        "application": "app",
        "component": "cpu",
        "severity": "high",
        "status": "firing",
        "site": "dc",
        "message": "x",
        "impact": "x",
        "runbook_url": "https://example.test/x",
    }
    at = datetime(2026, 9, 30, 1, 2, 3, 123456, tzinfo=UTC)
    status, _ = await client(publisher).admit(
        canonical(payload), "v2", gateway=gateway, received_at=at
    )
    assert status == 202
    body = parse_json(publisher.records[0][2])
    assert body["payload"]["time_created"] == "30-09-2026 01:02:03"
    assert body["time_defaulted"] is True and body["tenant_id"] == gateway.tenant_id


def test_http_ingress_has_no_postgresql_or_secret_dependency(monkeypatch, v1):
    from fastapi.testclient import TestClient
    from keep.config import Settings
    from keep.ingestion.main import create_app

    publisher = Publisher()
    publisher.close = lambda: None
    monkeypatch.setattr("keep.ingestion.main.KafkaPublisher", lambda settings: publisher)
    monkeypatch.setattr(
        "keep.main.Database", lambda *args, **kwargs: pytest.fail("Ingress opened PostgreSQL")
    )
    settings = Settings(
        role="ingestion-api",
        deployment_epoch="test",
        global_tenant_id=uuid4(),
        kafka_bootstrap="unavailable:9092",
        kafka_admission_enabled=True,
    )
    with TestClient(create_app(settings)) as http:
        assert http.app.state.db is None
        response = http.post("/api/ingest/v1/alerts", json=v1)
        assert response.status_code == 202
        assert response.json()["results"][0]["status"] == 202
        assert http.get("/api/ingest/v1/receipts/" + str(uuid4())).status_code == 404


@pytest.mark.asyncio
async def test_expired_gateway_lifetime_cannot_publish(v1):
    import time
    from keep.shared.errors import AppError
    from keep.ingestion.gateway import gateway_identity
    from starlette.requests import Request
    import base64

    headers = {
        "x-consumer-id": str(uuid4()),
        "x-credential-identifier": str(uuid4()),
        "x-keep-operator-id": str(uuid4()),
        "x-keep-tenant-id": str(uuid4()),
        "x-keep-policy-generation": "1",
        "x-keep-routing-names": base64.b64encode(b'["Operator"]').decode(),
        "x-keep-authorized-at": str(time.time() - 11),
    }
    with pytest.raises(AppError, match="invalid_credential"):
        gateway_identity(
            Request(
                {
                    "type": "http",
                    "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
                    "keep.gateway_peer_verified": True,
                }
            )
        )
