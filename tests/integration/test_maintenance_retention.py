# ruff: noqa: F811
"""Whole-timeline retention against isolated PostgreSQL and real HTTP commands."""

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from appchi.maintenance.cleanup import cleanup_window, cleanup_turn
from appchi.maintenance.processing import prepare_catalog, reconcile_alert, record_input
from appchi.shared.errors import AppError
from appchi.shared.transactions import coordinator
from test_maintenance_repository import body, create, maintenance_browser, maintenance_db  # noqa: F401
from test_maintenance_processing import snapshot

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def retention_schema(maintenance_db):
    db, _ = maintenance_db
    draft = Path(__file__).resolve().parents[2] / ".local/0033_maintenance_retention.sql"
    if draft.exists():
        with db.transaction() as conn:
            conn.execute(draft.read_text(), prepare=False)


def deleted(browser, db, *, active=False, final=True, hold=None):
    client, tenant, _, app = browser
    window = create(client, tenant, body(past=active))
    app.state.recovery_store.fail_final = not final
    response = client.delete(
        f"/api/tenants/{tenant}/maintenance/{window['id']}",
        headers={"Idempotency-Key": str(uuid4()), "If-Match": '"1"'},
    )
    assert response.status_code == (200 if final else 503), response.text
    with db.transaction(tenant) as conn:
        if hold:
            # Placed while the content is still retained; once expired, the
            # guard refuses new holds (no fan-out job holds it under REV-34).
            conn.execute(
                "INSERT INTO maintenance_configuration_holds(id,tenant_id,window_id,reason) VALUES(%s,%s,%s,'retained proof')",
                (hold, tenant, window["id"]),
            )
        # Time travel only the retention clock in this isolated synthetic fixture.
        conn.execute(
            "ALTER TABLE maintenance_identities DISABLE TRIGGER maintenance_fence_immutable"
        )
        aged = conn.execute(
            "UPDATE maintenance_windows SET deleted_at=deleted_at-interval '91 days' WHERE tenant_id=%s AND id=%s RETURNING deleted_at",
            (tenant, window["id"]),
        ).fetchone()["deleted_at"]
        conn.execute(
            "UPDATE maintenance_identities SET deleted_at=%s WHERE tenant_id=%s AND id=%s",
            (aged, tenant, window["id"]),
        )
        conn.execute(
            "ALTER TABLE maintenance_identities ENABLE TRIGGER maintenance_fence_immutable"
        )
    return client, tenant, window


