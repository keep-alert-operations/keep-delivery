"""Real PostgreSQL auth/publication tests; provider transport is a controlled double.

No login fixture or test route exists in the application. Fixtures seed an
isolated database and exercise the actual production router and SQL boundaries.
"""

import asyncio
import base64
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from appchi.config import Settings
from appchi.db import Database
from appchi.identity.directory import DirectoryObject, DirectoryVerifier, PrincipalKey
from appchi.identity.oidc import (
    LogoutEvent,
    OIDCClient,
    OIDCConfig,
    OIDCIdentity,
    OIDCRevoked,
    OIDCUnavailable,
    TokenSet,
)
from appchi.identity.publication import allocate_generation, publish
from appchi.identity.session import SESSION_COOKIE, router
from appchi.migrate import migrate
from appchi.shared.errors import AppError

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def identity_db():
    admin_dsn = os.environ.get("APPCHI_IDENTITY_TEST_ADMIN_URL")
    if not admin_dsn:
        pytest.skip(
            "Set APPCHI_IDENTITY_TEST_ADMIN_URL to an isolated local PostgreSQL cluster with CREATE DATABASE permission"
        )
    name = "appchi_identity_test_" + uuid4().hex
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    params = conninfo_to_dict(admin_dsn)
    params["dbname"] = name
    dsn = make_conninfo(**params)
    migrate(dsn, Path(__file__).resolve().parents[3] / "migrations")
    database = Database(dsn, size=4)
    yield database, dsn
    database.close()
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


class Provider(OIDCClient):
    def __init__(self, object_id):
        super().__init__(
            OIDCConfig(
                "https://identity.example/realms/test",
                "appchi-web",
                "secret",
                "https://app.example/auth/callback",
                "https://app.example/signed-out",
                "test-directory",
                "object_id",
                "directory-identity",
                "mfa",
                ("pwd", "otp"),
            )
        )
        self.identity = OIDCIdentity(
            self.config.issuer,
            "subject-" + object_id,
            "sid-" + object_id,
            "test-directory",
            object_id,
            int(datetime.now(UTC).timestamp()) - 60,
            "Test Person",
        )
        self.last_transaction = None
        self.refresh_count = 0
        self.refresh_error = None

    async def authorization_url(self, transaction):
        self.last_transaction = transaction
        return "https://identity.example/realms/test/authorize"

    async def exchange(self, code, transaction):
        assert code == "valid-code"
        return TokenSet(
            self.identity,
            {"access_token": "access", "refresh_token": "rotation-1", "id_token": "id-token"},
        )

    async def refresh(self, refresh_token, binding):
        self.refresh_count += 1
        await asyncio.sleep(0.1)
        if self.refresh_error:
            raise self.refresh_error
        return TokenSet(
            self.identity,
            {"access_token": "access-new", "refresh_token": "rotation-2", "id_token": "id-new"},
        )

    async def logout_url(self, id_token_hint):
        return "https://identity.example/realms/test/logout"

    async def validate_logout_token(self, token):
        assert token == "signed-token-validated-in-crypto-tests"
        now = int(datetime.now(UTC).timestamp())
        return LogoutEvent(
            self.identity.issuer,
            self.identity.sid,
            self.identity.subject,
            "event-1",
            now,
            now + 86700,
        )


@pytest.fixture
def browser(identity_db):
    database, dsn = identity_db
    object_id = uuid4().hex
    with database.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='human_auth'")
        principal = conn.execute(
            """INSERT INTO principals(directory_source,object_id,kind,display_name,eligible,evidence_generation,verified_at)
                     VALUES('test-directory',%s,'user','Test Person',true,1,clock_timestamp()) RETURNING id""",
            (object_id,),
        ).fetchone()["id"]
    settings = Settings(
        database_url=dsn,
        wrapping_key=base64.b64encode(b"k" * 32).decode(),
        deployment_epoch="test-epoch",
        public_origin="https://app.example",
    )
    app = FastAPI()
    app.state.db, app.state.settings, app.state.oidc = database, settings, Provider(object_id)

    @app.exception_handler(AppError)
    async def handler(request, error):
        return JSONResponse({"error": {"code": error.code}}, status_code=error.status)

    app.include_router(router)
    with TestClient(app, base_url="https://app.example") as client:
        yield client, app.state.oidc, principal


def sign_in(client, provider):
    response = client.get("/auth/login?return_to=/", follow_redirects=False)
    assert response.status_code == 302
    state = provider.last_transaction.state
    response = client.get(
        "/auth/callback", params={"state": state, "code": "valid-code"}, follow_redirects=False
    )
    assert response.status_code == 302, response.text
    return state, response


