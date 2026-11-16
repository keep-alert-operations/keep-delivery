# ruff: noqa: F811
"""Directory pickers: ADAPI search, its Redis cache, stored-state merge and cursors.

Requirements: REV-73; tenant access 5 (search, search cache, save on pick) and 12;
plan A11, A14, A19.
"""

import base64
import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from keep.identity.adapi import ADAPIConfig
from keep.identity.saver import PickSaver
from keep.identity.search_cache import CachedAnswer, SearchCache, cache_key
from keep.identity.selection_config import (
    SEARCH_CAP,
    ADAPISearch,
    configured_search,
    normalize_term,
    search_params,
)
from keep.main import create_app

from test_maintenance_repository import maintenance_browser, maintenance_db  # noqa: F401

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import local_adapi  # noqa: E402

CLIENT_ID = "selection-test-client"
TTL = 7200


# --- doubles ------------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeRedis:
    """``get``/``set(ex=)``/``close`` with expiry on a shared clock; can be made to fail."""

    def __init__(self, clock):
        self.clock, self.data, self.gets, self.sets, self.error = clock, {}, 0, [], None

    def get(self, key):
        self.gets += 1
        if self.error:
            raise self.error
        value = self.data.get(key)
        if value is None or value[1] <= self.clock():
            self.data.pop(key, None)
            return None
        return value[0]

    def set(self, key, value, ex=None):
        if self.error:
            raise self.error
        self.sets.append((key, ex))
        self.data[key] = (value.encode("utf-8"), self.clock() + ex)

    def close(self):
        pass


class ADAPIDouble:
    """An ``httpx.MockTransport`` answering ``/users`` and ``/groups`` with fixed rows."""

    def __init__(self, users=(), groups=()):
        self.users, self.groups, self.requests = list(users), list(groups), []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        self.requests.append(request)
        if request.headers.get("ClientId") != CLIENT_ID:
            return httpx.Response(401, json={"error": "unauthorized"})
        rows = {"/users": self.users, "/groups": self.groups}.get(request.url.path)
        if rows is None:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=rows[:SEARCH_CAP])

    def params(self, index):
        return parse_qs(urlsplit(str(self.requests[index].url)).query)


def config(base_url="http://adapi.test"):
    return ADAPIConfig(base_url, CLIENT_ID)


def user(name, label=None, control="512"):
    return {"sAMAccountName": name, "displayName": label or name, "userAccountControl": control}


def cache(clock=None):
    clock = clock or Clock()
    redis = FakeRedis(clock)
    return SearchCache(
        redis, ttl_seconds=TTL, prefix="keep:test:adapi-search", monotonic=clock
    ), redis


@pytest.fixture
def adapi():
    server = local_adapi.LocalADAPI(local_adapi.sample_directory(), client_id=CLIENT_ID).start()
    yield server
    server.stop()


# --- A11: escaping and matching ------------------------------------------------------------


def test_search_escapes_the_term_and_keeps_it_literal():
    """A filter-breaking term arrives RFC 4515 escaped and never as a wildcard.

    Requirements: REV-73; tenant access 5 (search); plan A11.
    """
    double = ADAPIDouble()
    search = ADAPISearch(config(), transport=double.transport)
    answer = search("x)(objectClass=*")
    assert answer.objects == () and not answer.capped and not answer.cached
    assert [r.url.path for r in double.requests] == ["/users", "/groups"]
    for index in (0, 1):
        params = double.params(index)
        assert params["customFilter"] == ["(displayName=*x\\29\\28objectclass=\\2a*)"]
        # '*', '(' and ')' can't be in a sAMAccountName: the display-name filter only.
        assert "samAccountName" not in params
    assert search_params("user", "a\\b")[-4] == ("customFilter", "(displayName=*a\\5cb*)")
    plain = dict(search_params("team", "dev"))
    assert plain["samAccountName"] == "*dev*" and plain["customFilter"] == "(displayName=*dev*)"


