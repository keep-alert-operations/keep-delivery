"""Real HTTP/PG receipt capture and atomic source linkage, without render claims."""
# ruff: noqa: F811

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
from time import monotonic, sleep
from uuid import uuid4

import pytest

from appchi.alerts.processor import process_one
from appchi.ingestion.native import canonical
from appchi.ingestion.service import Admission
from appchi.shared.errors import AppError
from appchi.visibility.inputs import InputLimits, record_input, registry_checkpoint
from appchi.visibility.registry import RegistryLimits
from appchi.visibility.runtime import capture_profile, capture_turn
from test_search_pipeline import real_db  # noqa: F401
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def capture(profile):
    return {
        **profile,
        "visibility": {
            "version": "appchi-visibility-capture-v1",
            "policy_epoch": "synthetic-capture",
            "deployment_epoch": "test",
            "qualification": True,
            "inputs": asdict(InputLimits(100, 100, 100, 100000, 2000, 1)),
            "registry": asdict(RegistryLimits(30, 8, 8, 100, 20000, 65536)),
        },
    }


def global_id(db):
    with db.transaction() as conn:
        return conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]


def test_http_receipt_includes_validation_and_retries_keep_original_seed(
    browser, real_db, capture, v1, monkeypatch
):
    import appchi.ingestion.service as service

    client, _ = browser
    client.app.state.profile = capture
    original = service.validate_native

    def slow_validation(*args):
        sleep(0.03)
        return original(*args)

    monkeypatch.setattr(service, "validate_native", slow_validation)
    response = client.post(
        "/api/ingest/v1/alerts", json=v1, headers={"Idempotency-Key": "capture-http"}
    )
    assert response.status_code == 202, response.text
    replay = client.post(
        "/api/ingest/v1/alerts", json=v1, headers={"Idempotency-Key": "capture-http"}
    )
    assert replay.json() == response.json()
    with real_db.transaction() as conn:
        rows = conn.execute("""SELECT v.*,i.received_at FROM visibility_inputs v
            JOIN ingest_items i ON i.id=v.id""").fetchall()
        assert len(rows) == 1
        assert rows[0]["received_at"] - rows[0]["started_at"] >= timedelta(milliseconds=30)
        assert rows[0]["deadline_at"] == rows[0]["started_at"] + timedelta(seconds=5)


def test_capture_commits_and_rolls_back_with_capacity_reservation(real_db, cipher, capture, v1):
    rejected = deepcopy(capture)
    rejected["global_items"] = 0
    with pytest.raises(AppError, match="capacity_exceeded"):
        Admission(real_db, cipher, rejected).admit(canonical(v1), "v1")
    with real_db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM visibility_inputs").fetchone()["n"] == 0
    Admission(real_db, cipher, capture).admit(canonical(v1), "v1")
    with real_db.transaction() as conn:
        assert (
            conn.execute("SELECT inputs FROM visibility_input_capacity").fetchone()["inputs"] == 1
        )


def test_source_links_even_after_capture_disabled_and_keeps_original_deadline(
    real_db, cipher, capture, v1
):
    result = Admission(real_db, cipher, capture).admit(
        canonical(v1), "v1", receipt_started=monotonic() - 7
    )[1]
    tenant = global_id(real_db)
    assert process_one(real_db, tenant, profile=None)
    assert not process_one(real_db, tenant, profile=None)
    with real_db.transaction() as conn:
        seed = conn.execute(
            "SELECT * FROM visibility_inputs WHERE id=%s", (result["items"][0]["event_id"],)
        ).fetchone()
        linked = conn.execute(
            "SELECT * FROM visibility_applications WHERE input_id=%s", (seed["id"],)
        ).fetchone()
        projection = conn.execute(
            "SELECT * FROM projection_work WHERE tenant_id=%s AND seq=%s",
            (tenant, linked["projection_seq"]),
        ).fetchone()
        assert linked["disposition"] == "applied" and linked["source_event_id"] == seed["id"]
        assert linked["projection_version"] == projection["version"]
        assert linked["alert_id"] == projection["alert_id"]
        assert linked["recorded_at"] > seed["deadline_at"]
        assert len(linked["after_digest"]) == 32 and linked["before_digest"] is None


def test_seed_exhaustion_admits_work_and_reports_gap(real_db, cipher, capture, v1):
    capture["visibility"]["inputs"]["max_tenant_inputs"] = 1
    admission = Admission(real_db, cipher, capture)
    for message in ("first", "second"):
        assert admission.admit(canonical({**v1, "message": message}), "v1")[0] == 202
    tenant = global_id(real_db)
    assert process_one(real_db, tenant) and process_one(real_db, tenant)
    with real_db.transaction() as conn:
        row = conn.execute("SELECT * FROM visibility_input_capacity").fetchone()
        assert row["inputs"] == 1 and row["seed_gap_attempts"] == 1
        assert (
            conn.execute("SELECT count(*) AS n FROM visibility_applications").fetchone()["n"] == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM ingest_items WHERE disposition='applied'"
            ).fetchone()["n"]
            == 2
        )


