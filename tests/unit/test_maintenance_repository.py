"""Real PostgreSQL maintenance API tests using synthetic database-only identity fixtures."""

import base64
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

from appchi.config import Settings
from appchi.db import Database
from appchi.maintenance.routes import router
from appchi.migrate import migrate
from appchi.recovery.evidence import DurableAck
from appchi.shared.errors import AppError, unavailable

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def maintenance_db():
    admin = os.environ.get("APPCHI_MAINTENANCE_TEST_ADMIN_URL") or os.environ.get(
        "APPCHI_IDENTITY_TEST_ADMIN_URL"
    )
    if not admin:
        pytest.skip(
            "Set APPCHI_MAINTENANCE_TEST_ADMIN_URL to an isolated test cluster with CREATE DATABASE privilege"
        )
    name = "appchi_maintenance_test_" + uuid4().hex
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    params = conninfo_to_dict(admin)
    params["dbname"] = name
    dsn = make_conninfo(**params)
    migrate(dsn, Path(__file__).resolve().parents[3] / "migrations")
    db = Database(dsn)
    yield db, dsn
    db.close()
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


class Store:
    def __init__(self):
        self.events = []
        self.fail_final = False

    def append(self, evidence):
        self.events.append(evidence)
        if evidence.state == "final" and self.fail_final:
            raise unavailable("controlled_final_failure")
        return DurableAck(evidence.id, "synthetic-independent-store", "checkpoint", evidence.digest)


@pytest.fixture
def maintenance_browser(maintenance_db):
    db, dsn = maintenance_db
    tenant, principal = uuid4(), uuid4()
    handle, csrf = uuid4().hex + uuid4().hex, uuid4().hex + uuid4().hex
    settings = Settings(
        database_url=dsn,
        wrapping_key=base64.b64encode(b"k" * 32).decode(),
        deployment_epoch="test",
        public_origin="https://app.test",
    )
    with db.transaction() as conn:
        conn.execute(
            "UPDATE capability_gates SET ready=true WHERE capability IN ('human_auth','maintenance','recovery')"
        )
        conn.execute(
            "INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
            (tenant, str(tenant), str(tenant)),
        )
        for table in ("tenant_work_state", "tenant_view_state", "tenant_configuration_guard"):
            conn.execute(
                sql.SQL("INSERT INTO {}(tenant_id) VALUES(%s)").format(sql.Identifier(table)),
                (tenant,),
            )
        conn.execute(
            "INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible,evidence_generation,verified_at) VALUES(%s,'synthetic',%s,'user','Test editor',true,1,clock_timestamp())",
            (principal, str(principal)),
        )
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, principal),
        )
        digest = hashlib.sha256(handle.encode()).digest()
        conn.execute(
            """INSERT INTO auth_sessions(handle_hash,principal_id,issuer,subject,provider_sid,csrf_hash,csrf_cipher,auth_time,activity_at,absolute_at,deployment_epoch)
                     VALUES(%s,%s,'https://synthetic.invalid',%s,%s,%s,%s,clock_timestamp(),clock_timestamp(),clock_timestamp()+interval '1 day','test')""",
            (
                digest,
                principal,
                str(principal),
                str(principal),
                hashlib.sha256(csrf.encode()).digest(),
                settings.cipher().seal(csrf.encode(), "session-csrf:" + digest.hex()),
            ),
        )
    app = FastAPI()
    app.state.db = db
    app.state.settings = settings
    app.state.profile = {"maintenance": {"max_overlap": 3, "max_work_bytes": 100000}}
    app.state.recovery_store = Store()

    @app.exception_handler(AppError)
    async def errors(request, error):
        return JSONResponse({"error": {"code": error.code}}, status_code=error.status)

    app.include_router(router)
    with TestClient(app, base_url="https://app.test") as client:
        client.cookies.set("__Host-appchi_session", handle)
        client.headers.update({"Origin": "https://app.test", "X-CSRF-Token": csrf})
        yield client, str(tenant), principal, app
    with db.transaction() as conn:
        conn.execute(
            "UPDATE capability_gates SET ready=false WHERE capability IN ('human_auth','maintenance','recovery')"
        )


