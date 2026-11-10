"""Historical replay is prepared without holding the tenant writer lock."""

# ruff: noqa: F401, F811
import json
from uuid import UUID, uuid4

import pytest

from keep.maintenance import preparation, processing
from keep.shared.errors import AppError
from test_correlation_configuration import rules
# Host-clock receipts may lead the database clock; wait it out like the consumer.
from test_kafka_first import apply_waiting, receipt
from test_kafka_maintenance_inline import CAPACITY, setup
from test_product import browser
from test_search_pipeline import real_db


pytestmark = pytest.mark.integration
PROFILE = {**CAPACITY, "maintenance_max_inputs": 3}


def receive(db, config, payload, offset, *, at=None):
    topic, key, value = receipt(config, payload, at=at)
    return apply_waiting(
        db,
        topic=topic,
        key=key,
        value=value,
        partition=0,
        offset=offset,
        settings=config,
        profile=PROFILE,
    )


def test_late_source_and_catalog_change_prepare_outside_writer_lock(
    setup, real_db, v1, monkeypatch
):
    _, client, tenant, config = setup
    first = receive(real_db, config, v1, 0)
    alert = UUID(json.loads(first)["alert_id"])
    for offset in range(1, 12):
        receive(real_db, config, v1, offset)
    original = preparation.reduce_alert
    preparations = []

    def unlocked(*args, **kwargs):
        # A second connection can take this lock only when the preparer owns no
        # tenant writer lock. A regression fails immediately instead of hanging.
        with real_db.transaction(tenant) as conn:
            conn.execute(
                "SELECT tenant_id FROM tenant_work_state WHERE tenant_id=%s FOR UPDATE NOWAIT",
                (tenant,),
            )
        preparations.append(True)
        return original(*args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("full historical reducer ran inside source/command transaction")

    monkeypatch.setattr(preparation, "reduce_alert", unlocked)
    monkeypatch.setattr(processing, "reduce_alert", forbidden)
    with real_db.transaction(tenant) as conn:
        first_at = conn.execute(
            "SELECT min(effective_at) AS at FROM maintenance_alert_inputs WHERE tenant_id=%s",
            (tenant,),
        ).fetchone()["at"]
    receive(real_db, config, v1, 12, at=first_at)
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE tenant_view_state SET maintenance_generation=maintenance_generation+1 WHERE tenant_id=%s",
            (tenant,),
        )
    receive(real_db, config, v1, 13)
    # A manual mutation uses a prepared existing baseline followed by one actual
    # input at the command's SQL timestamp/order, not a predicted user command.
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE tenant_view_state SET maintenance_generation=maintenance_generation+1 WHERE tenant_id=%s",
            (tenant,),
        )
        revision = conn.execute(
            "SELECT revision FROM alerts WHERE tenant_id=%s AND id=%s", (tenant, alert)
        ).fetchone()["revision"]
    client.app.state.profile = PROFILE
    response = client.post(
        f"/api/tenants/{tenant}/alerts/{alert}/commands",
        json={
            "command_id": str(uuid4()),
            "expected_revision": str(revision),
            "kind": "set_status",
            "value": "resolved",
        },
    )
    assert response.status_code == 200, response.text
    assert len(preparations) == 3


def test_unrelated_tenant_order_is_rebound_without_replaying(setup, real_db, v1, monkeypatch):
    from keep.maintenance import runtime

    _, _, tenant, config = setup
    receive(real_db, config, v1, 0)
    with real_db.transaction(tenant) as conn:
        at = conn.execute(
            "SELECT min(effective_at) AS at FROM maintenance_alert_inputs WHERE tenant_id=%s",
            (tenant,),
        ).fetchone()["at"]
    original = runtime.prepare_for_tenant

    def raced(*args, **kwargs):
        catalog = original(*args, **kwargs)
        with real_db.transaction(tenant) as conn:
            conn.execute(
                "UPDATE tenant_work_state SET admission_order=admission_order+1 WHERE tenant_id=%s",
                (tenant,),
            )
        return catalog

    monkeypatch.setattr(runtime, "prepare_for_tenant", raced)
    receive(real_db, config, v1, 1, at=at)
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM kafka_source_results WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 2
        )


def test_same_alert_input_head_conflict_retries_without_losing_source(
    setup, real_db, v1, monkeypatch
):
    from keep.maintenance import runtime

    _, _, tenant, config = setup
    receive(real_db, config, v1, 0)
    with real_db.transaction(tenant) as conn:
        at = conn.execute(
            "SELECT min(effective_at) AS at FROM maintenance_alert_inputs WHERE tenant_id=%s",
            (tenant,),
        ).fetchone()["at"]
    original = runtime.prepare_for_tenant
    raced_once = False

    def raced(*args, **kwargs):
        nonlocal raced_once
        catalog = original(*args, **kwargs)
        if not raced_once:
            raced_once = True
            receive(real_db, config, v1, 50)
        return catalog

    monkeypatch.setattr(runtime, "prepare_for_tenant", raced)
    with pytest.raises(AppError) as failure:
        receive(real_db, config, v1, 1, at=at)
    assert failure.value.code == "maintenance_preparation_changed"
    monkeypatch.setattr(runtime, "prepare_for_tenant", original)
    receive(real_db, config, v1, 1, at=at)
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM kafka_source_results WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 3
        )
