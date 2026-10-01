# ruff: noqa: F811
"""Authorized directory cache selection, live discovery and opaque cursor proofs."""

from dataclasses import replace
from contextlib import contextmanager
import base64
import json
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from appchi.identity.directory import DirectoryObject, DirectoryUnavailable, PrincipalKey
from appchi.identity.ldap import LDAPConfig, LDAPSchema
from appchi.identity.selection import ConfiguredDirectorySearch
from appchi.main import create_app

from test_maintenance_repository import maintenance_browser, maintenance_db  # noqa: F401


def principal(
    conn, label, *, kind="user", eligible=True, verified=True, deleted=False, super_admin=False
):
    identity = uuid4()
    conn.execute(
        """INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible,deleted,verified_at,super_admin)
        VALUES(%s,'selection-test',%s,%s,%s,%s,%s,CASE WHEN %s THEN clock_timestamp() END,%s)""",
        (identity, str(identity), kind, label, eligible, deleted, verified, super_admin),
    )
    return identity


def checkpoint(conn, *, changed=True, complete=True):
    conn.execute(
        """INSERT INTO directory_verifications(generation,source,started_at,completed_at,complete,semantic_revision,coverage)
        SELECT nextval('directory_generation_seq'),'selection-test',clock_timestamp(),clock_timestamp(),%s,
        coalesce(max(semantic_revision),0)+%s,jsonb_build_object('accepted',%s) FROM directory_verifications""",
        (complete, int(changed), complete),
    )


@pytest.fixture
def directory(maintenance_browser, maintenance_db):
    identity_client, tenant, actor, identity_app = maintenance_browser
    db, _ = maintenance_db
    app = create_app(identity_app.state.settings, db)
    prefix = str(uuid4())[:8]
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='admin' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, actor),
        )
        checkpoint(conn)
    with TestClient(app, base_url="https://app.test") as client:
        client.cookies.update(identity_client.cookies)
        client.headers.update(
            {"Origin": "https://app.test", "X-CSRF-Token": identity_client.headers["X-CSRF-Token"]}
        )
        yield client, db, tenant, actor, app, prefix


@pytest.mark.integration
def test_directory_authorization_literal_matching_and_safe_cache_metadata(directory):
    client, db, tenant, actor, _, prefix = directory
    with db.transaction() as conn:
        first = principal(conn, prefix + " %_ Alice")
        principal(conn, prefix + " Other")
    url = f"/api/tenants/{tenant}/permission-principals"
    response = client.get(url, params={"q": "%_"})
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["data"]] == [str(first)]
    assert (
        response.json()["meta"]["cached"]
        and not response.json()["meta"]["complete_company_directory"]
    )
    assert all("grants" not in row for row in response.json()["data"])
    assert client.get(url, params={"q": "x"}).status_code == 422
    assert client.get("/api/admin/directory/principals", params={"q": prefix}).status_code == 403
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, actor),
        )
    assert client.get(url, params={"q": prefix}).status_code == 403
    assert (
        client.get(
            f"/api/tenants/{uuid4()}/permission-principals", params={"q": prefix}
        ).status_code
        == 404
    )


@pytest.mark.integration
def test_assignees_filter_effective_access_before_limit_and_preserve_stale_evidence(directory):
    client, db, tenant, _, _, prefix = directory
    with db.transaction(tenant) as conn:
        principal(conn, prefix + " A no access")
        blocked = principal(conn, prefix + " B disabled", eligible=False)
        direct = principal(conn, prefix + " C direct")
        inherited = principal(conn, prefix + " D nested")
        root = principal(conn, prefix + " E super", super_admin=True)
        team = principal(conn, prefix + " F team", kind="team")
        for identity in (blocked, direct, team):
            conn.execute(
                "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
                (tenant, identity),
            )
        conn.execute(
            "INSERT INTO directory_memberships(team_id,user_id) VALUES(%s,%s)", (team, inherited)
        )
        conn.execute(
            "UPDATE principals SET verified_at=clock_timestamp()-interval '2 years' WHERE id=ANY(%s::uuid[])",
            ([direct, inherited],),
        )
        checkpoint(conn, changed=False, complete=False)
    url = f"/api/tenants/{tenant}/assignees"
    seen, cursor = [], None
    while True:
        response = client.get(
            url, params={"q": prefix, "limit": 1, **({"cursor": cursor} if cursor else {})}
        )
        assert response.status_code == 200, response.text
        seen += [row["id"] for row in response.json()["data"]]
        assert response.json()["meta"]["directory_degraded"]
        cursor = response.json()["next_cursor"]
        if not cursor:
            break
    assert seen == list(map(str, (direct, inherited, root)))


