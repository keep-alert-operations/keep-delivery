"""Real-PostgreSQL evidence that idle reconciliation waits without leases or claims."""

from contextlib import contextmanager
import time
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from appchi.search import reconciliation
from test_scalability_pipeline import configuration, fake_service, seed_scan

pytestmark = pytest.mark.integration


class Recording:
    """Delegate to the real database and record every statement it executes."""

    def __init__(self, db):
        self.db, self.statements = db, []

    @contextmanager
    def transaction(self, *args, **kwargs):
        statements = self.statements

        class Connection:
            def __init__(self, conn):
                self.conn = conn

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def execute(self, query, *args, **kwargs):
                statements.append((time.monotonic(), str(query)))
                return self.conn.execute(query, *args, **kwargs)

        with self.db.transaction(*args, **kwargs) as conn:
            yield Connection(conn)

    def since(self, start, fragment):
        return [at for at, sql in self.statements if at >= start and fragment in sql]


def finish_pass(real_db, epoch, tenant):
    """Leave this tenant just completed, so its next pass is due in two seconds."""
    with real_db.transaction() as conn:
        return conn.execute(
            """UPDATE search_reconciliation SET completed_at=clock_timestamp(),
                last_page_at=clock_timestamp(),lease_owner=NULL,lease_until=NULL
                WHERE index_epoch=%s AND tenant_id=%s
                RETURNING extract(epoch FROM clock_timestamp()) AS at""",
            (epoch, tenant),
        ).fetchone()["at"]


def turns_until(service, page, seconds):
    """Drive reconcile() at the cadence of an idle 100 ms Kafka poll."""
    started = time.monotonic()
    while time.monotonic() - started < seconds and not page.call_count:
        service.reconcile()
        time.sleep(0.1)
    return time.monotonic() - started


def leases(real_db):
    with real_db.transaction() as conn:
        return conn.execute("SELECT count(*) AS n FROM search_writer_leases").fetchone()["n"]


def test_idle_scan_takes_no_lease_or_claim_and_wakes_at_the_due_time(real_db, tenant, monkeypatch):
    epoch = seed_scan(real_db, tenant)
    page = Mock(return_value=True)
    monkeypatch.setattr(reconciliation, "reconcile_page", page)
    recorded = Recording(real_db)
    service = fake_service(recorded, tenant)
    finish_pass(real_db, epoch, tenant)
    start = time.monotonic()
    elapsed = turns_until(service, page, 4)
    assert page.call_count == 1 and page.call_args.args[3] == tenant
    due_checks = recorded.since(start, "AS due_in")
    work = [(at, sql) for at, sql in recorded.statements if at >= start and at not in due_checks]
    # About 20 idle turns ran only bounded read-only due checks. The first other
    # statement opens the writer lease, two seconds after completion (one idle turn).
    assert 2 <= len(due_checks) <= 4
    assert "search_index_control" in work[0][1], work
    assert "INSERT INTO search_writer_leases" in work[1][1], work
    assert 1.8 <= work[0][0] - start <= 2.4, elapsed
    assert len([sql for _, sql in work if "SKIP LOCKED" in sql]) == 1
    assert len(recorded.since(start, "INSERT INTO search_writer_leases")) == 1
    assert len(recorded.since(start, "DELETE FROM search_writer_leases")) == 1
    assert leases(real_db) == 0


def test_new_tenant_is_claimed_within_the_bounded_recheck(real_db, tenant, monkeypatch):
    epoch = seed_scan(real_db, tenant)
    page = Mock(return_value=True)
    monkeypatch.setattr(reconciliation, "reconcile_page", page)
    service = fake_service(real_db, tenant)
    finish_pass(real_db, epoch, tenant)
    assert service.reconcile() == 0
    assert 0 < service.idle_seconds() <= reconciliation.IDLE_RECHECK_SECONDS
    created = uuid4()
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
            (created, str(created), str(created)),
        )
        conn.execute("INSERT INTO tenant_work_state(tenant_id) VALUES(%s)", (created,))
        conn.execute(
            "INSERT INTO tenant_view_state(tenant_id,index_epoch) VALUES(%s,%s)", (created, epoch)
        )
        conn.execute("INSERT INTO tenant_configuration_guard(tenant_id) VALUES(%s)", (created,))
        conn.execute("INSERT INTO admission_capacity(scope) VALUES(%s)", (str(created),))
    try:
        elapsed = turns_until(service, page, 2)
        assert page.call_count == 1 and page.call_args.args[3] == created
        assert elapsed <= reconciliation.IDLE_RECHECK_SECONDS + 0.3
    finally:
        with real_db.transaction() as conn:
            # Keep the new tenant out of later tests sharing this database.
            conn.execute(
                "UPDATE search_reconciliation SET retry_after=clock_timestamp()+interval '1 hour' WHERE tenant_id=%s",
                (created,),
            )


