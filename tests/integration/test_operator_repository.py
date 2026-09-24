# ruff: noqa: F811 -- pytest fixtures imported by name
"""Real PostgreSQL workflow tests; verified remote facts are isolated adapter fixtures."""

import json
from uuid import uuid4, uuid5, NAMESPACE_URL
import pytest
from appchi.operators.routes import router
from appchi.operators.worker import run_one, VerifiedResult
from appchi.operators.remote import CapabilityManifest, REQUIRED_CAPABILITIES
from appchi.operators.repository import routing
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


class VerifiedFixture:
    manifest = CapabilityManifest(
        "synthetic-fixture",
        "fixture",
        "fixture",
        "fixture",
        "test-evidence",
        REQUIRED_CAPABILITIES,
        "controlled_writer",
        "test-writer-evidence",
        {},
    )
    callback = None

    def execute(self, work):
        self.last_work = work
        assert work.secret is None or "vena_" not in repr(work)
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        return VerifiedResult(
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            str(uuid5(NAMESPACE_URL, work.operator_id)),
            work.candidate_id,
        )


@pytest.fixture
def operators(browser, real_db, tenant):
    client, actor = browser
    if not any(
        getattr(r, "path", "") == "/api/tenants/{tenant_id}/operators" for r in client.app.routes
    ):
        client.app.include_router(router)
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (actor,))
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='v2_ingress'")
    return client, f"/api/tenants/{tenant}/operators", tenant


def command(revision):
    return {"command_id": str(uuid4()), "expected_revision": str(revision)}


def create(operators, name=None):
    client, base, _ = operators
    body = {"command_id": str(uuid4()), "name": name or "operator-" + str(uuid4())}
    response = client.post(base, json=body)
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    return data["operator"], data["operation"], body


def ready(operators, db, cipher):
    op, job, _ = create(operators)
    due(db, job["id"])
    assert run_one(db, cipher, VerifiedFixture())
    return operators[0].get(operators[1] + "/" + op["id"]).json()["data"]


def due(db, identifier):
    # Each test focuses the isolated fixture's durable job, avoiding another
    # test's intentionally pending work when using the same disposable database.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE operator_operations SET next_attempt_at=clock_timestamp()+interval '1 hour' WHERE completed_at IS NULL"
        )
        conn.execute(
            "UPDATE operator_operations SET next_attempt_at=clock_timestamp() WHERE id=%s",
            (identifier,),
        )
        conn.execute(
            "UPDATE operator_routing_guard SET lease_token=NULL,lease_until=NULL WHERE scope='grafana'"
        )


def test_setup_is_durable_encrypted_and_gate_stays_pending(operators, real_db, cipher):
    client, base, _ = operators
    op, job, body = create(operators)
    assert client.post(base, json=body).json()["data"]["operation"]["id"] == job["id"]
    assert op["lifecycle"] == "setup_pending" and op["keys"][0]["state"] == "prepared"
    assert (
        client.post(base + "/" + op["id"] + "/keys/current/copy", json=command(1)).status_code
        == 409
    )
    due(real_db, job["id"])
    assert not run_one(real_db, cipher)
    state = client.get(base + "/" + op["id"] + "/operations/" + job["id"]).json()["data"]
    assert (
        state["state"] == "needs_attention"
        and state["error_code"] == "operator_capabilities_unverified"
    )
    with real_db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM operator_credentials WHERE operator_id=%s", (op["id"],)
        ).fetchone()
        assert row["ciphertext"] and row["state"] == "prepared"
        assert "vena_" not in json.dumps(client.get(base + "/" + op["id"]).json())