@pytest.mark.integration
def test_cursor_reauthorization_semantic_invalidation_and_freshness_stability(directory):
    client, db, tenant, actor, _, prefix = directory
    with db.transaction() as conn:
        first = principal(conn, prefix + " Alice")
        principal(conn, prefix + " Bob")
    url = f"/api/tenants/{tenant}/permission-principals"
    page = client.get(url, params={"q": prefix, "limit": 1}).json()
    params = {"q": prefix, "limit": 1, "cursor": page["next_cursor"]}
    with db.transaction() as conn:
        conn.execute("UPDATE principals SET verified_at=clock_timestamp() WHERE id=%s", (first,))
        checkpoint(conn, changed=False)
    assert client.get(url, params=params).status_code == 200
    assert client.get(url, params={**params, "q": "changed"}).status_code == 409
    with db.transaction() as conn:
        conn.execute("UPDATE principals SET display_name=%s WHERE id=%s", (prefix + " Zed", first))
        checkpoint(conn)
    assert client.get(url, params=params).json()["error"]["code"] == "list_changed"
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='edit' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, actor),
        )
    assert client.get(url, params=params).status_code == 403


@pytest.mark.integration
def test_complete_team_preview_and_unverified_team_fail_closed(directory):
    client, db, tenant, _, _, prefix = directory
    with db.transaction() as conn:
        team = principal(conn, prefix + " Team", kind="team")
        user = principal(conn, prefix + " Member")
        conn.execute(
            "INSERT INTO directory_memberships(team_id,user_id) VALUES(%s,%s)", (team, user)
        )
    url = f"/api/tenants/{tenant}/permission-principals/{team}/members"
    assert client.get(url).status_code == 503
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO directory_root_checkpoints(root_id,generation,verified_at,semantic_digest) VALUES(%s,1,clock_timestamp(),'trusted-complete')",
            (team,),
        )
    response = client.get(url)
    assert response.status_code == 200 and [row["id"] for row in response.json()["data"]] == [
        str(user)
    ]
    assert response.json()["meta"]["membership_scope"] == "eligible_users"


@pytest.mark.integration
def test_live_unknown_matches_queue_without_creating_users_and_outage_falls_back(directory):
    client, db, tenant, _, app, prefix = directory
    with db.transaction() as conn:
        known = principal(conn, prefix + " Cached")
    unknown = DirectoryObject(
        PrincipalKey("selection-test", str(uuid4())), "user", prefix + " New", True
    )
    app.state.directory_search = lambda query: (unknown,)
    response = client.get(f"/api/tenants/{tenant}/permission-principals", params={"q": prefix})
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["search_complete"]
    assert response.json()["data"][0]["id"] is None and not response.json()["data"][0]["selectable"]
    with db.transaction() as conn:
        assert not conn.execute(
            "SELECT 1 FROM principals WHERE directory_source=%s AND object_id=%s",
            (unknown.key.source, unknown.key.object_id),
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM directory_verification_requests WHERE source=%s AND object_id=%s",
            (unknown.key.source, unknown.key.object_id),
        ).fetchone()

    def outage(query):
        raise DirectoryUnavailable("controlled")

    app.state.directory_search = outage
    response = client.get(f"/api/tenants/{tenant}/permission-principals", params={"q": prefix})
    assert response.json()["meta"]["cached"] and response.json()["meta"]["directory_degraded"]
    assert [row["id"] for row in response.json()["data"]] == [str(known)]


@pytest.mark.integration
def test_live_discovery_rechecks_privilege_before_returning_rows_or_queueing(directory):
    client, db, tenant, actor, app, prefix = directory
    unknown = DirectoryObject(
        PrincipalKey("selection-test", str(uuid4())), "user", prefix + " Private", True
    )

    def revoke_during_search(query):
        with db.transaction(tenant) as conn:
            conn.execute(
                "UPDATE tenant_grants SET role='edit' WHERE tenant_id=%s AND principal_id=%s",
                (tenant, actor),
            )
        return (unknown,)

    app.state.directory_search = revoke_during_search
    result = client.get(f"/api/tenants/{tenant}/permission-principals", params={"q": prefix})
    assert result.status_code == 403 and unknown.label not in result.text
    with db.transaction() as conn:
        assert not conn.execute(
            "SELECT 1 FROM directory_verification_requests WHERE source=%s AND object_id=%s",
            (unknown.key.source, unknown.key.object_id),
        ).fetchone()


@pytest.mark.integration
def test_nonowner_api_role_can_queue_discovery_and_never_read_other_grants(directory):
    client, db, tenant, _, app, prefix = directory

    class APIIdentity:
        @contextmanager
        def transaction(self, tenant_id=None):
            with db.transaction(tenant_id) as conn:
                conn.execute("SET LOCAL ROLE appchi_api")
                yield conn

    app.state.db = APIIdentity()
    unknown = DirectoryObject(
        PrincipalKey("selection-test", str(uuid4())), "team", prefix + " Team", True
    )
    app.state.directory_search = lambda query: (unknown,)
    response = client.get(f"/api/tenants/{tenant}/permission-principals", params={"q": prefix})
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["verification"] == "verification_pending"
    assert not response.json()["data"][0]["selectable"]


