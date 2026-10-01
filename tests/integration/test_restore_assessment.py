"""Read-only, fail-closed assessment of actual PostgreSQL restore staging."""
# ruff: noqa: F811

from contextlib import contextmanager
from dataclasses import asdict
import json
from uuid import uuid4

import psycopg
import pytest

from appchi.recovery.assessment import assess_restore
from appchi.recovery.evidence import (
    Evidence,
    EvidenceBinding,
    CheckpointPage,
    checkpoint_page_digest,
    PAGED_CHECKPOINT_VERSION,
)
from appchi.recovery.restore import begin_restore, collect_restore
from appchi.shared.errors import AppError
from test_maintenance_repository import maintenance_db  # noqa: F401
from test_restore_fencing import recovery, inputs  # noqa: F401

pytestmark = pytest.mark.integration


class Store:
    binding = EvidenceBinding(
        "https://evidence.invalid",
        "/append",
        "/checkpoint",
        "private-namespace-marker",
        "independent-fixture",
        "durable-fixture",
        paged_checkpoint_path="/pages",
        paged_checkpoint_version=PAGED_CHECKPOINT_VERSION,
    )

    def __init__(self, records):
        self.records = tuple(records)

    def checkpoint_page(self, **request):
        body = dict(
            version=PAGED_CHECKPOINT_VERSION,
            namespace=self.binding.namespace,
            after=None,
            checkpoint="private-checkpoint-marker",
            request_cursor=None,
            total_records=str(len(self.records)),
            records=[
                {**asdict(record), "revision": str(record.revision)} for record in self.records
            ],
            next_cursor=None,
            complete=True,
        )
        return CheckpointPage(
            self.binding.namespace,
            None,
            body["checkpoint"],
            None,
            len(self.records),
            self.records,
            None,
            True,
            checkpoint_page_digest(body),
        )


def collected(db, records=()):
    body = inputs()
    begin_restore(db, **body)
    collect_restore(db, Store(records), body["run_id"])
    return body


def assess(db, body):
    return assess_restore(db, body["run_id"], deployment_epoch=body["deployment_epoch"])


def evidence(family="alert", state="final", **overrides):
    return Evidence(
        **{
            "id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "resource_kind": family,
            "resource_id": str(uuid4()),
            "revision": 1,
            "state": state,
            "digest": "a" * 64,
            **overrides,
        }
    )


def test_empty_complete_collection_never_implies_family_coverage(recovery):
    db, _ = recovery
    body = collected(db)
    result = assess(db, body)
    assert not result.reopen_permitted and result.collected_records == 0
    assert {
        "authority_baseline_unestablished",
        "producer_coverage_unestablished",
        "kafka_delivery_and_retired_key_baseline_unverified",
    } <= set(result.blockers)
    assert all(not item.ready and item.blockers for item in result.capabilities)
    assert all(
        "kafka_delivery_and_retired_key_baseline_unverified" in item.blockers
        for item in result.capabilities
    )
    assert all(
        item.records == 0 and "producer_coverage_unestablished" in item.blockers
        for item in result.families
    )
    with db.transaction() as conn:
        assert conn.execute("SELECT fenced FROM restore_control").fetchone()["fenced"]
        assert not conn.execute("SELECT 1 FROM capability_gates WHERE ready").fetchone()


@pytest.mark.parametrize("family", ["permission", "credential"])
def test_unhandled_authority_family_cannot_be_promoted_by_final_or_large_revision(recovery, family):
    db, _ = recovery
    result = assess(db, collected(db, [evidence(family, revision=2**63 - 1)]))
    item = next(item for item in result.families if item.family == family)
    assert item.final_records == 1 and "authority_producer_unimplemented" in item.blockers
    assert not result.reopen_permitted


def test_intent_matching_is_scoped_and_never_assumes_attempt_linkage(recovery):
    db, _ = recovery
    intent = evidence(state="intent")
    matched = evidence(state="intent")
    final = evidence(**{**asdict(matched), "id": str(uuid4()), "state": "final"})
    wrong_tenant = evidence(
        **{**asdict(intent), "id": str(uuid4()), "tenant_id": str(uuid4()), "state": "final"}
    )
    result = assess(db, collected(db, [intent, matched, final, wrong_tenant]))
    item = next(item for item in result.families if item.family == "alert")
    assert item.unresolved_intent_groups == 1
    assert {"unresolved_intent", "attempt_linkage_unverified"} <= set(item.blockers)
    assert item.final_records == 2