def test_activation_copy_rotation_overlap_and_revoke_erase(operators, real_db, cipher):
    client, base, _ = operators
    op = ready(operators, real_db, cipher)
    path = base + "/" + op["id"]
    copied = client.post(path + "/keys/current/copy", json=command(op["revision"]))
    assert copied.status_code == 200, copied.text
    secret = copied.json()["data"]["key"]
    assert secret.startswith("vena_") and "no-store" in copied.headers["cache-control"]
    rotate = client.post(path + "/keys/rotate", json=command(op["revision"]))
    assert rotate.status_code == 202, rotate.text
    pending = rotate.json()["data"]["operator"]
    assert (
        client.post(path + "/keys/current/copy", json=command(pending["revision"])).json()["data"][
            "key"
        ]
        == secret
    )
    due(real_db, rotate.json()["data"]["operation"]["id"])
    assert run_one(real_db, cipher, VerifiedFixture())
    op = client.get(path).json()["data"]
    assert sorted(k["state"] for k in op["keys"]) == ["current", "retiring"]
    with real_db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM operator_credentials WHERE operator_id=%s", (op["id"],)
        ).fetchall()
        new = next(r for r in rows if r["state"] == "current")
        old = next(r for r in rows if r["state"] == "retiring")
        assert (old["expires_at"] - new["activated_at"]).total_seconds() == 86400
    assert client.post(path + "/keys/rotate", json=command(op["revision"])).status_code == 409
    revoke = client.post(path + "/keys/revoke-all", json=command(op["revision"]))
    assert revoke.status_code == 200, revoke.text
    with real_db.transaction() as conn:
        assert all(
            r["ciphertext"] is None and r["state"] == "revoked"
            for r in conn.execute(
                "SELECT * FROM operator_credentials WHERE operator_id=%s", (op["id"],)
            )
        )
        audits = conn.execute(
            "SELECT evidence FROM operator_activity WHERE operator_id=%s", (op["id"],)
        ).fetchall()
        assert secret not in str(audits)
        assert secret not in str(conn.execute("SELECT result FROM command_receipts").fetchall())


def test_revoke_during_external_attempt_fences_activation(operators, real_db, cipher):
    client, base, _ = operators
    op, job, _ = create(operators)
    adapter = VerifiedFixture()

    def revoke():
        # Proves remote execute holds neither coordinator nor operator SQL lock.
        response = client.post(base + "/" + op["id"] + "/keys/revoke-all", json=command(1))
        assert response.status_code == 200, response.text

    adapter.callback = revoke
    due(real_db, job["id"])
    assert not run_one(real_db, cipher, adapter)
    value = client.get(base + "/" + op["id"]).json()["data"]
    assert not value["current_key_available"]
    assert value["keys"][0]["state"] == "revoked"


@pytest.mark.parametrize(
    "phase,revoke_one",
    [
        ("ready", False),
        ("initial_rename", False),
        ("ready_rename", False),
        ("ready_rename", True),
        ("ready", True),
    ],
)
def test_replacement_supersedes_keyless_cleanup_and_fences_late_completion(
    operators, real_db, cipher, phase, revoke_one
):
    from appchi.operators.executor_ledger import PostgresLedger
    from appchi.operators.worker import claim, complete, work_input

    client, base, _ = operators
    if phase == "initial_rename":
        op, _, _ = create(operators)
    else:
        op = ready(operators, real_db, cipher)
    if phase.endswith("rename"):
        renamed = client.patch(
            base + "/" + op["id"],
            json={**command(op["revision"]), "name": "replacement-" + str(uuid4())},
        ).json()["data"]
        op = renamed["operator"]
        if phase == "ready_rename":
            due(real_db, renamed["operation"]["id"])
            rename_job, rename_op = claim(real_db)
            rename_work = work_input(real_db, cipher, rename_job, rename_op)
            rename_ledger = PostgresLedger(real_db).begin(rename_work, "profile-digest")
            rename_edit = {
                "uid": "rule1",
                "old_key": "Appchi - " + op["name"],
                "old_value": "true",
                "new_key": "Appchi - " + op["pending_name"],
                "new_value": None,
            }
            rename_ledger.record(rename_edit)
    path = base + "/" + op["id"]
    suffix = (
        "/keys/" + next(key["id"] for key in op["keys"] if key["state"] == "current") + "/revoke"
        if revoke_one
        else "/keys/revoke-all"
    )
    revoked = client.post(path + suffix, json=command(op["revision"])).json()["data"]
    cleanup = revoked["operation"]
    assert cleanup["kind"] == "cleanup"
    due(real_db, cleanup["id"])
    old_job, old_op = claim(real_db)
    old_work = work_input(real_db, cipher, old_job, old_op)
    assert old_work.secret is None
    if phase == "ready_rename":
        cleanup_ledger = PostgresLedger(real_db).begin(old_work, "profile-digest")
        assert cleanup_ledger.restore_edits == [rename_edit]
        with real_db.transaction() as conn:
            assert (
                len(
                    conn.execute(
                        "SELECT name_key FROM operator_name_claims WHERE operator_id=%s",
                        (op["id"],),
                    ).fetchall()
                )
                == 2
            )
    created = client.post(path + "/keys/create", json=command(revoked["operator"]["revision"]))
    assert created.status_code == 202, created.text
    data = created.json()["data"]
    with real_db.transaction() as conn:
        generation = conn.execute(
            "SELECT generation FROM operators WHERE id=%s", (op["id"],)
        ).fetchone()["generation"]
    assert generation > old_job["generation"]
    assert not complete(real_db, old_job, VerifiedFixture().execute(old_work))
    due(real_db, data["operation"]["id"])
    assert run_one(real_db, cipher, VerifiedFixture())
    fresh = client.get(path).json()["data"]
    assert fresh["current_key_available"] and fresh["pending_name"] is None
    if phase.endswith("rename"):
        assert fresh["name"] == op["pending_name"]
    with real_db.transaction() as conn:
        audit = conn.execute(
            "SELECT evidence FROM operator_activity WHERE operator_id=%s AND kind='operator-create_key' ORDER BY recorded_at DESC LIMIT 1",
            (op["id"],),
        ).fetchone()
        assert audit["evidence"]["superseded_operation_id"] == cleanup["id"]
        assert (
            conn.execute(
                "SELECT state FROM operator_operations WHERE id=%s", (cleanup["id"],)
            ).fetchone()["state"]
            == "superseded"
        )