def body(*, past=False):
    now = datetime.now(UTC).replace(microsecond=0)
    return {
        "command_id": str(uuid4()),
        "name": "Maintenance",
        "reason": "Planned test",
        "cel": 'alert.message == "match"',
        "timezone": "UTC",
        "start_local": (now + timedelta(minutes=-10 if past else 10))
        .replace(tzinfo=None)
        .isoformat(),
        "end_local": (now + timedelta(hours=1)).replace(tzinfo=None).isoformat(),
    }


def create(client, tenant, definition=None):
    response = client.post(f"/api/tenants/{tenant}/maintenance", json=definition or body())
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_create_replay_duplicate_names_no_backfill_and_history(maintenance_browser, maintenance_db):
    client, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    definition = body()
    first = create(client, tenant, definition)
    assert create(client, tenant, definition) == first
    second = create(client, tenant)
    assert second["id"] != first["id"] and second["name"] == first["name"]
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_controls WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 2
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM alert_maintenance_causes WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_fanout_jobs WHERE tenant_id=%s AND kind='control' AND state='complete'",
                (tenant,),
            ).fetchone()["n"]
            == 0
        )
    history = client.get(f"/api/tenants/{tenant}/maintenance/{first['id']}/history").json()["data"]
    assert len(history) == 1 and history[0]["action"] == "create"


def test_gate_strict_models_and_revision_concurrency(maintenance_browser, maintenance_db):
    client, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    path = f"/api/tenants/{tenant}/maintenance"
    with db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='maintenance'")
    assert client.post(path, json=body()).status_code == 503
    with db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='maintenance'")
    assert client.post(path, json={**body(), "recurrence": "daily"}).status_code == 422
    window = create(client, tenant)
    edit = {
        "command_id": str(uuid4()),
        "expected_revision": "1",
        "kind": "metadata",
        "name": "Updated",
    }
    first = client.patch(path + "/" + window["id"], json=edit)
    assert first.status_code == 200 and first.json()["data"]["revision"] == "2"
    assert client.patch(path + "/" + window["id"], json=edit).json() == first.json()
    assert (
        client.patch(
            path + "/" + window["id"], json={**edit, "command_id": str(uuid4())}
        ).status_code
        == 409
    )


def test_same_value_noop_preserves_revision_control_and_activity(
    maintenance_browser, maintenance_db
):
    client, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    window = create(client, tenant)
    result = client.patch(
        f"/api/tenants/{tenant}/maintenance/{window['id']}",
        json={
            "kind": "metadata",
            "name": window["name"],
            "command_id": str(uuid4()),
            "expected_revision": "1",
        },
    )
    assert result.status_code == 200 and result.json()["data"]["revision"] == "1"
    with db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_revisions WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_controls WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 1
        )


def test_started_lock_timezone_only_and_end_extension_keep_activation(
    maintenance_browser, maintenance_db
):
    client, tenant, _, _ = maintenance_browser
    db, _ = maintenance_db
    window = create(client, tenant, body(past=True))
    path = f"/api/tenants/{tenant}/maintenance/{window['id']}"
    response = client.patch(
        path,
        json={
            "kind": "schedule",
            "cel": 'alert.message == "other"',
            "command_id": str(uuid4()),
            "expected_revision": "1",
        },
    )
    assert response.status_code == 409
    changed = client.patch(
        path,
        json={
            "kind": "timezone",
            "timezone": "Asia/Jerusalem",
            "command_id": str(uuid4()),
            "expected_revision": "1",
        },
    )
    assert changed.status_code == 200 and changed.json()["data"]["start_at"] == window["start_at"]
    with db.transaction(tenant) as conn:
        old = conn.execute(
            "SELECT id,activation_at,activation_order FROM maintenance_segments WHERE tenant_id=%s",
            (tenant,),
        ).fetchone()
    new_end = (
        (datetime.now(UTC) + timedelta(hours=2)).replace(tzinfo=None, microsecond=0).isoformat()
    )
    changed = client.patch(
        path,
        json={
            "kind": "schedule",
            "timezone": "UTC",
            "end_local": new_end,
            "command_id": str(uuid4()),
            "expected_revision": "2",
        },
    )
    assert changed.status_code == 200, changed.text
    with db.transaction(tenant) as conn:
        current = conn.execute(
            "SELECT * FROM maintenance_segments WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert all(current[k] == v for k, v in old.items()) and len(current["end_revisions"]) == 2


def test_disable_enable_new_segment_without_fanout_and_capacity(
    maintenance_browser, maintenance_db
):
    client, tenant, _, app = maintenance_browser
    db, _ = maintenance_db
    app.state.profile["maintenance"]["max_overlap"] = 1
    window = create(client, tenant)
    path = f"/api/tenants/{tenant}/maintenance/{window['id']}"
    disabled = client.post(
        path + "/disable", json={"command_id": str(uuid4()), "expected_revision": "1"}
    )
    assert disabled.status_code == 200
    # Disabled windows retain their reservation until an explicit release policy.
    assert client.post(f"/api/tenants/{tenant}/maintenance", json=body()).status_code == 409
    assert (
        client.post(
            path + "/enable", json={"command_id": str(uuid4()), "expected_revision": "2"}
        ).status_code
        == 200
    )
    with db.transaction(tenant) as conn:
        segments = conn.execute(
            "SELECT * FROM maintenance_segments WHERE tenant_id=%s ORDER BY activation_order",
            (tenant,),
        ).fetchall()
        assert (
            len(segments) == 2
            and segments[0]["deactivation_id"]
            and segments[1]["deactivation_id"] is None
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM maintenance_fanout_jobs WHERE tenant_id=%s AND state='pending' AND kind='control'",
                (tenant,),
            ).fetchone()["n"]
            == 0
        )


