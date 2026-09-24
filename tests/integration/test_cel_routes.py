"""Editor HTTP tests with real PostgreSQL authorization and the native compiler."""
# ruff: noqa: F811

import pytest

from appchi.cel import compiler
from test_product import browser  # noqa: F401
from test_maintenance_repository import maintenance_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def real_db(maintenance_db):
    return maintenance_db[0]


@pytest.fixture
def editor(browser, real_db, tenant):
    client, principal = browser
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'view')",
            (tenant, principal),
        )
    return client, principal, tenant, f"/api/tenants/{tenant}/cel"


def test_profile_catalog_and_viewer_native_validation(editor):
    from appchi.cel.catalog import FIELDS, RULE_FIELDS

    client, _, _, base = editor
    feed = client.get(base + "/profile")
    assert feed.status_code == 200, feed.text
    profile = feed.json()["data"]
    assert {field["name"] for field in profile["fields"]} == set(FIELDS)
    assert profile["limits"]["source_bytes"] == compiler.MAX_SOURCE_BYTES
    assert "now" in {fn["name"] for fn in profile["functions"]}
    rule = client.get(base + "/profile", params={"context": "rule"}).json()["data"]
    assert {field["name"] for field in rule["fields"]} == RULE_FIELDS
    assert "now" not in {fn["name"] for fn in rule["functions"]}
    for context, expected in (("feed", True), ("rule", False)):
        response = client.post(
            base + "/validate",
            json={
                "cel": "alert.time_created < now()",
                "context": context,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["valid"] is expected
    assert feed.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "source",
    [
        'alert.private_secret_marker == "sensitive_literal_marker"',
        'alert.message == "sensitive_literal_marker',
        'alert.message.matches("sensitive_literal_marker")',
        " ",
    ],
)
def test_invalid_diagnostics_never_reflect_source(editor, source):
    client, _, _, base = editor
    response = client.post(base + "/validate", json={"cel": source, "context": "feed"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["valid"] is False
    assert response.json()["data"]["diagnostics"]
    assert "private_secret_marker" not in response.text
    assert "sensitive_literal_marker" not in response.text


def test_invalid_body_query_csrf_and_current_tenant_access(editor, real_db, monkeypatch):
    client, principal, tenant, base = editor
    assert client.get(base + "/profile", params={"context": "other"}).status_code == 422
    assert client.get(base + "/profile?context=feed&context=rule").status_code == 400
    assert client.get(base + "/profile", params={"extra": "ignored"}).status_code == 422
    body = {"cel": "true", "context": "feed"}
    assert client.post(base + "/validate", json={**body, "extra": True}).status_code == 422
    assert client.post(base + "/validate", json={**body, "cel": 123}).status_code == 422
    assert (
        client.post(base + "/validate", json=body, headers={"X-CSRF-Token": "invalid"}).status_code
        == 403
    )
    with real_db.transaction() as conn:
        conn.execute(
            "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, principal)
        )

    def forbidden(*args, **kwargs):
        pytest.fail("Compilation must not run without current tenant permission")

    monkeypatch.setattr(compiler, "compile_expression", forbidden)
    assert client.get(base + "/profile").status_code == 404
    assert client.post(base + "/validate", json=body).status_code == 404


@pytest.mark.parametrize("outcome", ["valid", "invalid", "unavailable"])
def test_compile_releases_sql_locks_and_rechecks_access(editor, real_db, monkeypatch, outcome):
    client, principal, tenant, base = editor
    native = compiler.compile_expression

    def compile_and_revoke(source, *, rule):
        # NOWAIT proves the first authorization lock was released before compile.
        with real_db.transaction() as conn:
            conn.execute(
                "SELECT scope FROM authorization_guards WHERE scope='identity' FOR UPDATE NOWAIT"
            )
            conn.execute(
                "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s",
                (tenant, principal),
            )
        if outcome == "invalid":
            raise compiler.CELValidationError("Invalid enum literal", 0)
        if outcome == "unavailable":
            raise compiler.CELUnavailableError("private internal detail")
        return native(source, rule=rule)

    monkeypatch.setattr(compiler, "compile_expression", compile_and_revoke)
    response = client.post(base + "/validate", json={"cel": "true", "context": "feed"})
    assert response.status_code == 404, response.text
    assert "private internal detail" not in response.text


def test_compiler_unavailable_is_not_invalid_or_success(editor, monkeypatch):
    client, _, _, base = editor

    def failed(*args, **kwargs):
        raise compiler.CELUnavailableError("sensitive native internals")

    monkeypatch.setattr(compiler, "compile_expression", failed)
    response = client.post(base + "/validate", json={"cel": "true", "context": "feed"})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "cel_unavailable"
    assert "sensitive" not in response.text
