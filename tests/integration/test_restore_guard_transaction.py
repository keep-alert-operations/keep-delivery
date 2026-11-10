"""Real PostgreSQL in-transaction restore guard: drain, fence and snapshot order."""
# ruff: noqa: F811

from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
import threading
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from keep.db import Database
from keep.recovery.guard import RESTORE_LOCK, runtime_guard
from keep.recovery.restore import begin_restore
from keep.shared.errors import AppError
from test_maintenance_repository import maintenance_db  # noqa: F401

pytestmark = pytest.mark.integration

EPOCH = "previous-test-epoch"
ELIGIBLE = [{"tenant_id": uuid4()}, {"read_committed": True}]
RESTORE_LOCKS = (
    "SELECT mode,granted FROM pg_locks WHERE locktype='advisory' AND classid=0 "
    "AND objid=%s AND objsubid=1"
)


@pytest.fixture
def recovery(maintenance_db):  # noqa: F811
    admin, dsn = maintenance_db
    runtime = Database(dsn, 2, deployment_epoch=EPOCH)
    yield admin, runtime, dsn
    runtime.close()
    with admin.transaction() as conn:
        conn.execute("UPDATE restore_control SET fenced=false,run_id=NULL,deployment_epoch=NULL")
        conn.execute("DELETE FROM restore_evidence_stage")
        conn.execute("DELETE FROM restore_runs")


def restore_lock_free(admin):
    with admin.transaction() as conn:
        return conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s) AS acquired", (RESTORE_LOCK,)
        ).fetchone()["acquired"]


def wait_for(admin, query, params, seconds=3):
    deadline = monotonic() + seconds
    while True:
        with admin.transaction() as conn:
            if conn.execute(query, params).fetchone():
                return
        assert monotonic() < deadline, "Expected lock state was not reached"
        sleep(0.01)


@pytest.mark.parametrize("kwargs", ELIGIBLE)
def test_eligible_unit_holds_a_shared_transaction_lock_released_at_end(recovery, kwargs):
    admin, runtime, _ = recovery
    with runtime.transaction(**kwargs) as conn:
        assert conn.execute(
            RESTORE_LOCKS + " AND pid=pg_backend_pid()", (RESTORE_LOCK,)
        ).fetchall() == [{"mode": "ShareLock", "granted": True}]
        assert (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            == "read committed"
        )
        assert not restore_lock_free(admin)
        pid = conn.info.backend_pid
    with admin.transaction() as conn:
        assert not conn.execute(RESTORE_LOCKS + " AND pid=%s", (RESTORE_LOCK, pid)).fetchall(), (
            "No session lock may survive the unit"
        )
    with pytest.raises(RuntimeError):
        with runtime.transaction(**kwargs):
            raise RuntimeError("controlled rollback")
    assert restore_lock_free(admin)


@pytest.mark.parametrize("kwargs", ELIGIBLE)
def test_in_transaction_guard_refuses_a_later_repeatable_read_switch(recovery, kwargs):
    admin, runtime, _ = recovery
    with pytest.raises(psycopg.errors.ActiveSqlTransaction):
        with runtime.transaction(**kwargs) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    assert restore_lock_free(admin)


def test_restore_drains_eligible_unit_rejects_new_units_then_fences(recovery):
    admin, runtime, _ = recovery
    tenant = uuid4()
    held, release = threading.Event(), threading.Event()
    body = dict(
        run_id=str(uuid4()),
        previous_epoch=EPOCH,
        deployment_epoch=str(uuid4()),
        operation_reference="synthetic-restore-drill",
    )

    def business():
        with runtime.transaction(tenant) as conn:
            conn.execute("SELECT 1")
            held.set()
            assert release.wait(5)

    with ThreadPoolExecutor(2) as pool:
        unit = pool.submit(business)
        assert held.wait(2)
        attempt = pool.submit(begin_restore, admin, **body)
        wait_for(
            admin,
            RESTORE_LOCKS + " AND mode='ExclusiveLock' AND NOT granted",
            (RESTORE_LOCK,),
        )
        # A waiting exclusive request blocks new shared requests: fail fast.
        for kwargs in ELIGIBLE:
            with pytest.raises(AppError) as busy:
                with runtime.transaction(**kwargs):
                    pytest.fail("No unit may start while restore drains")
            assert busy.value.code == "restore_in_progress"
        with admin.transaction() as conn:
            assert conn.execute("SELECT count(*) AS n FROM restore_runs").fetchone()["n"] == 0
        release.set()
        unit.result(5)
        assert attempt.result(5)["state"] == "fenced"
    for kwargs in ELIGIBLE:
        with pytest.raises(AppError) as fenced:
            with runtime.transaction(**kwargs):
                pytest.fail("A restored runtime transaction must not execute")
        assert fenced.value.code == "restore_reconciliation_required"
    assert restore_lock_free(admin)


def test_epoch_mismatch_fences_eligible_units(recovery):
    admin, runtime, _ = recovery
    with admin.transaction() as conn:
        conn.execute("UPDATE restore_control SET deployment_epoch=%s", (str(uuid4()),))
    for kwargs in ELIGIBLE:
        with pytest.raises(AppError) as error:
            with runtime.transaction(**kwargs):
                pytest.fail("Old deployment must remain fenced")
        assert error.value.code == "deployment_epoch_fenced"
    assert restore_lock_free(admin)


class PausedAcquisition:
    def __init__(self, connection, barrier):
        self.connection, self.barrier = connection, barrier

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, params=None):
        if query.startswith("SELECT pg_try_advisory_xact_lock_shared"):
            # The statement snapshot exists while this FROM function blocks.
            # A combined lock/control SELECT would retain that old snapshot.
            return self.connection.execute(
                "SELECT pg_try_advisory_xact_lock_shared(%s) AS acquired "
                "FROM pg_advisory_xact_lock(%s)",
                (RESTORE_LOCK, self.barrier),
            )
        return self.connection.execute(query, params)


def test_pipelined_control_read_sees_fence_committed_after_acquisition_starts(recovery):
    _, runtime, dsn = recovery
    barrier = RESTORE_LOCK + 1
    with (
        psycopg.connect(dsn, row_factory=dict_row) as connection,
        psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as admin,
        ThreadPoolExecutor(1) as executor,
    ):
        admin.execute("SELECT pg_advisory_lock(%s)", (RESTORE_LOCK,))
        admin.execute("SELECT pg_advisory_lock(%s)", (barrier,))

        def business():
            unit = runtime._transaction(
                PausedAcquisition(connection, barrier), uuid4(), runtime_guard
            )
            with unit:
                pytest.fail("A fence committed before acquisition must prevent business work")

        attempt = executor.submit(business)
        try:
            deadline = monotonic() + 1.5
            while not admin.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s "
                "AND locktype='advisory' AND objid=%s AND NOT granted) AS waiting",
                (connection.info.backend_pid, barrier),
            ).fetchone()["waiting"]:
                assert monotonic() < deadline, "Guard did not reach the acquisition barrier"
                sleep(0.01)
            admin.execute("UPDATE restore_control SET fenced=true WHERE singleton")
            admin.execute("SELECT pg_advisory_unlock(%s)", (RESTORE_LOCK,))
            admin.execute("SELECT pg_advisory_unlock(%s)", (barrier,))
            with pytest.raises(AppError, match="restore_reconciliation_required"):
                attempt.result(4)
            assert admin.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired", (RESTORE_LOCK,)
            ).fetchone()["acquired"]
        finally:
            admin.execute("SELECT pg_advisory_unlock_all()")
            if not attempt.done():
                attempt.exception(4)