def test_search_matches_users_and_groups_by_username_or_display_name(adapi):
    """``samAccountName=*term*`` OR ``(displayName=*term*)``, users and groups alike.

    Requirements: REV-73; tenant access 5 (search); plan A11.
    """
    search = ADAPISearch(config(adapi.base_url))
    by_name = {o.key.object_id: o for o in search("local-ed").objects}
    assert set(by_name) == {"local-editor"}
    assert by_name["local-editor"].label == "Local Editor" and by_name["local-editor"].eligible
    by_label = {o.key.object_id: o for o in search("Local Hebrew").objects}
    assert set(by_label) == {"local-hebrew"}
    teams = {o.key.object_id: o for o in search("dev").objects}
    assert (
        teams["keep-developers"].kind == "team" and teams["keep-developers"].label == "Developers"
    )
    # No security-group filter: the distribution group is found too, labelled by cn.
    assert teams["keep-dev-announce"].label == "Developer Announcements"
    assert {o.key.object_id for o in search("Developers").objects} == {"keep-developers"}
    disabled = {o.key.object_id: o for o in search("disabled").objects}
    assert disabled["local-disabled"].eligible is False
    # Computer accounts never become principals.
    assert search("ws-0001").objects == ()
    query = adapi.log()["requests"][0]["query"]
    assert query["samAccountName"] == ["*local-ed*"]
    assert query["customFilter"] == ["(displayName=*local-ed*)"]


# --- A14: the 1,000-row cap ----------------------------------------------------------------


def test_a_capped_answer_asks_the_user_to_type_more():
    """ADAPI's silent 1,000-row cap is reported; 999 rows are a complete answer.

    Requirements: REV-73; tenant access 5 (search); plan A14.
    """
    double = ADAPIDouble(users=[user(f"user{i:04d}") for i in range(SEARCH_CAP)])
    answer = ADAPISearch(config(), transport=double.transport)("user")
    assert answer.capped and len(answer.objects) == SEARCH_CAP
    double.users.pop()
    assert not ADAPISearch(config(), transport=double.transport)("user").capped


# --- A19: the search cache -----------------------------------------------------------------


def test_cache_hit_within_ttl_and_miss_after_expiry():
    """A repeated search within two hours doesn't call ADAPI; after the TTL it does.

    Requirements: REV-73; tenant access 5 (search cache); plan A19.
    """
    clock = Clock()
    store, redis = cache(clock)
    double = ADAPIDouble(users=[user("alice", "Alice Example")], groups=[])
    search = ADAPISearch(config(), cache=store, transport=double.transport)
    first = search("Alice")
    assert len(double.requests) == 2 and not first.cached
    assert {ex for _, ex in redis.sets} == {TTL}
    # The key is a SHA-256 of kind and normalized term: no raw term in Redis.
    assert all("alice" not in key.lower() for key in redis.data)
    assert cache_key(store.prefix, "user", normalize_term("Alice")) in redis.data
    clock.now += TTL - 1
    again = search("  ALICE ")
    assert len(double.requests) == 2 and again.cached and again.objects == first.objects
    clock.now += 2
    expired = search("alice")
    assert len(double.requests) == 4 and not expired.cached


def test_cache_keeps_the_cap_flag():
    """Cached answers keep ADAPI's hit-the-cap flag.

    Requirements: REV-73; tenant access 5 (search cache); plan A14, A19.
    """
    store, _ = cache()
    double = ADAPIDouble(users=[user(f"user{i:04d}") for i in range(SEARCH_CAP)])
    search = ADAPISearch(config(), cache=store, transport=double.transport)
    assert search("user").capped
    hit = search("user")
    assert hit.cached and hit.capped and len(double.requests) == 2


