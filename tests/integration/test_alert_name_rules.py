# ruff: noqa: F811, F401
"""REV-54 alert names in maintenance windows and correlation rules on PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from time import sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest

from keep.ingestion.kafka_admission import KafkaAdmission
from keep.ingestion.native import canonical, identity

from test_correlation_configuration import definition, rules  # noqa: F401
from test_kafka_first import Capture
from test_kafka_maintenance_inline import consume, incidents, receive, setup
from test_product import browser  # noqa: F401
from test_search_pipeline import real_db  # noqa: F401

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


def created(client, path, body):
    """Each new expression compiles in a fresh native process with a one-second
    deadline; on a loaded machine a cold start can miss it, which the API reports
    as a retryable 503. Retry that response as a client would."""
    for _ in range(6):
        response = client.post(path, json={"command_id": str(uuid4()), **body})
        if response.status_code != 503:
            break
        assert response.json()["error"]["code"] == "cel_compilation_unavailable"
        sleep(1)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def window(client, tenant, expression):
    now = datetime.now(UTC)
    return created(
        client,
        f"/api/tenants/{tenant}/maintenance",
        {
            "name": "Named alerts",
            "reason": "",
            "cel": expression,
            "timezone": "UTC",
            "start_local": (now - timedelta(minutes=1))
            .replace(tzinfo=None)
            .isoformat("T", "seconds"),
            "end_local": (now + timedelta(hours=1)).replace(tzinfo=None).isoformat("T", "seconds"),
        },
    )


def operator(db, tenant):
    value = uuid4()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO operators(id,tenant_id,name,name_key,lifecycle) VALUES(%s,%s,'Payments',%s,'ready')",
            (value, tenant, str(value)),
        )
    return value


def named_receipt(config, tenant, op, payload):
    publisher = Capture()
    gateway = SimpleNamespace(
        verified=True,
        tenant_id=str(tenant),
        operator_id=str(op),
        credential_id=str(uuid4()),
        policy_generation="1",
        routing_names=("Payments",),
        authorized_at=None,
    )
    admission = KafkaAdmission(
        publisher, environment=config.environment, global_tenant_id=config.global_tenant_id
    )
    status, body = asyncio.run(admission.admit(canonical(payload), "v2", gateway=gateway))
    assert status == 202, body
    return publisher.records[-1]


def test_maintenance_window_matches_alert_names(setup, real_db):
    """Requirements: alert operations 3; maintenance 6 (REV-54)."""
    _, client, tenant, config = setup
    op = operator(real_db, tenant)
    unnamed_payload = v2()
    key = identity(unnamed_payload, "v2", op)[0]
    saved = window(client, tenant, f'name == "Checkout errors" || name == "{key}"')
    other = v2()
    alerts = {}
    for offset, (label, payload) in enumerate(
        [("named", v2(name="Checkout errors")), ("unnamed", unnamed_payload), ("other", other)]
    ):
        changed = receive(real_db, config, None, offset, named_receipt(config, tenant, op, payload))
        alerts[label] = consume(real_db, config, changed, offset)
    base = f"/api/tenants/{tenant}"
    suppressed = {
        label: client.get(f"{base}/alerts/{alert}").json()["data"]["suppressed"]
        for label, alert in alerts.items()
    }
    assert suppressed == {"named": True, "unnamed": True, "other": False}
    listed = client.get(f"{base}/maintenance/{saved['id']}/alerts")
    assert listed.status_code == 200, listed.text
    names = {row["alert_id"]: row["name"] for row in listed.json()["data"]}
    assert names == {str(alerts["named"]): "Checkout errors", str(alerts["unnamed"]): key}


def test_correlation_rule_matches_alert_names(setup, real_db):
    """Requirements: alert operations 3; correlation 3 (REV-54)."""
    rules_, client, tenant, config = setup
    op = operator(real_db, tenant)
    body = definition()
    body["conditions"] = [
        {"id": str(uuid4()), "label": "Match", "cel": 'name == "Checkout errors"'}
    ]
    created(client, rules_[3], body)
    changed = receive(real_db, config, None, 0, named_receipt(config, tenant, op, v2()))
    consume(real_db, config, changed, 0)
    assert incidents(real_db, tenant) == 0
    named = v2(name="Checkout errors")
    changed = receive(real_db, config, None, 1, named_receipt(config, tenant, op, named))
    consume(real_db, config, changed, 1)
    assert incidents(real_db, tenant) == 1
