# ruff: noqa: F811 -- imported reusable fixtures
"""Save on pick and the background saver over the mock ADAPI on real PostgreSQL.

Each test gets a fresh database cloned from a migrated template (needs
KEEP_IDENTITY_TEST_ADMIN_URL), a fresh mock ADAPI (scripts/local_adapi.py) and the
Product API in a TestClient, whose lifespan starts and stops the saver thread.

Requirements: REV-73; tenant access 5 (save on pick, polling, background save) and
12; worker scope 4-6; plan "Save on pick", A12, A21, A22, A26. REV-76; plan (REV-75/76)
B2, B6 (b), B9.
"""

import base64
import hashlib
import logging
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import keep.main
from adapi_fixture import MOCK_CLIENT_ID, adapi_factory, mock_adapi  # noqa: F401
import local_adapi
from test_adapi_publication import database, template  # noqa: F401

from keep.access import effective_role
from keep.config import Settings
from keep.db import Database
from keep.identity.adapi import ADAPIConfig, principal_key, user_evidence
from keep.identity.directory import DirectoryEvidence, DirectoryObject
from keep.identity.publication import allocate_generation, lock_identity, publish
from keep.identity.saver import PickSaver
from keep.identity.worker import run_cycle
from keep.main import create_app

pytestmark = pytest.mark.integration

SUPERADMIN = local_adapi.SUPERADMIN_GROUP
ORIGIN = "https://app.test"
SESSION_COOKIE = "__Host-keep_session"
REPOSITORY = Path(__file__).resolve().parents[2]
WRAPPING = {
    "wrapping_key": base64.b64encode(b"k" * 32).decode(),
    "wrapping_key_id": "test",
    "deployment_epoch": "test",
    "public_origin": ORIGIN,
}


# --- helpers ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Person:
    id: object
    headers: dict


def new_tenant(db):
    tenant = uuid4()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
            (tenant, str(tenant), str(tenant)),
        )
        for table in ("tenant_work_state", "tenant_view_state", "tenant_configuration_guard"):
            conn.execute(f"INSERT INTO {table}(tenant_id) VALUES(%s)", (tenant,))
        conn.execute("INSERT INTO admission_capacity(scope) VALUES(%s)", (str(tenant),))
    return tenant


def person(db, tenant, role):
    """A signed-in synthetic user (source ``test``, so the refresh never tracks it)."""
    identity = uuid4()
    handle, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    hashed = hashlib.sha256(handle.encode()).digest()
    with db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='human_auth'")
        conn.execute(
            """INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible,evidence_generation,verified_at)
            VALUES(%s,'test',%s,'user',%s,true,1,clock_timestamp())""",
            (identity, str(identity), f"Synthetic {role}"),
        )
        if role:
            conn.execute(
                "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,%s)",
                (tenant, identity, role),
            )
        conn.execute(
            """INSERT INTO auth_sessions(handle_hash,principal_id,csrf_hash,csrf_cipher,auth_time,absolute_at,deployment_epoch)
            VALUES(%s,%s,%s,%s,clock_timestamp(),clock_timestamp()+interval '1 day','test')""",
            (
                hashed,
                identity,
                hashlib.sha256(csrf.encode()).digest(),
                Settings(database_url="unused", **WRAPPING)
                .cipher()
                .seal(csrf.encode(), "session-csrf:" + hashed.hex()),
            ),
        )
    return Person(
        identity,
        {"Cookie": f"{SESSION_COOKIE}={handle}", "Origin": ORIGIN, "X-CSRF-Token": csrf},
    )


@dataclass
class Site:
    db: object
    tenant: object
    admin: Person
    editor: Person
    viewer: Person


@pytest.fixture
def site(database):
    tenant = new_tenant(database)
    return Site(
        database,
        tenant,
        person(database, tenant, "admin"),
        person(database, tenant, "edit"),
        person(database, tenant, "view"),
    )


def settings(mock, **extra):
    return Settings(
        database_url="unused",
        **WRAPPING,
        capacity_profile=REPOSITORY / "qualification" / "development-capacity.json",
        adapi_url=mock.base_url,
        adapi_client_id=mock.client_id,
        superadmin_group=SUPERADMIN,
        **extra,
    )


def adapi_config(mock):
    return ADAPIConfig(mock.base_url, mock.client_id)


@contextmanager
def api(db, mock, **saver):
    """The Product API on ``db``; ``saver`` options replace its PickSaver (lease, budget)."""
    app = create_app(settings(mock), db)
    with TestClient(app, base_url=ORIGIN) as client:
        if saver:
            app.state.pick_saver.close()
            app.state.pick_saver = PickSaver(db, adapi_config(mock), SUPERADMIN, **saver)
        client.saver = app.state.pick_saver
        yield client


def url(tenant):
    return f"/api/tenants/{tenant}/permission-principals/resolve"


