# ruff: noqa: F811 -- imported reusable fixtures
"""Keep password sign-in through the real Product API, OpenLDAP and the mock ADAPI.

Each test gets a fresh database cloned from a migrated template (needs
KEEP_IDENTITY_TEST_ADMIN_URL), the session OpenLDAP container (needs Docker) and a fresh
mock ADAPI (scripts/local_adapi.py). The ``stack`` fixture runs the bootstrap directory
refresh, which opens human authorization and makes local-admin a global super-admin, as
local Start does. Requests go through ``keep.main.create_app`` with its middleware.

Requirements: REV-73; identity configuration 2-5, 8-10; plan A1-A10, A20, A23, A24.
REV-76; plan (REV-75/76) B3, B6 (a), B9.
"""

import base64
import json
import logging
import os
import socket
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from ldap3.utils import log as ldap3_log
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from adapi_fixture import adapi_factory, mock_adapi, openldap  # noqa: F401
import local_adapi

from keep.config import Settings
from keep.db import Database
from keep.identity.adapi import ADAPIConfig
from keep.identity.worker import run_cycle
from keep.main import create_app
from keep.migrate import migrate

pytestmark = pytest.mark.integration

SUPERADMIN = local_adapi.SUPERADMIN_GROUP
ORIGIN = "https://app.test"
SESSION_COOKIE = "__Host-keep_session"
REJECTED_MESSAGE = "The username or password wasn't accepted."


def _dsn(admin, name):
    params = conninfo_to_dict(admin)
    params["dbname"] = name
    return make_conninfo(**params)


@pytest.fixture(scope="module")
def template():
    admin = os.environ.get("KEEP_IDENTITY_TEST_ADMIN_URL")
    if not admin:
        pytest.skip("Set KEEP_IDENTITY_TEST_ADMIN_URL to an isolated PostgreSQL cluster")
    name = "keep_login_template_" + uuid4().hex[:12]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        migrate(_dsn(admin, name))
        yield admin, name
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def database(template):
    admin, source = template
    name = "keep_login_test_" + uuid4().hex[:12]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(source)
            )
        )
    db = Database(_dsn(admin, name), size=6)
    yield db
    db.close()
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def adapi_config(mock):
    return ADAPIConfig(mock.base_url, mock.client_id)


@pytest.fixture
def stack(database, openldap, mock_adapi):
    """The bootstrap refresh has run: human_auth is open and local-admin is super-admin."""
    summary = run_cycle(database, adapi_config(mock_adapi), SUPERADMIN, bootstrap_qualified=True)
    assert summary.ok and summary.bootstrap_ready
    mock_adapi.reset_log()
    return SimpleNamespace(db=database, ldap=openldap, adapi=mock_adapi)


def settings(stack, **overrides):
    values = dict(
        database_url="unused",
        wrapping_key=base64.b64encode(b"k" * 32).decode(),
        wrapping_key_id="test",
        deployment_epoch="test",
        public_origin=ORIGIN,
        ldap_host=stack.ldap.host,
        ldap_port=stack.ldap.port,
        ldap_bind_format=stack.ldap.bind_format,
        adapi_url=stack.adapi.base_url,
        adapi_client_id=stack.adapi.client_id,
        superadmin_group=SUPERADMIN,
    )
    values.update(overrides)
    return Settings(**values)


@contextmanager
def api(stack, **overrides):
    with TestClient(create_app(settings(stack, **overrides), stack.db), base_url=ORIGIN) as client:
        yield client


@pytest.fixture
def client(stack):
    with api(stack) as browser:
        yield browser


def sign_in(client, username, password, return_to="/"):
    return client.post(
        "/auth/login",
        json={"username": username, "password": password, "return_to": return_to},
        headers={"Origin": ORIGIN},
    )


def signed_in(client, stack, username):
    response = sign_in(client, username, stack.ldap.users[username])
    assert response.status_code == 200, response.text
    return client.cookies[SESSION_COOKIE]


def use_cookie(client, handle):
    client.cookies.clear()
    if handle:
        client.cookies.set(SESSION_COOKIE, handle)


def query(db, statement, params=()):
    with db.transaction() as conn:
        return conn.execute(statement, params).fetchall()


def principal(db, name):
    rows = query(
        db, "SELECT * FROM principals WHERE directory_source='adapi' AND object_id=%s", (name,)
    )
    return rows[0] if rows else None