def test_callback_is_one_use_cookie_secure_and_csrf_session_bound(browser):
    client, provider, _ = browser
    state, response = sign_in(client, provider)
    cookies = response.headers.get_list("set-cookie")
    assert any(
        "__Host-appchi_session=" in c
        and "Secure" in c
        and "HttpOnly" in c
        and "SameSite=lax" in c
        and "Domain=" not in c
        for c in cookies
    )
    assert (
        client.get(
            "/auth/callback", params={"state": state, "code": "valid-code"}, follow_redirects=False
        ).status_code
        == 401
    )
    session = client.get("/auth/session").json()["data"]
    assert session["principal"]["display_name"] == "Test Person"
    assert session["tenants"][0]["kind"] == "global"
    assert (
        client.post("/auth/activity", headers={"Origin": "https://app.example"}).status_code == 403
    )
    assert (
        client.post(
            "/auth/activity",
            headers={"Origin": "https://evil.example", "X-CSRF-Token": session["csrf_token"]},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/auth/activity",
            headers={"Origin": "https://app.example", "X-CSRF-Token": session["csrf_token"]},
        ).status_code
        == 200
    )


@pytest.mark.parametrize("reason", ["expired", "consumed", "missing"])
def test_stale_browser_callback_offers_fresh_login_without_replaying(browser, identity_db, reason):
    client, provider, principal = browser
    database, _ = identity_db
    client.get("/auth/login", follow_redirects=False)
    state = provider.last_transaction.state
    state_hash = hashlib.sha256(state.encode()).digest()
    with database.transaction() as conn:
        if reason == "expired":
            conn.execute(
                "UPDATE login_transactions SET expires_at=clock_timestamp()-interval '1 second' WHERE state_hash=%s",
                (state_hash,),
            )
        elif reason == "consumed":
            conn.execute("UPDATE login_transactions SET consumed=true WHERE state_hash=%s", (state_hash,))
        else:
            conn.execute("DELETE FROM login_transactions WHERE state_hash=%s", (state_hash,))
    params = {"state": state, "code": "must-not-be-exchanged"}
    result = client.get(
        "/auth/callback", params=params, headers={"Accept": "text/html"}, follow_redirects=False
    )
    assert result.status_code == 303
    assert result.headers["location"] == "/sign-in-expired"
    assert result.headers["cache-control"] == "private, no-store"
    assert SESSION_COOKIE not in client.cookies
    with database.transaction() as conn:
        assert conn.execute(
            "SELECT count(*) AS n FROM auth_sessions WHERE principal_id=%s", (principal,)
        ).fetchone()["n"] == 0
    assert client.get(
        "/auth/callback", params=params, headers={"Accept": "application/json"}, follow_redirects=False
    ).status_code == 401
    sign_in(client, provider)
    assert client.get("/auth/session").status_code == 200


def test_idle_absolute_epoch_and_known_disable_override_cached_identity(browser, identity_db):
    client, provider, principal = browser
    database, _ = identity_db
    sign_in(client, provider)
    with database.transaction() as conn:
        conn.execute("UPDATE principals SET verified_at='2000-01-01' WHERE id=%s", (principal,))
    assert client.get("/auth/session").status_code == 200
    with database.transaction() as conn:
        conn.execute(
            "UPDATE auth_sessions SET activity_at=clock_timestamp()-interval '31 minutes' WHERE principal_id=%s",
            (principal,),
        )
    assert client.get("/auth/session").status_code == 401
    sign_in(client, provider)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE auth_sessions SET absolute_at=clock_timestamp()-interval '1 second' WHERE principal_id=%s",
            (principal,),
        )
    assert client.get("/auth/session").status_code == 401
    sign_in(client, provider)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE auth_sessions SET deployment_epoch='old' WHERE principal_id=%s", (principal,)
        )
    assert client.get("/auth/session").status_code == 401
    sign_in(client, provider)
    with database.transaction() as conn:
        conn.execute("UPDATE principals SET eligible=false WHERE id=%s", (principal,))
    assert client.get("/auth/session").status_code == 401


