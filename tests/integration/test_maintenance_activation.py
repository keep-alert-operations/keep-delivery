# ruff: noqa: F811
"""Atomic initial activation with actual coordinator and source-proof races."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from time import monotonic, sleep

import pytest

from appchi.maintenance import activation
from appchi.maintenance.warmup import warmup_page
from appchi.shared.errors import AppError
from test_maintenance_processing import PROFILE, publish_source
from test_maintenance_repository import maintenance_browser, maintenance_db  # noqa: F401

pytestmark = pytest.mark.integration
QUALIFIED = {
    **PROFILE,
    "id": "synthetic-test-only",
    "qualification_evidence": "controlled-fixture-not-production-evidence",
}


def disable(db):
    with db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='maintenance'")


def warm_all(db):
    with db.transaction() as conn:
        tenants = [row["id"] for row in conn.execute("SELECT id FROM tenants")]
    for tenant in tenants:
        cursor = None
        while True:
            page = warmup_page(db, tenant, QUALIFIED, cursor=cursor)
            assert not page.blocked
            cursor = page.cursor
            if cursor is None:
                break


def test_requires_qualification_current_proof_and_records_one_activation(
    maintenance_browser, maintenance_db
):
    _, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    disable(db)
    with pytest.raises(AppError) as error:
        activation.activate(db, PROFILE, "test-operation")
    assert error.value.code == "maintenance_qualification_required"
    publish_source(db, tenant, None, None)
    with pytest.raises(AppError) as error:
        activation.activate(db, QUALIFIED, "test-operation")
    assert error.value.code == "maintenance_activation_proof_incomplete"
    warm_all(db)
    transaction = db.transaction

    @contextmanager
    def worker_role(tenant_id=None):
        with transaction(tenant_id) as conn:
            conn.execute("SET LOCAL ROLE appchi_worker")
            yield conn

    # prepare_catalog selects REPEATABLE READ before any domain query; SET ROLE
    # is deliberately not a query or a different statement-order contract.
    db.transaction = worker_role
    try:
        assert activation.activate(db, QUALIFIED, "test-operation")["state"] == "active"
        assert activation.activate(db, QUALIFIED, "test-operation")["state"] == "already_active"
    finally:
        db.transaction = transaction
    with db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM audit_events WHERE kind='maintenance-capability-activated'"
            ).fetchone()["n"]
            == 1
        )
    with pytest.raises(AppError) as error:
        activation.activate(db, {**QUALIFIED, "maintenance_max_inputs": 99}, "another-operation")
    assert error.value.code == "maintenance_profile_conflict"


def test_activation_holds_coordinators_through_proof_and_gate_commit(
    maintenance_browser, maintenance_db, monkeypatch
):
    _, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    disable(db)
    alert, _, _ = publish_source(db, tenant, None, None)
    warm_all(db)
    checked, release, waiting = Event(), Event(), Event()
    original = activation.proof_counts
    pid = []

    def paused(conn, current):
        result = original(conn, current)
        if str(current) == tenant:
            checked.set()
            assert release.wait(5)
        return result

    def writer():
        with db.transaction(tenant) as conn:
            pid.append(conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"])
            waiting.set()
            conn.execute(
                "SELECT tenant_id FROM tenant_work_state WHERE tenant_id=%s FOR UPDATE", (tenant,)
            )
            return conn.execute(
                "SELECT ready FROM capability_gates WHERE capability='maintenance'"
            ).fetchone()["ready"]

    monkeypatch.setattr(activation, "proof_counts", paused)
    with ThreadPoolExecutor(2) as pool:
        switch = pool.submit(activation.activate, db, QUALIFIED, "atomic-test")
        assert checked.wait(3)
        change = pool.submit(writer)
        assert waiting.wait(2)
        deadline = monotonic() + 1
        blocked = False
        while monotonic() < deadline:
            with db.transaction() as conn:
                blocked = bool(
                    conn.execute("SELECT pg_blocking_pids(%s) AS blockers", (pid[0],)).fetchone()[
                        "blockers"
                    ]
                )
            if blocked:
                break
            sleep(0.01)
        release.set()
        assert blocked
        assert switch.result(5)["state"] == "active"
        assert change.result(5)
    # A source unit prepared under the old disabled gate must retry, not publish
    # an unmaintained snapshot after activation.
    with pytest.raises(AppError) as error:
        publish_source(db, tenant, alert, None)
    assert error.value.code == "maintenance_gate_changed"


def test_activation_never_invents_a_missing_creation_baseline(maintenance_browser, maintenance_db):
    _, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    disable(db)
    alert, _, _ = publish_source(db, tenant, None, None)
    warm_all(db)
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE maintenance_alert_inputs SET new_logical_alert=false WHERE tenant_id=%s AND alert_id=%s",
            (tenant, alert["id"]),
        )
    with pytest.raises(AppError) as error:
        activation.activate(db, QUALIFIED, "missing-baseline")
    assert error.value.code == "maintenance_activation_proof_incomplete"
    with db.transaction() as conn:
        assert not conn.execute(
            "SELECT ready FROM capability_gates WHERE capability='maintenance'"
        ).fetchone()["ready"]