def test_repeat_and_semantic_rejection_have_distinct_proof(real_db, cipher, capture, v1):
    admission = Admission(real_db, cipher, capture)
    tenant = global_id(real_db)
    for value in (v1, v1, {**v1, "severity": "warning"}):
        admission.admit(canonical(value), "v1")
        assert process_one(real_db, tenant)
    with real_db.transaction() as conn:
        rows = conn.execute("""SELECT a.* FROM visibility_applications a
            JOIN visibility_inputs i ON i.tenant_id=a.tenant_id AND i.id=a.input_id
            ORDER BY i.original_order""").fetchall()
        assert [row["disposition"] for row in rows] == ["applied", "unchanged", "rejected"]
        assert rows[0]["journal_order"] is not None
        assert rows[1]["journal_order"] is None and rows[1]["projection_version"] == 2
        assert rows[1]["before_digest"] is not None
        assert rows[2]["error_code"] == "identity_conflict"
        assert rows[2]["alert_id"] is None and rows[2]["projection_seq"] is None


def test_source_rollback_cannot_commit_observation_before_product(
    real_db, cipher, capture, v1, monkeypatch
):
    import appchi.alerts.processor as processor

    Admission(real_db, cipher, capture).admit(canonical(v1), "v1")
    tenant = global_id(real_db)
    release = processor.release_reservation

    def fail_after_link(*args):
        raise RuntimeError("injected failure after visibility link")

    monkeypatch.setattr(processor, "release_reservation", fail_after_link)
    with pytest.raises(RuntimeError, match="after visibility"):
        process_one(real_db, tenant)
    with real_db.transaction() as conn:
        assert (
            conn.execute("SELECT disposition FROM ingest_items").fetchone()["disposition"]
            == "pending"
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM visibility_applications").fetchone()["n"] == 0
        )
        assert conn.execute("SELECT count(*) AS n FROM alerts").fetchone()["n"] == 0
    monkeypatch.setattr(processor, "release_reservation", release)
    assert process_one(real_db, tenant)


def test_application_exhaustion_does_not_block_ordered_source(real_db, cipher, capture, v1):
    capture["visibility"]["inputs"]["max_application_bytes"] = 1
    Admission(real_db, cipher, capture).admit(canonical(v1), "v1")
    assert process_one(real_db, global_id(real_db))
    with real_db.transaction() as conn:
        assert (
            conn.execute("SELECT application_gap FROM visibility_inputs").fetchone()[
                "application_gap"
            ]
            == "application_allocation_exhausted"
        )
        assert (
            conn.execute("SELECT disposition FROM ingest_items").fetchone()["disposition"]
            == "applied"
        )


def test_capture_worker_expands_before_source_and_does_not_claim_success(
    browser, real_db, cipher, capture, v1
):
    client, _ = browser
    tenant = global_id(real_db)
    client.app.state.visibility_registry_limits = capture_profile(capture).registry
    response = client.post(
        f"/api/tenants/{tenant}/view-subscriptions",
        json={
            "command_id": str(uuid4()),
            "tab_id": str(uuid4()),
            "view": {"surface": "alert_feed"},
            "state": "foreground",
        },
    )
    assert response.status_code == 200, response.text
    Admission(real_db, cipher, capture).admit(canonical(v1), "v1")
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
    capture_turn(real_db)
    with real_db.transaction() as conn:
        candidate = conn.execute("SELECT * FROM visibility_candidates").fetchone()
        assert (
            candidate["eligibility"] == "unclassified"
            and candidate["coverage_reason"] == "proof_pending"
        )
        assert (
            conn.execute("SELECT disposition FROM ingest_items").fetchone()["disposition"]
            == "pending"
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM visibility_applications").fetchone()["n"] == 0
        )


def test_worker_seals_delayed_expiry_at_original_end(real_db, capture):
    tenant, identifier = global_id(real_db), uuid4()
    configured = capture_profile(capture)
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
        now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        record_input(
            conn,
            tenant=tenant,
            input_id=identifier,
            kind="maintenance_expiry",
            started_at=now - timedelta(seconds=10),
            original_order=0,
            registry_checkpoint=registry_checkpoint(conn, tenant),
            policy=configured.policy,
            limits=configured.inputs,
            schedule={"window_id": str(uuid4()), "revision": 1, "generation": 1},
        )
    capture_turn(real_db)
    with real_db.transaction() as conn:
        seed = conn.execute("SELECT * FROM visibility_inputs WHERE id=%s", (identifier,)).fetchone()
        assert seed["cohort_checkpoint"] == 0 and seed["deadline_at"] == now - timedelta(seconds=5)
        assert seed["expansion_state"] == "expanded"


