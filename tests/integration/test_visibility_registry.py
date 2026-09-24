# ruff: noqa: F811 -- shared synthetic authenticated browser fixture
"""Draft schema runs only in an explicitly supplied isolated test database."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401
from test_permission_revision import RestrictedDatabase

from appchi.visibility.registry import RegistryLimits
from appchi.visibility.repository import snapshot_at
from appchi.visibility.routes import router

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def registry_schema(real_db):
    draft = Path(__file__).resolve().parents[3] / ".local/0031_visibility_registry.sql"
    with real_db.transaction() as conn:
        exists = conn.execute("SELECT to_regclass('visibility_views') AS name").fetchone()["name"]
        if not exists:
            conn.execute(draft.read_text(encoding="utf-8"))


@pytest.fixture
def registry(browser, real_db, tenant, registry_schema):
    client, actor = browser
    with real_db.transaction(tenant) as conn:
        conn.execute("INSERT INTO tenant_grants VALUES(%s,%s,'view')", (tenant, actor))
    client.app.include_router(router)
    client.app.state.visibility_registry_limits = RegistryLimits(
        lease_seconds=30,
        max_active_session_views=4,
        max_active_tenant_views=8,
        max_tenant_controls=100,
        max_descriptor_bytes=20000,
        max_request_bytes=65536,
    )
    original = client.app.state.db
    client.app.state.db = RestrictedDatabase(real_db)
    try:
        yield client, tenant, actor
    finally:
        client.app.state.db = original


def registration():
    return {
        "command_id": str(uuid4()),
        "tab_id": str(uuid4()),
        "view": {"surface": "alert_feed"},
        "state": "foreground",
    }


def test_register_lifecycle_replay_scope_and_no_activity(registry, real_db):
    client, tenant, actor = registry
    path = f"/api/tenants/{tenant}/view-subscriptions"
    body = registration()
    with real_db.transaction() as conn:
        activity = conn.execute(
            "SELECT activity_at FROM auth_sessions WHERE principal_id=%s", (actor,)
        ).fetchone()["activity_at"]
    result = client.post(path, json=body)
    assert result.status_code == 200, result.text
    first = result.json()
    assert first["meta"] == {"eligibility": "unproven", "render_proof": "unavailable"}
    assert client.post(path, json=body).json() == first
    view_path = path + "/" + first["data"]["id"]
    changed = {"command_id": str(uuid4()), "expected_revision": "1", "state": "background"}
    second = client.patch(view_path, json=changed)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["revision"] == "2"
    assert second.json()["data"]["view_epoch"] == "1"
    assert client.patch(view_path, json=changed).json() == second.json()
    assert client.get(view_path).json() == second.json()
    stale = client.patch(view_path, json={**changed, "command_id": str(uuid4())})
    assert stale.status_code == 409
    third = client.patch(
        view_path,
        json={
            "command_id": str(uuid4()),
            "expected_revision": "2",
            "state": "foreground",
            "view": {"surface": "alert_feed", "expression": "false"},
        },
    )
    assert third.status_code == 200 and third.json()["data"]["view_epoch"] == "2"
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT activity_at FROM auth_sessions WHERE principal_id=%s", (actor,)
            ).fetchone()["activity_at"]
            == activity
        )
        controls = conn.execute(
            "SELECT * FROM visibility_view_controls WHERE tenant_id=%s ORDER BY checkpoint",
            (tenant,),
        ).fetchall()
        assert len(controls) == 3
        old = snapshot_at(
            conn, tenant, first["data"]["id"], checkpoint=1, at=controls[-1]["recorded_at"]
        )
        assert old["state"] == "foreground" and old["view_epoch"] == "1"
        before_any = snapshot_at(
            conn,
            tenant,
            first["data"]["id"],
            checkpoint=3,
            at=controls[0]["recorded_at"] - timedelta(microseconds=1),
        )
        assert before_any is None


def test_current_auth_and_csrf_precede_replay(registry, real_db):
    client, tenant, actor = registry
    path = f"/api/tenants/{tenant}/view-subscriptions"
    body = registration()
    assert client.post(path, json=body, headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert (
        client.post(path, json=body, headers={"Origin": "https://foreign.invalid"}).status_code
        == 403
    )
    good = client.post(path, json=body)
    assert good.status_code == 200
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, actor)
        )
    assert client.post(path, json=body).status_code == 404
    assert client.get(path + "/" + good.json()["data"]["id"]).status_code == 404
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM visibility_view_controls WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 1
        )


def test_other_session_cannot_read_or_change_view(registry, real_db):
    import hashlib

    client, tenant, actor = registry
    path = f"/api/tenants/{tenant}/view-subscriptions"
    view = client.post(path, json=registration()).json()["data"]
    handle = "different-synthetic-session-" + str(uuid4())
    with real_db.transaction() as conn:
        conn.execute(
            """INSERT INTO auth_sessions(handle_hash,principal_id,issuer,subject,provider_sid,
          csrf_hash,csrf_cipher,auth_time,activity_at,absolute_at,deployment_epoch)
          SELECT %s,principal_id,issuer,subject,provider_sid,csrf_hash,csrf_cipher,auth_time,
          activity_at,absolute_at,deployment_epoch FROM auth_sessions WHERE principal_id=%s""",
            (hashlib.sha256(handle.encode()).digest(), actor),
        )
    client.cookies.set("__Host-appchi_session", handle)
    assert client.get(path + "/" + view["id"]).status_code == 404
    assert (
        client.patch(
            path + "/" + view["id"],
            json={"command_id": str(uuid4()), "expected_revision": "1", "state": "closed"},
        ).status_code
        == 404
    )


def test_concurrent_same_revision_only_one_control_commits(registry, real_db):
    client, tenant, _ = registry
    path = f"/api/tenants/{tenant}/view-subscriptions"
    first = client.post(path, json=registration()).json()["data"]
    path += "/" + first["id"]

    def send(state):
        return client.patch(
            path, json={"command_id": str(uuid4()), "expected_revision": "1", "state": state}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(send, ["foreground", "background"]))
    assert sorted(r.status_code for r in outcomes) == [200, 409]
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM visibility_view_controls WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 2
        )


def test_explicit_capacity_body_limit_closed_fence_and_unconfigured(registry, real_db):
    client, tenant, _ = registry
    path = f"/api/tenants/{tenant}/view-subscriptions"
    limits = client.app.state.visibility_registry_limits
    client.app.state.visibility_registry_limits = replace(limits, max_active_session_views=1)
    first = client.post(path, json=registration()).json()["data"]
    assert client.post(path, json=registration()).status_code == 503
    close = {"command_id": str(uuid4()), "expected_revision": "1", "state": "closed"}
    assert client.patch(path + "/" + first["id"], json=close).status_code == 200
    assert client.patch(path + "/" + first["id"], json=close).status_code == 200
    assert (
        client.patch(
            path + "/" + first["id"],
            json={
                **close,
                "command_id": str(uuid4()),
                "expected_revision": "2",
                "state": "foreground",
            },
        ).status_code
        == 409
    )
    assert client.post(path, json=registration()).status_code == 200
    assert (
        client.post(
            path, content=b" " * 65537, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )
    client.app.state.visibility_registry_limits = replace(limits, max_tenant_controls=3)
    assert client.post(path, json=registration()).status_code == 503
    client.app.state.visibility_registry_limits = None
    assert client.post(path, json=registration()).status_code == 503
    assert (
        client.post(
            f"/api/tenants/{tenant}/view-observations", json={"observations": []}
        ).status_code
        == 404
    )


def test_concurrent_registration_cannot_exceed_capacity(registry, real_db):
    client, tenant, _ = registry
    client.app.state.visibility_registry_limits = replace(
        client.app.state.visibility_registry_limits, max_active_session_views=1
    )
    path = f"/api/tenants/{tenant}/view-subscriptions"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(lambda body: client.post(path, json=body), [registration(), registration()])
        )
    assert sorted(r.status_code for r in outcomes) == [200, 503]
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM visibility_views WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 1
        )


def test_authorization_change_wins_before_waiting_registration(registry, real_db):
    client, tenant, actor = registry
    path = f"/api/tenants/{tenant}/view-subscriptions"
    attempted = Event()

    def hook(conn, query):
        if query == "SELECT scope FROM authorization_guards WHERE scope='identity' FOR SHARE":
            attempted.set()

    client.app.state.db.hook = hook
    with ThreadPoolExecutor(max_workers=1) as pool:
        with real_db.transaction(tenant) as conn:
            conn.execute("SELECT scope FROM authorization_guards WHERE scope='identity' FOR UPDATE")
            response = pool.submit(client.post, path, json=registration())
            assert attempted.wait(1)
            assert not response.done()
            conn.execute(
                "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, actor)
            )
        assert response.result().status_code == 404
    client.app.state.db.hook = None
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM visibility_view_controls WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )


def test_noncanonical_paths_rejected(registry):
    client, tenant, _ = registry
    assert (
        client.post(
            f"/api/tenants/{str(tenant).upper()}/view-subscriptions", json=registration()
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/tenants/{str(tenant).replace('-', '')}/view-subscriptions", json=registration()
        ).status_code
        == 422
    )