@pytest.mark.integration
def test_expired_and_tampered_cursor_require_restart(directory):
    client, db, tenant, _, app, prefix = directory
    with db.transaction() as conn:
        principal(conn, prefix + " Alice")
        principal(conn, prefix + " Bob")
    url = f"/api/tenants/{tenant}/permission-principals"
    token = client.get(url, params={"q": prefix, "limit": 1}).json()["next_cursor"]
    payload = json.loads(
        app.state.settings.cipher().open(base64.urlsafe_b64decode(token), "cursor:v1")
    )
    payload["expires_at"] = "2000-01-01T00:00:00+00:00"
    expired = base64.urlsafe_b64encode(
        app.state.settings.cipher().seal(json.dumps(payload).encode(), "cursor:v1")
    ).decode()
    response = client.get(url, params={"q": prefix, "limit": 1, "cursor": expired})
    assert response.status_code == 409 and response.json()["error"]["code"] == "cursor_expired"
    assert client.get(url, params={"q": prefix, "limit": 1, "cursor": "bad"}).status_code == 400


def ldap_config():
    schema = LDAPSchema(
        "dc=test",
        "(type=user)",
        "(type=team)",
        "entryUUID",
        "displayName",
        "member",
        "active",
        "text",
        eligibility_values=("yes",),
        schema_qualified=True,
    )
    return LDAPConfig("test", "ldap.test", "read-only", "injected", "ca.pem", schema)


class Connection:
    def __init__(self, *, partial=False):
        self.partial, self.calls = partial, []

    def search(self, base, query, **kwargs):
        self.calls.append(query)
        self.result = {
            "result": 0,
            "controls": {"1.2.840.113556.1.4.319": {"value": {"cookie": b""}}},
        }
        if self.partial:
            self.result["result"] = 4
        self.response = (
            [
                {
                    "type": "searchResEntry",
                    "raw_attributes": {
                        "entryUUID": [b"one"],
                        "displayName": [b"Alice"],
                        "active": [b"yes"],
                    },
                }
            ]
            if "type=user" in query
            else []
        )


def test_configured_ldap_search_escapes_literal_and_requires_complete_pages():
    from appchi.identity.ldap import LDAPDirectoryReader

    connection = Connection()
    search = ConfiguredDirectorySearch(
        ldap_config(),
        qualified=True,
        max_seconds=3,
        max_matches=20,
        max_pages=2,
        reader_factory=lambda config: LDAPDirectoryReader(config, connection=connection),
    )
    assert search("a*)(")[0].label == "Alice"
    assert "a\\2a\\29\\28" in connection.calls[0]
    connection.partial = True
    with pytest.raises(DirectoryUnavailable):
        search("Alice")
    with pytest.raises(ValueError):
        ConfiguredDirectorySearch(
            replace(ldap_config(), schema=replace(ldap_config().schema, schema_qualified=False)),
            qualified=True,
            max_seconds=3,
            max_matches=20,
            max_pages=2,
        )


def test_configured_search_factory_accepts_only_complete_qualified_profile(tmp_path, monkeypatch):
    from appchi.identity.selection_config import configured_search
    from appchi.identity import selection_config

    config_path = tmp_path / "directory.json"
    profile_path = tmp_path / "search.json"
    calls = []

    def fake_config_reader(path):
        calls.append(path)
        return ldap_config()

    monkeypatch.setattr(selection_config, "load_directory_config", fake_config_reader)
    settings = SimpleNamespace(directory_config=config_path, directory_search_profile=profile_path)
    valid = {"qualified": True, "max_seconds": 2.5, "max_matches": 100, "max_pages": 2}
    profile_path.write_text(json.dumps(valid), encoding="utf-8")
    search = configured_search(settings)
    assert isinstance(search, ConfiguredDirectorySearch) and search.max_matches == 100
    assert calls == [config_path]
    assert (
        configured_search(
            SimpleNamespace(directory_config=config_path, directory_search_profile=None)
        )
        is None
    )
    for invalid in (
        [],
        {"qualified": True},
        {**valid, "credential": "unsupported"},
        {**valid, "qualified": False},
        {**valid, "max_seconds": True},
        {**valid, "max_matches": 0},
        {**valid, "max_pages": 101},
    ):
        profile_path.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(ValueError):
            configured_search(settings)
    profile_path.write_text(
        '{"qualified":false,"qualified":true,"max_seconds":2,"max_matches":100,"max_pages":2}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        configured_search(settings)
    profile_path.write_text(" " * 65537, encoding="utf-8")
    with pytest.raises(ValueError, match="size"):
        configured_search(settings)
    with pytest.raises(ValueError, match="binding"):
        configured_search(
            SimpleNamespace(directory_config=None, directory_search_profile=profile_path)
        )