def sessions(db, name):
    return query(
        db,
        """SELECT s.* FROM auth_sessions s JOIN principals p ON p.id=s.principal_id
        WHERE p.directory_source='adapi' AND p.object_id=%s ORDER BY s.auth_time""",
        (name,),
    )


def revision(db):
    return query(db, "SELECT directory_revision FROM authorization_guards WHERE scope='identity'")[
        0
    ]["directory_revision"]


def verifications(db):
    return query(db, "SELECT count(*) AS n FROM directory_verifications")[0]["n"]


def error(response):
    return response.json()["error"]


def global_role(session):
    return next(t["role"] for t in session["tenants"] if t["kind"] == "global")


def test_correct_password_signs_in_and_super_admin_comes_from_the_group(client, stack):
    """Requirements: REV-73; identity configuration 3, 12; plan A1."""
    response = sign_in(client, "local-admin", stack.ldap.users["local-admin"], "/admin?x=1")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["return_to"] == "/admin?x=1"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(SESSION_COOKIE + "=") and "Secure" in cookie and "HttpOnly" in cookie
    session = client.get("/auth/session").json()["data"]
    assert session["global_super_admin"] is True
    assert session["principal"]["display_name"] == "Local Administrator"
    assert global_role(session) == "admin"
    assert "refresh_uncertain" not in session and session["absolute_at"]


def test_wrong_password_is_refused_without_cookie_session_or_adapi_call(client, stack):
    """Requirements: REV-73; identity configuration 3; plan A2."""
    response = sign_in(client, "local-editor", "not-the-password")
    assert response.status_code == 401
    assert error(response)["code"] == "authentication_rejected"
    assert error(response)["message"] == REJECTED_MESSAGE
    assert "set-cookie" not in response.headers and SESSION_COOKIE not in client.cookies
    assert query(stack.db, "SELECT count(*) AS n FROM auth_sessions")[0]["n"] == 0
    assert stack.adapi.count() == 0


def test_a_person_adapi_reports_disabled_signs_in_and_ends_no_other_session(stack):
    """Requirements: REV-76; identity configuration 3, 6; plan (REV-75/76) B3, B9.

    ADAPI's ``userAccountControl`` is ignored: a correct bind for a person ADAPI reports
    as 514 signs in, publishes nothing, and leaves that person's other sessions open.
    The person is read with one ``/users/user`` call and no ``/users`` search.
    """
    with api(stack) as first, api(stack) as second:
        signed_in(first, stack, "local-viewer")
        stack.adapi.remove_user("local-viewer")
        stack.adapi.add_user("local-viewer", displayName="Local Viewer", disabled=True)
        row = stack.adapi.directory.search("users", {"samaccountname": ["local-viewer"]}, 10)
        assert [r["userAccountControl"] for r in row] == [local_adapi.DISABLED]
        before = revision(stack.db)
        stack.adapi.reset_log()
        response = sign_in(second, "local-viewer", stack.ldap.users["local-viewer"])
        assert response.status_code == 200, response.text
        assert stack.adapi.count("user") == 1 and stack.adapi.count("users") == 0
        assert revision(stack.db) == before
        saved = principal(stack.db, "local-viewer")
        assert saved["eligible"] and not saved["deleted"]
        assert [row["revoked_at"] for row in sessions(stack.db, "local-viewer")] == [None, None]
        assert first.get("/auth/session").status_code == 200
        assert second.get("/auth/session").status_code == 200


def test_sign_in_lifts_a_person_stored_ineligible_or_deleted(client, stack):
    """Requirements: REV-76; identity configuration 3, 7; plan (REV-75/76) B6 (a).

    A row an older version wrote (ineligible, or deleted) is published again at the
    person's next sign-in, which then succeeds.
    """
    signed_in(client, stack, "local-editor")
    for change in ("eligible=false", "eligible=false,deleted=true"):
        with stack.db.transaction() as conn:
            conn.execute(
                f"UPDATE principals SET {change} WHERE directory_source='adapi' AND object_id='local-editor'"
            )
        assert client.get("/auth/session").status_code == 401
        before = revision(stack.db)
        signed_in(client, stack, "local-editor")
        saved = principal(stack.db, "local-editor")
        assert saved["eligible"] and not saved["deleted"], change
        assert revision(stack.db) == before + 1
        assert client.get("/auth/session").status_code == 200