def test_due_check_reports_no_rows_as_nothing_registered(real_db, tenant):
    """The statement's empty case is NULL, not 0, so an empty scan waits."""

    class Rollback(Exception):
        pass

    try:
        with real_db.transaction() as conn:
            # Empty temporary tables shadow the real ones for unqualified names.
            for table in ("search_reconciliation", "tenant_view_state", "tenant_work_state"):
                conn.execute(f"CREATE TEMP TABLE {table} (LIKE public.{table}) ON COMMIT DROP")
            # Open the gate here too, so the test does not rely on earlier tests.
            conn.execute("UPDATE capability_gates SET ready=true WHERE capability='search'")

            @contextmanager
            def same(*args, **kwargs):
                yield conn

            service = fake_service(SimpleNamespace(transaction=same), tenant)
            assert service.next_due() is None
            assert service.reconcile() == 0
            assert service.idle_seconds() == pytest.approx(
                reconciliation.IDLE_RECHECK_SECONDS, abs=0.1
            )
            raise Rollback
    except Rollback:
        pass


@contextmanager
def index_control(real_db, **values):
    """Install a search binding for this test and restore the previous one."""
    with real_db.transaction() as conn:
        previous = conn.execute("SELECT * FROM search_index_control").fetchone()
        conn.execute("DELETE FROM search_index_control")
        conn.execute(
            """INSERT INTO search_index_control(alias_name,physical_index,index_epoch,
                writers_paused,binding_digest) VALUES(%(alias_name)s,%(physical_index)s,
                %(index_epoch)s,%(writers_paused)s,'perf15')""",
            values,
        )
    try:
        yield
    finally:
        with real_db.transaction() as conn:
            conn.execute("DELETE FROM search_index_control")
            if previous:
                columns = list(previous)
                conn.execute(
                    "INSERT INTO search_index_control("
                    + ",".join(columns)
                    + ") VALUES("
                    + ",".join(["%s"] * len(columns))
                    + ")",
                    [previous[column] for column in columns],
                )


def test_paused_and_mismatched_writers_stay_fenced_when_due(real_db, tenant, monkeypatch):
    epoch = seed_scan(real_db, tenant)
    page = Mock(return_value=True)
    monkeypatch.setattr(reconciliation, "reconcile_page", page)
    service = fake_service(real_db, tenant)
    alias = service.settings.elasticsearch_index
    with index_control(
        real_db,
        alias_name=alias,
        physical_index=alias + "-1",
        index_epoch=epoch,
        writers_paused=True,
    ):
        for _ in range(2):  # A fenced due tenant is retried, never cached as idle.
            with pytest.raises(Exception) as failure:
                service.reconcile()
            assert failure.value.code == "projection_fenced"
        assert page.call_count == 0 and leases(real_db) == 0
        with real_db.transaction() as conn:
            conn.execute("UPDATE search_index_control SET writers_paused=false")
        assert service.reconcile() == 1
        assert page.call_args.args[2] == alias + "-1"  # The captured physical index.
        assert leases(real_db) == 0
    other = fake_service(real_db, tenant)
    other.settings = configuration(tenant)  # A different alias binding.
    with index_control(
        real_db,
        alias_name=alias,
        physical_index=alias + "-1",
        index_epoch=epoch,
        writers_paused=False,
    ):
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE search_reconciliation SET completed_at=NULL WHERE index_epoch=%s AND tenant_id=%s",
                (epoch, tenant),
            )
        with pytest.raises(Exception) as failure:
            other.reconcile()
        assert failure.value.code == "projection_fenced"
        assert page.call_count == 1 and leases(real_db) == 0