def test_rename_claims_are_global_and_commit_only_after_verification(operators, real_db, cipher):
    client, base, tenant = operators
    op = ready(operators, real_db, cipher)
    old = op["name"]
    new = "renamed-" + str(uuid4())
    path = base + "/" + op["id"]
    response = client.patch(path, json={**command(op["revision"]), "name": new})
    assert response.status_code == 202, response.text
    pending = response.json()["data"]["operator"]
    assert pending["name"] == old and pending["pending_name"] == new
    for name in (old, new):
        assert client.post(base, json={"command_id": str(uuid4()), "name": name}).status_code == 409
    with real_db.transaction() as conn:
        row = conn.execute("SELECT * FROM operators WHERE id=%s", (op["id"],)).fetchone()
        assert routing(conn, row).labels() == ("Appchi - " + old, "Appchi - " + new)
    due(real_db, response.json()["data"]["operation"]["id"])
    assert run_one(real_db, cipher, VerifiedFixture())
    result = client.get(path).json()["data"]
    assert result["name"] == new and result["pending_name"] is None
    create(operators, old)


@pytest.mark.parametrize("original", ["rename", "delete", "delete_initial", "rotate"])
def test_keyless_cancellation_can_prepare_replacement_without_losing_restoration(
    operators, real_db, cipher, original
):
    from dataclasses import replace
    from appchi.operators.executor_ledger import PostgresLedger
    from appchi.operators.worker import claim, complete, work_input
    from appchi.shared.errors import AppError

    client, base, _ = operators
    op = create(operators)[0] if original == "delete_initial" else ready(operators, real_db, cipher)
    path = base + "/" + op["id"]
    old_name = op["name"]
    if original == "rename":
        response = client.patch(
            path, json={**command(op["revision"]), "name": "cancelled-" + str(uuid4())}
        )
    elif original == "rotate":
        response = client.post(path + "/keys/rotate", json=command(op["revision"]))
    else:
        response = client.delete(
            path, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"' + op["revision"] + '"'}
        )
    assert response.status_code == 202, response.text
    pending = response.json()["data"]
    due(real_db, pending["operation"]["id"])
    original_job, original_op = claim(real_db)
    original_work = work_input(real_db, cipher, original_job, original_op)
    journal = PostgresLedger(real_db).begin(original_work, "profile-digest")
    edit = {
        "uid": "rule1",
        "old_key": "Appchi - " + old_name,
        "old_value": "true",
        "new_key": "Appchi - " + pending["operator"]["pending_name"]
        if original == "rename"
        else None,
        "new_value": None,
    }
    if original != "rotate":
        journal.record(edit)
    cancelled = client.post(
        path + "/operations/" + pending["operation"]["id"] + "/cancel",
        json=command(pending["operator"]["revision"]),
    ).json()["data"]
    revoked = client.post(
        path + "/keys/revoke-all", json=command(cancelled["operator"]["revision"])
    ).json()["data"]
    assert revoked["operation"]["kind"] == "cancel"
    due(real_db, revoked["operation"]["id"])
    cancel_job, cancel_op = claim(real_db)
    cancel_work = work_input(real_db, cipher, cancel_job, cancel_op)
    assert cancel_work.secret is None
    body = command(revoked["operator"]["revision"])
    created = client.post(path + "/keys/create", json=body)
    assert created.status_code == 202, created.text
    result = created.json()["data"]
    assert (
        client.post(path + "/keys/create", json=body).json()["data"]["operation"]["id"]
        == result["operation"]["id"]
    )
    if original == "delete":
        # Revoking the replacement does not erase the earlier cancellation's
        # journal or unblock admission. Another explicit command may replace it.
        revoked_again = client.post(
            path + "/keys/revoke-all", json=command(result["operator"]["revision"])
        ).json()["data"]
        assert revoked_again["operation"]["restoring_cancellation"]
        retry = client.post(
            path + "/keys/create", json=command(revoked_again["operator"]["revision"])
        )
        assert retry.status_code == 202, retry.text
        result = retry.json()["data"]
    assert result["operator"]["deletion_pending"] is original.startswith("delete")
    assert (
        result["operator"]["name"] == old_name and not result["operator"]["current_key_available"]
    )
    assert not complete(real_db, cancel_job, VerifiedFixture().execute(cancel_work))
    assert not complete(real_db, original_job, VerifiedFixture().execute(original_work))
    due(real_db, result["operation"]["id"])
    replacement_job, replacement_op = claim(real_db)
    replacement_work = work_input(real_db, cipher, replacement_job, replacement_op)
    restored = PostgresLedger(real_db).begin(replacement_work, "profile-digest")
    assert restored.restore_edits == ([] if original == "rotate" else [edit])
    evidence = VerifiedFixture().execute(replacement_work)
    with pytest.raises(AppError, match="grafana_verification_required"):
        complete(real_db, replacement_job, replace(evidence, labels_verified=False))
    gated = client.get(path).json()["data"]
    assert gated["deletion_pending"] is original.startswith("delete")
    assert not gated["current_key_available"] and gated["name"] == old_name
    assert complete(real_db, replacement_job, evidence)
    finished = client.get(path).json()["data"]
    assert finished["current_key_available"] and not finished["deletion_pending"]
    assert finished["name"] == old_name and finished["pending_name"] is None
    assert all(
        k["state"] == "revoked"
        for k in finished["keys"]
        if k["id"] != replacement_work.candidate_id
    )


