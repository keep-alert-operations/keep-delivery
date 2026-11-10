"""Retirement keeps replay fences while releasing payloads in bounded chunks."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import UUID

import pytest

from keep.recovery.alert_cleanup import cleanup_alert, dependencies_released
from keep.recovery.retirement import retire_alert
from keep.shared.errors import AppError
from test_kafka_first import apply, receipt, settings
from test_recovery_gateway import IndependentStore

pytestmark = pytest.mark.integration


@pytest.fixture
def kafka_retirement(real_db, v1):
    config = settings(real_db)
    tenant = config.global_tenant_id
    with real_db.transaction() as conn:
        mode = conn.execute("SELECT enabled FROM kafka_pipeline_control WHERE singleton").fetchone()
        gate = conn.execute(
            "SELECT ready,reason FROM capability_gates WHERE capability='recovery'"
        ).fetchone()
        view = conn.execute(
            "SELECT searchable_seq FROM tenant_view_state WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        conn.execute("UPDATE kafka_pipeline_control SET enabled=true WHERE singleton")
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
    try:
        original = receipt(config, v1, at=datetime.now(UTC) - timedelta(seconds=2))
        changed = json.loads(apply(real_db, config, original))
        alert = UUID(changed["alert_id"])
        with real_db.transaction(tenant) as conn:
            conn.execute(
                """UPDATE alerts SET status='resolved',resolved_at=clock_timestamp()-interval '91 days',
              retention_at=clock_timestamp()-interval '1 day' WHERE tenant_id=%s AND id=%s""",
                (tenant, alert),
            )
        yield config, tenant, alert, original, changed
    finally:
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE kafka_pipeline_control SET enabled=%s WHERE singleton", (mode["enabled"],)
            )
            conn.execute(
                "UPDATE capability_gates SET ready=%s,reason=%s WHERE capability='recovery'",
                (gate["ready"], gate["reason"]),
            )
            conn.execute(
                "UPDATE tenant_view_state SET searchable_seq=%s WHERE tenant_id=%s",
                (view["searchable_seq"], tenant),
            )


def protect_retirement(db, tenant, alert):
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE kafka_source_results SET kafka_confirmed=true WHERE tenant_id=%s AND alert_id=%s",
            (tenant, alert),
        )
    assert retire_alert(db, IndependentStore(), tenant, alert)
    with db.transaction(tenant) as conn:
        identity = conn.execute(
            "SELECT * FROM alert_identities WHERE tenant_id=%s AND id=%s", (tenant, alert)
        ).fetchone()
        conn.execute(
            "UPDATE tenant_view_state SET searchable_seq=%s WHERE tenant_id=%s",
            (identity["current_projection_seq"], tenant),
        )
    return identity


def correlation_complete(db, tenant, changed):
    with db.transaction(tenant) as conn:
        conn.execute(
            """INSERT INTO kafka_correlation_events(event_id,tenant_id,topic,partition,input_offset)
          VALUES(%s,%s,'fixture.changed',0,0)""",
            (changed["event_id"], tenant),
        )


def test_unconfirmed_publication_and_correlation_proof_hold_payload_cleanup(
    real_db, kafka_retirement
):
    _, tenant, alert, _, changed = kafka_retirement
    store = IndependentStore()
    assert not retire_alert(real_db, store, tenant, alert)
    assert not store.records
    identity = protect_retirement(real_db, tenant, alert)
    assert not cleanup_alert(real_db, store, tenant, alert)
    with real_db.transaction(tenant) as conn:
        assert conn.execute(
            "SELECT changed_envelope FROM kafka_source_results WHERE alert_id=%s", (alert,)
        ).fetchone()["changed_envelope"]
    correlation_complete(real_db, tenant, changed)
    with real_db.transaction(tenant) as conn:
        assert dependencies_released(conn, tenant, alert, identity["final_version"])


@pytest.mark.parametrize("missing", ["tombstone", "projection_sequence", "retired_key"])
def test_missing_replay_fence_never_permits_erasure(real_db, kafka_retirement, missing):
    _, tenant, alert, _, changed = kafka_retirement
    identity = protect_retirement(real_db, tenant, alert)
    correlation_complete(real_db, tenant, changed)
    # Roll back the controlled corruption after checking the guard.
    with pytest.raises(AppError) as error, real_db.transaction(tenant) as conn:
        if missing == "tombstone":
            conn.execute(
                "DELETE FROM projection_work WHERE tenant_id=%s AND alert_id=%s AND version=%s",
                (tenant, alert, identity["final_version"]),
            )
        elif missing == "projection_sequence":
            conn.execute(
                "UPDATE alert_identities SET current_projection_seq=0 WHERE tenant_id=%s AND id=%s",
                (tenant, alert),
            )
        else:
            key = conn.execute(
                "SELECT key_field FROM alerts WHERE tenant_id=%s AND id=%s", (tenant, alert)
            ).fetchone()["key_field"]
            conn.execute(
                "DELETE FROM alert_retired_keys WHERE tenant_id=%s AND key_digest=%s",
                (tenant, sha256(key.encode()).digest()),
            )
        dependencies_released(conn, tenant, alert, identity["final_version"])
    assert error.value.code == (
        "retirement_replay_fence_missing"
        if missing == "retired_key"
        else "retirement_projection_missing"
    )


def test_cleanup_retains_delivery_and_key_fences_and_later_admission_gets_new_uuid(
    real_db, kafka_retirement, v1
):
    config, tenant, alert, original, changed = kafka_retirement
    identity = protect_retirement(real_db, tenant, alert)
    correlation_complete(real_db, tenant, changed)
    # One-row chunks count source publication payloads in the same erasure budget.
    assert not cleanup_alert(real_db, IndependentStore(), tenant, alert, limit=1)
    with real_db.transaction(tenant) as conn:
        assert conn.execute(
            "SELECT 1 FROM alerts WHERE tenant_id=%s AND id=%s", (tenant, alert)
        ).fetchone()
        result = conn.execute(
            "SELECT * FROM kafka_source_results WHERE alert_id=%s", (alert,)
        ).fetchone()
        assert result["changed_envelope"] is None and result["kafka_confirmed"]
        assert result["input_digest"] and result["changed_event_id"]
    for _ in range(100):
        if cleanup_alert(real_db, IndependentStore(), tenant, alert, limit=1):
            break
    else:
        pytest.fail("Bounded cleanup failed to finish")
    assert apply(real_db, config, original) is None
    stale = receipt(config, v1, at=identity["retired_at"])
    assert apply(real_db, config, stale, 1) is None
    with real_db.transaction(tenant) as conn:
        rejected = conn.execute(
            "SELECT error_code FROM kafka_source_results WHERE delivery_id=%s",
            (json.loads(stale[2])["event_id"],),
        ).fetchone()
        assert rejected["error_code"] == "retired_receipt"
        assert not conn.execute(
            "SELECT 1 FROM alerts WHERE tenant_id=%s AND id=%s", (tenant, alert)
        ).fetchone()
        assert (
            conn.execute(
                "SELECT current_projection_seq FROM alert_identities WHERE tenant_id=%s AND id=%s",
                (tenant, alert),
            ).fetchone()["current_projection_seq"]
            == identity["current_projection_seq"]
        )
    fresh = receipt(config, v1, at=identity["retired_at"] + timedelta(milliseconds=1))
    new = json.loads(apply(real_db, config, fresh, 2))
    assert new["alert_id"] != str(alert)
