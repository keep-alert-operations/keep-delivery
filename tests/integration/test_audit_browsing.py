# ruff: noqa: F811 -- shared synthetic authenticated fixture
"""Read-only audit filters exercise real PostgreSQL and the restricted API role."""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


class RestrictedDatabase:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def transaction(self, *args, **kwargs):
        with self.database.transaction(*args, **kwargs) as conn:
            conn.execute("SET LOCAL ROLE appchi_api")
            yield conn
            assert conn.execute("SELECT current_user AS role").fetchone()["role"] == "appchi_api"


@pytest.fixture
def audit_browser(browser, real_db, tenant):
    client, actor = browser
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'admin')",
            (tenant, actor),
        )
        now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    original = client.app.state.db
    client.app.state.db = RestrictedDatabase(real_db)
    try:
        yield client, tenant, actor, now
    finally:
        client.app.state.db = original


def event(db, tenant, actor, at, kind="tenant-renamed", evidence=None):
    identifier = uuid4()
    with db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO audit_events(id,tenant_id,actor_id,actor_label,kind,recorded_at,evidence) VALUES(%s,%s,%s,'Captured <actor>',%s,%s,%s)",
            (identifier, tenant, actor, kind, at, Jsonb(evidence or {})),
        )
    return str(identifier)


def ids(response):
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["data"]]


def test_default_30_days_explicit_retained_protected_and_time_range(audit_browser, real_db):
    client, tenant, actor, now = audit_browser
    recent = event(real_db, tenant, actor, now - timedelta(days=1))
    older = event(real_db, tenant, actor, now - timedelta(days=40))
    expired = event(real_db, tenant, actor, now - timedelta(days=366))
    protected = event(real_db, tenant, actor, now - timedelta(days=400))
    fenced = event(real_db, tenant, actor, now - timedelta(hours=1))
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO audit_retention_holds(event_id,kind,reference) VALUES(%s,'recovery','proof')",
            (protected,),
        )
        conn.execute(
            "INSERT INTO audit_expiry_fences(event_id,scope_tenant_id,event_recorded_at,final_evidence_id,digest,retired_at) VALUES(%s,%s,%s,%s,%s,%s)",
            (fenced, tenant, now, uuid4(), "a" * 64, now),
        )
    path = f"/api/tenants/{tenant}/audit"
    assert ids(client.get(path)) == [recent]
    retained = client.get(path, params={"period": "retained"})
    assert ids(retained) == [recent, older, protected]
    assert retained.json()["data"][-1]["dependency_protected"] is True
    assert expired not in ids(retained) and fenced not in ids(retained)
    ranged = client.get(
        path,
        params={
            "from_at": (now - timedelta(days=50)).isoformat(),
            "to_at": (now - timedelta(days=2)).isoformat(),
        },
    )
    assert ids(ranged) == [older]
    for params in (
        {"from_at": "2026-01-01T00:00:00"},
        {"period": "forever"},
        {"actor_id": "not-a-uuid"},
        {"limit": 201},
        {"from_at": now.isoformat(), "to_at": now.isoformat()},
    ):
        assert client.get(path, params=params).status_code == 422


def test_exact_filters_match_affected_evidence_not_unrelated_before_roster(audit_browser, real_db):
    client, tenant, actor, now = audit_browser
    target, unrelated = str(uuid4()), str(uuid4())
    changed = event(
        real_db,
        tenant,
        actor,
        now,
        "permissions-changed",
        {
            "changes": [{"principal_id": target, "action": "remove"}],
            "before": {target: "edit", unrelated: "admin"},
        },
    )
    created = event(
        real_db,
        tenant,
        actor,
        now - timedelta(seconds=1),
        "tenant-created",
        {"initial_grants": [{"principal_id": target, "role": "admin"}]},
    )
    directory = event(
        real_db,
        tenant,
        None,
        now - timedelta(seconds=2),
        "directory_access_changed",
        {"principal_id": target, "before": 2, "after": None},
    )
    path = f"/api/tenants/{tenant}/audit"
    assert ids(client.get(path, params={"affected_principal_id": target})) == [
        changed,
        created,
        directory,
    ]
    assert ids(client.get(path, params={"affected_principal_id": unrelated})) == []
    assert ids(
        client.get(
            path,
            params={
                "action": "permissions-changed",
                "actor_id": str(actor),
                "affected_principal_id": target,
                "resource_id": str(tenant),
            },
        )
    ) == [changed]
    assert ids(client.get(path, params={"actor_id": "system"})) == [directory]
    assert ids(client.get(path, params={"action": "permissions%"})) == []
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET display_name='New name' WHERE id=%s", (actor,))
    assert client.get(path).json()["data"][0]["actor_label"] == "Captured <actor>"


def test_cursor_fixes_watermark_filters_and_rechecks_holds_and_authority(audit_browser, real_db):
    client, tenant, actor, now = audit_browser
    first_id = event(real_db, tenant, actor, now)
    old_id = event(real_db, tenant, actor, now - timedelta(days=400))
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO audit_retention_holds(event_id,kind,reference) VALUES(%s,'reference','retained')",
            (old_id,),
        )
    path = f"/api/tenants/{tenant}/audit"
    params = {"period": "retained", "limit": 1}
    first = client.get(path, params=params).json()
    assert first["data"][0]["id"] == first_id and first["next_cursor"]
    new_id = event(real_db, tenant, actor, now + timedelta(microseconds=1))
    # Use an actual later recording instant, after the first upper watermark.
    with real_db.transaction() as conn:
        conn.execute("UPDATE audit_events SET recorded_at=clock_timestamp() WHERE id=%s", (new_id,))
    next_params = {**params, "cursor": first["next_cursor"]}
    assert ids(client.get(path, params=next_params)) == [old_id]
    assert client.get(path, params={**next_params, "action": "tenant-renamed"}).status_code == 409
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE audit_retention_holds SET released_at=clock_timestamp() WHERE event_id=%s",
            (old_id,),
        )
    assert ids(client.get(path, params=next_params)) == []
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, actor),
        )
    assert client.get(path, params=next_params).status_code == 403


def test_platform_tenant_resource_filters_never_mix_tenant_trail(audit_browser, real_db):
    client, tenant, actor, now = audit_browser
    path = "/api/admin/access-audit"
    assert client.get(path).status_code == 403
    head = str(uuid4())
    platform = event(
        real_db,
        None,
        actor,
        now,
        "source-processing-retry",
        {"target_tenant_id": str(tenant), "head_id": head, "reason": "Reviewed repair"},
    )
    event(
        real_db,
        None,
        actor,
        now - timedelta(seconds=1),
        "source-processing-retry",
        {"target_tenant_id": str(uuid4()), "head_id": str(uuid4())},
    )
    event(real_db, tenant, actor, now, "tenant-renamed")
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (actor,))
    assert ids(client.get(path, params={"tenant_id": str(tenant)})) == [platform]
    assert ids(client.get(path, params={"resource_id": head})) == [platform]
    missing = client.get(path, params={"resource_id": head}).json()["data"][0]["references"]
    assert {"kind": "source-item", "id": head, "available": False} in missing
    assert ids(client.get(path, params={"resource_id": str(tenant)})) == [platform]
    first = client.get(path, params={"limit": 1}).json()
    assert first["next_cursor"]
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=false WHERE id=%s", (actor,))
    assert client.get(path, params={"limit": 1, "cursor": first["next_cursor"]}).status_code == 403
