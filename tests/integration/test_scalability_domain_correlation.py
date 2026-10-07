"""Real tuple-write and row-lock checks for differential correlation storage."""

# ruff: noqa: F401, F811
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from appchi.alerts.lifecycle import apply_source
from appchi.alerts.repository import store_alert
from appchi.correlation import repository, runtime
from appchi.correlation.evidence import group_key
from appchi.correlation.models import grouping
from appchi.shared.transactions import coordinator
from test_correlation_configuration import TEST_CAPACITY, create, definition, rules
from test_correlation_runtime import drain, running, source
from test_product import browser


pytestmark = pytest.mark.integration


def _supports(db, tenant):
    with db.transaction(tenant) as conn:
        return {
            row["alert_id"]: row
            for row in conn.execute(
                "SELECT xmin::text AS tuple_version,* FROM correlation_evidence WHERE tenant_id=%s",
                (tenant,),
            )
        }


def test_unchanged_support_retains_postgres_tuple_and_changed_support_is_differential(
    running,
    real_db,
    v1,
):
    _, _, tenant, _ = running
    # The unmatched second condition keeps evidence pending without ownership.
    create(running, definition())
    first = source(real_db, tenant, v1)
    second = source(real_db, tenant, v1)
    drain(real_db, tenant)
    before = _supports(real_db, tenant)
    assert set(before) == {first["id"], second["id"]}
    prepared = runtime.prepare_tenant(real_db, tenant, TEST_CAPACITY)
    with real_db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        states, _, _, _, snapshot = runtime._load(conn, prepared, 0, now, bounded=True)
        runtime._persist(conn, prepared, states, snapshot)
    assert _supports(real_db, tenant) == before
    with real_db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        states, _, _, _, snapshot = runtime._load(conn, prepared, 0, now, bounded=True)
        changed = tuple(
            replace(
                state,
                memberships=tuple(
                    replace(member, observed_revision=member.observed_revision + 1)
                    if member.alert_id == first["id"]
                    else member
                    for member in state.memberships
                ),
            )
            for state in states
        )
        runtime._persist(conn, prepared, changed, snapshot)
    after = _supports(real_db, tenant)
    assert after[second["id"]] == before[second["id"]]
    assert after[first["id"]]["tuple_version"] != before[first["id"]]["tuple_version"]
    assert after[first["id"]]["observed_revision"] == before[first["id"]]["observed_revision"] + 1
    for column in ("start_at", "expires_at", "qualifier_ref"):
        assert after[first["id"]][column] == before[first["id"]][column]


def test_live_scope_does_not_lock_or_load_unrelated_dormant_context(running, real_db, v1):
    _, _, tenant, _ = running
    result, _ = create(running, {**definition(), "group_by": ["node_name"]})
    source(real_db, tenant, v1)
    drain(real_db, tenant)
    with real_db.transaction(tenant) as conn:
        coordinator(conn, tenant)
        row = repository.get_rule(conn, tenant, result["id"])
        dormant = repository.ensure_context(
            conn,
            row,
            group_key(grouping(["node_name"]), {"node_name": "dormant unrelated group"}),
            max_bucket=5,
        )
        active = conn.execute(
            "SELECT context_id FROM correlation_evidence WHERE tenant_id=%s",
            (tenant,),
        ).fetchone()["context_id"]
    prepared = runtime.prepare_tenant(real_db, tenant, TEST_CAPACITY)
    with real_db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        runtime.lock_contexts(conn, tenant, targets=(), live=True)
        _, _, _, _, snapshot = runtime._load(conn, prepared, 0, now, bounded=True)
        assert set(snapshot.contexts.values()) == {active}
        with real_db.transaction(tenant) as other:
            assert (
                other.execute(
                    "SELECT id FROM correlation_contexts WHERE id=%s FOR UPDATE NOWAIT",
                    (dormant,),
                ).fetchone()["id"]
                == dormant
            )
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with other.transaction():
                    other.execute(
                        "SELECT id FROM correlation_contexts WHERE id=%s FOR UPDATE NOWAIT",
                        (active,),
                    )


def test_correlation_prepares_incoming_and_live_attachment_targets(
    running, real_db, v1, monkeypatch
):
    from appchi.maintenance import runtime as maintenance

    _, _, tenant, _ = running
    create(running, definition())
    support = source(real_db, tenant, v1)
    drain(real_db, tenant)
    incoming = uuid4()
    observed = []
    original = maintenance.prepare_for_tenant

    def capture(database, tenant_id, profile, **kwargs):
        observed.append(set(map(str, kwargs["alert_ids"])))
        return original(database, tenant_id, profile, **kwargs)

    monkeypatch.setattr(maintenance, "prepare_for_tenant", capture)
    runtime.prepare_tenant(real_db, tenant, TEST_CAPACITY, alert_ids=(incoming,))
    assert observed == [{str(incoming), str(support["id"])}]