def resolve(client, site, who, kind, username, **extra):
    return client.post(
        url(site.tenant), json={"kind": kind, "username": username, **extra}, headers=who.headers
    )


def poll(client, site, who, kind, username):
    return client.get(
        url(site.tenant), params={"kind": kind, "username": username}, headers=who.headers
    )


def wait_saved(client, site, kind, username, timeout=60.0):
    """Poll as the admin until the save is no longer pending; returns the last answer."""
    deadline = time.monotonic() + timeout
    while True:
        response = poll(client, site, site.admin, kind, username)
        if response.status_code != 202 or time.monotonic() > deadline:
            return response
        assert response.json() == {"data": {"status": "pending"}, "meta": {}}
        time.sleep(0.2)


def wait_until(condition, timeout=60.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.05)


def pend(db, name, kind="team", *, owner=None, lease="NULL", failure=None):
    """A pending save as a hand-over (or a stopped process) would have left it."""
    with db.transaction() as conn:
        conn.execute(
            f"""INSERT INTO directory_verification_requests(source,object_id,kind,lease_owner,lease_until,last_failure)
            VALUES('adapi',%s,%s,%s,{lease},%s)""",
            (name, kind, owner, failure),
        )


def pending(db, name):
    with db.transaction() as conn:
        return conn.execute(
            """SELECT kind,lease_owner,lease_until,last_failure FROM directory_verification_requests
            WHERE source='adapi' AND object_id=%s""",
            (name,),
        ).fetchone()


def stored(db, name):
    with db.transaction() as conn:
        return conn.execute(
            """SELECT p.*,r.generation AS checkpoint FROM principals p
            LEFT JOIN directory_root_checkpoints r ON r.root_id=p.id
            WHERE p.directory_source='adapi' AND p.object_id=%s""",
            (name,),
        ).fetchone()


def publications(db, root):
    """Accepted publications whose coverage roots include ``root``."""
    with db.transaction() as conn:
        return conn.execute(
            """SELECT count(*) AS n FROM directory_verifications
            WHERE coverage->'roots' ? %s AND coverage->>'accepted'='true'""",
            (root,),
        ).fetchone()["n"]


def members(db, group):
    with db.transaction() as conn:
        return {
            row["object_id"]: row["display_name"]
            for row in conn.execute(
                """SELECT u.object_id,u.display_name FROM directory_memberships m
                JOIN principals t ON t.id=m.team_id JOIN principals u ON u.id=m.user_id
                WHERE t.directory_source='adapi' AND t.object_id=%s""",
                (group,),
            ).fetchall()
        }


def requests_naming(mock, route, name):
    """Completed mock requests on ``route`` whose samAccountName parameter is ``name``."""
    return [
        r
        for r in mock.log()["requests"]
        if r["route"] == route
        and any(k.casefold() == "samaccountname" and v == [name] for k, v in r["query"].items())
    ]


def outcomes(saver, name):
    return [(r.outcome, r.reason) for r in saver.results if r.object_id == name]


def big_directory(**options):
    return local_adapi.merge_directories(
        local_adapi.sample_directory(), local_adapi.large_group(**options)
    )


# --- A12 ----------------------------------------------------------------------------------


def test_unknown_group_is_saved_on_pick_and_granted_at_once(site, mock_adapi):
    """An admin's pick saves a group with its recursive members; it can be granted at once.

    Requirements: REV-73; tenant access 5 (save on pick) and 12; plan A12.
    """
    with api(site.db, mock_adapi) as client:
        response = resolve(client, site, site.admin, "team", "  Keep-Developers ")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["id"] and data["kind"] == "team" and data["display_name"] == "Developers"
        assert data["object_id"] == "keep-developers" and data["directory_source"] == "adapi"
        assert data["selectable"] and data["verification"] == "verified"
        with site.db.transaction() as conn:
            revision = conn.execute(
                "SELECT revision FROM tenants WHERE id=%s", (site.tenant,)
            ).fetchone()["revision"]
        granted = client.patch(
            f"/api/tenants/{site.tenant}/permissions",
            json={
                "command_id": str(uuid4()),
                "expected_revision": str(revision),
                "operations": [{"principal_id": data["id"], "action": "set", "role": "edit"}],
            },
            headers=site.admin.headers,
        )
        assert granted.status_code == 200, granted.text
        # Already saved: answered as stored, with no ADAPI read.
        mock_adapi.reset_log()
        again = resolve(client, site, site.admin, "team", "keep-developers")
        assert again.status_code == 200 and again.json()["data"]["id"] == data["id"]
        assert poll(client, site, site.admin, "team", "keep-developers").status_code == 200
        assert mock_adapi.count() == 0
        for who in (site.viewer, site.editor):
            assert resolve(client, site, who, "team", "keep-observers").status_code == 403
            assert poll(client, site, who, "team", "keep-observers").status_code == 403
    assert stored(site.db, "keep-observers") is None
    with site.db.transaction() as conn:
        roles = {}
        for name in ("local-editor", "local-hebrew", "local-distribution"):
            row = conn.execute(
                "SELECT * FROM principals WHERE directory_source='adapi' AND object_id=%s", (name,)
            ).fetchone()
            roles[name] = effective_role(conn, row, site.tenant)
    # local-distribution is reached only through the distribution group keep-dev-announce.
    assert roles == {
        "local-editor": "edit",
        "local-hebrew": "edit",
        "local-distribution": "edit",
    }
    assert stored(site.db, "ws-0001$") is None


