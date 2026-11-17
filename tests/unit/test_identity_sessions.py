"""Real PostgreSQL session and publication tests; the password check is a controlled double.

No login fixture or test route exists in the application. Fixtures seed an isolated
database and exercise the production ``/auth`` router and SQL boundaries; the LDAP bind
is replaced by a double and ADAPI by an ``httpx.MockTransport`` behind the real client.
The real OpenLDAP and mock ADAPI path is in tests/integration/test_password_login.py.

Requirements: REV-73; identity configuration 2-5, 8, 10.
"""

import base64
import os
from functools import partial
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from keep.config import Settings
from keep.db import Database
from keep.identity import session as session_routes
from keep.identity.adapi import ADAPIClient
from keep.identity.directory import DirectoryObject, DirectoryVerifier, PrincipalKey
from keep.identity.login import SignInRejected
from keep.identity.publication import allocate_generation, publish
from keep.identity.session import SESSION_COOKIE, router
from keep.migrate import migrate
from keep.shared.errors import AppError

pytestmark = pytest.mark.integration

ORIGIN = "https://app.example"
PASSWORD = "correct-Pass-1"


@pytest.fixture(scope="module")
def identity_db():
    admin_dsn = os.environ.get("KEEP_IDENTITY_TEST_ADMIN_URL")
    if not admin_dsn:
        pytest.skip(
            "Set KEEP_IDENTITY_TEST_ADMIN_URL to an isolated local PostgreSQL cluster with CREATE DATABASE permission"
        )
    name = "keep_identity_test_" + uuid4().hex
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    params = conninfo_to_dict(admin_dsn)
    params["dbname"] = name
    dsn = make_conninfo(**params)
    migrate(dsn)
    database = Database(dsn, size=4)
    yield database, dsn
    database.close()
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def browser(identity_db, monkeypatch):
    """A client of the real /auth router; ``username`` signs in with PASSWORD."""
    database, dsn = identity_db
    username = "person-" + uuid4().hex[:12]
    with database.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='human_auth'")
        principal = conn.execute(
            """INSERT INTO principals(directory_source,object_id,kind,display_name,eligible,evidence_generation,verified_at)
                     VALUES('adapi',%s,'user','Test Person',true,1,clock_timestamp()) RETURNING id""",
            (username,),
        ).fetchone()["id"]

    def check_password(config, name, password):
        assert config.bind_user(name) == f"{name}@corp.example"
        if password != PASSWORD:
            raise SignInRejected("invalid_credentials")

    def adapi(request):
        name = request.url.params["samAccountName"]
        rows = [{"sAMAccountName": name, "displayName": "Test Person", "userAccountControl": "512"}]
        return httpx.Response(200, json=rows if name == username else [])

    monkeypatch.setattr(session_routes, "check_password", check_password)
    monkeypatch.setattr(
        session_routes, "ADAPIClient", partial(ADAPIClient, transport=httpx.MockTransport(adapi))
    )
    settings = Settings(
        database_url=dsn,
        wrapping_key=base64.b64encode(b"k" * 32).decode(),
        deployment_epoch="test-epoch",
        public_origin=ORIGIN,
        ldap_host="ldap.corp.example",
        ldap_bind_format="{username}@corp.example",
        adapi_url="http://adapi.corp.example",
        adapi_client_id="test-client",
        superadmin_group="keep-administrators",
    )
    app = FastAPI()
    app.state.db, app.state.settings = database, settings

    @app.exception_handler(AppError)
    async def handler(request, error):
        return JSONResponse({"error": {"code": error.code}}, status_code=error.status)

    app.include_router(router)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, username, principal