def test_redis_errors_fall_through_to_adapi():
    """Redis down or timing out never fails a search; it goes to ADAPI uncached.

    Requirements: REV-73; tenant access 5 (search cache); plan A19.
    """
    clock = Clock()
    store, redis = cache(clock)
    redis.error = ConnectionError("redis://secret@cache:6379 refused")
    double = ADAPIDouble(users=[user("alice", "Alice")])
    search = ADAPISearch(config(), cache=store, transport=double.transport)
    assert [o.key.object_id for o in search("alice").objects] == ["alice"]
    assert len(double.requests) == 2
    # During the short back-off Redis is not asked again.
    gets = redis.gets
    search("alice")
    assert redis.gets == gets and len(double.requests) == 4
    redis.error = TimeoutError("timed out")
    clock.now += 10
    assert not search("alice").cached and len(double.requests) == 6


def test_corrupt_cache_values_are_ignored_and_replaced():
    """A corrupt or foreign-format value is a miss, and the fresh answer replaces it.

    Requirements: REV-73; tenant access 5 (search cache); plan A19.
    """
    clock = Clock()
    store, redis = cache(clock)
    double = ADAPIDouble(users=[user("alice", "Alice")])
    search = ADAPISearch(config(), cache=store, transport=double.transport)
    for value in (b"\xff not json", b"[]", b'{"v":1,"capped":"no","rows":[]}', b'{"v":9}'):
        for kind in ("user", "team"):
            redis.data[cache_key(store.prefix, kind, "alice")] = (value, clock() + 60)
        before = len(double.requests)
        assert not search("alice").cached
        assert len(double.requests) == before + 2
    assert search("alice").cached


def test_cache_from_settings_needs_redis_and_uses_the_ttl_setting():
    """Without KEEP_REDIS_URL there is no cache; the TTL setting is used as given.

    Requirements: REV-73; tenant access 5 (search cache); plan A19.
    """
    base = {"environment": "development", "adapi_search_cache_ttl_seconds": 7200}
    assert SearchCache.from_settings(SimpleNamespace(redis_url=None, **base)) is None
    with pytest.raises(ValueError):
        SearchCache(FakeRedis(Clock()), ttl_seconds=0)
    assert configured_search(SimpleNamespace(adapi_url=None, adapi_client_id=None), None) is None
    store, redis = cache()
    store.put("user", "x", CachedAnswer(({"sAMAccountName": "x"},), False))
    assert redis.sets[0][1] == TTL


# --- pickers on a database -----------------------------------------------------------------


def principal(
    conn,
    label,
    *,
    kind="user",
    eligible=True,
    verified=True,
    deleted=False,
    super_admin=False,
    source="selection-test",
    object_id=None,
):
    identity = uuid4()
    conn.execute(
        """INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible,deleted,verified_at,super_admin,evidence_generation)
        VALUES(%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN clock_timestamp() END,%s,%s)""",
        (
            identity,
            source,
            object_id or str(identity),
            kind,
            label,
            eligible,
            deleted,
            verified,
            super_admin,
            1 if verified else 0,
        ),
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


def pending_rows(db):
    with db.transaction() as conn:
        return conn.execute("SELECT count(*) AS n FROM directory_verification_requests").fetchone()[
            "n"
        ]


@pytest.mark.integration
def test_directory_authorization_literal_matching_and_safe_cache_metadata(directory):
    """Without ADAPI the picker lists saved principals only, labelled as cached.

    Requirements: tenant access 5 and 12; plan A11.
    """
    client, db, tenant, actor, _, prefix = directory
    with db.transaction() as conn:
        first = principal(conn, prefix + " %_ Alice")
        principal(conn, prefix + " Other")
    url = f"/api/tenants/{tenant}/permission-principals"
    response = client.get(url, params={"q": "%_"})
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["data"]] == [str(first)]
    meta = response.json()["meta"]
    assert meta["cached"] and not meta["complete_company_directory"] and not meta["type_more"]
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
    """Requirements: tenant access 5 (assignee selection)."""
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
    """Requirements: tenant access 12 (cursors)."""
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
    """Requirements: tenant access 5 (team previews)."""
    client, db, tenant, _, _, prefix = directory
    with db.transaction() as conn:
        team = principal(conn, prefix + " Team", kind="team")
        user_id = principal(conn, prefix + " Member")
        conn.execute(
            "INSERT INTO directory_memberships(team_id,user_id) VALUES(%s,%s)", (team, user_id)
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
        str(user_id)
    ]
    assert response.json()["meta"]["membership_scope"] == "eligible_users"