def _closed_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_ldap_or_adapi_down_answers_503_without_a_session(stack):
    """Requirements: REV-73; identity configuration 9; plan A5."""
    password = stack.ldap.users["local-editor"]
    with api(stack, ldap_host="127.0.0.1", ldap_port=_closed_port()) as unreachable:
        response = sign_in(unreachable, "local-editor", password)
        assert response.status_code == 503 and error(response)["code"] == "sign_in_unavailable"
    with api(stack) as client:
        stack.adapi.fail("user", status=500)  # REV-76 B9: sign-in reads /users/user
        response = sign_in(client, "local-editor", password)
        assert response.status_code == 503 and error(response)["code"] == "sign_in_unavailable"
        stack.adapi.clear_failures()
        stack.adapi.stop()
        response = sign_in(client, "local-editor", password)
        assert response.status_code == 503 and error(response)["code"] == "sign_in_unavailable"
        assert "set-cookie" not in response.headers
    assert query(stack.db, "SELECT count(*) AS n FROM auth_sessions")[0]["n"] == 0
    assert principal(stack.db, "local-editor") is None


def test_first_sign_in_saves_the_person_with_global_edit(client, stack):
    """Requirements: REV-73; identity configuration 3, 7; tenant access 5; plan A6."""
    assert principal(stack.db, "local-viewer") is None
    signed_in(client, stack, "local-viewer")
    saved = principal(stack.db, "local-viewer")
    assert saved["kind"] == "user" and saved["eligible"] and not saved["deleted"]
    assert saved["display_name"] == "Local Viewer" and saved["evidence_generation"] >= 1
    assert not saved["super_admin"]
    session = client.get("/auth/session").json()["data"]
    assert session["global_super_admin"] is False
    assert [t["kind"] for t in session["tenants"]] == ["global"]
    assert global_role(session) == "edit"


def test_session_survives_idle_time_and_ends_at_24_hours(client, stack):
    """Requirements: REV-73; identity configuration 4; plan A7."""
    signed_in(client, stack, "local-editor")
    [row] = sessions(stack.db, "local-editor")
    assert (row["absolute_at"] - row["auth_time"]).total_seconds() == 24 * 3600
    with stack.db.transaction() as conn:
        conn.execute("UPDATE auth_sessions SET auth_time=clock_timestamp()-interval '31 minutes'")
    assert client.get("/auth/session").status_code == 200
    with stack.db.transaction() as conn:
        conn.execute(
            """UPDATE auth_sessions SET auth_time=clock_timestamp()-interval '24 hours 1 second',
            absolute_at=clock_timestamp()-interval '1 second'"""
        )
    assert client.get("/auth/session").status_code == 401


def test_sign_out_is_local_and_has_no_provider_url(client, stack):
    """Requirements: REV-73; identity configuration 10; plan A8."""
    signed_in(client, stack, "local-editor")
    csrf = client.get("/auth/session").json()["data"]["csrf_token"]
    response = client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json() == {"data": {"signed_out": True}, "meta": {}}
    assert client.get("/auth/session").status_code == 401
    [row] = sessions(stack.db, "local-editor")
    assert row["revoked_at"] is not None


def test_mixed_hebrew_english_password_signs_in_on_openldap(client, stack):
    """Requirements: REV-73; identity configuration 3; plan A9."""
    password = stack.ldap.users["local-hebrew"]
    assert any("֐" <= ch <= "׿" for ch in password)
    assert any(ch.isascii() and ch.isalpha() for ch in password)
    response = sign_in(client, "local-hebrew", password)
    assert response.status_code == 200, response.text
    session = client.get("/auth/session").json()["data"]
    assert session["principal"]["display_name"] == "משתמש Local Hebrew"