def complete_empty_jobs(db, tenant):
    with db.transaction(tenant) as conn:
        assert not conn.execute(
            "SELECT 1 FROM alert_maintenance_causes WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        # No alerts exist in this fixture; model the acknowledged empty fanout.
        conn.execute(
            "UPDATE maintenance_fanout_jobs SET state='complete' WHERE tenant_id=%s", (tenant,)
        )


def converge(db, tenant, window):
    for _ in range(30):
        if cleanup_window(db, tenant, window, limit=1):
            return
    pytest.fail("Bounded cleanup did not converge")


def test_ended_non_deleted_timeline_never_expires(maintenance_browser, maintenance_db):
    db, _ = maintenance_db
    client, tenant, _, _ = maintenance_browser
    window = create(client, tenant)
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE maintenance_windows SET start_at=start_at-interval '200 days',end_at=end_at-interval '200 days' WHERE tenant_id=%s",
            (tenant,),
        )
        conn.execute(
            "UPDATE maintenance_fanout_jobs SET state='complete' WHERE tenant_id=%s", (tenant,)
        )
    assert cleanup_window(db, tenant, window["id"])
    assert (
        client.get(f"/api/tenants/{tenant}/maintenance/{window['id']}/history").status_code == 200
    )
    assert (
        len(
            client.get(f"/api/tenants/{tenant}/maintenance", params={"phase": "ended"}).json()[
                "data"
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "blocker",
    [
        "final_proof",
        pytest.param(
            "fanout",
            marks=pytest.mark.skip(
                reason="REV-34: deletion creates no release fan-out job and 0046 made old ones obsolete"
            ),
        ),
    ],
)
def test_missing_final_proof_and_pending_fanout_preserve_history(
    maintenance_browser, maintenance_db, blocker
):
    db, _ = maintenance_db
    client, tenant, window = deleted(maintenance_browser, db, final=blocker != "final_proof")
    if blocker == "final_proof":
        complete_empty_jobs(db, tenant)
    assert not cleanup_window(db, tenant, window["id"])
    assert (
        client.get(f"/api/tenants/{tenant}/maintenance/{window['id']}/history").status_code == 200
    )


def test_hold_release_bounded_erasure_and_minimal_fence(maintenance_browser, maintenance_db):
    db, _ = maintenance_db
    hold = uuid4()
    client, tenant, window = deleted(maintenance_browser, db, hold=hold)
    complete_empty_jobs(db, tenant)
    assert not cleanup_window(db, tenant, window["id"])
    path = f"/api/tenants/{tenant}/maintenance/{window['id']}/history"
    assert client.get(path).status_code == 200
    assert (
        len(
            client.get(f"/api/tenants/{tenant}/maintenance", params={"phase": "deleted"}).json()[
                "data"
            ]
        )
        == 1
    )
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE maintenance_configuration_holds SET released_at=clock_timestamp() WHERE id=%s",
            (hold,),
        )
    assert client.get(path).status_code == 404  # hidden before physical cleanup
    assert not cleanup_window(db, tenant, window["id"], limit=1)
    with pytest.raises(psycopg.errors.RaiseException, match="retirement already started"):
        with db.transaction(tenant) as conn:
            conn.execute(
                "INSERT INTO maintenance_configuration_holds(id,tenant_id,window_id,reason) VALUES(%s,%s,%s,'late hold')",
                (uuid4(), tenant, window["id"]),
            )
    converge(db, tenant, window["id"])
    with db.transaction(tenant) as conn:
        for table in (
            "maintenance_windows",
            "maintenance_revisions",
            "maintenance_controls",
            "maintenance_segments",
            "maintenance_fanout_jobs",
        ):
            key = "id" if table == "maintenance_windows" else "window_id"
            assert not conn.execute(
                sql.SQL("SELECT 1 FROM {} WHERE tenant_id=%s AND {}=%s").format(
                    sql.Identifier(table), sql.Identifier(key)
                ),
                (tenant, window["id"]),
            ).fetchone()
        identity = conn.execute(
            "SELECT * FROM maintenance_identities WHERE tenant_id=%s AND id=%s",
            (tenant, window["id"]),
        ).fetchone()
        assert identity["final_revision"] == 2 and identity["deleted_at"] is not None
        assert (
            conn.execute(
                "SELECT state FROM maintenance_content_retirements WHERE tenant_id=%s AND window_id=%s",
                (tenant, window["id"]),
            ).fetchone()["state"]
            == "cleaned"
        )
    assert client.get(path).status_code == 404


def test_same_time_earlier_order_negative_input_preserves_timeline(
    maintenance_browser, maintenance_db
):
    db, _ = maintenance_db
    client, tenant, window = deleted(maintenance_browser, db, active=True)
    complete_empty_jobs(db, tenant)
    alert = snapshot(tenant, message="never matched")
    with db.transaction(tenant) as conn:
        coordinator(conn, tenant)
        segment = conn.execute(
            "SELECT * FROM maintenance_segments WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        conn.execute(
            "INSERT INTO alert_identities(tenant_id,id) VALUES(%s,%s)", (tenant, alert["id"])
        )
        # An admitted input can precede a control at exactly the same clock time.
        record_input(
            conn,
            None,
            alert,
            segment["deactivation_at"],
            segment["deactivation_order"] - 1,
            "source",
            datetime.now(UTC),
        )
    assert not cleanup_window(db, tenant, window["id"])
    assert (
        client.get(f"/api/tenants/{tenant}/maintenance/{window['id']}/history").status_code == 200
    )
    with db.transaction(tenant) as conn:
        conn.execute("DELETE FROM maintenance_alert_input_heads WHERE tenant_id=%s", (tenant,))
        conn.execute("DELETE FROM maintenance_alert_inputs WHERE tenant_id=%s", (tenant,))
    converge(db, tenant, window["id"])


def test_retained_cause_history_preserves_whole_timeline(maintenance_browser, maintenance_db):
    db, _ = maintenance_db
    client, tenant, window = deleted(maintenance_browser, db)
    complete_empty_jobs(db, tenant)
    with db.transaction(tenant) as conn:
        alert = uuid4()
        conn.execute("INSERT INTO alert_identities(tenant_id,id) VALUES(%s,%s)", (tenant, alert))
        conn.execute(
            "INSERT INTO maintenance_cause_history(tenant_id,alert_id,id,cause_id,boundary_kind,boundary_id,effective_at,recorded_at,evidence) VALUES(%s,%s,%s,'synthetic','close','synthetic',clock_timestamp(),clock_timestamp(),%s)",
            (tenant, alert, uuid4(), Jsonb({"window_id": window["id"]})),
        )
    assert not cleanup_window(db, tenant, window["id"])
    assert (
        client.get(f"/api/tenants/{tenant}/maintenance/{window['id']}/history/1").status_code == 200
    )


def test_new_hold_cannot_revive_expired_content_before_physical_cleanup(
    maintenance_browser, maintenance_db
):
    db, _ = maintenance_db
    client, tenant, window = deleted(maintenance_browser, db)
    complete_empty_jobs(db, tenant)
    with pytest.raises(psycopg.errors.RaiseException, match="cannot be revived"):
        with db.transaction(tenant) as conn:
            conn.execute(
                "INSERT INTO maintenance_configuration_holds(id,tenant_id,window_id,reason) VALUES(%s,%s,%s,'late')",
                (uuid4(), tenant, window["id"]),
            )
    assert (
        client.get(f"/api/tenants/{tenant}/maintenance/{window['id']}/history").status_code == 404
    )


def test_cleanup_invalidates_precompiled_catalog_once(maintenance_browser, maintenance_db):
    db, _ = maintenance_db
    _, tenant, window = deleted(maintenance_browser, db)
    complete_empty_jobs(db, tenant)
    catalog = prepare_catalog(db, tenant, max_work=10000, max_inputs=100, max_segments=20)
    assert not cleanup_window(db, tenant, window["id"], limit=1)
    with pytest.raises(AppError, match="maintenance_catalog_changed"):
        with db.transaction(tenant) as conn:
            coordinator(conn, tenant)
            reconcile_alert(conn, snapshot(tenant), datetime.now(UTC), catalog=catalog)
    converge(db, tenant, window["id"])
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT maintenance_generation FROM tenant_view_state WHERE tenant_id=%s", (tenant,)
            ).fetchone()["maintenance_generation"]
            == catalog.generation + 1
        )