@pytest.mark.integration
def test_search_merges_stored_state_and_never_writes(directory):
    """Unsaved ADAPI rows return id null; saved rows show what is stored; nothing is written.

    Requirements: REV-73; tenant access 5 (search); plan "Save on pick" (search rows).
    """
    client, db, tenant, _, app, prefix = directory
    double = ADAPIDouble(
        users=[
            user(f"{prefix}-new", f"{prefix} New"),
            user(f"{prefix}-known", f"{prefix} Renamed"),
        ],
        groups=[{"sAMAccountName": f"{prefix}-team", "cn": f"{prefix} Team"}],
    )
    app.state.directory_search = ADAPISearch(config(), transport=double.transport)
    with db.transaction() as conn:
        known = principal(
            conn, f"{prefix} Stored", source="adapi", object_id=f"{prefix}-known", eligible=False
        )
        before = conn.execute("SELECT count(*) AS n FROM principals").fetchone()["n"]
    response = client.get(f"/api/tenants/{tenant}/permission-principals", params={"q": prefix})
    assert response.status_code == 200, response.text
    rows = {row["object_id"]: row for row in response.json()["data"]}
    assert rows[f"{prefix}-new"] == {
        "id": None,
        "directory_source": "adapi",
        "object_id": f"{prefix}-new",
        "kind": "user",
        "display_name": f"{prefix} New",
        "deleted": False,
        "verified_at": None,
        "eligible": True,
        "verification": "unverified",
        "selectable": False,
    }
    assert rows[f"{prefix}-team"]["kind"] == "team" and rows[f"{prefix}-team"]["id"] is None
    stored = rows[f"{prefix}-known"]
    assert stored["id"] == str(known) and stored["display_name"] == f"{prefix} Stored"
    assert stored["eligible"] is False and stored["selectable"] is False
    meta = response.json()["meta"]
    assert meta["coverage"] == "configured_directory" and meta["complete_company_directory"]
    assert not meta["cached"] and not meta["type_more"] and meta["search_complete"]
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM principals").fetchone()["n"] == before
    assert pending_rows(db) == 0

    def outage(query):
        from keep.identity.directory import DirectoryUnavailable

        raise DirectoryUnavailable("directory_timeout")

    app.state.directory_search = outage
    response = client.get(f"/api/tenants/{tenant}/permission-principals", params={"q": prefix})
    assert response.json()["meta"]["cached"] and response.json()["meta"]["directory_degraded"]
    assert [row["id"] for row in response.json()["data"]] == [str(known)]


@pytest.mark.integration
def test_capped_answer_sets_type_more_in_the_listing_meta(directory):
    """A 1,000-row ADAPI answer sets ``meta.type_more`` and is not the complete directory.

    Requirements: REV-73; tenant access 5 (search); plan A14.
    """
    client, _, tenant, _, app, prefix = directory
    double = ADAPIDouble(users=[user(f"{prefix}{i:04d}") for i in range(SEARCH_CAP)])
    app.state.directory_search = ADAPISearch(config(), transport=double.transport)
    url = f"/api/tenants/{tenant}/permission-principals"
    response = client.get(url, params={"q": prefix, "limit": 200})
    assert response.status_code == 200, response.text
    meta = response.json()["meta"]
    assert meta["type_more"] and not meta["search_complete"]
    assert not meta["complete_company_directory"]
    assert len(response.json()["data"]) == 200 and response.json()["next_cursor"]
    assert client.get(url, params={"q": prefix, "limit": 201}).status_code == 422


