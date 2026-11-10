# ruff: noqa: F811 -- shared synthetic authenticated browser fixture
"""Permission roster/revision regression through the real restricted API role."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


class RestrictedDatabase:
    def __init__(self, database):
        self.database = database
        self.roles = []
        self.hook = None

    @contextmanager
    def transaction(self, *args, **kwargs):
        with self.database.transaction(*args, **kwargs) as conn:
            conn.execute("SET LOCAL ROLE keep_api")
            self.roles.append(conn.execute("SELECT current_user AS role").fetchone()["role"])
            yield HookedConnection(conn, self.hook) if self.hook else conn


class HookedConnection:
    def __init__(self, conn, hook):
        self.conn, self.hook = conn, hook

    def execute(self, query, *args, **kwargs):
        self.hook(self.conn, query)
        return self.conn.execute(query, *args, **kwargs)


@pytest.fixture
def permissions(browser, real_db, tenant):
    client, actor = browser
    other = uuid4()
    with real_db.transaction(tenant) as conn:
        conn.execute(
            """INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible,verified_at)
            VALUES(%s,'permission-revision-test',%s,'user','Other eligible user',true,clock_timestamp())""",
            (other, str(other)),
        )
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'admin'),(%s,%s,'edit')",
            (tenant, actor, tenant, other),
        )
    restricted = RestrictedDatabase(real_db)
    original = client.app.state.db
    client.app.state.db = restricted
    try:
        yield client, tenant, actor, other, restricted
    finally:
        client.app.state.db = original


def payload(revision, principal, role="view"):
    return {
        "command_id": str(uuid4()),
        "expected_revision": revision,
        "operations": [{"principal_id": str(principal), "action": "set", "role": role}],
    }


def test_restricted_role_uses_roster_revision_and_rejects_intervening_change(permissions, real_db):
    client, tenant, actor, other, restricted = permissions
    path = f"/api/tenants/{tenant}/permissions"
    before = client.get(path)
    assert before.status_code == 200, before.text
    envelope = before.json()
    assert envelope["meta"]["revision"] == "1"
    assert {row["principal_id"]: row["role"] for row in envelope["data"]} == {
        str(actor): "admin",
        str(other): "edit",
    }
    first = client.patch(path, json=payload(envelope["meta"]["revision"], other))
    assert first.status_code == 200 and first.json()["data"]["revision"] == "2"
    stale = client.patch(path, json=payload(envelope["meta"]["revision"], other, "admin"))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"
    after = client.get(path).json()
    assert after["meta"]["revision"] == "2"
    assert next(row for row in after["data"] if row["principal_id"] == str(other))["role"] == "view"
    assert restricted.roles and set(restricted.roles) == {"keep_api"}
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM audit_events WHERE tenant_id=%s AND kind='permissions-changed'",
                (tenant,),
            ).fetchone()["n"]
            == 1
        )


def test_permission_noop_records_receipt_without_revision_or_audit(permissions, real_db):
    client, tenant, _, other, _ = permissions
    path = f"/api/tenants/{tenant}/permissions"
    before = client.get(path).json()
    body = payload(before["meta"]["revision"], other, "edit")
    body["operations"].append({"principal_id": str(uuid4()), "action": "remove"})
    result = client.patch(path, json=body)
    assert (
        result.status_code == 200
        and result.json()["data"]["revision"] == before["meta"]["revision"]
    )
    assert client.patch(path, json=body).json() == result.json()
    assert client.get(path).json() == before
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM audit_events WHERE tenant_id=%s AND kind='permissions-changed'",
                (tenant,),
            ).fetchone()["n"]
            == 0
        )
        receipt = conn.execute(
            "SELECT status,result FROM command_receipts WHERE command_id=%s", (body["command_id"],)
        ).fetchone()
        assert receipt["status"] == 200 and receipt["result"] == result.json()


def test_roster_revision_stays_atomic_when_writer_arrives_between_reads(permissions, real_db):
    client, tenant, _, other, restricted = permissions
    path = f"/api/tenants/{tenant}/permissions"
    reading, continue_read, writer_attempted = Event(), Event(), Event()
    writer_pid = []

    def hook(conn, query):
        if query.startswith("SELECT g.principal_id,g.role,p.display_name"):
            reading.set()
            assert continue_read.wait(2), "Test did not release the paused roster read"
        if query == "SELECT scope FROM authorization_guards WHERE scope='identity' FOR UPDATE":
            writer_pid.append(conn.info.backend_pid)
            writer_attempted.set()

    restricted.hook = hook
    with ThreadPoolExecutor(max_workers=2) as executor:
        read = executor.submit(client.get, path)
        assert reading.wait(2)
        write = executor.submit(client.patch, path, json=payload("1", other))
        try:
            assert writer_attempted.wait(1)
            blocked = False
            deadline = monotonic() + 0.75
            while monotonic() < deadline and not write.done():
                with real_db.transaction() as conn:
                    blocked = bool(
                        conn.execute(
                            "SELECT pg_blocking_pids(%s) AS blockers", (writer_pid[0],)
                        ).fetchone()["blockers"]
                    )
                if blocked:
                    break
                sleep(0.01)
            assert blocked, "The permission writer was not fenced by the roster read"
        finally:
            continue_read.set()
        snapshot, changed = read.result(), write.result()
    restricted.hook = None
    assert snapshot.status_code == 200 and snapshot.json()["meta"]["revision"] == "1"
    assert (
        next(row for row in snapshot.json()["data"] if row["principal_id"] == str(other))["role"]
        == "edit"
    )
    assert changed.status_code == 200 and changed.json()["data"]["revision"] == "2"
    after = client.get(path).json()
    assert after["meta"]["revision"] == "2"
    assert next(row for row in after["data"] if row["principal_id"] == str(other))["role"] == "view"