def test_worker_role_can_clean_but_api_cannot_add_holds_or_change_retired_fence(
    maintenance_browser, maintenance_db
):
    db, _ = maintenance_db
    _, tenant, window = deleted(maintenance_browser, db)
    complete_empty_jobs(db, tenant)

    class Worker:
        @contextmanager
        def transaction(self, tenant_id=None):
            with db.transaction(tenant_id) as conn:
                conn.execute("SET LOCAL ROLE appchi_worker")
                yield conn

    converge(Worker(), tenant, window["id"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction(tenant) as conn:
            conn.execute("SET LOCAL ROLE appchi_api")
            conn.execute(
                "INSERT INTO maintenance_configuration_holds(id,tenant_id,window_id,reason) VALUES(%s,%s,%s,'forbidden')",
                (uuid4(), tenant, window["id"]),
            )
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        with db.transaction(tenant) as conn:
            conn.execute("SET LOCAL ROLE appchi_worker")
            conn.execute(
                "UPDATE maintenance_identities SET deleted_at=NULL WHERE tenant_id=%s AND id=%s",
                (tenant, window["id"]),
            )


def test_scan_scrubs_terminal_job_actor_only_after_command_dependency(
    maintenance_browser, maintenance_db
):
    db, _ = maintenance_db
    _, tenant, window = deleted(maintenance_browser, db)
    complete_empty_jobs(db, tenant)
    converge(db, tenant, window["id"])
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE maintenance_delete_jobs SET updated_at=clock_timestamp()-interval '91 days',receipt=NULL WHERE tenant_id=%s",
            (tenant,),
        )
    cleanup_window(db, tenant, window["id"])
    with db.transaction(tenant) as conn:
        assert conn.execute(
            "SELECT actor_label FROM maintenance_delete_jobs WHERE tenant_id=%s", (tenant,)
        ).fetchone()["actor_label"]
        # The independently protected command compactor releases this FK.
        conn.execute(
            "UPDATE command_retention SET maintenance_delete_id=NULL WHERE tenant_id=%s", (tenant,)
        )
    cursor = None
    for _ in range(30):
        turn = cleanup_turn(db, cursor=cursor, limit=1)
        cursor = turn.cursor
        if cursor is None:
            break
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT actor_label FROM maintenance_delete_jobs WHERE tenant_id=%s", (tenant,)
            ).fetchone()["actor_label"]
            is None
        )
