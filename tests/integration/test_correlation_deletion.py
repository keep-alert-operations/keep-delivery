"""Real SQL retirement with controlled independent-store acknowledgements.

This exercises application failure boundaries, not external store qualification.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from keep.correlation.deletion import resume_delete
from keep.recovery.evidence import DurableAck
from keep.shared.errors import AppError, unavailable
from test_correlation_configuration import create, definition, edit, rules  # noqa: F401
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


class Store:
    binding = SimpleNamespace(namespace="synthetic-independent-store")

    def __init__(self):
        self.events = []
        self.fail = None
        self.bad = None
        self.on_intent = None
        self.on_final = None

    def append(self, evidence):
        self.events.append(evidence)
        if evidence.state == "intent" and self.on_intent:
            callback, self.on_intent = self.on_intent, None
            callback()
        if evidence.state == "final" and self.on_final:
            callback, self.on_final = self.on_final, None
            callback()
        if self.fail == evidence.state:
            raise unavailable("controlled_store_unavailable")
        return DurableAck(
            evidence.id,
            "wrong" if self.bad == evidence.state else self.binding.namespace,
            "checkpoint",
            evidence.digest,
        )

    def close(self):
        pass


@pytest.fixture
def protected(request, real_db):
    fixture = request.getfixturevalue("rules")
    client, *_ = fixture
    store = Store()
    client.app.state.recovery_store = store
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
    try:
        yield fixture, store
    finally:
        client.app.state.recovery_store = None
        with real_db.transaction() as conn:
            conn.execute("UPDATE capability_gates SET ready=false WHERE capability='recovery'")


def remove(fixture, row, command=None, expected=None):
    client, _, _, base = fixture
    return client.delete(
        base + "/" + row["id"],
        headers={
            "Idempotency-Key": command or str(uuid4()),
            "If-Match": '"' + (expected or row["revision"]) + '"',
        },
    )


def test_delete_protects_final_revision_replay_history_and_name_reuse(protected, real_db):
    fixture, store = protected
    client, _, tenant, base = fixture
    row, _ = create(fixture)
    command = str(uuid4())
    response = remove(fixture, row, command)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["revision"] == "2"
    assert [e.state for e in store.events] == ["intent", "final"]
    assert {e.resource_kind for e in store.events} == {"correlation"}
    assert remove(fixture, row, command).json() == response.json()
    assert len(store.events) == 2
    assert client.get(base + "/" + row["id"]).status_code == 404
    assert client.get(base + "/" + row["id"] + "/history/2").json()["data"]["definition"][
        "deleted_at"
    ]
    operation = client.get(base + "/deletions/" + response.json()["data"]["operation_id"])
    assert operation.status_code == 200 and operation.json()["data"]["state"] == "final_protected"
    replacement, _ = create(fixture)
    assert replacement["id"] != row["id"]
    with real_db.transaction() as conn:
        old = conn.execute("SELECT * FROM correlation_rules WHERE id=%s", (row["id"],)).fetchone()
        assert old["epoch"] == 2 and old["enabled"] is False
        assert (
            conn.execute(
                "SELECT final_revision FROM correlation_rule_identities WHERE id=%s", (row["id"],)
            ).fetchone()["final_revision"]
            == 2
        )
    assert remove(fixture, row, command, expected="2").status_code == 409


def test_final_failure_keeps_durable_deleted_state_and_retry_recovers(protected, real_db):
    fixture, store = protected
    client, actor, tenant, base = fixture
    row, _ = create(fixture)
    command = str(uuid4())
    store.fail = "final"
    failed = remove(fixture, row, command)
    assert failed.status_code == 503
    with real_db.transaction() as conn:
        job = conn.execute(
            "SELECT * FROM correlation_delete_jobs WHERE rule_id=%s", (row["id"],)
        ).fetchone()
        assert job["state"] == "deleted"
        assert not conn.execute(
            "SELECT 1 FROM command_receipts WHERE scope=%s AND command_id=%s",
            (job["scope"], command),
        ).fetchone()
    store.fail = None
    result = remove(fixture, row, command)
    assert result.status_code == 200, result.text
    finals = [e for e in store.events if e.state == "final"]
    assert len(finals) == 2 and finals[0] == finals[1]
    with real_db.transaction() as conn:
        conn.execute(
            "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, actor)
        )
    assert remove(fixture, row, command).status_code == 404


def test_bad_intent_ack_cannot_mutate_rule(protected, real_db):
    fixture, store = protected
    row, _ = create(fixture)
    command = str(uuid4())
    store.bad = "intent"
    assert remove(fixture, row, command).status_code == 503
    with real_db.transaction() as conn:
        assert conn.execute(
            "SELECT revision,deleted_at FROM correlation_rules WHERE id=%s", (row["id"],)
        ).fetchone() == {"revision": 1, "deleted_at": None}
    store.bad = None
    assert remove(fixture, row, command).status_code == 200


def test_revision_race_requires_verified_cancel_before_new_attempt(protected, real_db):
    fixture, store = protected
    _, _, tenant, _ = fixture
    row, body = create(fixture)
    change = {key: body[key] for key in definition()}
    change["description"] = "Changed while intent was protected"
    store.on_intent = lambda: edit(fixture, row, change)
    store.bad = "cancel"
    assert remove(fixture, row).status_code == 503
    with real_db.transaction() as conn:
        job = conn.execute(
            "SELECT * FROM correlation_delete_jobs WHERE rule_id=%s", (row["id"],)
        ).fetchone()
        assert job["state"] == "cancelled" and job["cancel_ack"] is None
        assert (
            conn.execute(
                "SELECT deleted_at FROM correlation_rules WHERE id=%s", (row["id"],)
            ).fetchone()["deleted_at"]
            is None
        )
    assert remove(fixture, row, expected="2").status_code == 409
    store.bad = None
    with pytest.raises(AppError) as error:
        resume_delete(real_db, store, tenant, job["id"])
    assert error.value.code == "revision_conflict"
    assert remove(fixture, row, expected="2").status_code == 200
    intents = [e for e in store.events if e.state == "intent"]
    assert len({e.digest for e in intents}) == 2


def test_authorization_loss_during_intent_cancels_before_retirement(protected, real_db):
    fixture, store = protected
    _, actor, tenant, _ = fixture
    row, _ = create(fixture)

    def revoke():
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
                (tenant, actor),
            )

    store.on_intent = revoke
    response = remove(fixture, row)
    assert response.status_code == 403, response.text
    assert [e.state for e in store.events] == ["intent", "cancel"]
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT deleted_at FROM correlation_rules WHERE id=%s", (row["id"],)
            ).fetchone()["deleted_at"]
            is None
        )


def test_worker_defers_prepared_delete_while_identity_recovery_is_gated(protected, real_db):
    fixture, store = protected
    _, _, tenant, _ = fixture
    row, _ = create(fixture)
    store.fail = "intent"
    assert remove(fixture, row).status_code == 503
    with real_db.transaction() as conn:
        job = conn.execute(
            "SELECT * FROM correlation_delete_jobs WHERE rule_id=%s", (row["id"],)
        ).fetchone()
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='human_auth'")
    store.fail = None
    try:
        with pytest.raises(AppError) as failure:
            resume_delete(real_db, store, tenant, job["id"])
        assert failure.value.status == 503
        with real_db.transaction() as conn:
            assert (
                conn.execute(
                    "SELECT state FROM correlation_delete_jobs WHERE id=%s", (job["id"],)
                ).fetchone()["state"]
                == "prepared"
            )
    finally:
        with real_db.transaction() as conn:
            conn.execute("UPDATE capability_gates SET ready=true WHERE capability='human_auth'")
    assert resume_delete(real_db, store, tenant, job["id"])["data"]["deleted"]


def test_api_rechecks_permission_after_final_external_ack(protected, real_db):
    fixture, store = protected
    _, actor, tenant, _ = fixture
    row, _ = create(fixture)

    def revoke():
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
                (tenant, actor),
            )

    store.on_final = revoke
    assert remove(fixture, row).status_code == 403
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT state FROM correlation_delete_jobs WHERE rule_id=%s", (row["id"],)
            ).fetchone()["state"]
            == "final_protected"
        )