def test_passwords_never_reach_the_database_or_logs(client, stack, caplog):
    """Requirements: REV-73; identity configuration 3, 13; plan A10."""
    used = {name: stack.ldap.users[name] for name in ("local-admin", "local-hebrew")}
    previous = (
        ldap3_log.get_library_log_detail_level(),
        ldap3_log.get_library_log_activation_lavel(),
        ldap3_log.get_library_log_hide_sensitive_data(),
    )
    # The worst case: ldap3's own logging turned all the way up by a deployment.
    ldap3_log.set_library_log_hide_sensitive_data(False)
    ldap3_log.set_library_log_activation_level(logging.DEBUG)
    ldap3_log.set_library_log_detail_level(ldap3_log.EXTENDED)
    try:
        with caplog.at_level(logging.DEBUG):
            with caplog.at_level(logging.DEBUG, logger="ldap3"):
                for name, password in used.items():
                    assert sign_in(client, name, password).status_code == 200
                    assert sign_in(client, name, password + "-wrong").status_code == 401
                unlisted = sign_in(client, "local-unlisted", stack.ldap.users["local-unlisted"])
                assert unlisted.status_code == 403
    finally:
        ldap3_log.set_library_log_detail_level(previous[0])
        ldap3_log.set_library_log_activation_level(previous[1])
        ldap3_log.set_library_log_hide_sensitive_data(previous[2])
    assert any(record.name == "ldap3" for record in caplog.records)
    logged = "\n".join(r.getMessage() + repr(r.args) + str(r.exc_info) for r in caplog.records)
    stored = json.dumps(
        [
            query(stack.db, f"SELECT row_to_json(t)::text AS row FROM {table} t")
            for table in ("auth_sessions", "audit_events", "principals", "directory_verifications")
        ],
        default=str,
    )
    for password in [*used.values(), stack.ldap.users["local-unlisted"]]:
        for form in (
            password,
            password + "-wrong",
            repr(password.encode("utf-8")),
            password.encode("utf-8").decode("latin-1"),
            password.encode("utf-8").hex(),
        ):
            assert form not in logged
            assert form not in stored


def test_which_account_signed_in_and_directory_rows_decide(client, stack):
    """Requirements: REV-73; identity configuration 3; plan A20."""
    connection = stack.ldap.connect("local-editor", stack.ldap.users["local-editor"])
    try:
        # OpenLDAP answers WhoAmI in the dn: form, which sign-in accepts.
        assert connection.extend.standard.who_am_i().startswith("dn:")
    finally:
        connection.unbind()
    signed_in(client, stack, "local-editor")
    use_cookie(client, None)
    response = sign_in(client, "local-unlisted", stack.ldap.users["local-unlisted"])
    assert response.status_code == 403
    assert error(response) | {"request_id": None} == {
        "code": "account_not_in_directory",
        "message": "Your account isn't in the company directory.",
        "request_id": None,
    }
    assert principal(stack.db, "local-unlisted") is None
    stack.adapi.add_user("local-viewer", displayName="Another Viewer")
    response = sign_in(client, "local-viewer", stack.ldap.users["local-viewer"])
    assert response.status_code == 403
    assert error(response)["code"] == "account_ambiguous"
    assert error(response)["message"] == (
        "More than one directory account has this username. Contact your administrator."
    )
    assert "set-cookie" not in response.headers and sessions(stack.db, "local-viewer") == []


def test_unchanged_sign_in_publishes_nothing(client, stack):
    """Requirements: REV-73; identity configuration 3, 7; plan A23."""
    signed_in(client, stack, "local-viewer")
    before, published = revision(stack.db), verifications(stack.db)
    signed_in(client, stack, "local-viewer")
    signed_in(client, stack, "local-admin")
    assert revision(stack.db) == before and verifications(stack.db) == published
    stack.adapi.rename("local-viewer", "Renamed Viewer")
    signed_in(client, stack, "local-viewer")
    assert revision(stack.db) == before + 1
    assert principal(stack.db, "local-viewer")["display_name"] == "Renamed Viewer"


def test_closed_human_auth_gate_answers_503_and_publishes_nothing(client, stack):
    """Requirements: REV-73; identity configuration 2, 11; plan A23."""
    with stack.db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='human_auth'")
    before, published = revision(stack.db), verifications(stack.db)
    response = sign_in(client, "local-viewer", stack.ldap.users["local-viewer"])
    assert response.status_code == 503
    assert error(response)["code"] == "human_authorization_gated"
    assert stack.adapi.count() == 0
    assert principal(stack.db, "local-viewer") is None
    assert revision(stack.db) == before and verifications(stack.db) == published
    assert query(stack.db, "SELECT count(*) AS n FROM auth_sessions")[0]["n"] == 0


def test_signing_in_with_an_old_cookie_revokes_the_old_session(client, stack):
    """Requirements: REV-73; identity configuration 3, 5; plan A24."""
    old = signed_in(client, stack, "local-editor")
    new = signed_in(client, stack, "local-editor")
    assert new != old
    assert [row["revoked_at"] is not None for row in sessions(stack.db, "local-editor")] == [
        True,
        False,
    ]
    assert client.get("/auth/session").status_code == 200
    use_cookie(client, old)
    assert client.get("/auth/session").status_code == 401