def test_pagination_is_session_query_bound_and_current_access_is_rechecked(
    maintenance_browser, maintenance_db
):
    client, tenant, principal, app = maintenance_browser
    db, _ = maintenance_db
    app.state.profile["maintenance"]["max_overlap"] = 10
    for _ in range(3):
        create(client, tenant)
    path = f"/api/tenants/{tenant}/maintenance"
    first = client.get(path, params={"limit": 1}).json()
    second = client.get(path, params={"limit": 1, "cursor": first["next_cursor"]})
    assert second.status_code == 200 and second.json()["data"][0]["id"] != first["data"][0]["id"]
    assert (
        client.get(
            path, params={"limit": 1, "name": "changed", "cursor": first["next_cursor"]}
        ).status_code
        == 409
    )
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, principal)
        )
    assert client.get(path, params={"cursor": first["next_cursor"]}).status_code == 404


def test_protected_delete_retries_uncertain_final_with_stable_distinct_evidence(
    maintenance_browser, maintenance_db
):
    client, tenant, _, app = maintenance_browser
    db, _ = maintenance_db
    window = create(client, tenant)
    path = f"/api/tenants/{tenant}/maintenance/{window['id']}"
    headers = {"Idempotency-Key": str(uuid4()), "If-Match": '"1"'}
    store = app.state.recovery_store
    store.fail_final = True
    result = client.delete(path, headers=headers)
    assert result.status_code == 503, result.text
    assert client.get(path).status_code == 404
    with db.transaction(tenant) as conn:
        job = conn.execute(
            "SELECT * FROM maintenance_delete_jobs WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert job["state"] == "deleted"
        assert len({job["intent_id"], job["final_id"], job["cancel_id"]}) == 3
    store.fail_final = False
    result = client.delete(path, headers=headers)
    assert result.status_code == 200 and result.json()["data"]["deleted"]
    assert client.delete(path, headers=headers).json() == result.json()
    final_ids = [event.id for event in store.events if event.state == "final"]
    assert len(final_ids) == 2 and len(set(final_ids)) == 1
    assert client.get(path + "/history").status_code == 200


def test_delete_without_independent_store_leaves_window_intact(maintenance_browser):
    client, tenant, _, app = maintenance_browser
    window = create(client, tenant)
    path = f"/api/tenants/{tenant}/maintenance/{window['id']}"
    app.state.recovery_store = None
    assert (
        client.delete(
            path, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"1"'}
        ).status_code
        == 503
    )
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("change", ["role", "recovery", "maintenance"])
def test_delete_rechecks_current_authorization_and_gates_after_external_intent(
    maintenance_browser, maintenance_db, change
):
    client, tenant, principal, app = maintenance_browser
    db, _ = maintenance_db
    window = create(client, tenant)
    path = f"/api/tenants/{tenant}/maintenance/{window['id']}"
    store = app.state.recovery_store
    append = store.append

    def race(evidence):
        ack = append(evidence)
        if evidence.state == "intent":
            with db.transaction(tenant) as conn:
                if change == "role":
                    conn.execute(
                        "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
                        (tenant, principal),
                    )
                else:
                    conn.execute(
                        "UPDATE capability_gates SET ready=false WHERE capability=%s", (change,)
                    )
        return ack

    store.append = race
    result = client.delete(path, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"1"'})
    assert result.status_code == (409 if change == "role" else 503), result.text
    with db.transaction(tenant) as conn:
        row = conn.execute(
            "SELECT * FROM maintenance_windows WHERE tenant_id=%s AND id=%s", (tenant, window["id"])
        ).fetchone()
        assert row["revision"] == 1 and row["deleted_at"] is None
        job = conn.execute(
            "SELECT * FROM maintenance_delete_jobs WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert job["state"] == ("cancelled" if change == "role" else "prepared")
    assert not any(event.state == "final" for event in store.events)


def test_delete_cancel_ack_is_verified_before_persisting(maintenance_browser, maintenance_db):
    from appchi.cel import compile_expression
    from appchi.maintenance.routes import resume_delete

    client, tenant, principal, app = maintenance_browser
    db, _ = maintenance_db
    window = create(client, tenant)
    path = f"/api/tenants/{tenant}/maintenance/{window['id']}"
    store = app.state.recovery_store
    append = store.append

    def reject_cancel(evidence):
        ack = append(evidence)
        if evidence.state == "intent":
            with db.transaction(tenant) as conn:
                conn.execute(
                    "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
                    (tenant, principal),
                )
        if evidence.state == "cancel":
            return DurableAck(str(uuid4()), ack.namespace, ack.checkpoint, ack.digest)
        return ack

    store.append = reject_cancel
    result = client.delete(path, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"1"'})
    assert (
        result.status_code == 503
        and result.json()["error"]["code"] == "recovery_protection_unproven"
    )
    with db.transaction(tenant) as conn:
        job = conn.execute(
            "SELECT * FROM maintenance_delete_jobs WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert job["state"] == "cancelled" and job["cancel_ack"] is None
    store.append = append
    with pytest.raises(AppError, match="revision_conflict"):
        resume_delete(db, store, tenant, job["id"], compile_expression(window["cel"], rule=True))
    cancel_ids = [event.id for event in store.events if event.state == "cancel"]
    assert len(cancel_ids) == 2 and len(set(cancel_ids)) == 1


def test_viewer_can_validate_and_test_but_not_mutate(maintenance_browser, maintenance_db):
    client, tenant, principal, _ = maintenance_browser
    db, _ = maintenance_db
    with db.transaction() as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, principal),
        )
    definition = body()
    definition.pop("command_id")
    response = client.post(f"/api/tenants/{tenant}/maintenance/validate", json=definition)
    assert response.status_code == 200 and response.json()["data"]["valid"]
    assert client.post(f"/api/tenants/{tenant}/maintenance", json=body()).status_code == 403
    assert client.get(f"/api/tenants/{tenant}/timezones").status_code == 200


def test_parallel_expected_revision_and_capacity_races_serialize(
    maintenance_browser, maintenance_db
):
    client, tenant, _, app = maintenance_browser
    window = create(client, tenant)
    path = f"/api/tenants/{tenant}/maintenance/{window['id']}"

    def edit(name):
        return client.patch(
            path,
            json={
                "kind": "metadata",
                "name": name,
                "expected_revision": "1",
                "command_id": str(uuid4()),
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(edit, ("One", "Two"))) == [200, 409]
    app.state.profile["maintenance"]["max_overlap"] = 2

    def create_another(_):
        return client.post(f"/api/tenants/{tenant}/maintenance", json=body()).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(create_another, range(2))) == [201, 409]


def test_nonowner_api_role_and_rls_protect_maintenance_and_delete_resume(
    maintenance_browser, maintenance_db
):
    client, tenant, _, app = maintenance_browser
    db, _ = maintenance_db

    class RuntimeDatabase:
        @contextmanager
        def transaction(self, tenant_id=None):
            with db.transaction(tenant_id) as conn:
                conn.execute("SET LOCAL ROLE appchi_api")
                yield conn

    app.state.db = RuntimeDatabase()
    window = create(client, tenant)
    with db.transaction() as conn:
        conn.execute("SET LOCAL ROLE appchi_api")
        assert conn.execute("SELECT count(*) AS n FROM maintenance_windows").fetchone()["n"] == 0
    result = client.delete(
        f"/api/tenants/{tenant}/maintenance/{window['id']}",
        headers={"Idempotency-Key": str(uuid4()), "If-Match": '"1"'},
    )
    assert result.status_code == 200, result.text