@pytest.mark.integration
def test_cached_answer_is_labelled_and_resolve_reads_adapi(directory, adapi):
    """Picker listings use the cache; save on pick always reads ADAPI.

    Requirements: REV-73; tenant access 5 (search cache, save on pick); plan A19.
    """
    client, db, tenant, _, app, _ = directory
    store, redis = cache()
    app.state.directory_search = ADAPISearch(config(adapi.base_url), cache=store)
    app.state.pick_saver = PickSaver(db, config(adapi.base_url), local_adapi.SUPERADMIN_GROUP)
    url = f"/api/tenants/{tenant}/permission-principals"
    assert client.get(url, params={"q": "local-ed"}).status_code == 200
    calls = adapi.count()
    cached = client.get(url, params={"q": "local-ed"})
    assert adapi.count() == calls
    meta = cached.json()["meta"]
    assert meta["cached"] and meta["coverage"] == "search_cache"
    assert not meta["complete_company_directory"]
    row = cached.json()["data"][0]
    assert row["object_id"] == "local-editor" and row["id"] is None
    gets = redis.gets
    saved = client.post(f"{url}/resolve", json={"kind": "user", "username": "local-editor"})
    assert saved.status_code == 200, saved.text
    assert adapi.count() > calls and redis.gets == gets
    after = client.get(url, params={"q": "local-ed"})
    assert after.json()["data"][0]["id"] == saved.json()["data"]["id"]
    assert after.json()["meta"]["coverage"] == "search_cache"


@pytest.mark.integration
def test_live_discovery_rechecks_privilege_before_returning_rows(directory):
    """Requirements: tenant access 5 and 10."""
    client, db, tenant, actor, app, prefix = directory
    double = ADAPIDouble(users=[user(f"{prefix}-private", f"{prefix} Private")])
    inner = ADAPISearch(config(), transport=double.transport)

    def revoke_during_search(query):
        with db.transaction(tenant) as conn:
            conn.execute(
                "UPDATE tenant_grants SET role='edit' WHERE tenant_id=%s AND principal_id=%s",
                (tenant, actor),
            )
        return inner(query)

    app.state.directory_search = revoke_during_search
    result = client.get(f"/api/tenants/{tenant}/permission-principals", params={"q": prefix})
    assert result.status_code == 403 and f"{prefix} Private" not in result.text
    assert pending_rows(db) == 0


@pytest.mark.integration
def test_nonowner_api_role_can_search_without_reading_other_grants(directory):
    """Requirements: tenant access 5; database privileges (keep_api)."""
    client, db, tenant, _, app, prefix = directory

    class APIIdentity:
        @contextmanager
        def transaction(self, *args, **kwargs):
            with db.transaction(*args, **kwargs) as conn:
                conn.execute("SET LOCAL ROLE keep_api")
                yield conn

    app.state.db = APIIdentity()
    double = ADAPIDouble(groups=[{"sAMAccountName": f"{prefix}-team", "cn": f"{prefix} Team"}])
    app.state.directory_search = ADAPISearch(config(), transport=double.transport)
    response = client.get(f"/api/tenants/{tenant}/permission-principals", params={"q": prefix})
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["id"] is None
    assert not response.json()["data"][0]["selectable"]


@pytest.mark.integration
def test_expired_and_tampered_cursor_require_restart(directory):
    """Requirements: tenant access 12 (cursors)."""
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


def test_cache_key_is_a_sha256_of_kind_and_term():
    """Requirements: REV-73; tenant access 5 (search cache); plan A19."""
    key = cache_key("p", "user", "alice")
    assert key == "p:" + hashlib.sha256(b"user\x00alice").hexdigest()
    assert cache_key("p", "team", "alice") != key