def test_a_saved_user_and_the_422_cases_save_nothing(site, mock_adapi):
    """Users save inline; unknown, ambiguous, wrong-kind or invalid names save nothing.

    Requirements: REV-73; tenant access 5 (save on pick: 422 with nothing saved). REV-76;
    plan (REV-75/76) B2 (a missing person is 422 like a missing group), B9 (a person is
    read with ``/users/user``, never a ``/users`` search).
    """
    mock_adapi.add_user("dup-user", displayName="First")
    mock_adapi.add_user("DUP-USER", displayName="Second")
    with api(site.db, mock_adapi) as client:
        saved = resolve(client, site, site.admin, "user", "local-hebrew")
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["display_name"] == "משתמש Local Hebrew"
        mock_adapi.reset_log()
        for kind, name, code in (
            ("team", "no-such-group", "principal_not_in_directory"),
            ("user", "no-such-user", "principal_not_in_directory"),
            ("user", "dup-user", "principal_ambiguous"),
            ("team", "local-editor", "principal_kind_mismatch"),
        ):
            response = resolve(client, site, site.admin, kind, name)
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == code
            assert stored(site.db, name) is None and pending(site.db, name) is None
        assert requests_naming(mock_adapi, "user", "no-such-user")
        assert not [r for r in mock_adapi.log()["requests"] if r["route"] == "users"]
        for name in ("a*b", "x@corp", "", "a" * 65, "back\\slash"):
            response = resolve(client, site, site.admin, "user", name)
            assert response.status_code == 422 and response.json()["error"]["code"] == (
                "invalid_username"
            )
        assert resolve(client, site, site.admin, "group", "x").status_code == 422
        assert (
            client.post(
                url(site.tenant),
                json={"kind": "user", "username": "local-viewer", "extra": 1},
                headers=site.admin.headers,
            ).status_code
            == 422
        )
        missing = poll(client, site, site.admin, "team", "keep-observers")
        assert missing.status_code == 404


def test_a_pick_lifts_a_person_stored_ineligible_or_deleted(site, mock_adapi):
    """A stored person row an older version wrote (ineligible, or deleted) is not usable,
    so the pick reads ADAPI again and publishes the person as eligible.

    Requirements: REV-76; tenant access 5 (save on pick); plan (REV-75/76) B6 (b), B9.
    """
    with api(site.db, mock_adapi) as client:
        assert resolve(client, site, site.admin, "user", "local-viewer").status_code == 200
        for change in ("eligible=false", "eligible=false,deleted=true"):
            with site.db.transaction() as conn:
                conn.execute(
                    f"UPDATE principals SET {change} WHERE directory_source='adapi' AND object_id='local-viewer'"
                )
            mock_adapi.reset_log()
            response = resolve(client, site, site.admin, "user", "local-viewer")
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["selectable"] and data["verification"] == "verified", change
            row = stored(site.db, "local-viewer")
            assert row["eligible"] and not row["deleted"], change
            assert len(requests_naming(mock_adapi, "user", "local-viewer")) == 1
            assert mock_adapi.count("users") == 0


# --- A22 ----------------------------------------------------------------------------------


def test_large_group_saves_inline_within_the_request(site, adapi_factory):
    """1,500 members (some through a distribution group) save inline in one request.

    Requirements: REV-73; tenant access 5 (inline save); plan A22.
    """
    mock = adapi_factory(big_directory())
    mock.set_latency("*", 0.05)
    with api(site.db, mock) as client:
        mock.reset_log()
        started = time.monotonic()
        response = resolve(client, site, site.admin, "team", "big-team")
        elapsed = time.monotonic() - started
        assert response.status_code == 200, response.text
    calls = mock.count()
    print(f"A22 inline: {elapsed:.2f}s, {calls} ADAPI calls")
    assert elapsed <= 8.0
    assert calls <= 1 + 1 + 30
    saved = members(site.db, "big-team")
    assert len(saved) == 1500
    assert saved["big01500"] == "Member 01500" and saved["big00001"] == "Member 00001"
    assert all(label == f"Member {name[3:]}" for name, label in saved.items())
    assert pending(site.db, "big-team") is None and publications(site.db, "big-team") == 1


