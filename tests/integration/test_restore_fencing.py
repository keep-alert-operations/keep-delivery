"""Real SQL drain, epoch, revocation and closed-capability restore boundaries."""
# ruff: noqa: F811

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import threading
from uuid import UUID, uuid4

import pytest

from appchi.db import Database
from appchi.recovery.guard import RESTORE_LOCK
from appchi.recovery.restore import begin_restore
from appchi.shared.errors import AppError
from test_maintenance_repository import maintenance_db, maintenance_browser  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def recovery(maintenance_db):  # noqa: F811
    admin, dsn = maintenance_db
    runtime = Database(dsn, 2, deployment_epoch="previous-test-epoch")
    yield admin, runtime
    runtime.close()
    with admin.transaction() as conn:
        conn.execute("UPDATE restore_control SET fenced=false,run_id=NULL,deployment_epoch=NULL")
        conn.execute("DELETE FROM restore_evidence_stage")
        conn.execute("DELETE FROM restore_runs")


def inputs():
    return dict(
        run_id=str(uuid4()),
        previous_epoch="previous-test-epoch",
        deployment_epoch=str(uuid4()),
        operation_reference="synthetic-restore-drill",
    )


def test_restore_drains_runtime_then_fences_all_business_transactions(recovery):
    admin, runtime = recovery
    held, release, started = threading.Event(), threading.Event(), threading.Event()
    body = inputs()

    def business():
        with runtime.transaction() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SELECT 1")
            held.set()
            assert release.wait(5)

    def restore():
        started.set()
        return begin_restore(admin, **body)

    with ThreadPoolExecutor(2) as pool:
        unit = pool.submit(business)
        assert held.wait(2)
        attempt = pool.submit(restore)
        assert started.wait(2)
        with admin.transaction() as conn:
            assert conn.execute("SELECT count(*) AS n FROM restore_runs").fetchone()["n"] == 0
        release.set()
        unit.result(5)
        assert attempt.result(5)["state"] == "fenced"
    with pytest.raises(AppError) as error:
        with runtime.transaction():
            pytest.fail("A restored runtime transaction must not execute")
    assert error.value.code == "restore_reconciliation_required"
    assert begin_restore(admin, **body)["id"] == UUID(body["run_id"])
    with pytest.raises(AppError) as conflict:
        begin_restore(admin, **{**body, "operation_reference": "changed"})
    assert conflict.value.code == "idempotency_conflict"


def test_failed_business_and_guard_reads_release_session_lock(recovery):
    admin, runtime = recovery
    with pytest.raises(RuntimeError):
        with runtime.transaction():
            raise RuntimeError("controlled rollback")
    with admin.transaction() as conn:
        assert conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s) AS acquired", (RESTORE_LOCK,)
        ).fetchone()["acquired"]
    with admin.transaction() as conn:
        conn.execute("UPDATE restore_control SET deployment_epoch=%s", (str(uuid4()),))
    with pytest.raises(AppError) as error:
        with runtime.transaction():
            pytest.fail("Old deployment must remain fenced")
    assert error.value.code == "deployment_epoch_fenced"
    with admin.transaction() as conn:
        assert conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s) AS acquired", (RESTORE_LOCK,)
        ).fetchone()["acquired"]


