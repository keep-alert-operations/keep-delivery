# ruff: noqa: F811 -- pytest fixture import
"""Actual PostgreSQL lease, authority and durable label-journal fences."""

import pytest
from appchi.operators.executor_ledger import PostgresLedger
from appchi.operators.worker import claim, work_input
from appchi.shared.errors import AppError
from test_operator_repository import operators, create, due  # noqa: F401
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


def attempt(operators, real_db, cipher):
    op, job, _ = create(operators)
    due(real_db, job["id"])
    job, op = claim(real_db)
    work = work_input(real_db, cipher, job, op)
    return work, PostgresLedger(real_db).begin(work, "profile-digest")


def test_profile_and_label_journal_persist_across_attempts(operators, real_db, cipher):
    work, ledger = attempt(operators, real_db, cipher)
    edit = {
        "uid": "rule1",
        "old_key": "Appchi - Old",
        "old_value": "true",
        "new_key": "Appchi - New",
        "new_value": None,
    }
    ledger.record(edit)
    assert not ledger.owns("grafana_receiver", "receiver1")
    ledger.claim_resource("grafana_receiver", "receiver1")
    again = PostgresLedger(real_db).begin(work, "profile-digest")
    assert again.edits == [edit] and work.candidate_id in again.valid_ids
    assert again.owns("grafana_receiver", "receiver1")
    with pytest.raises(AppError, match="remote_ownership_conflict"):
        again.claim_resource("grafana_receiver", "different-receiver")
    with real_db.transaction() as conn:
        row = conn.execute(
            "SELECT progress FROM operator_operations WHERE id=%s", (work.operation_id,)
        ).fetchone()
    assert work.secret not in str(row)
    with pytest.raises(AppError, match="operator_capabilities_unverified"):
        PostgresLedger(real_db).begin(work, "different-profile")


@pytest.mark.parametrize("change", ["generation", "revision", "lease", "global_lease"])
def test_generation_revision_and_both_leases_fence_remote_work(operators, real_db, cipher, change):
    work, ledger = attempt(operators, real_db, cipher)
    with real_db.transaction() as conn:
        if change == "generation":
            conn.execute(
                "UPDATE operators SET generation=generation+1 WHERE id=%s", (work.operator_id,)
            )
        elif change == "revision":
            conn.execute(
                "UPDATE operators SET revision=revision+1 WHERE id=%s", (work.operator_id,)
            )
        elif change == "lease":
            conn.execute(
                "UPDATE operator_operations SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
                (work.operation_id,),
            )
        else:
            conn.execute(
                "UPDATE operator_routing_guard SET lease_until=clock_timestamp()-interval '1 second' WHERE scope='grafana'"
            )
    with pytest.raises(AppError, match="stale_provisioning_generation"):
        ledger.check()
    with pytest.raises(AppError, match="stale_provisioning_generation"):
        ledger.claim_resource("grafana_receiver", "receiver1")


@pytest.mark.parametrize(
    "delay,expected", [(1200, 1200), (999999, 3600), (0, 5), (float("nan"), 5)]
)
def test_retry_after_is_persisted_without_shortening_backoff(
    operators, real_db, cipher, delay, expected
):
    from appchi.operators.worker import failed

    op, job, _ = create(operators)
    due(real_db, job["id"])
    claimed, _ = claim(real_db)
    failed(
        real_db,
        claimed,
        AppError("remote_unavailable", "Unavailable", 503, fields={"retry_after_seconds": delay}),
    )
    with real_db.transaction() as conn:
        state = conn.execute(
            "SELECT state,progress,EXTRACT(epoch FROM next_attempt_at-clock_timestamp()) AS delay FROM operator_operations WHERE id=%s",
            (job["id"],),
        ).fetchone()
        credential = conn.execute(
            "SELECT state FROM operator_credentials WHERE operator_id=%s", (op["id"],)
        ).fetchone()
    assert expected - 2 <= state["delay"] <= expected + 1
    assert state["state"] == "pending" and state["progress"]["uncertain"] is True
    assert credential["state"] == "prepared"