def test_group_too_slow_for_the_request_saves_in_the_background(site, adapi_factory):
    """Past the 7 s verifier deadline: 202 with nothing published, then the saver saves it.

    Requirements: REV-73; tenant access 5 (inline save, polling, background save);
    worker scope 4; plan A22.
    """
    mock = adapi_factory(big_directory())
    mock.set_latency("users", 0.25)
    with api(site.db, mock) as client:
        started = time.monotonic()
        response = resolve(client, site, site.admin, "team", "big-team")
        inline = time.monotonic() - started
        assert response.status_code == 202, response.text
        assert response.json() == {"data": {"status": "pending"}, "meta": {}}
        # Nothing was published by the request itself.
        assert stored(site.db, "big-team") is None and publications(site.db, "big-team") == 0
        row = pending(site.db, "big-team")
        assert row["kind"] == "team" and row["lease_owner"] and not row["last_failure"]
        polls = 0
        while True:
            answer = poll(client, site, site.admin, "team", "big-team")
            if answer.status_code != 202:
                break
            polls += 1
            time.sleep(0.5)
        total = time.monotonic() - started
        assert answer.status_code == 200, answer.text
        assert answer.json()["data"]["display_name"] == "big-team"
        assert outcomes(client.saver, "big-team") == [("published", None)]
    print(f"A22 background: inline {inline:.2f}s, saved after {total:.2f}s, {polls} polls")
    assert 7.0 <= inline < 10.0 and polls >= 1
    saved = members(site.db, "big-team")
    assert len(saved) == 1500 and saved["big00042"] == "Member 00042"
    assert pending(site.db, "big-team") is None and publications(site.db, "big-team") == 1


def test_one_call_too_slow_for_the_request_saves_in_the_background(site, mock_adapi):
    """A members call past the 3 s inline call cap is a save that doesn't fit, not an
    outage: 202 with nothing published, then the saver (5 s call cap) saves it.

    Requirements: REV-73; tenant access 5 (inline save, background save); plan A22.
    """
    mock_adapi.set_latency("members", 3.5)
    with api(site.db, mock_adapi) as client:
        response = resolve(client, site, site.admin, "team", "keep-observers")
        assert response.status_code == 202, response.text
        assert publications(site.db, "keep-observers") == 0
        row = pending(site.db, "keep-observers")
        assert row["lease_owner"] and not row["last_failure"]
        answer = wait_saved(client, site, "team", "keep-observers")
        assert answer.status_code == 200, answer.text
        assert outcomes(client.saver, "keep-observers") == [("published", None)]
    assert pending(site.db, "keep-observers") is None
    assert publications(site.db, "keep-observers") == 1


# --- A21 (resolve side) -------------------------------------------------------------------


def team_evidence(db, name):
    """A complete evidence of the group alone, at a newly allocated generation."""
    with db.transaction() as conn:
        generation = allocate_generation(conn)
    key = principal_key(name)
    now = datetime.now(UTC)
    return DirectoryEvidence(
        "adapi",
        generation,
        now,
        now,
        frozenset({key}),
        {key: DirectoryObject(key, "team", name, True)},
        {key: frozenset()},
        {key: frozenset()},
        True,
    )


def publish_now(db, evidence):
    with db.transaction() as conn:
        lock_identity(conn)
        return publish(conn, evidence, superadmin_group=principal_key(SUPERADMIN))


def test_a_deleted_group_back_in_the_directory_is_saved_again_on_pick(site, mock_adapi):
    """A stored tombstone isn't a saved group: picking the name reads ADAPI again.

    A team a refresh marked deleted, which no grant references any more, is never read
    by a later refresh; recreated in AD, it must be savable on pick.
    Requirements: REV-73; tenant access 5 (save on pick); plan A12.
    """
    evidence = team_evidence(site.db, "keep-observers")
    key = principal_key("keep-observers")
    tombstone = replace(
        evidence,
        objects={key: DirectoryObject(key, "team", "keep-observers", False, True)},
        edges={},
    )
    assert publish_now(site.db, tombstone).accepted
    assert stored(site.db, "keep-observers")["deleted"]
    with api(site.db, mock_adapi) as client:
        # The picker saves only a listed row that is unsaved, eligible and not deleted.
        listing = client.get(
            f"/api/tenants/{site.tenant}/permission-principals",
            params={"q": "keep-observers"},
            headers=site.admin.headers,
        )
        assert listing.status_code == 200, listing.text
        [listed] = [row for row in listing.json()["data"] if row["object_id"] == key.object_id]
        assert listed["id"] is None and not listed["deleted"] and listed["eligible"]
        response = resolve(client, site, site.admin, "team", "keep-observers")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["selectable"]
    row = stored(site.db, "keep-observers")
    assert not row["deleted"] and row["checkpoint"] is not None
    assert publications(site.db, "keep-observers") == 2


