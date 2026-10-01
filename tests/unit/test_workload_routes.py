import base64

from fastapi.testclient import TestClient
import pytest

from appchi.config import Settings
from appchi.main import create_app
from appchi.ingestion.main import create_app as create_ingestion_app


class UnusedDatabase:
    def transaction(self, *args, **kwargs):
        raise AssertionError("Blocked workload routes cannot reach the database")


@pytest.mark.parametrize(
    "role,paths",
    [
        (
            "product-api",
            ["/api/ingest/v1/alerts", "/api/ingest/v2/alerts", "/api/ingest/v2/grafana"],
        ),
        (
            "ingestion-api",
            ["/auth/login", "/api/tenants", "/api/admin/health", "/api/ingest/v2/alerts"],
        ),
        ("private-ingestion", ["/auth/login", "/api/tenants", "/api/ingest/v1/alerts"]),
    ],
)
def test_deployed_http_role_rejects_other_surfaces_before_body_or_database(role, paths, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    monkeypatch.setattr("appchi.ingestion.main.KafkaPublisher", lambda settings: SimpleNamespace(close=lambda: None))
    settings = Settings(
        database_url="unused",
        wrapping_key=base64.b64encode(b"k" * 32).decode(),
        deployment_epoch="test",
        environment="staging", kafka_bootstrap="unused:9092", kafka_security_protocol="SSL", global_tenant_id=uuid4(),
        role=role,
    )
    app = create_app(settings, UnusedDatabase()) if role == "product-api" else create_ingestion_app(settings)
    with TestClient(app) as client:
        assert client.get("/livez").status_code == 200
        for path in paths:
            response = client.post(path, content=b"malformed body")
            assert response.status_code == 404, response.text
            assert response.json()["error"]["code"] == "not_found"


def test_worker_role_cannot_start_http_application():
    settings = Settings(
        database_url="unused",
        wrapping_key=base64.b64encode(b"k" * 32).decode(),
        deployment_epoch="test",
        role="source",
    )
    with pytest.raises(RuntimeError, match="does not expose"):
        with TestClient(create_app(settings, UnusedDatabase())):
            pytest.fail("A worker is not an HTTP host")