def test_begin_invalidates_sessions_and_application_roles_cannot_reopen(
    recovery, maintenance_browser
):
    admin, runtime = recovery
    client, _, _, _ = maintenance_browser
    with admin.transaction() as conn:
        before = conn.execute(
            "SELECT count(*) AS n FROM auth_sessions WHERE revoked_at IS NULL"
        ).fetchone()["n"]
        assert before > 0
    original = admin.transaction

    @contextmanager
    def recovery_role(tenant_id=None):
        with original(tenant_id) as conn:
            conn.execute("SET LOCAL ROLE appchi_recovery")
            yield conn

    admin.transaction = recovery_role
    try:
        begin_restore(admin, **inputs())
    finally:
        admin.transaction = original
    with admin.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM auth_sessions WHERE revoked_at IS NULL"
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM capability_gates WHERE ready").fetchone()["n"]
            == 0
        )
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        with admin.transaction() as conn:
            conn.execute("SET LOCAL ROLE appchi_api")
            conn.execute("UPDATE capability_gates SET ready=true WHERE capability='human_auth'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with admin.transaction() as conn:
            conn.execute("SET LOCAL ROLE appchi_worker")
            conn.execute("UPDATE restore_control SET fenced=false")
    with pytest.raises(AppError):
        with runtime.transaction():
            pytest.fail("Every guarded workload stays closed")


def test_restore_rejects_reused_or_conflicting_epoch(recovery):
    admin, _ = recovery
    body = inputs()
    with pytest.raises(ValueError):
        begin_restore(admin, **{**body, "previous_epoch": body["deployment_epoch"]})
    with admin.transaction() as conn:
        conn.execute("UPDATE restore_control SET deployment_epoch='different-authority'")
    with pytest.raises(AppError) as error:
        begin_restore(admin, **body)
    assert error.value.code == "deployment_epoch_conflict"


def test_partial_collection_is_staged_but_never_ready_and_retry_uses_new_scan(recovery):
    from dataclasses import asdict
    from appchi.recovery.evidence import (
        EvidenceBinding,
        Evidence,
        CheckpointPage,
        checkpoint_page_digest,
        PAGED_CHECKPOINT_VERSION,
    )
    from appchi.recovery.restore import collect_restore, status

    admin, runtime = recovery
    body = inputs()
    begin_restore(admin, **body)
    records = [
        Evidence(str(uuid4()), str(uuid4()), "alert", str(uuid4()), index, "final", "a" * 64)
        for index in (1, 2)
    ]

    class Store:
        binding = EvidenceBinding(
            "https://evidence.invalid",
            "/append",
            "/checkpoint",
            "fixture",
            "independent-fixture",
            "durable-fixture",
            paged_checkpoint_path="/pages",
            paged_checkpoint_version=PAGED_CHECKPOINT_VERSION,
        )
        fail = True

        def checkpoint_page(self, **request):
            second = request["cursor"] is not None
            if second and self.fail:
                raise RuntimeError("controlled outage")
            record = records[int(second)]
            wire = dict(
                version=PAGED_CHECKPOINT_VERSION,
                namespace="fixture",
                after=None,
                checkpoint="frozen-1",
                request_cursor=request["cursor"],
                total_records="2",
                records=[{**asdict(record), "revision": str(record.revision)}],
                next_cursor=None if second else "next",
                complete=second,
            )
            return CheckpointPage(
                "fixture",
                None,
                "frozen-1",
                request["cursor"],
                2,
                (record,),
                wire["next_cursor"],
                second,
                checkpoint_page_digest(wire),
            )

    store = Store()
    with pytest.raises(TypeError):
        collect_restore(admin, store, body["run_id"], after="unproven-base")
    with pytest.raises(ValueError):
        collect_restore(admin, store, body["run_id"], page_size=0)
    assert status(admin, body["run_id"])["state"] == "fenced"
    with pytest.raises(AppError):
        collect_restore(admin, store, body["run_id"])
    partial = status(admin, body["run_id"])
    assert partial["state"] == "collecting" and partial["collected_count"] == 1
    store.fail = False
    complete = collect_restore(admin, store, body["run_id"])
    assert complete["state"] == "collected" and complete["collected_count"] == 2
    assert complete["collection_id"] != partial["collection_id"]
    with admin.transaction() as conn:
        assert conn.execute("SELECT fenced FROM restore_control").fetchone()["fenced"]
        assert (
            conn.execute("SELECT count(*) AS n FROM capability_gates WHERE ready").fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM restore_evidence_stage WHERE collection_id=%s",
                (complete["collection_id"],),
            ).fetchone()["n"]
            == 2
        )
    with pytest.raises(AppError):
        with runtime.transaction():
            pytest.fail("Transport completeness cannot reopen business paths")


def test_quiesced_kafka_admission_and_restored_human_paths_remain_closed(recovery, tmp_path):
    import base64
    from fastapi.testclient import TestClient
    from appchi.config import Settings
    from appchi.main import create_app

    admin, runtime = recovery
    begin_restore(admin, **inputs())
    settings = Settings(
        database_url="unused",
        wrapping_key=base64.b64encode(b"k" * 32).decode(),
        deployment_epoch="previous-test-epoch",
        kafka_admission_enabled=False,
        capacity_profile=Path("qualification/development-capacity.json").resolve(),
    )
    with TestClient(create_app(settings, runtime)) as client:
        result = client.post(
            "/api/ingest/v1/alerts",
            json={
                "application": "restore-test",
                "object": "cpu",
                "node_name": "node",
                "message": "synthetic",
                "operator": "legacy",
                "severity": "error",
                "time_created": "01-01-2026 00:00:00",
            },
        )
        assert result.status_code == 503, result.text
        assert result.json()["error"]["code"] == "kafka_cutover_required"
        session = client.get("/auth/session")
        assert session.status_code == 503
        assert session.json()["error"]["code"] == "restore_reconciliation_required"


def test_restore_keeps_protocol_selection_and_fences_kafka_workers(recovery):
    from types import SimpleNamespace
    from appchi.kafka_source import SourceConsumer
    from appchi.kafka_changed import ChangedConsumer

    admin, runtime = recovery
    with admin.transaction() as conn:
        conn.execute("UPDATE kafka_pipeline_control SET enabled=true WHERE singleton")
    begin_restore(admin, **inputs())
    with admin.transaction() as conn:
        assert conn.execute(
            "SELECT enabled FROM kafka_pipeline_control WHERE singleton"
        ).fetchone()["enabled"]
    for constructor in (
        lambda: SourceConsumer(runtime, SimpleNamespace(), None),
        lambda: ChangedConsumer(runtime, SimpleNamespace(), None, "correlation"),
        lambda: ChangedConsumer(runtime, SimpleNamespace(), None, "projection"),
    ):
        with pytest.raises(AppError, match="restore_reconciliation_required"):
            constructor()