def test_resolve_fenced_twice_answers_directory_busy(site, mock_adapi):
    """A newer publication of the same root fences the save; one retry, then 503 busy.

    Requirements: REV-73; tenant access 5 (inline save); plan "Concurrent
    publications", A21.
    """
    # Gate one blocks the first attempt's members read; gate two (skipping that read)
    # blocks the retry's. Gate one keeps counting arrivals after it is opened.
    first = mock_adapi.pause("members", name="keep-observers")
    mock_adapi.pause("members", name="keep-observers", skip=1)
    with api(site.db, mock_adapi) as client:
        result = {}
        request = threading.Thread(
            target=lambda: result.update(
                response=resolve(client, site, site.admin, "team", "keep-observers")
            )
        )
        request.start()
        assert mock_adapi.wait_for_pause("members", "keep-observers")
        assert publish_now(site.db, team_evidence(site.db, "keep-observers")).accepted
        first.set()
        assert mock_adapi.wait_for_pause("members", "keep-observers", count=2)
        assert publish_now(site.db, team_evidence(site.db, "keep-observers")).accepted
        mock_adapi.release_all()
        request.join(10)
        response = result["response"]
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "directory_busy"
    assert pending(site.db, "keep-observers")["last_failure"] == "directory_busy"
    assert publications(site.db, "keep-observers") == 2


# --- A26 ----------------------------------------------------------------------------------


def test_a26_1_polls_never_call_adapi_and_never_queue_a_second_job(site, mock_adapi):
    """A queued, unstarted job: polls answer 202 from PostgreSQL and queue nothing.

    Requirements: REV-73; tenant access 5 (polling); worker scope 4; plan A26 (1).
    """
    pend(site.db, "keep-developers")
    pend(site.db, "keep-observers")
    mock_adapi.set_latency("members", 3.0)
    with api(site.db, mock_adapi) as client:
        # The first job keeps the single saver thread busy for about 3 s.
        assert poll(client, site, site.admin, "team", "keep-developers").status_code == 202
        wait_until(lambda: requests_naming(mock_adapi, "groups", "keep-developers"))
        assert poll(client, site, site.admin, "team", "keep-observers").status_code == 202
        owner = pending(site.db, "keep-observers")["lease_owner"]
        assert owner and client.saver.queued() == 1
        for _ in range(4):
            assert poll(client, site, site.admin, "team", "keep-observers").status_code == 202
            assert pending(site.db, "keep-observers")["lease_owner"] == owner
            assert client.saver.queued() == 1
        assert not requests_naming(mock_adapi, "groups", "keep-observers")
        assert wait_saved(client, site, "team", "keep-developers").status_code == 200
        assert wait_saved(client, site, "team", "keep-observers").status_code == 200
        for name in ("keep-developers", "keep-observers"):
            assert len(requests_naming(mock_adapi, "members", name)) == 1
        # A failed save: polls answer 503 without any ADAPI call.
        pend(site.db, "keep-administrators", failure="directory_unavailable")
        mock_adapi.reset_log()
        for _ in range(3):
            failed = poll(client, site, site.admin, "team", "keep-administrators")
            assert failed.status_code == 503
            assert failed.json()["error"]["code"] == "directory_unavailable"
        assert mock_adapi.count() == 0
        saver = client.saver
    for name in ("keep-developers", "keep-observers"):
        assert outcomes(saver, name) == [("published", None)]
        assert publications(site.db, name) == 1
    assert len(saver.results) == 2


