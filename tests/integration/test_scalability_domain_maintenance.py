"""Real PostgreSQL long-lived maintenance proof, on disposable fixtures."""

# ruff: noqa: F401, F811
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from keep.maintenance.processing import prepare_catalog, reconcile_alert
from keep.shared.transactions import coordinator
from test_maintenance_processing import apply, snapshot
from test_maintenance_repository import (
    body,
    create,
    maintenance_browser,
    maintenance_db,
)


pytestmark = pytest.mark.integration


def test_thousand_receipts_and_delayed_replay_exceed_page_size_without_lifetime_cap(
    maintenance_browser,
    maintenance_db,
):
    _, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    catalog = prepare_catalog(db, tenant, max_work=20, max_inputs=8, max_segments=2)
    value = snapshot(tenant)
    base = datetime.now(UTC) + timedelta(seconds=1)
    apply(db, catalog, None, value, base, 1)
    for order in range(2, 1002):
        apply(db, catalog, value, value, base + timedelta(seconds=order), order)
    # A late timestamp forces exact original-application-order replay in pages.
    apply(
        db,
        catalog,
        value,
        value,
        base + timedelta(microseconds=1),
        1002,
        now=base + timedelta(seconds=1003),
    )
    with db.transaction(tenant) as conn:
        checkpoint = conn.execute(
            "SELECT * FROM maintenance_alert_checkpoints WHERE tenant_id=%s AND alert_id=%s",
            (tenant, value["id"]),
        ).fetchone()
        assert checkpoint["complete"] and checkpoint["input_revision"] == 1002
        assert len(str(checkpoint["continuation"])) < 2048
        assert checkpoint["union_intervals"] == []
        assert datetime.fromisoformat(checkpoint["continuation"]["max_at"]) > base + timedelta(
            seconds=999
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_alert_inputs WHERE tenant_id=%s",
                (tenant,),
            ).fetchone()["n"]
            == 1002
        )


def test_ended_windows_do_not_exhaust_active_catalog_budget(maintenance_browser, maintenance_db):
    client, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    for _ in range(3):
        create(client, tenant, body(past=True))
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE maintenance_segments SET start_at=clock_timestamp()-interval '2 hours',"
            "end_at=clock_timestamp()-interval '1 hour' WHERE tenant_id=%s",
            (tenant,),
        )
    historical = prepare_catalog(db, tenant, max_work=20, max_inputs=8, max_segments=2)
    assert len(historical.segments) == 3
    fresh = prepare_catalog(
        db, tenant, max_work=20, max_inputs=8, max_segments=2, effective_at=datetime.now(UTC)
    )
    assert fresh.segments == () and not fresh.history_complete


def test_checkpoint_tail_bounded_and_generation_change_reconstructs_all_intervals(
    maintenance_browser,
    maintenance_db,
):
    client, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    create(client, tenant, body(past=True))
    catalog = prepare_catalog(db, tenant, max_work=20, max_inputs=8, max_segments=2)
    value = snapshot(tenant)
    base = datetime.now(UTC) + timedelta(seconds=1)
    previous = None
    for order in range(1, 66):
        current = dict(value, message="match" if order % 2 else "other")
        apply(db, catalog, previous, current, base + timedelta(seconds=order), order)
        previous = current
    with db.transaction(tenant) as conn:
        checkpoint = conn.execute(
            "SELECT * FROM maintenance_alert_checkpoints WHERE tenant_id=%s AND alert_id=%s",
            (tenant, value["id"]),
        ).fetchone()
        assert len(checkpoint["union_intervals"]) == 1
        assert len(checkpoint["continuation"]["causes"]) == 1
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_union_history WHERE tenant_id=%s AND representation='tail'",
                (tenant,),
            ).fetchone()["n"]
            > 30
        )
        conn.execute(
            "UPDATE tenant_view_state SET maintenance_generation=maintenance_generation+1 WHERE tenant_id=%s",
            (tenant,),
        )
    rebuilt = prepare_catalog(
        db,
        tenant,
        max_work=20,
        max_inputs=8,
        max_segments=2,
        alert_id=value["id"],
        effective_at=base + timedelta(seconds=70),
    )
    assert rebuilt.history_complete
    with db.transaction(tenant) as conn:
        coordinator(conn, tenant)
        _, effects = reconcile_alert(conn, previous, base + timedelta(seconds=70), catalog=rebuilt)
        assert not effects.withdrawn_qualifiers
        history = conn.execute(
            "SELECT effective_intervals FROM maintenance_union_history WHERE tenant_id=%s AND representation='full' ORDER BY recorded_at DESC LIMIT 1",
            (tenant,),
        ).fetchone()
        assert len(history["effective_intervals"]) == 33


def test_deactivated_future_revisions_are_excluded_for_new_single_and_bulk_targets(
    maintenance_browser,
    maintenance_db,
    monkeypatch,
):
    from keep.maintenance import processing
    from keep.maintenance.runtime import prepare_for_tenant

    client, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    window = create(client, tenant, body())
    for revision in range(1, 13):
        response = client.patch(
            f"/api/tenants/{tenant}/maintenance/{window['id']}",
            json={
                "kind": "schedule",
                "cel": f'alert.message == "revision-{revision}"',
                "command_id": str(uuid4()),
                "expected_revision": str(revision),
            },
        )
        assert response.status_code == 200, response.text
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_segments WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 13
        )
    calls = []
    compile_expression = processing.compile_expression

    def compile_counted(expression, **kwargs):
        calls.append(expression)
        return compile_expression(expression, **kwargs)

    monkeypatch.setattr(processing, "compile_expression", compile_counted)
    base = datetime.now(UTC)
    catalog = prepare_catalog(
        db, tenant, max_work=20, max_inputs=8, max_segments=2, effective_at=base
    )
    assert len(catalog.segments) == len(calls) == 1
    first, second = snapshot(tenant), snapshot(tenant)
    apply(db, catalog, None, first, base, 20)
    apply(db, catalog, None, second, base + timedelta(microseconds=1), 21)
    calls.clear()
    single = prepare_catalog(
        db,
        tenant,
        max_work=20,
        max_inputs=8,
        max_segments=2,
        alert_id=first["id"],
        effective_at=base + timedelta(seconds=1),
    )
    assert len(single.segments) == len(calls) == 1
    calls.clear()
    bulk = prepare_for_tenant(
        db,
        tenant,
        {
            "qualified": True,
            "maintenance_max_work": 20,
            "maintenance_max_inputs": 8,
            "maintenance_max_segments": 2,
        },
        inline=False,
        alert_ids=(first["id"], second["id"]),
    )
    assert len(bulk.segments) == len(calls) == 1
    assert not bulk.history_complete and not bulk.replays