def test_registry_stays_unavailable_without_explicit_limits(browser, real_db):
    client, _ = browser
    response = client.post(f"/api/tenants/{global_id(real_db)}/view-subscriptions", json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "visibility_registry_unconfigured"


def test_expiry_fanout_commits_projection_link_and_cursor_atomically(
    browser, real_db, cipher, capture, v1, monkeypatch
):
    import appchi.maintenance.processing as processing
    import appchi.visibility.runtime as visibility
    from appchi.maintenance.runtime import publish_reconciliation
    from test_maintenance_repository import body

    client, _ = browser
    tenant = global_id(real_db)
    capture.update(
        qualified=True,
        maintenance_max_work=10000,
        maintenance_max_inputs=100,
        maintenance_max_segments=20,
        maintenance={"max_overlap": 3, "max_work_bytes": 100000},
    )
    client.app.state.profile = capture
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='maintenance'")
    definition = body(past=True)
    definition["cel"] = "true"
    response = client.post(f"/api/tenants/{tenant}/maintenance", json=definition)
    assert response.status_code == 201, response.text
    window = response.json()["data"]
    Admission(real_db, cipher, capture).admit(canonical(v1), "v1")
    assert process_one(real_db, tenant, profile=capture)
    # Only the reducer's effective test clock advances. Product receipt and the
    # immutable captured schedule remain actual values from the preceding APIs.
    effective = datetime.fromisoformat(window["end_at"]) + timedelta(seconds=1)
    real_coordinator = processing.coordinator

    def at_expiry(conn, selected):
        state, _ = real_coordinator(conn, selected)
        return state, effective

    monkeypatch.setattr(processing, "coordinator", at_expiry)
    lease = processing.claim_due(real_db, tenant)
    assert lease is not None
    catalog = processing.prepare_catalog(
        real_db, tenant, max_work=10000, max_inputs=100, max_segments=20
    )

    def lock(conn, selected, alert):
        return conn.execute(
            "SELECT * FROM alerts WHERE tenant_id=%s AND id=%s FOR UPDATE", (selected, alert)
        ).fetchone()

    original = visibility.link_expiry_result

    def fail_after_link(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected expiry rollback")

    monkeypatch.setattr(visibility, "link_expiry_result", fail_after_link)
    with pytest.raises(RuntimeError, match="expiry rollback"):
        processing.step_job(
            real_db, lease, catalog, lock_alert=lock, publish=publish_reconciliation
        )
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM visibility_applications WHERE input_id=%s",
                (lease.job_id,),
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT cursor_alert_id FROM maintenance_fanout_jobs WHERE id=%s", (lease.job_id,)
            ).fetchone()["cursor_alert_id"]
            is None
        )
        assert conn.execute("SELECT suppressed FROM alerts").fetchone()["suppressed"]
    monkeypatch.setattr(visibility, "link_expiry_result", original)
    assert (
        processing.step_job(
            real_db, lease, catalog, lock_alert=lock, publish=publish_reconciliation
        )
        == "processed"
    )
    assert (
        processing.step_job(
            real_db, lease, catalog, lock_alert=lock, publish=publish_reconciliation
        )
        == "complete"
    )
    assert (
        processing.step_job(
            real_db, lease, catalog, lock_alert=lock, publish=publish_reconciliation
        )
        == "stale"
    )
    with real_db.transaction() as conn:
        linked = conn.execute(
            "SELECT * FROM visibility_applications WHERE input_id=%s", (lease.job_id,)
        ).fetchone()
        assert linked["disposition"] == "applied" and linked["source_event_id"] is None
        assert linked["projection_version"] == 2
        assert (
            conn.execute(
                "SELECT required_projection_seq FROM maintenance_fanout_jobs WHERE id=%s",
                (lease.job_id,),
            ).fetchone()["required_projection_seq"]
            >= linked["projection_seq"]
        )
        assert not conn.execute("SELECT suppressed FROM alerts").fetchone()["suppressed"]


def test_explicit_profile_rejects_unknown_epoch_and_invalid_allocations(capture):
    assert capture_profile({}) is None
    with pytest.raises(ValueError, match="another deployment"):
        capture_profile(capture, deployment_epoch="different")
    capture["visibility"]["inputs"]["max_tenant_inputs"] = True
    with pytest.raises(ValueError, match="Invalid visibility"):
        capture_profile(capture)