def test_a26_2_two_api_processes_take_one_lease_and_publish_once(site, mock_adapi):
    """Two API apps on one database with concurrent polls and POSTs: one lease, one save.

    Requirements: REV-73; tenant access 5 (background save); plan A26 (2).
    """
    pend(site.db, "keep-developers")
    mock_adapi.set_latency("members", 1.5)
    with api(site.db, mock_adapi) as first, api(site.db, mock_adapi) as second:
        barrier = threading.Barrier(8)
        answers = []

        def call(client, method):
            barrier.wait()
            if method == "poll":
                answers.append(poll(client, site, site.admin, "team", "keep-developers"))
            else:
                answers.append(resolve(client, site, site.admin, "team", "keep-developers"))

        threads = [
            threading.Thread(target=call, args=(client, method))
            for client in (first, second)
            for method in ("poll", "poll", "post", "post")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert sorted(a.status_code for a in answers) == [202] * 8
        for client in (first, second):
            for _ in range(3):
                assert poll(client, site, site.admin, "team", "keep-developers").status_code in (
                    200,
                    202,
                )
        assert wait_saved(first, site, "team", "keep-developers").status_code == 200
        assert wait_saved(second, site, "team", "keep-developers").status_code == 200
        savers = (first.saver, second.saver)
    results = [outcomes(saver, "keep-developers") for saver in savers]
    assert sorted(results) == [[], [("published", None)]]
    assert publications(site.db, "keep-developers") == 1
    assert len(requests_naming(mock_adapi, "members", "keep-developers")) == 1
    assert pending(site.db, "keep-developers") is None


def test_a26_3_a_late_owner_changes_nothing_after_its_lease_was_taken(site, mock_adapi):
    """A lease shorter than the job: the next poll takes it over; the late owner's
    failure update changes nothing and exactly one publication happens.

    Requirements: REV-73; worker scope 4 (lease, owner-filtered updates); plan A26 (3).
    """
    pend(site.db, "keep-developers")
    # The first job blocks on its member-label read (a /users batch naming local-editor).
    mock_adapi.pause("users", name="local-editor")
    with api(site.db, mock_adapi, lease_seconds=1.0) as client:
        assert poll(client, site, site.admin, "team", "keep-developers").status_code == 202
        late = pending(site.db, "keep-developers")["lease_owner"]
        assert mock_adapi.wait_for_pause("users", "local-editor")
        time.sleep(1.1)
        # The first job's lease expired while it reads: this poll hands it over again.
        assert poll(client, site, site.admin, "team", "keep-developers").status_code == 202
        owner = pending(site.db, "keep-developers")["lease_owner"]
        assert owner != late
        # Hold the new job at its first call, and fail the late job's blocked read:
        # its failure update (filtered on its lease owner) must change nothing.
        mock_adapi.pause("groups", name="keep-developers")
        mock_adapi.fail("users", 500, name="local-editor")
        mock_adapi.release("users")
        wait_until(lambda: len(client.saver.results) >= 1)
        row = pending(site.db, "keep-developers")
        assert row["last_failure"] is None and row["lease_owner"] == owner
        mock_adapi.clear_failures()
        mock_adapi.release_all()
        wait_until(lambda: len(client.saver.results) >= 2)
        saver = client.saver
        assert poll(client, site, site.admin, "team", "keep-developers").status_code == 200
    assert [(r.job, r.outcome) for r in saver.results] == [
        (late, "failed"),
        (owner, "published"),
    ]
    assert publications(site.db, "keep-developers") == 1
    assert pending(site.db, "keep-developers") is None


def test_a26_4_an_expired_lease_is_handed_over_and_completes(site, mock_adapi):
    """A save whose process died (expired lease) restarts on the next poll; a live foreign
    lease is left alone.

    Requirements: REV-73; tenant access 5 (polling); plan A26 (4).
    """
    foreign = uuid4()
    pend(
        site.db,
        "keep-observers",
        owner=foreign,
        lease="clock_timestamp()+interval '60 seconds'",
    )
    pend(
        site.db,
        "keep-developers",
        owner=uuid4(),
        lease="clock_timestamp()-interval '1 second'",
    )
    with api(site.db, mock_adapi) as client:
        assert poll(client, site, site.admin, "team", "keep-observers").status_code == 202
        assert resolve(client, site, site.admin, "team", "keep-observers").status_code == 202
        assert pending(site.db, "keep-observers")["lease_owner"] == foreign
        assert client.saver.queued() == 0 and not outcomes(client.saver, "keep-observers")
        assert wait_saved(client, site, "team", "keep-developers").status_code == 200
        assert outcomes(client.saver, "keep-developers") == [("published", None)]
    assert pending(site.db, "keep-developers") is None
    assert stored(site.db, "keep-developers")["checkpoint"]


def test_a26_5_a_member_signing_in_mid_save_keeps_the_newer_row(site, mock_adapi):
    """A sign-in publishes a member while the group save pauses; both publish and the
    member keeps the sign-in's newer row.

    Requirements: REV-73; plan "Concurrent publications" (narrower fence), A26 (5).
    """
    pend(site.db, "keep-developers")
    mock_adapi.pause("users", name="local-editor")
    with api(site.db, mock_adapi) as client:
        assert poll(client, site, site.admin, "team", "keep-developers").status_code == 202
        assert mock_adapi.wait_for_pause("users", "local-editor")
        # Sign-in style: the person's own /users row at a newer generation.
        with site.db.transaction() as conn:
            generation = allocate_generation(conn)
        row = {
            "sAMAccountName": "local-editor",
            "displayName": "Editor Signed In",
            "userAccountControl": "512",
        }
        signed = publish_now(site.db, user_evidence("adapi", row, generation, datetime.now(UTC)))
        assert signed.accepted
        mock_adapi.release_all()
        assert wait_saved(client, site, "team", "keep-developers").status_code == 200
        assert outcomes(client.saver, "keep-developers") == [("published", None)]
    editor = stored(site.db, "local-editor")
    assert editor["display_name"] == "Editor Signed In"
    assert editor["evidence_generation"] == generation
    group = stored(site.db, "keep-developers")
    assert group["checkpoint"] < generation
    assert members(site.db, "keep-developers")["local-editor"] == "Editor Signed In"
    assert publications(site.db, "keep-developers") == 1


def test_a26_6_an_adapi_failure_is_shown_and_try_again_completes(site, mock_adapi):
    """A failed background save answers 503 with its reason; POST retry: true saves it.

    Requirements: REV-73; tenant access 5 (polling, Try again); plan A26 (6).
    """
    pend(site.db, "keep-observers")
    mock_adapi.fail("members", 500)
    with api(site.db, mock_adapi) as client:
        assert poll(client, site, site.admin, "team", "keep-observers").status_code == 202
        failed = wait_saved(client, site, "team", "keep-observers")
        assert failed.status_code == 503, failed.text
        assert failed.json()["error"]["code"] == "directory_unavailable"
        mock_adapi.reset_log()
        assert poll(client, site, site.admin, "team", "keep-observers").status_code == 503
        again = resolve(client, site, site.admin, "team", "keep-observers")
        assert again.status_code == 503
        assert again.json()["error"]["code"] == "directory_unavailable"
        assert mock_adapi.count() == 0
        mock_adapi.clear_failures()
        retried = resolve(client, site, site.admin, "team", "keep-observers", retry=True)
        assert retried.status_code == 202, retried.text
        assert wait_saved(client, site, "team", "keep-observers").status_code == 200
        assert outcomes(client.saver, "keep-observers") == [
            ("failed", "directory_unavailable"),
            ("published", None),
        ]
    assert pending(site.db, "keep-observers") is None
    assert members(site.db, "keep-observers") == {"local-viewer": "Local Viewer"}


def test_an_inline_outage_is_remembered_and_try_again_saves_in_the_background(site, mock_adapi):
    """An ADAPI outage during the inline save: 503 directory_unavailable, nothing saved;
    Try again hands the save to the background saver.

    Requirements: REV-73; tenant access 5 (inline save, Try again); plan A26 (6).
    """
    mock_adapi.fail("groups", 503)
    with api(site.db, mock_adapi) as client:
        response = resolve(client, site, site.admin, "team", "keep-observers")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "directory_unavailable"
        assert stored(site.db, "keep-observers") is None
        assert poll(client, site, site.admin, "team", "keep-observers").status_code == 503
        mock_adapi.clear_failures()
        retried = resolve(client, site, site.admin, "team", "keep-observers", retry=True)
        assert retried.status_code == 202
        assert wait_saved(client, site, "team", "keep-observers").status_code == 200


def test_a26_7_a_group_over_the_cap_saves_nothing_and_the_refresh_still_publishes(
    site, adapi_factory
):
    """A group over 10,000 objects: 503 directory_group_too_large and nothing saved; the
    refresh skips the failed pick and publishes everything else.

    Requirements: REV-73; worker scope 4 (failed pending saves are skipped); plan A26 (7).
    """
    mock = adapi_factory(big_directory(name="huge-team", members=10001, distribution_members=0))
    with api(site.db, mock) as client:
        observers = resolve(client, site, site.admin, "team", "keep-observers")
        assert observers.status_code == 200
        with site.db.transaction() as conn:
            conn.execute(
                "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'view')",
                (site.tenant, observers.json()["data"]["id"]),
            )
        response = resolve(client, site, site.admin, "team", "huge-team")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "directory_group_too_large"
        failed = poll(client, site, site.admin, "team", "huge-team")
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "directory_group_too_large"
    assert stored(site.db, "huge-team") is None
    with site.db.transaction() as conn:
        assert not conn.execute(
            "SELECT 1 FROM principals WHERE directory_source='adapi' AND object_id LIKE 'big%%'"
        ).fetchone()
    before = publications(site.db, "keep-observers")
    summary = run_cycle(site.db, adapi_config(mock), SUPERADMIN)
    assert summary.ok, summary.failed
    assert publications(site.db, "keep-observers") == before + 1
    assert stored(site.db, SUPERADMIN)["checkpoint"]
    assert pending(site.db, "huge-team")["last_failure"] == "directory_group_too_large"
    assert stored(site.db, "huge-team") is None


class RecordingDatabase(Database):
    closed_at = None

    def close(self):
        RecordingDatabase.closed_at = time.monotonic()
        super().close()


def test_a26_8_shutdown_releases_the_lease_and_joins_before_the_pool_closes(
    site, mock_adapi, monkeypatch, caplog
):
    """Lifespan shutdown during a save: lease released, saver joined before the pool
    closes, nothing logged from a closed pool, and the next poll restarts the save.

    Requirements: REV-73; worker scope 6 (shutdown before the pool closes); plan A26 (8).
    """
    caplog.set_level(logging.WARNING)
    RecordingDatabase.closed_at = None
    monkeypatch.setattr(
        keep.main, "Database", lambda *args, **kwargs: RecordingDatabase(site.db.dsn, 4)
    )
    pend(site.db, "keep-developers")
    mock_adapi.pause("members")
    times = {}
    app = create_app(settings(mock_adapi))
    with TestClient(app, base_url=ORIGIN) as client:
        saver = app.state.pick_saver
        original = saver.close

        def close():
            original()
            times["saver_joined"] = time.monotonic()

        saver.close = close
        assert poll(client, site, site.admin, "team", "keep-developers").status_code == 202
        assert mock_adapi.wait_for_pause("members")
        assert pending(site.db, "keep-developers")["lease_owner"]
        threading.Timer(1.0, mock_adapi.release_all).start()
    assert times["saver_joined"] <= RecordingDatabase.closed_at
    assert [(r.outcome, r.reason) for r in saver.results] == [("stopped", None)]
    row = pending(site.db, "keep-developers")
    assert row["lease_owner"] is None and row["lease_until"] is None
    assert row["last_failure"] is None
    assert not [r for r in caplog.records if r.name.startswith("keep.identity")]
    assert "PoolClosed" not in caplog.text and "already closed" not in caplog.text
    assert stored(site.db, "keep-developers") is None
    monkeypatch.undo()
    with api(site.db, mock_adapi) as client:
        assert poll(client, site, site.admin, "team", "keep-developers").status_code == 202
        assert wait_saved(client, site, "team", "keep-developers").status_code == 200


def test_a26_9_only_tenant_admins_and_super_admins_may_save_or_poll(site, mock_adapi):
    """Viewers and editors get 403 on POST and GET; other tenants 404; no session 401.

    Requirements: REV-73; tenant access 5 and 8; plan A12, A26 (9).
    """
    other = new_tenant(site.db)
    outsider = person(site.db, other, "admin")
    super_admin = person(site.db, None, None)
    with site.db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (super_admin.id,))
    with api(site.db, mock_adapi) as client:
        for who in (site.viewer, site.editor):
            for call in (resolve, poll):
                response = call(client, site, who, "user", "local-viewer")
                assert response.status_code == 403, response.text
        assert resolve(client, site, outsider, "user", "local-viewer").status_code == 404
        assert poll(client, site, outsider, "user", "local-viewer").status_code == 404
        anonymous = Person(None, {"Origin": ORIGIN})
        assert resolve(client, site, anonymous, "user", "local-viewer").status_code == 401
        assert poll(client, site, anonymous, "user", "local-viewer").status_code == 401
        stale_csrf = Person(None, {**site.admin.headers, "X-CSRF-Token": "wrong"})
        assert resolve(client, site, stale_csrf, "user", "local-viewer").status_code == 403
        assert stored(site.db, "local-viewer") is None and mock_adapi.count() == 0
        saved = resolve(client, site, super_admin, "user", "local-viewer")
        assert saved.status_code == 200, saved.text
        assert poll(client, site, super_admin, "user", "local-viewer").status_code == 200


