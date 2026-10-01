# ruff: noqa: F811 -- shared isolated database/browser fixtures
"""Durable input coverage prerequisites; no rendered-success assertions."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event
from uuid import UUID, uuid4

import psycopg
import pytest

from test_maintenance_repository import maintenance_db, maintenance_browser  # noqa: F401

from appchi.shared.errors import AppError
from appchi.shared.transactions import coordinator
from appchi.visibility.inputs import (
    InputLimits,
    SamplingPolicy,
    coverage,
    dispose_schedule,
    expand_input,
    expand_next,
    link_application,
    record_input,
    registry_checkpoint,
    seal_expiry_boundary,
)
from appchi.visibility.registry import RegistryLimits
from appchi.visibility.routes import router

pytestmark = pytest.mark.integration
LIMITS = InputLimits(100, 100, 100, 100000, 2000, 1)
POLICY = SamplingPolicy("synthetic-policy", True, "test")


@pytest.fixture
def inputs(maintenance_browser, maintenance_db):
    client, tenant, _, app = maintenance_browser
    app.include_router(router)
    app.state.visibility_registry_limits = RegistryLimits(30, 8, 8, 100, 20000, 65536)
    return maintenance_db[0], UUID(tenant), client


def register(client, tenant):
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
    return response.json()["data"]


def seed(conn, tenant, **changes):
    _, now = coordinator(conn, tenant)
    params = dict(
        tenant=tenant,
        input_id=uuid4(),
        kind="incoming",
        started_at=now,
        original_order=1,
        registry_checkpoint=registry_checkpoint(conn, tenant),
        policy=POLICY,
        limits=LIMITS,
    )
    params.update(changes)
    return record_input(conn, **params)


def rejected(conn, tenant, input_id, **changes):
    params = dict(
        tenant=tenant,
        input_id=input_id,
        application_id=input_id,
        alert_id=None,
        before=None,
        after=None,
        projection_seq=None,
        projection_version=None,
        disposition="rejected",
        error_code="source_invalid",
    )
    params.update(changes)
    return link_application(conn, **params)


def test_seed_is_atomic_replay_safe_and_keeps_original_policy(inputs):
    db, tenant, _ = inputs
    input_id = uuid4()
    with pytest.raises(RuntimeError), db.transaction(tenant) as conn:
        seed(conn, tenant, input_id=input_id)
        raise RuntimeError("Simulated failed admission commit")
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM visibility_inputs WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )
        first = seed(conn, tenant, input_id=input_id)["input"]
        replay = record_input(
            conn,
            tenant=tenant,
            input_id=input_id,
            kind="incoming",
            started_at=first["started_at"],
            original_order=1,
            registry_checkpoint=first["seed_checkpoint"],
            policy=POLICY,
            limits=replace(LIMITS, max_tenant_inputs=200),
        )
        assert replay["status"] == "existing"
        assert replay["input"]["allocations"]["max_tenant_inputs"] == 100
        assert replay["input"]["deadline_at"] == first["started_at"] + timedelta(seconds=5)
    with (
        pytest.raises(AppError, match="visibility_input_conflict"),
        db.transaction(tenant) as conn,
    ):
        record_input(
            conn,
            tenant=tenant,
            input_id=input_id,
            kind="incoming",
            started_at=first["started_at"],
            original_order=2,
            registry_checkpoint=first["seed_checkpoint"],
            policy=POLICY,
            limits=LIMITS,
        )
    with pytest.raises(psycopg.errors.RaiseException), db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE visibility_inputs SET started_at=started_at-interval '1 second',deadline_at=deadline_at-interval '1 second' WHERE tenant_id=%s",
            (tenant,),
        )


def test_seed_capacity_is_explicit_gap_and_concurrent_admission_cannot_overfill(inputs):
    db, tenant, _ = inputs
    limits = replace(LIMITS, max_tenant_inputs=1)

    def admit(_):
        with db.transaction(tenant) as conn:
            return seed(conn, tenant, limits=limits)["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(admit, range(2))) == ["capacity_gap", "recorded"]
    with db.transaction(tenant) as conn:
        report = coverage(conn, tenant)
        assert report["seeds"] == 1 and report["seed_gap_attempts"] == 1
        assert report["eligibility"] == "unclassified"
        assert report["render_proof"] == "unavailable"


def test_bounded_expansion_resumes_original_view_epoch_after_query_change(inputs):
    db, tenant, client = inputs
    views = [register(client, tenant) for _ in range(3)]
    with db.transaction(tenant) as conn:
        original = seed(conn, tenant)["input"]
    response = client.patch(
        f"/api/tenants/{tenant}/view-subscriptions/{views[0]['id']}",
        json={
            "command_id": str(uuid4()),
            "expected_revision": "1",
            "state": "foreground",
            "view": {"surface": "alert_feed", "expression": "false"},
        },
    )
    assert response.status_code == 200 and response.json()["data"]["view_epoch"] == "2"
    with pytest.raises(RuntimeError), db.transaction(tenant) as conn:
        assert expand_input(conn, tenant=tenant, input_id=original["id"])["enumerated"] == 1
        raise RuntimeError("Crash before expansion commit")
    for index in range(3):
        with db.transaction(tenant) as conn:
            result = expand_input(conn, tenant=tenant, input_id=original["id"])
            assert result["selected"] == 1
            assert result["status"] == ("expanded" if index == 2 else "pending")
    with db.transaction(tenant) as conn:
        assert expand_input(conn, tenant=tenant, input_id=original["id"])["selected"] == 0
        candidates = conn.execute(
            "SELECT * FROM visibility_candidates WHERE tenant_id=%s", (tenant,)
        ).fetchall()
        assert len(candidates) == 3
        assert all(
            row["view_epoch"] == 1 and row["eligibility"] == "unclassified" for row in candidates
        )
        assert {row["query_digest"] for row in candidates} == {
            view["query_digest"] for view in views
        }
        assert all(row["deadline_at"] == original["deadline_at"] for row in candidates)


def test_expiry_uses_original_end_even_when_sealed_after_background_control(inputs):
    db, tenant, client = inputs
    view = register(client, tenant)
    with db.transaction(tenant) as conn:
        _, end = coordinator(conn, tenant)
    response = client.patch(
        f"/api/tenants/{tenant}/view-subscriptions/{view['id']}",
        json={"command_id": str(uuid4()), "expected_revision": "1", "state": "background"},
    )
    assert response.status_code == 200
    with db.transaction(tenant) as conn:
        original = seed(
            conn,
            tenant,
            kind="maintenance_expiry",
            started_at=end,
            original_order=0,
            schedule={"window_id": str(uuid4()), "revision": 1, "generation": 1},
        )["input"]
        assert original["cohort_checkpoint"] is None
        assert (
            expand_input(conn, tenant=tenant, input_id=original["id"])["status"]
            == "boundary_unsealed"
        )
        sealed = seal_expiry_boundary(conn, tenant=tenant, input_id=original["id"])
        assert sealed["cohort_checkpoint"] == 2
        assert expand_input(conn, tenant=tenant, input_id=original["id"])["selected"] == 1
        candidate = conn.execute(
            "SELECT * FROM visibility_candidates WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert candidate["control_checkpoint"] == 1
        assert candidate["coverage_reason"] == "proof_pending"
        assert candidate["deadline_at"] == end + timedelta(seconds=5)


def test_schedule_cancellation_is_proved_idempotent_and_cannot_hide_due_expiry(inputs):
    db, tenant, _ = inputs
    with db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        original = seed(
            conn,
            tenant,
            kind="maintenance_expiry",
            started_at=now + timedelta(days=1),
            original_order=0,
            schedule={"window_id": str(uuid4()), "revision": 1, "generation": 1},
        )["input"]
        assert (
            seal_expiry_boundary(conn, tenant=tenant, input_id=original["id"])["cohort_checkpoint"]
            is None
        )
        args = dict(
            tenant=tenant,
            input_id=original["id"],
            generation=1,
            disposition="cancelled",
            evidence_reference="synthetic-control:1",
        )
        disposed = dispose_schedule(conn, **args)
        assert dispose_schedule(conn, **args)["disposition_proof"] == disposed["disposition_proof"]
        due = seed(
            conn,
            tenant,
            kind="maintenance_expiry",
            started_at=now - timedelta(seconds=30),
            original_order=0,
            schedule={"window_id": str(uuid4()), "revision": 1, "generation": 1},
        )["input"]
    with (
        pytest.raises(AppError, match="visibility_boundary_passed"),
        db.transaction(tenant) as conn,
    ):
        dispose_schedule(conn, **{**args, "input_id": due["id"]})
    with db.transaction(tenant) as conn:
        assert coverage(conn, tenant)["overdue_unclassified"] == 1


def test_rejected_application_is_replay_safe_without_fabricated_alert_proof(inputs):
    db, tenant, _ = inputs
    with db.transaction(tenant) as conn:
        original = seed(conn, tenant, limits=replace(LIMITS, max_tenant_applications=1))["input"]
        assert rejected(conn, tenant, original["id"])["status"] == "recorded"
        assert rejected(conn, tenant, original["id"])["status"] == "existing"
        assert (
            rejected(conn, tenant, original["id"], application_id=uuid4())["status"]
            == "capacity_gap"
        )
        row = conn.execute(
            "SELECT * FROM visibility_applications WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert row["alert_id"] is None and row["after_digest"] is None
        assert row["projection_seq"] is None and row["error_code"] == "source_invalid"
        assert (
            conn.execute(
                "SELECT application_gap FROM visibility_inputs WHERE tenant_id=%s", (tenant,)
            ).fetchone()["application_gap"]
            == "application_allocation_exhausted"
        )
    with (
        pytest.raises(AppError, match="visibility_application_conflict"),
        db.transaction(tenant) as conn,
    ):
        rejected(conn, tenant, original["id"], error_code="different_error")


def test_applied_link_keeps_only_digests_and_nonowning_product_locators(inputs):
    db, tenant, _ = inputs
    alert_id, event_id = uuid4(), uuid4()
    before = {"id": str(alert_id), "message": "Private source content before"}
    after = {"id": str(alert_id), "message": "Private source content after"}
    with db.transaction(tenant) as conn:
        conn.execute("INSERT INTO alert_identities(id,tenant_id) VALUES(%s,%s)", (alert_id, tenant))
        original = seed(conn, tenant)["input"]
        args = dict(
            tenant=tenant,
            input_id=original["id"],
            application_id=uuid4(),
            alert_id=alert_id,
            before=before,
            after=after,
            projection_seq=7,
            projection_version=2,
            disposition="applied",
            journal_order=6,
            source_event_id=event_id,
        )
        assert link_application(conn, **args)["status"] == "recorded"
        assert link_application(conn, **args)["status"] == "existing"
        row = conn.execute(
            "SELECT * FROM visibility_applications WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert "Private source content" not in str(row)
        assert len(row["before_digest"]) == len(row["after_digest"]) == 32
        assert row["before_digest"] != row["after_digest"]
        assert row["source_event_id"] == event_id and row["journal_order"] == 6
        # There is no owning FK to source/history or current-alert content.
        # Retiring the permanent identity leaves the operational locator intact.
        conn.execute(
            "UPDATE alert_identities SET retired_at=clock_timestamp(),final_version=2 WHERE id=%s",
            (alert_id,),
        )
        assert (
            link_application(conn, **{**args, "input_id": uuid4()})["status"] == "seed_unavailable"
        )
    with (
        pytest.raises(AppError, match="visibility_application_conflict"),
        db.transaction(tenant) as conn,
    ):
        link_application(conn, **{**args, "after": {**after, "message": "changed proof"}})


def test_capacity_gap_does_not_starve_other_seeds_and_never_becomes_ineligible(inputs):
    db, tenant, client = inputs
    register(client, tenant)
    register(client, tenant)
    with db.transaction(tenant) as conn:
        first = seed(conn, tenant, limits=replace(LIMITS, max_tenant_candidates=1))["input"]
        second = seed(conn, tenant)["input"]
        assert expand_input(conn, tenant=tenant, input_id=first["id"])["selected"] == 1
        blocked = expand_input(conn, tenant=tenant, input_id=first["id"])
        assert blocked["gap"] == "candidate_allocation_exhausted"
        assert expand_next(conn, tenant=tenant)["input_id"] == second["id"]
        row = conn.execute(
            "SELECT * FROM visibility_inputs WHERE tenant_id=%s AND id=%s", (tenant, first["id"])
        ).fetchone()
        assert row["expansion_state"] == "pending" and row["enumerated"] == 1


def test_expansion_obeys_coordinator_before_seed_lock(inputs):
    db, tenant, _ = inputs
    with db.transaction(tenant) as conn:
        original = seed(conn, tenant)["input"]
    attempted = Event()

    def expand():
        with db.transaction(tenant) as conn:
            conn.execute("SET LOCAL lock_timeout='3s'")

            class Probe:
                def execute(self, query, params=()):
                    if query == "SELECT * FROM tenant_work_state WHERE tenant_id=%s FOR UPDATE":
                        attempted.set()
                    return conn.execute(query, params)

            return expand_input(Probe(), tenant=tenant, input_id=original["id"])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.transaction(tenant) as conn:
            coordinator(conn, tenant)
            result = pool.submit(expand)
            assert attempted.wait(1)
            # If expansion acquired the seed first, linking while this transaction
            # owns the coordinator forms a deadlock. Both operations must finish.
            rejected(conn, tenant, original["id"])
        assert result.result(timeout=5)["status"] == "expanded"


def test_restricted_roles_cannot_rewrite_original_proof_or_cross_tenant(inputs):
    db, tenant, _ = inputs
    with db.transaction(tenant) as conn:
        conn.execute("SET LOCAL ROLE appchi_api")
        original = seed(conn, tenant)["input"]
        rejected(conn, tenant, original["id"])
    with db.transaction(uuid4()) as conn:
        conn.execute("SET LOCAL ROLE appchi_api")
        assert (
            conn.execute("SELECT * FROM visibility_inputs WHERE tenant_id=%s", (tenant,)).fetchone()
            is None
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction(tenant) as conn:
        conn.execute("SET LOCAL ROLE appchi_api")
        conn.execute("DELETE FROM visibility_inputs WHERE tenant_id=%s", (tenant,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction(tenant) as conn:
        conn.execute("SET LOCAL ROLE appchi_worker")
        conn.execute(
            "UPDATE visibility_applications SET error_code='forged' WHERE tenant_id=%s", (tenant,)
        )
    with db.transaction(tenant) as conn:
        conn.execute("SET LOCAL ROLE appchi_worker")
        assert expand_input(conn, tenant=tenant, input_id=original["id"])["status"] == "expanded"


def test_worker_skips_busy_tenant_before_acquiring_any_seed(inputs):
    db, tenant, _ = inputs
    other = uuid4()
    with db.transaction() as conn:
        seed(conn, tenant)
        conn.execute(
            "INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
            (other, str(other), str(other)),
        )
        conn.execute("INSERT INTO tenant_work_state(tenant_id) VALUES(%s)", (other,))
        second = seed(conn, other)["input"]

    def work():
        with db.transaction() as conn:
            conn.execute("SET LOCAL ROLE appchi_worker")
            conn.execute("SET LOCAL lock_timeout='1s'")
            return expand_next(conn)

    # Isolate this assertion from prior test inputs by selecting an otherwise
    # empty pair of tenants through an equivalent explicit candidate filter.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE visibility_inputs SET expansion_state='expanded' WHERE tenant_id NOT IN (%s,%s)",
            (tenant, other),
        )
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.transaction(tenant) as conn:
            coordinator(conn, tenant)
            result = pool.submit(work).result(timeout=3)
            assert result["input_id"] == second["id"] and result["status"] == "expanded"


def test_rejected_application_database_constraint_requires_error_code(inputs):
    db, tenant, _ = inputs
    with db.transaction(tenant) as conn:
        original = seed(conn, tenant)["input"]
    # Bypass Python validation as the producer role to verify SQL's NULL handling.
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction(tenant) as conn:
        conn.execute("SET LOCAL ROLE appchi_api")
        conn.execute(
            """INSERT INTO visibility_applications
            (tenant_id,input_id,id,disposition,error_code,fingerprint,byte_charge)
            VALUES(%s,%s,%s,'rejected',NULL,%s,100)""",
            (tenant, original["id"], uuid4(), b"x" * 32),
        )


def test_historical_other_deployment_view_is_retained_as_explicit_coverage_gap(inputs):
    db, tenant, client = inputs
    view = register(client, tenant)
    with db.transaction(tenant) as conn:
        original = seed(conn, tenant, policy=replace(POLICY, deployment_epoch="next-deployment"))[
            "input"
        ]
        assert expand_input(conn, tenant=tenant, input_id=original["id"])["selected"] == 1
        candidate = conn.execute(
            "SELECT * FROM visibility_candidates WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert str(candidate["view_id"]) == view["id"]
        assert candidate["eligibility"] == "unclassified"
        assert candidate["coverage_reason"] == "deployment_epoch_mismatch"
        assert candidate["deadline_at"] == original["deadline_at"]