def test_refresh_serialization_and_transport_uncertainty_preserve_session(browser, identity_db):
    client, provider, principal = browser
    database, _ = identity_db
    sign_in(client, provider)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE auth_sessions SET refresh_at=clock_timestamp()-interval '5 minutes' WHERE principal_id=%s",
            (principal,),
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: client.get("/auth/session").status_code, range(2)))
    assert results == [200, 200]
    assert provider.refresh_count == 1
    provider.refresh_error = OIDCUnavailable("unknown_exchange_outcome")
    with database.transaction() as conn:
        conn.execute(
            "UPDATE auth_sessions SET refresh_at=clock_timestamp()-interval '5 minutes' WHERE principal_id=%s",
            (principal,),
        )
    assert client.get("/auth/session").json()["data"]["refresh_uncertain"]
    assert client.get("/auth/session").status_code == 200
    assert provider.refresh_count == 2


def test_definitive_provider_revocation_ends_session(browser, identity_db):
    client, provider, principal = browser
    database, _ = identity_db
    sign_in(client, provider)
    provider.refresh_error = OIDCRevoked("invalid_grant")
    with database.transaction() as conn:
        conn.execute(
            "UPDATE auth_sessions SET refresh_at=clock_timestamp()-interval '5 minutes' WHERE principal_id=%s",
            (principal,),
        )
    assert client.get("/auth/session").status_code == 401
    with database.transaction() as conn:
        assert (
            conn.execute(
                "SELECT tokens FROM auth_sessions WHERE principal_id=%s", (principal,)
            ).fetchone()["tokens"]
            is None
        )


def test_signed_backchannel_logout_replay_and_callback_race_are_fenced(browser):
    client, provider, _ = browser
    sign_in(client, provider)
    for _ in range(2):
        assert (
            client.post(
                "/auth/backchannel-logout",
                data={"logout_token": "signed-token-validated-in-crypto-tests"},
            ).status_code
            == 200
        )
    assert client.get("/auth/session").status_code == 401
    client.get("/auth/login", follow_redirects=False)
    assert (
        client.get(
            "/auth/callback",
            params={"state": provider.last_transaction.state, "code": "valid-code"},
            follow_redirects=False,
        ).status_code
        == 401
    )


def test_logout_revokes_locally_and_clears_cookie(browser):
    client, provider, _ = browser
    sign_in(client, provider)
    csrf = client.get("/auth/session").json()["data"]["csrf_token"]
    result = client.post(
        "/auth/logout", headers={"Origin": "https://app.example", "X-CSRF-Token": csrf}
    )
    assert result.status_code == 200
    assert result.json()["data"]["logout_url"].endswith("/logout")
    assert SESSION_COOKIE not in client.cookies
    assert client.get("/auth/session").status_code == 401


def test_unknown_binding_cannot_login_from_claim_alone(browser, identity_db):
    client, provider, principal = browser
    database, _ = identity_db
    with database.transaction() as conn:
        conn.execute(
            "UPDATE principals SET verified_at=NULL,evidence_generation=0 WHERE id=%s", (principal,)
        )
    client.get("/auth/login", follow_redirects=False)
    result = client.get(
        "/auth/callback",
        params={"state": provider.last_transaction.state, "code": "valid-code"},
        follow_redirects=False,
    )
    assert result.status_code == 503


class Directory:
    def __init__(self, source):
        self.source = source
        self.group, self.user, self.nested = (
            PrincipalKey(source, value) for value in ("admins", "person", "nested")
        )
        self.disabled = False
        self.failed = False

    def lookup(self, key):
        from appchi.identity.directory import DirectoryUnavailable

        if self.failed and key == self.nested:
            raise DirectoryUnavailable("unavailable")
        return DirectoryObject(
            key,
            "user" if key == self.user else "team",
            key.object_id,
            not (key == self.user and self.disabled),
        )

    def members(self, key):
        return [self.user, self.nested] if key == self.group else [self.group]


def test_real_publication_semantic_revisions_partial_negative_and_generation_fence(identity_db):
    database, _ = identity_db
    directory = Directory("publication-" + uuid4().hex)

    def verify():
        with database.transaction() as conn:
            generation = allocate_generation(conn)
        return DirectoryVerifier(directory).verify([directory.group], generation)

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
    directory.disabled, directory.failed = True, True
    negative = verify()
    with database.transaction() as conn:
        result = publish(conn, negative, superadmin_group=directory.group)
        assert not result.accepted and str(user["id"]) in result.revoked_principals
    with database.transaction() as conn:
        result = publish(conn, stale, superadmin_group=directory.group)
        assert not result.accepted
        row = conn.execute(
            "SELECT eligible,super_admin FROM principals WHERE id=%s", (user["id"],)
        ).fetchone()
        assert not row["eligible"] and not row["super_admin"]
