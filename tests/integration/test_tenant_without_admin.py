# ruff: noqa: F811 -- shared synthetic authenticated browser fixture
"""Tenant creation without grants and the required first administrator (REV-71)."""

from contextlib import contextmanager
from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


class Restricted:
    """Run the Product API's transactions under its restricted database role."""

    def __init__(self, database):
        self.database = database

    @contextmanager
    def transaction(self, *args, **kwargs):
        with self.database.transaction(*args, **kwargs) as conn:
            conn.execute("SET LOCAL ROLE keep_api")
            yield conn


@pytest.fixture
def superadmin(browser, real_db):
    client, actor = browser
    other = uuid4()
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (actor,))
        conn.execute(
            """INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible,verified_at)
            VALUES(%s,'tenant-without-admin-test',%s,'user','First administrator',true,clock_timestamp())""",
            (other, str(other)),
        )
    original = client.app.state.db
    client.app.state.db = Restricted(real_db)
    try:
        yield client, actor, other
    finally:
        client.app.state.db = original
        with real_db.transaction() as conn:
            conn.execute("UPDATE principals SET super_admin=false WHERE id=%s", (actor,))


def create(client, grants):
    body = {
        "command_id": str(uuid4()),
        "name": "No admin yet " + str(uuid4()),
        "initial_grants": grants,
    }
    return body, client.post("/api/tenants", json=body)


def grant(revision, principal, role):
    return {
        "command_id": str(uuid4()),
        "expected_revision": revision,
        "operations": [{"principal_id": str(principal), "action": "set", "role": role}],
    }


def test_superadmin_creates_tenant_without_grants_and_is_not_added(superadmin, real_db):
    """Requirements: REV-71; tenant access 2, 12 and 13.

    An empty grant list commits the tenant, its audit and receipt without adding
    the creator, and the roster reports that no local administrator exists.
    """
    client, _, _ = superadmin
    body, response = create(client, [])
    assert response.status_code == 201, response.text
    created = response.json()["data"]
    assert created["role"] == "admin" and created["revision"] == "1"
    assert client.post("/api/tenants", json=body).json() == response.json()
    tenant = created["id"]
    listed = client.get("/api/tenants").json()["data"]
    assert next(row for row in listed if row["id"] == tenant)["role"] == "admin"
    roster = client.get(f"/api/tenants/{tenant}/permissions")
    assert roster.status_code == 200, roster.text
    assert roster.json()["data"] == []
    assert roster.json()["meta"] == {"revision": "1", "has_local_admin": False}
    with real_db.transaction(tenant) as conn:
        assert not conn.execute(
            "SELECT 1 FROM tenant_grants WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        event = conn.execute(
            "SELECT evidence FROM audit_events WHERE tenant_id=%s AND kind='tenant-created'",
            (tenant,),
        ).fetchone()
        assert event and event["evidence"]["initial_grants"] == []
        assert not conn.execute(
            "SELECT 1 FROM audit_events WHERE tenant_id=%s AND kind='local_admin_vacancy'",
            (tenant,),
        ).fetchone()


def test_first_permission_batch_must_add_an_eligible_admin(superadmin, real_db):
    """Requirements: REV-71; tenant access 2 and 4.

    A tenant created without grants rejects a batch that leaves it without an
    eligible local administrator and accepts the batch that adds one.
    """
    client, _, other = superadmin
    _, response = create(client, [])
    tenant = response.json()["data"]["id"]
    path = f"/api/tenants/{tenant}/permissions"
    rejected = client.patch(path, json=grant("1", other, "view"))
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "last_admin_required"
    assert client.get(path).json() == {
        "data": [],
        "meta": {"revision": "1", "has_local_admin": False},
        "next_cursor": None,
    }
    accepted = client.patch(path, json=grant("1", other, "admin"))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["revision"] == "2"
    roster = client.get(path).json()
    assert roster["meta"] == {"revision": "2", "has_local_admin": True}
    assert [(row["principal_id"], row["role"]) for row in roster["data"]] == [
        (str(other), "admin")
    ]


def test_nonempty_initial_grants_still_need_an_admin(superadmin, real_db):
    """Requirements: REV-71; tenant access 2.

    Grants given at creation keep the earlier rule: without an eligible local
    administrator nothing is created.
    """
    client, _, other = superadmin
    body, response = create(client, [{"principal_id": str(other), "role": "edit"}])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "last_admin_required"
    with real_db.transaction() as conn:
        assert not conn.execute(
            "SELECT 1 FROM tenants WHERE name=%s", (body["name"],)
        ).fetchone()
    _, ok = create(client, [{"principal_id": str(other), "role": "admin"}])
    assert ok.status_code == 201, ok.text
    roster = client.get(f"/api/tenants/{ok.json()['data']['id']}/permissions").json()
    assert roster["meta"]["has_local_admin"] is True
