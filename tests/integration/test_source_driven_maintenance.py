# ruff: noqa: F811, F401
"""Current release: no clock-driven unsuppression or synthetic correlation."""
from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401
from test_search_pipeline import real_db  # noqa: F401
from test_correlation_configuration import rules  # noqa: F401
from test_kafka_maintenance_inline import (setup, rule, window, receive, consume,
                                          expire, mutate, incidents)  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("end_kind", ["expiry", "disable"])
def test_suppression_survives_read_note_and_correlation_until_source(setup, real_db, v1, end_kind):
    _, client, tenant, config = setup
    rule(setup)
    window(setup)
    alert = consume(real_db, config, receive(real_db, config, v1))
    base = f"/api/tenants/{tenant}"
    with real_db.transaction(tenant) as conn:
        saved = conn.execute("SELECT id,revision FROM maintenance_windows WHERE tenant_id=%s", (tenant,)).fetchone()
        assert not conn.execute("SELECT 1 FROM maintenance_fanout_jobs WHERE tenant_id=%s AND state='pending'", (tenant,)).fetchone()
    if end_kind == "expiry":
        expire(real_db, tenant)
    else:
        response = client.post(base + f"/maintenance/{saved['id']}/disable", json={
            "command_id": str(uuid4()), "expected_revision": str(saved["revision"])})
        assert response.status_code == 200, response.text
    detail = client.get(base + f"/alerts/{alert}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["suppressed"] is True
    mutate(real_db, setup, alert)
    assert incidents(real_db, tenant) == 0
    assert client.get(base + f"/alerts/{alert}").json()["data"]["suppressed"] is True
    causes = client.get(base + f"/maintenance/{saved['id']}").json()["data"]
    assert causes["current_causes_count"] == "1"
    changed = receive(real_db, config, v1, 1)
    assert client.get(base + f"/alerts/{alert}").json()["data"]["suppressed"] is False
    consume(real_db, config, changed, 1)
    assert incidents(real_db, tenant) == 1
    with real_db.transaction(tenant) as conn:
        closed = conn.execute("SELECT closed_at FROM alert_maintenance_causes WHERE tenant_id=%s AND alert_id=%s", (tenant, alert)).fetchone()
        latest = conn.execute("SELECT max(received_at) AS at FROM alert_source_events WHERE tenant_id=%s AND alert_id=%s", (tenant, alert)).fetchone()
        assert closed["closed_at"] == latest["at"]