@pytest.mark.parametrize("explicit_expired_target", [False, True])
def test_decision_scope_excludes_expired_and_old_epoch_evidence_but_keeps_targets(
    real_db,
    tenant,
    v1,
    explicit_expired_target,
):
    # This is a row-lock test, not a compiler/evaluator test. Seed the retained
    # identities and support rows directly, as other SQL ownership fixtures do.
    # No executable condition program or native compilation is needed here.
    rule, condition, actor = uuid4(), uuid4(), uuid4()
    alerts, contexts = {}, {}
    with real_db.transaction(tenant) as conn:
        _, now = coordinator(conn, tenant)
        conn.execute(
            "INSERT INTO correlation_rule_identities(id,tenant_id) VALUES(%s,%s)",
            (rule, tenant),
        )
        conn.execute(
            """INSERT INTO correlation_rules(id,tenant_id,revision,epoch,grouping_generation,
            name,name_key,name_profile,description,severity,window_minutes,group_by,enabled,
            health,profile,evaluation_cost,epoch_boundary_at,epoch_boundary_order,
            created_at,updated_at,created_by,updated_by)
            VALUES(%s,%s,2,2,1,'Lock scope','lock scope',%s,'','high',10,
            '["node_name"]',true,'healthy',%s,0,%s,1,%s,%s,%s,%s)""",
            (
                rule,
                tenant,
                repository.NAME_PROFILE,
                repository.PROFILE,
                now,
                now,
                now,
                actor,
                actor,
            ),
        )
        conn.execute(
            "INSERT INTO correlation_condition_identities(id,tenant_id,rule_id) VALUES(%s,%s,%s)",
            (condition, tenant, rule),
        )
        for name in ("live", "expired", "obsolete"):
            alert, _ = apply_source(
                None,
                {
                    "id": uuid4(),
                    "tenant_id": tenant,
                    "version": "v1",
                    "key_field": str(uuid4()),
                    "received_at": now,
                    "payload": {**v1, "node_name": name},
                },
            )
            store_alert(conn, alert, new=True)
            context = repository.ensure_context(
                conn,
                {
                    "id": rule,
                    "tenant_id": tenant,
                    "grouping_generation": 1,
                    "group_by": ["node_name"],
                },
                group_key(grouping(["node_name"]), {"node_name": name}),
                max_bucket=5,
            )
            conn.execute(
                """INSERT INTO correlation_evidence(tenant_id,rule_id,epoch,context_id,
                condition_id,alert_id,qualifier_ref,start_at,expires_at,observed_revision)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    tenant,
                    rule,
                    1 if name == "obsolete" else 2,
                    context,
                    condition,
                    alert["id"],
                    uuid4(),
                    now - timedelta(hours=2),
                    now + timedelta(hours=-1 if name == "expired" else 1),
                    alert["revision"],
                ),
            )
            alerts[name], contexts[alert["id"]] = alert, context
    targets = (alerts["expired"]["id"],) if explicit_expired_target else ()
    with real_db.transaction(tenant) as conn:
        coordinator(conn, tenant)
        runtime.lock_decision_scope(conn, tenant, targets)
        with real_db.transaction(tenant) as other:
            for name, alert in alerts.items():
                must_lock = name == "live" or name == "expired" and explicit_expired_target
                if must_lock:
                    with pytest.raises(psycopg.errors.LockNotAvailable):
                        with other.transaction():
                            other.execute(
                                "SELECT id FROM alerts WHERE id=%s FOR UPDATE NOWAIT",
                                (alert["id"],),
                            )
                else:
                    assert (
                        other.execute(
                            "SELECT id FROM alerts WHERE id=%s FOR UPDATE NOWAIT", (alert["id"],)
                        ).fetchone()["id"]
                        == alert["id"]
                    )
                if name == "live":
                    with pytest.raises(psycopg.errors.LockNotAvailable):
                        with other.transaction():
                            other.execute(
                                "SELECT id FROM correlation_contexts WHERE id=%s FOR UPDATE NOWAIT",
                                (contexts[alert["id"]],),
                            )
                else:
                    assert (
                        other.execute(
                            "SELECT id FROM correlation_contexts WHERE id=%s FOR UPDATE NOWAIT",
                            (contexts[alert["id"]],),
                        ).fetchone()["id"]
                        == contexts[alert["id"]]
                    )
