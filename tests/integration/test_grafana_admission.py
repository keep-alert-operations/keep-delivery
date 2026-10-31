"""Real admission/provenance against SQL-managed operators; remote setup uses fixture facts."""

# ruff: noqa: F811
import json
from uuid import uuid4
import pytest
from appchi.ingestion.service import Admission
from appchi.ingestion.gateway import GatewayIdentity
from appchi.alerts.processor import process_one
from appchi.operators.grafana import ADAPTER_VERSION
from appchi.shared.errors import AppError
from test_operator_repository import operators, ready, create  # noqa: F401
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


def gateway(db, op):
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT kong_consumer_id,kong_credential_id FROM operator_credentials WHERE operator_id=%s AND state='current'",
            (op,),
        ).fetchone()
    return GatewayIdentity(row["kong_consumer_id"], row["kong_credential_id"])


def webhook(name, label_value="true"):
    return {
        "version": "1",
        "status": "firing",
        "truncatedAlerts": 0,
        "alerts": [
            {
                "status": "firing",
                "startsAt": "2026-09-22T12:00:00Z",
                "labels": {
                    "Appchi - " + name: label_value,
                    "alertname": "Grafana rule",
                    "application": "test-" + str(uuid4()),
                    "component": "cpu",
                    "site": "lab",
                    "severity": "critical",
                },
                "annotations": {
                    "message": "Grafana source",
                    "impact": "test",
                    "runbook_url": "https://example.invalid/runbook",
                },
            }
        ],
    }


def test_grafana_provenance_partial_batch_and_idempotent_delivery(
    operators, real_db, cipher, profile, monkeypatch
):
    op = ready(operators, real_db, cipher)
    tenant = operators[2]
    identity = gateway(real_db, op["id"])
    payload = webhook(op["name"])
    payload["alerts"].append(dict(payload["alerts"][0], status="bad"))
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    service = Admission(real_db, cipher, profile)
    status, response = service.admit_grafana(raw, "grafana-retry", identity)
    assert status == 202
    assert response["items"][0]["disposition"] == "accepted"
    assert response["items"][1]["disposition"] == "rejected"
    assert service.admit_grafana(raw, "grafana-retry", identity) == (status, response)
    assert process_one(real_db, tenant)
    receipt = service.receipt(response["request_id"], response["receipt_token"], "v2", identity)
    alert = receipt["items"][0]["alert_id"]
    with real_db.transaction() as conn:
        row = conn.execute(
            "SELECT raw_json,payload,adapter_version FROM alert_source_events WHERE tenant_id=%s AND alert_id=%s",
            (tenant, alert),
        ).fetchone()
        assert json.loads(bytes(row["raw_json"])) == payload["alerts"][0]
        assert row["adapter_version"] == ADAPTER_VERSION
        assert row["payload"]["message"] == "Grafana source"
        # REV-54: the rule name is the alert name and part of its identity.
        assert row["payload"]["name"] == "Grafana rule"
        assert (
            conn.execute("SELECT operator FROM alerts WHERE id=%s", (alert,)).fetchone()["operator"]
            == op["id"]
        )
    history = operators[0].get(f"/api/tenants/{tenant}/alerts/{alert}/source-events")
    assert history.status_code == 200, history.text
    assert history.json()["data"][0]["adapter_version"] == ADAPTER_VERSION
    assert "raw_json" not in history.json()["data"][0]
    changed = json.dumps(dict(payload, status="resolved")).encode()
    with pytest.raises(AppError) as failure:
        service.admit_grafana(changed, "grafana-retry", identity)
    assert failure.value.status == 409


def test_grafana_unverified_transport_and_wrong_literal_label_rejected(
    operators, real_db, cipher, profile, monkeypatch
):
    op = ready(operators, real_db, cipher)
    raw = json.dumps(webhook(op["name"], "True")).encode()
    identity = gateway(real_db, op["id"])
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from appchi.config import Settings
    from appchi.ingestion.main import create_app
    monkeypatch.setattr("appchi.ingestion.main.KafkaPublisher", lambda *_: SimpleNamespace(close=lambda: None))
    settings = Settings(role="private-ingestion", deployment_epoch="test",
                        kafka_bootstrap="unused:9092", global_tenant_id=op["tenant_id"])
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/ingest/v2/grafana",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Consumer-ID": identity.consumer_id,
                "X-Credential-Identifier": identity.credential_id,
            },
        )
    assert response.status_code == 401
    status, result = Admission(real_db, cipher, profile).admit_grafana(raw, None, identity)
    assert all(item["disposition"] == "rejected" for item in result["items"])
    assert status == 422