def test_a26_10_the_refresh_saves_a_pending_row_nobody_polls(site, mock_adapi):
    """``directory --once`` (run_cycle) saves a pending pick and deletes its row.

    Requirements: REV-73; worker scope 4 (manual refresh saves pending rows); plan A26 (10).
    """
    pend(site.db, "keep-observers")
    pend(site.db, "keep-developers", failure="directory_unavailable")
    summary = run_cycle(site.db, adapi_config(mock_adapi), SUPERADMIN)
    assert summary.ok, summary.failed
    assert stored(site.db, "keep-observers")["checkpoint"]
    assert pending(site.db, "keep-observers") is None
    assert members(site.db, "keep-observers") == {"local-viewer": "Local Viewer"}
    # A failed pick is skipped, never retried by the refresh.
    assert stored(site.db, "keep-developers") is None
    assert pending(site.db, "keep-developers")["last_failure"] == "directory_unavailable"


# --- database role ------------------------------------------------------------------------


class APIRole:
    """The Product API's database identity (keep_api) on the test database."""

    def __init__(self, db):
        self.db = db

    @contextmanager
    def transaction(self, *args, **kwargs):
        with self.db.transaction(*args, **kwargs) as conn:
            conn.execute("SET LOCAL ROLE keep_api")
            yield conn

    def close(self):
        pass


def test_save_on_pick_runs_with_the_api_database_role(site, mock_adapi):
    """Inline and background saves need only keep_api's privileges (migration 0070).

    Requirements: REV-73; migration 0070 grants; tenant access 5.
    """
    pend(site.db, "keep-developers")
    with api(APIRole(site.db), mock_adapi) as client:
        inline = resolve(client, site, site.admin, "team", "keep-observers")
        assert inline.status_code == 200, inline.text
        assert poll(client, site, site.admin, "team", "keep-developers").status_code == 202
        assert wait_saved(client, site, "team", "keep-developers").status_code == 200
        assert outcomes(client.saver, "keep-developers") == [("published", None)]
    assert members(site.db, "keep-developers")["local-distribution"] == "Local Distribution"