def sign_in(client, username, password=PASSWORD):
    response = client.post(
        "/auth/login",
        json={"username": username, "password": password, "return_to": "/"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    return response


def test_sign_in_cookie_is_secure_and_csrf_session_bound(browser):
    """Requirements: REV-73; identity configuration 2, 5."""
    client, username, _ = browser
    response = sign_in(client, username)
    assert response.json()["data"]["return_to"] == "/"
    cookies = response.headers.get_list("set-cookie")
    assert any(
        "__Host-keep_session=" in c
        and "Secure" in c
        and "HttpOnly" in c
        and "SameSite=lax" in c
        and "Path=/" in c
        and "Domain=" not in c
        for c in cookies
    )
    session = client.get("/auth/session").json()["data"]
    assert session["principal"]["display_name"] == "Test Person"
    assert session["tenants"][0]["kind"] == "global"
    assert set(session) == {
        "principal",
        "global_super_admin",
        "tenants",
        "csrf_token",
        "absolute_at",
        "verified_at",
        "last_tenant_id",
    }
    assert client.post("/auth/logout", headers={"Origin": ORIGIN}).status_code == 403
    assert (
        client.post(
            "/auth/logout",
            headers={"Origin": "https://evil.example", "X-CSRF-Token": session["csrf_token"]},
        ).status_code
        == 403
    )
    assert client.get("/auth/session").status_code == 200


def test_wrong_password_creates_no_session(browser, identity_db):
    """Requirements: REV-73; identity configuration 3; plan A2."""
    client, username, principal = browser
    database, _ = identity_db
    response = client.post(
        "/auth/login",
        json={"username": username, "password": "wrong"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_rejected"
    assert SESSION_COOKIE not in client.cookies
    with database.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM auth_sessions WHERE principal_id=%s", (principal,)
            ).fetchone()["n"]
            == 0
        )


def test_no_idle_timeout_but_absolute_epoch_and_known_disable_end_sessions(browser, identity_db):
    """Requirements: REV-73; identity configuration 4, 8; plan A7. REV-76: no publication
    writes a person ineligible any more, but the read-side guard stays for rows an older
    version wrote."""
    client, username, principal = browser
    database, _ = identity_db
    sign_in(client, username)
    with database.transaction() as conn:
        conn.execute("UPDATE principals SET verified_at='2000-01-01' WHERE id=%s", (principal,))
        conn.execute(
            "UPDATE auth_sessions SET auth_time=clock_timestamp()-interval '23 hours' WHERE principal_id=%s",
            (principal,),
        )
    assert client.get("/auth/session").status_code == 200
    with database.transaction() as conn:
        conn.execute(
            "UPDATE auth_sessions SET absolute_at=clock_timestamp()-interval '1 second' WHERE principal_id=%s",
            (principal,),
        )
    assert client.get("/auth/session").status_code == 401
    sign_in(client, username)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE auth_sessions SET deployment_epoch='old' WHERE principal_id=%s", (principal,)
        )
    assert client.get("/auth/session").status_code == 401
    sign_in(client, username)
    with database.transaction() as conn:
        conn.execute("UPDATE principals SET eligible=false WHERE id=%s", (principal,))
    assert client.get("/auth/session").status_code == 401


def test_new_sign_in_revokes_the_browser_session_it_replaces(browser, identity_db):
    """Requirements: REV-73; identity configuration 3, 5; plan A24."""
    client, username, principal = browser
    database, _ = identity_db
    sign_in(client, username)
    old = client.cookies[SESSION_COOKIE]
    sign_in(client, username)
    new = client.cookies[SESSION_COOKIE]
    assert new != old
    with database.transaction() as conn:
        rows = conn.execute(
            "SELECT revoked_at IS NULL AS live FROM auth_sessions WHERE principal_id=%s ORDER BY auth_time",
            (principal,),
        ).fetchall()
    assert [row["live"] for row in rows] == [False, True]
    client.cookies.set(SESSION_COOKIE, old)
    assert client.get("/auth/session").status_code == 401


def test_logout_revokes_locally_and_clears_cookie(browser):
    """Requirements: REV-73; identity configuration 10; plan A8."""
    client, username, _ = browser
    sign_in(client, username)
    csrf = client.get("/auth/session").json()["data"]["csrf_token"]
    result = client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert result.status_code == 200
    assert result.json() == {"data": {"signed_out": True}, "meta": {}}
    assert SESSION_COOKIE not in client.cookies
    assert client.get("/auth/session").status_code == 401


def test_unverified_principal_cannot_use_a_session(browser, identity_db):
    """Requirements: REV-73; identity configuration 7-8."""
    client, username, principal = browser
    database, _ = identity_db
    sign_in(client, username)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE principals SET verified_at=NULL,evidence_generation=0 WHERE id=%s", (principal,)
        )
    assert client.get("/auth/session").status_code == 503


