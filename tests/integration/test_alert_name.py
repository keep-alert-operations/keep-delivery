"""REV-54 alert names through Kafka-first admission and source application on PostgreSQL."""

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from appchi.alerts.presentation import present_alert
from appchi.ingestion.kafka_admission import KafkaAdmission
from appchi.ingestion.native import canonical

from test_kafka_first import Capture, apply_waiting, settings

pytestmark = pytest.mark.integration


def v2(**extra):
    return {
        "application": "test-" + uuid4().hex,
        "component": "api",
        "message": "Checkout failing",
        "severity": "critical",
        "status": "firing",
        "site": "lab",
        "impact": "Orders fail",
        "runbook_url": "https://example.invalid/runbook",
        **extra,
    }


class Source:
    def __init__(self, db, tenant):
        self.db, self.tenant, self.operator = db, tenant, uuid4()
        self.config = settings(db)
        self.offset = 0
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO operators(id,tenant_id,name,name_key,lifecycle) VALUES(%s,%s,'Payments',%s,'ready')",
                (self.operator, tenant, str(self.operator)),
            )
        self.gateway = SimpleNamespace(
            verified=True,
            tenant_id=str(tenant),
            operator_id=str(self.operator),
            credential_id=str(uuid4()),
            policy_generation="1",
            routing_names=("Payments",),
            authorized_at=None,
        )

    def admit(self, raw, grafana=False):
        publisher = Capture()
        admission = KafkaAdmission(
            publisher,
            environment=self.config.environment,
            global_tenant_id=self.config.global_tenant_id,
        )
        if grafana:
            status, body = asyncio.run(admission.admit_grafana(raw, None, self.gateway))
        else:
            status, body = asyncio.run(admission.admit(raw, "v2", gateway=self.gateway))
        return status, body, publisher.records

    def apply(self, payload):
        status, body, records = self.admit(canonical(payload))
        assert status == 202, body
        return self.apply_record(records[0])

    def apply_record(self, record):
        topic, key, value = record
        self.offset += 1
        changed = apply_waiting(
            self.db,
            topic=topic,
            key=key,
            value=value,
            partition=0,
            offset=self.offset,
            settings=self.config,
            profile=None,
        )
        return UUID(json.loads(changed)["alert_id"]) if changed else None

    def row(self, alert):
        with self.db.transaction(self.tenant) as conn:
            return conn.execute(
                "SELECT * FROM alerts WHERE tenant_id=%s AND id=%s", (self.tenant, alert)
            ).fetchone()

    def inputs(self, alert):
        with self.db.transaction(self.tenant) as conn:
            return conn.execute(
                "SELECT after_snapshot FROM maintenance_alert_inputs WHERE tenant_id=%s AND alert_id=%s ORDER BY original_order",
                (self.tenant, alert),
            ).fetchall()


def test_unnamed_alerts_keep_their_key_and_are_named_by_it(real_db, tenant):
    """Requirements: alert operations 3, 5 (REV-54)."""
    source = Source(real_db, tenant)
    payload = v2()
    alert = source.apply(payload)
    row = source.row(alert)
    assert row["name"] is None
    assert len(row["key_field"]) == 64
    assert present_alert(row)["name"] == row["key_field"]
    assert source.apply(dict(payload, message="Still failing")) == alert
    # Proof records no name for an unnamed alert, exactly as before REV-54.
    assert all("name" not in item["after_snapshot"] for item in source.inputs(alert))


def test_a_name_is_part_of_the_v2_identity(real_db, tenant):
    """Requirements: alert operations 5, 7 (REV-54)."""
    source = Source(real_db, tenant)
    payload = v2()
    unnamed = source.apply(payload)
    named = source.apply(dict(payload, name="Checkout errors"))
    assert named != unnamed
    assert source.row(named)["name"] == "Checkout errors"
    assert source.row(named)["key_field"] != source.row(unnamed)["key_field"]
    again = source.apply(
        dict(payload, name="Checkout errors", message="Recovered", status="resolved")
    )
    assert again == named and source.row(named)["status"] == "resolved"
    assert source.row(named)["name"] == "Checkout errors"
    renamed = source.apply(dict(payload, name="Checkout outage"))
    assert renamed not in {named, unnamed}
    assert source.row(unnamed)["status"] == "firing" and source.row(unnamed)["name"] is None
    assert [item["after_snapshot"]["name"] for item in source.inputs(named)] == [
        "Checkout errors",
        "Checkout errors",
    ]


def test_grafana_rule_name_is_the_alert_name(real_db, tenant):
    """Requirements: operator operations 18; alert operations 5 (REV-54)."""
    source = Source(real_db, tenant)
    member = {
        "status": "firing",
        "startsAt": "2026-10-31T12:00:00Z",
        "labels": {
            "Appchi - Payments": "true",
            "alertname": "High CPU",
            "application": "test-" + uuid4().hex,
            "component": "cpu",
            "severity": "critical",
            "site": "lab",
        },
        "annotations": {
            "message": "CPU above 90%",
            "impact": "Slow checkout",
            "runbook_url": "https://example.invalid/runbook",
        },
    }
    unnamed = json.loads(json.dumps(member))
    del unnamed["labels"]["alertname"]
    raw = json.dumps(
        {"version": "1", "status": "firing", "truncatedAlerts": 0, "alerts": [member, unnamed]}
    ).encode()
    status, body, records = source.admit(raw, grafana=True)
    assert status == 207, body
    assert [item["disposition"] for item in body["results"]] == ["accepted", "rejected"]
    assert body["results"][1]["error"]["fields"] == [{"path": "labels.alertname"}]
    alert = source.apply_record(records[0])
    row = source.row(alert)
    assert row["name"] == "High CPU" and row["message"] == "CPU above 90%"
