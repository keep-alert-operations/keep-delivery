"""Real PostgreSQL pipeline snapshot and uncertain-session-lock regressions."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from time import monotonic, sleep

import psycopg
from psycopg.rows import dict_row
import pytest

from appchi.recovery.guard import RESTORE_LOCK, runtime_guard
from appchi.shared.errors import AppError

pytestmark = pytest.mark.integration


class ConnectionProxy:
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)


@pytest.mark.parametrize("failed_phase", [1, 2])
def test_uncertain_pipeline_completion_does_not_leak_restore_lock(real_db, failed_phase):
    class FailedCompletion(ConnectionProxy):
        phases = 0

        @contextmanager
        def pipeline(self):
            with self.connection.pipeline():
                yield
            self.phases += 1
            if self.phases == failed_phase:
                raise RuntimeError("controlled completion uncertainty")

    entered = False
    with psycopg.connect(real_db.pool.conninfo, row_factory=dict_row) as connection:
        with pytest.raises(RuntimeError, match="controlled completion uncertainty"):
            with runtime_guard(FailedCompletion(connection), "pipeline-test"):
                entered = True
        assert connection.closed
    assert entered is (failed_phase == 2)
    with real_db.transaction() as conn:
        assert conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s) AS acquired", (RESTORE_LOCK,)
        ).fetchone()["acquired"]


def test_pipelined_control_read_sees_fence_committed_after_acquisition_statement_starts(real_db):
    barrier = RESTORE_LOCK + 1

    class PausedAcquisition(ConnectionProxy):
        def execute(self, query, params=None):
            if query.startswith("SELECT pg_try_advisory_lock_shared"):
                # The statement snapshot exists while this FROM function blocks.
                # A combined lock/control SELECT would retain that old snapshot.
                return self.connection.execute(
                    "SELECT pg_try_advisory_lock_shared(%s) AS acquired "
                    "FROM pg_advisory_xact_lock(%s)",
                    (RESTORE_LOCK, barrier),
                )
            return self.connection.execute(query, params)

    with (
        psycopg.connect(real_db.pool.conninfo, row_factory=dict_row) as runtime,
        psycopg.connect(real_db.pool.conninfo, autocommit=True, row_factory=dict_row) as admin,
        ThreadPoolExecutor(1) as executor,
    ):
        original = admin.execute("SELECT deployment_epoch,fenced FROM restore_control").fetchone()
        admin.execute("SELECT pg_advisory_lock(%s)", (RESTORE_LOCK,))
        admin.execute("SELECT pg_advisory_lock(%s)", (barrier,))

        def business():
            with runtime_guard(PausedAcquisition(runtime), "pipeline-test"):
                pytest.fail("A fence committed before acquisition must prevent business work")

        attempt = executor.submit(business)
        try:
            deadline = monotonic() + 3
            while not admin.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s "
                "AND locktype='advisory' AND objid=%s AND NOT granted) AS waiting",
                (runtime.info.backend_pid, barrier),
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
            try:
                if not attempt.done():
                    attempt.result(4)
            finally:
                admin.execute(
                    "UPDATE restore_control SET deployment_epoch=%s,fenced=%s WHERE singleton",
                    (original["deployment_epoch"], original["fenced"]),
                )