class Directory:
    def __init__(self, source):
        self.source = source
        self.group, self.user, self.nested = (
            PrincipalKey(source, value) for value in ("admins", "person", "nested")
        )
        self.label = "person"
        self.nested_deleted = False
        self.failed = False

    def lookup(self, key):
        from keep.identity.directory import DirectoryUnavailable

        if self.failed and key == self.user:
            raise DirectoryUnavailable("unavailable")
        if key == self.user:
            return DirectoryObject(key, "user", self.label, True)
        if key == self.nested and self.nested_deleted:
            return DirectoryObject(key, "team", key.object_id, False, deleted=True)
        return DirectoryObject(key, "team", key.object_id, True)

    def members(self, key):
        return [self.user, self.nested] if key == self.group else [self.group]


def test_real_publication_semantic_revisions_partial_negative_and_generation_fence(identity_db):
    """Requirements: REV-73; identity configuration 7-8 (narrower generation fence). REV-76:
    only a group not found is a negative fact; a person is never negated."""
    database, _ = identity_db
    directory = Directory("publication-" + uuid4().hex)

    def verify(root=None):
        with database.transaction() as conn:
            generation = allocate_generation(conn)
        return DirectoryVerifier(directory).verify([root or directory.group], generation)

    def stored(object_id):
        with database.transaction() as conn:
            return conn.execute(
                "SELECT * FROM principals WHERE directory_source=%s AND object_id=%s",
                (directory.source, object_id),
            ).fetchone()

    first = verify()
    with database.transaction() as conn:
        one = publish(conn, first, superadmin_group=directory.group)
    second = verify()
    with database.transaction() as conn:
        two = publish(conn, second, superadmin_group=directory.group)
        assert one.accepted and two.accepted and one.semantic_revision == two.semantic_revision
        user = conn.execute(
            "SELECT * FROM principals WHERE directory_source=%s AND object_id='person'",
            (directory.source,),
        ).fetchone()
        assert user["super_admin"]
    stale = verify()
    directory.label = "Person Renamed"
    with database.transaction() as conn:
        assert publish(conn, verify(directory.user), superadmin_group=directory.group).accepted
    with database.transaction() as conn:
        # REV-73 narrower fence: the older complete evidence still publishes for its
        # root (no root or team holds a newer generation), but it skips the user leaf
        # the newer sign-in evidence holds, so it cannot restore the older name.
        result = publish(conn, stale, superadmin_group=directory.group)
        assert result.accepted and not result.fenced
    row = stored("person")
    assert row["display_name"] == "Person Renamed" and row["eligible"] and row["super_admin"]
    stale = verify()
    directory.nested_deleted, directory.failed = True, True
    negative = verify()
    with database.transaction() as conn:
        # Partial evidence publishes its negative facts only: the deleted group.
        assert not publish(conn, negative, superadmin_group=directory.group).accepted
    assert stored("nested")["deleted"] and not stored("nested")["eligible"]
    assert stored("person")["eligible"] and not stored("person")["deleted"]
    with database.transaction() as conn:
        # The older complete evidence covers the nested team, which now holds a newer
        # generation, so it is fenced and cannot resurrect the deleted group.
        result = publish(conn, stale, superadmin_group=directory.group)
        assert not result.accepted and result.fenced
    assert stored("nested")["deleted"]