def test_deletion_and_cancel_keep_fence_until_verified_restore(operators, real_db, cipher):
    client, base, _ = operators
    op = ready(operators, real_db, cipher)
    path = base + "/" + op["id"]
    response = client.delete(
        path, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"' + op["revision"] + '"'}
    )
    assert response.status_code == 202, response.text
    pending = response.json()["data"]
    assert pending["operator"]["deletion_pending"]
    assert (
        client.post(
            path + "/keys/current/copy", json=command(pending["operator"]["revision"])
        ).status_code
        == 409
    )
    cancelled = client.post(
        path + "/operations/" + pending["operation"]["id"] + "/cancel",
        json=command(pending["operator"]["revision"]),
    )
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["data"]["operator"]["deletion_pending"]
    due(real_db, cancelled.json()["data"]["operation"]["id"])
    assert run_one(real_db, cipher, VerifiedFixture())
    assert not client.get(path).json()["data"]["deletion_pending"]


def test_operator_authorization_and_cross_tenant_concealment(operators, browser, real_db):
    client, base, _ = operators
    op, _, _ = create(operators)
    _, actor = browser
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=false WHERE id=%s", (actor,))
    assert client.get(base + "/" + op["id"]).status_code == 404
    assert (
        client.post(base + "/" + op["id"] + "/keys/revoke-all", json=command(1)).status_code == 404
    )
    with real_db.transaction() as conn:
        global_id = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (actor,))
    assert (
        client.post(
            f"/api/tenants/{global_id}/operators",
            json={"command_id": str(uuid4()), "name": "invalid"},
        ).status_code
        == 422
    )


