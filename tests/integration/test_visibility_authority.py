# ruff: noqa: F811 -- shared isolated database/browser fixtures
"""Historical authority continuity only, never complete observation eligibility."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import sleep
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_maintenance_repository import maintenance_db, maintenance_browser  # noqa: F401
from test_visibility_inputs import inputs, register, seed  # noqa: F401

from keep.identity.directory import DirectoryEvidence, DirectoryObject, PrincipalKey
from keep.identity.publication import allocate_generation, publish
from keep.visibility.authority import prove_authority, recheck_authority
from keep.visibility.inputs import expand_input

pytestmark = pytest.mark.integration


def candidate(db, tenant, client):
    view = register(client, tenant)
    with db.transaction(tenant) as conn:
        original = seed(conn, tenant)["input"]
        expand_input(conn, tenant=tenant, input_id=original["id"])
    return dict(tenant=tenant, input_id=original["id"], view_id=UUID(view["id"]), view_epoch=1)


def prove(db, args):
    with db.transaction(args["tenant"]) as conn:
        conn.execute("SET LOCAL ROLE keep_worker")
        return prove_authority(conn, **args)


def test_unchanged_authority_proves_original_input_and_rechecks_without_activity(inputs):
    db, tenant, client = inputs
    args = candidate(db, tenant, client)
    with db.transaction(tenant) as conn:
        before = conn.execute(
            "SELECT s.revoked_at,s.absolute_at FROM auth_sessions s JOIN visibility_views v ON v.session_hash=s.handle_hash WHERE v.tenant_id=%s",
            (tenant,),
        ).fetchone()
    result = prove(db, args)
    assert result.proven and result.reason == "uninterrupted_authority"
    with db.transaction(tenant) as conn:
        assert recheck_authority(conn, result.token) == result
        after = conn.execute(
            "SELECT s.revoked_at,s.absolute_at FROM auth_sessions s JOIN visibility_views v ON v.session_hash=s.handle_hash WHERE v.tenant_id=%s",
            (tenant,),
        ).fetchone()
        assert after == before


def test_gate_close_reopen_is_not_hidden_by_current_ready_or_supplied_generation(inputs):
    db, tenant, client = inputs
    args = candidate(db, tenant, client)
    original = prove(db, args)
    assert original.proven
    with db.transaction(tenant) as conn:
        conn.execute("SET LOCAL ROLE keep_api")
        before = conn.execute(
            "SELECT generation FROM capability_gates WHERE capability='human_auth'"
        ).fetchone()["generation"]
        conn.execute(
            "UPDATE capability_gates SET ready=false,generation=0 WHERE capability='human_auth'"
        )
        conn.execute(
            "UPDATE capability_gates SET ready=true,generation=0 WHERE capability='human_auth'"
        )
        after = conn.execute(
            "SELECT generation FROM capability_gates WHERE capability='human_auth'"
        ).fetchone()["generation"]
        assert after == before + 2
        conn.execute(
            "UPDATE capability_gates SET generation=0,reason=reason WHERE capability='human_auth'"
        )
        assert (
            conn.execute(
                "SELECT generation FROM capability_gates WHERE capability='human_auth'"
            ).fetchone()["generation"]
            == after
        )
    result = prove(db, args)
    assert not result.proven and result.reason == "authority_human_auth_generation_changed"
    with db.transaction(tenant) as conn:
        assert not recheck_authority(conn, original.token).proven


def test_runtime_roles_cannot_reset_or_remove_authority_fences(inputs):
    db, tenant, _ = inputs
    with db.transaction(tenant) as conn:
        conn.execute("SET LOCAL ROLE keep_worker")
        conn.execute(
            "UPDATE authorization_guards SET directory_revision=directory_revision+1 WHERE scope='identity'"
        )
    with pytest.raises(psycopg.errors.RaiseException), db.transaction(tenant) as conn:
        conn.execute("SET LOCAL ROLE keep_worker")
        conn.execute("UPDATE authorization_guards SET directory_revision=0 WHERE scope='identity'")
    for role, table in (
        ("keep_api", "authorization_guards"),
        ("keep_worker", "capability_gates"),
        ("keep_recovery", "capability_gates"),
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction(tenant) as conn:
            conn.execute("SET LOCAL ROLE " + role)
            conn.execute("DELETE FROM " + table)


def _publication(conn, *, objects, edges, roots, group, complete=True):
    generation = allocate_generation(conn)
    while generation <= 1:
        generation = allocate_generation(conn)
    now = datetime.now(UTC)
    evidence = DirectoryEvidence(
        group.source,
        generation,
        now,
        now,
        frozenset(roots),
        objects,
        edges,
        {},
        complete,
        () if complete else ("synthetic_outage",),
    )
    return publish(conn, evidence, superadmin_group=group)


def test_directory_mapping_away_and_back_advances_both_fences_without_user_refresh(inputs):
    db, tenant, client = inputs
    with db.transaction(tenant) as conn:
        user = conn.execute(
            "SELECT p.* FROM principals p JOIN tenant_grants g ON g.principal_id=p.id WHERE g.tenant_id=%s",
            (tenant,),
        ).fetchone()
        user_key = PrincipalKey(user["directory_source"], user["object_id"])
        first = PrincipalKey(user_key.source, "group-a-" + uuid4().hex)
        second = PrincipalKey(user_key.source, "group-b-" + uuid4().hex)
        groups = {key: DirectoryObject(key, "team", key.object_id, True) for key in (first, second)}
        edges = {first: frozenset({user_key}), second: frozenset()}
        baseline = _publication(
            conn,
            objects={
                **groups,
                user_key: DirectoryObject(user_key, "user", user["display_name"], True),
            },
            edges=edges,
            roots=(first, second),
            group=first,
        )
        assert baseline.accepted
        user_generation = conn.execute(
            "SELECT evidence_generation FROM principals WHERE id=%s", (user["id"],)
        ).fetchone()["evidence_generation"]
    args = candidate(db, tenant, client)
    assert prove(db, args).proven
    with db.transaction(tenant) as conn:
        away = _publication(conn, objects=groups, edges=edges, roots=(first, second), group=second)
        back = _publication(conn, objects=groups, edges=edges, roots=(first, second), group=first)
        assert away.semantic_revision == baseline.semantic_revision + 1
        assert back.semantic_revision == baseline.semantic_revision + 2
        unchanged = _publication(
            conn, objects=groups, edges=edges, roots=(first, second), group=first
        )
        assert unchanged.semantic_revision == back.semantic_revision
        current = conn.execute("SELECT * FROM principals WHERE id=%s", (user["id"],)).fetchone()
        assert current["evidence_generation"] == user_generation and current["super_admin"]
        revisions = conn.execute(
            "SELECT evidence->>'semantic_revision' AS revision FROM audit_events WHERE kind='global_privilege_changed' AND evidence->>'principal_id'=%s ORDER BY recorded_at,id",
            (str(user["id"]),),
        ).fetchall()
        assert {int(row["revision"]) for row in revisions} == {
            baseline.semantic_revision,
            away.semantic_revision,
            back.semantic_revision,
        }
    result = prove(db, args)
    assert not result.proven and result.reason == "authority_directory_revision_changed"


def test_failed_directory_refresh_preserves_trusted_authority(inputs):
    db, tenant, client = inputs
    args = candidate(db, tenant, client)
    with db.transaction(tenant) as conn:
        result = _publication(
            conn,
            objects={},
            edges={},
            roots=(),
            group=PrincipalKey("synthetic", "unavailable"),
            complete=False,
        )
        assert not result.accepted
    assert prove(db, args).proven


def test_newly_tracked_existing_nested_root_advances_semantic_revision(inputs):
    db, tenant, _ = inputs
    with db.transaction(tenant) as conn:
        user = conn.execute(
            "SELECT p.* FROM principals p JOIN tenant_grants g ON g.principal_id=p.id WHERE g.tenant_id=%s",
            (tenant,),
        ).fetchone()
        user_key = PrincipalKey(user["directory_source"], user["object_id"])
        first = PrincipalKey(user_key.source, "outer-" + uuid4().hex)
        nested = PrincipalKey(user_key.source, "nested-" + uuid4().hex)
        objects = {
            key: DirectoryObject(key, "team", key.object_id, True) for key in (first, nested)
        }
        objects[user_key] = DirectoryObject(user_key, "user", user["display_name"], True)
        edges = {first: frozenset({nested}), nested: frozenset({user_key})}
        baseline = _publication(conn, objects=objects, edges=edges, roots=(first,), group=first)
        nested_id = conn.execute(
            "SELECT id FROM principals WHERE directory_source=%s AND object_id=%s",
            (nested.source, nested.object_id),
        ).fetchone()["id"]
        assert (
            conn.execute(
                "SELECT 1 FROM directory_memberships WHERE team_id=%s", (nested_id,)
            ).fetchone()
            is None
        )
        changed = _publication(conn, objects=objects, edges=edges, roots=(nested,), group=first)
        assert changed.semantic_revision == baseline.semantic_revision + 1
        assert (
            conn.execute(
                "SELECT user_id FROM directory_memberships WHERE team_id=%s", (nested_id,)
            ).fetchone()["user_id"]
            == user["id"]
        )
        unchanged = _publication(conn, objects=objects, edges=edges, roots=(nested,), group=first)
        assert unchanged.semantic_revision == changed.semantic_revision


@pytest.mark.parametrize(
    "change,reason",
    [
        ("tenant_revision", "authority_tenant_revision_changed"),
        ("principal_generation", "authority_principal_changed"),
        ("revoke", "authority_session_no_longer_valid"),
        ("binding", "authority_session_binding_changed"),
        ("missing_snapshot", "authority_snapshot_legacy_or_missing"),
    ],
)
def test_changed_or_legacy_authority_is_unclassified_never_retroactive_exclusion(
    inputs, change, reason
):
    db, tenant, client = inputs
    args = candidate(db, tenant, client)
    with db.transaction(tenant) as conn:
        row = conn.execute(
            "SELECT * FROM visibility_views WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        if change == "tenant_revision":
            conn.execute("UPDATE tenants SET revision=revision+1 WHERE id=%s", (tenant,))
        elif change == "principal_generation":
            conn.execute(
                "UPDATE principals SET evidence_generation=evidence_generation+1 WHERE id=%s",
                (row["principal_id"],),
            )
        elif change == "revoke":
            conn.execute(
                "UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE handle_hash=%s",
                (row["session_hash"],),
            )
        elif change == "binding":
            conn.execute(
                # REV-73: the binding covers the sign-in time, not provider fields.
                "UPDATE auth_sessions SET auth_time=auth_time-interval '1 second' WHERE handle_hash=%s",
                (row["session_hash"],),
            )
        else:
            snapshot = conn.execute(
                "SELECT snapshot FROM visibility_view_controls WHERE tenant_id=%s", (tenant,)
            ).fetchone()["snapshot"]
            del snapshot["authority"]["schema"]
            conn.execute(
                "UPDATE visibility_view_controls SET snapshot=%s WHERE tenant_id=%s",
                (Jsonb(snapshot), tenant),
            )
    result = prove(db, args)
    assert not result.proven and result.reason == reason and result.token is None
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT eligibility FROM visibility_candidates WHERE tenant_id=%s", (tenant,)
            ).fetchone()["eligibility"]
            == "unclassified"
        )


def test_original_snapshot_authorization_ends_at_the_session_absolute_deadline(inputs):
    """Requirements: REV-73 (sessions have no idle timeout; the absolute deadline bounds authority)."""
    db, tenant, client = inputs
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE auth_sessions SET absolute_at=clock_timestamp()+interval '2 seconds' WHERE principal_id IN (SELECT principal_id FROM tenant_grants WHERE tenant_id=%s)",
            (tenant,),
        )
    view = register(client, tenant)
    sleep(2.1)
    with db.transaction(tenant) as conn:
        original = seed(conn, tenant)["input"]
        expand_input(conn, tenant=tenant, input_id=original["id"])
    result = prove(
        db, dict(tenant=tenant, input_id=original["id"], view_id=UUID(view["id"]), view_epoch=1)
    )
    assert not result.proven and result.reason == "authority_original_session_bound_exceeded"


def test_proof_holds_gate_row_and_recheck_detects_later_transition(inputs):
    db, tenant, client = inputs
    args = candidate(db, tenant, client)
    attempted = Event()

    def close_gate():
        with db.transaction(tenant) as conn:
            attempted.set()
            conn.execute("UPDATE capability_gates SET ready=false WHERE capability='human_auth'")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.transaction(tenant) as conn:
            proof = prove_authority(conn, **args)
            assert proof.proven
            writer = pool.submit(close_gate)
            assert attempted.wait(1)
            assert not writer.done()
        writer.result(timeout=3)
    with db.transaction(tenant) as conn:
        result = recheck_authority(conn, proof.token)
        assert not result.proven and result.reason == "authority_gate_unavailable"
