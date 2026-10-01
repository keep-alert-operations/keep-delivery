import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from appchi.alerts.processor import process_one
from appchi.ingestion.native import canonical
from appchi.ingestion.service import Admission
from appchi.shared.errors import AppError

pytestmark = pytest.mark.integration


def test_durable_replay_lifecycle(real_db, cipher, profile, v1):
    service = Admission(real_db, cipher, profile)
    key = str(uuid4())
    status, receipt = service.admit(canonical(v1), "v1", key)
    assert status == 202
    with real_db.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    while process_one(real_db, tenant):
        pass
    replay = service.admit(json.dumps(v1, indent=4).encode(), "v1", key)[1]
    assert replay == receipt
    result = service.receipt(receipt["request_id"], receipt["receipt_token"], "v1")
    assert result["items"][0]["processing_status"] == "applied"
    alert_id = result["items"][0]["alert_id"]
    for severity in ("error", "clear", "error"):
        service.admit(canonical({**v1, "severity": severity}), "v1")
        assert process_one(real_db, tenant)
    with real_db.transaction() as conn:
        row = conn.execute("SELECT * FROM alerts WHERE id=%s", (alert_id,)).fetchone()
        assert row["occurrence_number"] == 2
        assert row["revision"] == 3
        assert row["projection_version"] == 4
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM alert_source_events WHERE alert_id=%s", (alert_id,)
            ).fetchone()["n"]
            == 4
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM admission_reservations r JOIN ingest_items i ON i.id=r.item_id WHERE i.alert_id=%s AND NOT r.released",
                (alert_id,),
            ).fetchone()["n"]
            == 0
        )


def test_partial_batch_and_exact_object_bytes(real_db, cipher, profile, v1):
    service = Admission(real_db, cipher, profile)
    raw = (
        b'{"alerts":[{"payload": '
        + json.dumps(v1, indent=2).encode()
        + b'}, {"payload":{"bad":1}}]}'
    )
    status, result = service.admit(raw, "v1")
    assert status == 202
    assert [r["disposition"] for r in result["items"]] == ["accepted", "rejected"]
    with real_db.transaction() as conn:
        item = conn.execute(
            "SELECT * FROM ingest_items WHERE id=%s", (result["items"][0]["event_id"],)
        ).fetchone()
        assert bytes(item["raw_json"]) == json.dumps(v1, indent=2).encode()


def test_concurrent_global_capacity_is_atomic(real_db, cipher, profile, v1):
    with real_db.transaction() as conn:
        current = conn.execute(
            "SELECT pending_items FROM admission_capacity WHERE scope='global'"
        ).fetchone()["pending_items"]
    service = Admission(real_db, cipher, {**profile, "global_items": current + 1})

    def submit(n):
        try:
            return service.admit(
                canonical({**v1, "application": v1["application"] + str(n)}), "v1"
            )[0]
        except AppError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sorted(results) == [202, 429]


def test_identity_conflict_is_terminal(real_db, cipher, profile, v1):
    service = Admission(real_db, cipher, profile)
    service.admit(canonical(v1), "v1")
    with real_db.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    while process_one(real_db, tenant):
        pass
    second = service.admit(canonical({**v1, "operator": "different"}), "v1")[1]
    assert process_one(real_db, tenant)
    result = service.receipt(second["request_id"], second["receipt_token"], "v1")
    assert result["items"][0]["error_code"] == "identity_conflict"


def test_old_failure_cannot_block_next_head(real_db, cipher, profile, v1):
    from appchi.alerts.processor import record_failure

    service = Admission(real_db, cipher, profile)
    service.admit(canonical(v1), "v1")
    with real_db.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    while process_one(real_db, tenant):
        pass
    service.admit(canonical({**v1, "message": "next"}), "v1")
    for _ in range(10):
        record_failure(real_db, tenant, uuid4())
    with real_db.transaction() as conn:
        row = conn.execute(
            "SELECT failures,blocked FROM tenant_work_state WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert row == {"failures": 0, "blocked": False}