def test_activation_restore_gate_and_expired_material_cleanup(operators, real_db, cipher):
    from appchi.operators.worker import expire_due

    client, base, _ = operators
    op, job, _ = create(operators)
    due(real_db, job["id"])
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='v2_ingress'")
    assert not run_one(real_db, cipher, VerifiedFixture())
    assert not client.get(base + "/" + op["id"]).json()["data"]["current_key_available"]
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='v2_ingress'")
    due(real_db, job["id"])
    assert run_one(real_db, cipher, VerifiedFixture())
    op = client.get(base + "/" + op["id"]).json()["data"]
    rotated = client.post(
        base + "/" + op["id"] + "/keys/rotate", json=command(op["revision"])
    ).json()["data"]
    due(real_db, rotated["operation"]["id"])
    assert run_one(real_db, cipher, VerifiedFixture())
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE operator_credentials SET expires_at=clock_timestamp()-interval '1 second' WHERE operator_id=%s AND state='retiring'",
            (op["id"],),
        )
    expire_due(real_db)
    with real_db.transaction() as conn:
        expired = conn.execute(
            "SELECT * FROM operator_credentials WHERE operator_id=%s AND state='expired'",
            (op["id"],),
        ).fetchone()
        assert expired and expired["ciphertext"] is None


def test_initial_delete_cancel_resumes_same_prepared_setup(operators, real_db, cipher):
    client, base, _ = operators
    op, job, _ = create(operators)
    path = base + "/" + op["id"]
    deleting = client.delete(
        path, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"1"'}
    ).json()["data"]
    cancel = client.post(
        path + "/operations/" + deleting["operation"]["id"] + "/cancel",
        json=command(deleting["operator"]["revision"]),
    ).json()["data"]
    due(real_db, cancel["operation"]["id"])
    adapter = VerifiedFixture()
    assert run_one(real_db, cipher, adapter)
    assert adapter.last_work.candidate_id == op["keys"][0]["id"]
    assert adapter.last_work.secret is not None
    assert adapter.last_work.current_id is None
    restored = client.get(path).json()["data"]
    assert restored["operation"]["kind"] == "setup"
    assert restored["keys"][0]["id"] == op["keys"][0]["id"]
    due(real_db, restored["operation"]["id"])
    assert run_one(real_db, cipher, VerifiedFixture())
    assert client.get(path).json()["data"]["lifecycle"] == "ready"


def test_name_conflict_stays_generic_under_runtime_rls(operators, real_db):
    from appchi.operators.repository import claim_name
    from appchi.shared.errors import AppError

    op, _, _ = create(operators)
    other = str(uuid4())
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
            (other, other, other),
        )
    with pytest.raises(AppError) as error:
        with real_db.transaction(other) as conn:
            conn.execute("SET LOCAL ROLE appchi_api")
            new = conn.execute(
                "INSERT INTO operators(id,tenant_id,name,name_key,lifecycle) VALUES(%s,%s,%s,%s,'setup_pending') RETURNING *",
                (uuid4(), other, op["name"], op["name"].casefold()),
            ).fetchone()
            claim_name(conn, new, op["name"])
    assert error.value.code == "name_unavailable" and error.value.status == 409


def test_pending_native_input_blocks_delete_without_changing_keys(
    operators, real_db, cipher, profile
):
    from appchi.ingestion.service import Admission
    from appchi.ingestion.gateway import GatewayIdentity
    from appchi.ingestion.native import canonical

    client, base, _ = operators
    op = ready(operators, real_db, cipher)
    with real_db.transaction() as conn:
        key = conn.execute(
            "SELECT * FROM operator_credentials WHERE operator_id=%s AND state='current'",
            (op["id"],),
        ).fetchone()
    payload = {
        "application": "test",
        "component": "cpu",
        "message": "test",
        "severity": "warning",
        "status": "firing",
        "site": "test",
        "impact": "test",
        "runbook_url": "https://example.invalid",
    }
    Admission(real_db, cipher, profile).admit(
        canonical(payload),
        "v2",
        gateway=GatewayIdentity(key["kong_consumer_id"], key["kong_credential_id"]),
    )
    result = client.delete(
        base + "/" + op["id"],
        headers={"Idempotency-Key": str(uuid4()), "If-Match": '"' + op["revision"] + '"'},
    )
    assert result.status_code == 409, result.text
    row = client.get(base + "/" + op["id"]).json()["data"]
    assert (
        not row["deletion_pending"]
        and row["current_key_available"]
        and row["revision"] == op["revision"]
    )