def test_conflicting_final_and_cancel_remain_a_blocker(recovery):
    db, _ = recovery
    final = evidence()
    cancel = evidence(**{**asdict(final), "id": str(uuid4()), "state": "cancel"})
    result = assess(db, collected(db, [final, cancel]))
    item = next(item for item in result.families if item.family == "alert")
    assert item.conflicting_terminal_groups == 1
    assert "conflicting_terminal_evidence" in item.blockers


@pytest.mark.parametrize("state", ["fenced", "collecting"])
def test_incomplete_collection_is_rejected(recovery, state):
    db, _ = recovery
    body = inputs()
    begin_restore(db, **body)
    with db.transaction() as conn:
        conn.execute("UPDATE restore_runs SET state=%s WHERE id=%s", (state, body["run_id"]))
    with pytest.raises(AppError) as error:
        assess(db, body)
    assert error.value.code == "restore_collection_incomplete"


@pytest.mark.parametrize("change", ["expected_epoch", "control_epoch", "current_run", "unfenced"])
def test_assessment_rejects_stale_deployment_or_restore_authority(recovery, change):
    db, _ = recovery
    body = collected(db)
    with db.transaction() as conn:
        if change == "control_epoch":
            conn.execute("UPDATE restore_control SET deployment_epoch=%s", (str(uuid4()),))
        elif change == "current_run":
            conn.execute("UPDATE restore_control SET run_id=NULL")
        elif change == "unfenced":
            conn.execute("UPDATE restore_control SET fenced=false")
    if change == "expected_epoch":
        body["deployment_epoch"] = str(uuid4())
    with pytest.raises(AppError) as error:
        assess(db, body)
    assert error.value.code in {"deployment_epoch_conflict", "restore_attempt_superseded"}


@pytest.mark.parametrize("change", ["missing_row", "ordinal_gap", "missing_digest"])
def test_inconsistent_collection_is_rejected(recovery, change):
    db, _ = recovery
    body = collected(db, [evidence()])
    with db.transaction() as conn:
        if change == "missing_row":
            conn.execute("DELETE FROM restore_evidence_stage WHERE run_id=%s", (body["run_id"],))
        elif change == "ordinal_gap":
            conn.execute(
                "UPDATE restore_evidence_stage SET ordinal=2 WHERE run_id=%s", (body["run_id"],)
            )
        else:
            conn.execute(
                "UPDATE restore_runs SET collection_digest=NULL WHERE id=%s", (body["run_id"],)
            )
    with pytest.raises(AppError) as error:
        assess(db, body)
    assert error.value.code == "restore_collection_invalid"


def test_unknown_staged_family_is_bounded_and_not_echoed(recovery):
    db, _ = recovery
    body = collected(db, [evidence()])
    with db.transaction() as conn:
        conn.execute(
            "UPDATE restore_evidence_stage SET resource_kind='untrusted-private-marker' WHERE run_id=%s",
            (body["run_id"],),
        )
    result = assess(db, body)
    assert result.unsupported_records == 1
    assert "unsupported_evidence_family" in result.blockers
    assert "untrusted-private-marker" not in json.dumps(asdict(result))


def test_assessment_has_no_writes_or_content_and_runs_as_recovery_role(recovery):
    db, _ = recovery
    record = evidence()
    body = collected(db, [record])

    def snapshot():
        with db.transaction() as conn:
            return tuple(
                conn.execute(f"SELECT * FROM {table}").fetchall()
                for table in (
                    "restore_runs",
                    "restore_control",
                    "restore_evidence_stage",
                    "capability_gates",
                )
            )

    before = snapshot()
    original = db.transaction

    @contextmanager
    def recovery_role(tenant=None):
        with original(tenant) as conn:
            conn.execute("SET LOCAL ROLE appchi_recovery")
            yield conn

    db.transaction = recovery_role
    try:
        result = assess(db, body)
    finally:
        db.transaction = original
    assert snapshot() == before
    output = json.dumps(asdict(result))
    assert all(
        value not in output
        for value in (
            record.resource_id,
            record.tenant_id,
            record.digest,
            "private-checkpoint-marker",
            "private-namespace-marker",
        )
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction() as conn:
        conn.execute("SET LOCAL ROLE appchi_api")
        conn.execute("SELECT * FROM restore_evidence_stage")
