# ruff: noqa: F811 -- imported reusable fixtures
"""Directory refresh and single-root publication over the mock ADAPI on real PostgreSQL.

Each test gets a fresh database cloned from a migrated template (needs
KEEP_IDENTITY_TEST_ADMIN_URL) and a fresh mock ADAPI (scripts/local_adapi.py).

Requirements: REV-73; identity configuration 6-8, 12; worker scope 4; plan A13, A21,
A25, A27 and "Concurrent publications", "Refresh command".
"""

import base64
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from adapi_fixture import adapi_factory, mock_adapi  # noqa: F401
import local_adapi

from keep.db import Database
from keep.identity.adapi import (
    INLINE_VERIFY_DEADLINE,
    REQUEST_CALL_CAP,
    ADAPIClient,
    ADAPIConfig,
    principal_key,
    user_evidence,
    verify_root,
)
from keep.identity.directory import FAILURE_BUSY
from keep.identity.publication import (
    allocate_generation,
    lock_identity,
    publish,
    publish_with_retry,
)
from keep.identity.worker import PUBLICATION_INCOMPLETE, run_cycle, tracked_roots
from keep.migrate import migrate

pytestmark = pytest.mark.integration

SUPERADMIN = local_adapi.SUPERADMIN_GROUP
REPOSITORY = Path(__file__).resolve().parents[2]


def _dsn(admin, name):
    params = conninfo_to_dict(admin)
    params["dbname"] = name
    return make_conninfo(**params)


@pytest.fixture(scope="module")
def template():
    admin = os.environ.get("KEEP_IDENTITY_TEST_ADMIN_URL")
    if not admin:
        pytest.skip("Set KEEP_IDENTITY_TEST_ADMIN_URL to an isolated PostgreSQL cluster")
    name = "keep_adapi_template_" + uuid4().hex[:12]
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
    name = "keep_adapi_test_" + uuid4().hex[:12]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(source)
            )
        )
    db = Database(_dsn(admin, name), size=6)
    db.dsn = _dsn(admin, name)
    yield db
    db.close()
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def config(mock):
    return ADAPIConfig(mock.base_url, mock.client_id)


def superadmin():
    return principal_key(SUPERADMIN)


def principal(db, name):
    with db.transaction() as conn:
        return conn.execute(
            "SELECT * FROM principals WHERE directory_source='adapi' AND object_id=%s", (name,)
        ).fetchone()


def gate(db):
    with db.transaction() as conn:
        return conn.execute(
            "SELECT ready,reason FROM capability_gates WHERE capability='human_auth'"
        ).fetchone()


def checkpoint(db, name):
    with db.transaction() as conn:
        row = conn.execute(
            """SELECT c.generation FROM directory_root_checkpoints c JOIN principals p ON p.id=c.root_id
            WHERE p.directory_source='adapi' AND p.object_id=%s""",
            (name,),
        ).fetchone()
    return row["generation"] if row else None


def new_tenant(db):
    tenant = uuid4()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
            (tenant, str(tenant), str(tenant)),
        )
    return tenant


def grant(db, tenant, principal_id, role="admin"):
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,%s)",
            (tenant, principal_id, role),
        )


def open_sessions(db, principal_ids):
    """One live session per principal, on the post-REV-73 session columns only."""
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO auth_sessions(handle_hash,principal_id,csrf_hash,csrf_cipher,auth_time,absolute_at,deployment_epoch)
            SELECT sha256(convert_to(gen_random_uuid()::text,'UTF8')),p,
              sha256(convert_to(gen_random_uuid()::text,'UTF8')),'\\x00'::bytea,
              clock_timestamp(),clock_timestamp()+interval '1 day','test'
            FROM unnest(%s::uuid[]) AS p""",
            (list(principal_ids),),
        )


def live_sessions(db, principal_id):
    with db.transaction() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM auth_sessions WHERE principal_id=%s AND revoked_at IS NULL",
            (principal_id,),
        ).fetchone()["n"]


def stub_principals(db, names, kind):
    """Unverified principal rows (as left before their first publication)."""
    with db.transaction() as conn:
        return {
            row["object_id"]: row["id"]
            for row in conn.execute(
                """INSERT INTO principals(directory_source,object_id,kind,display_name,eligible)
                SELECT 'adapi',n,%s,n,true FROM unnest(%s::text[]) AS n RETURNING id,object_id""",
                (kind, list(names)),
            ).fetchall()
        }


def save(db, mock, name, kind, **options):
    """Save on pick as the API composes it: a single-root publication with one retry."""
    return publish_with_retry(
        db,
        lambda generation: verify_root(
            config(mock),
            name,
            kind,
            generation,
            deadline_seconds=INLINE_VERIFY_DEADLINE,
            call_cap=REQUEST_CALL_CAP,
        ),
        superadmin_group=superadmin(),
        **options,
    )


def test_bootstrap_opens_human_auth_only_from_the_super_admin_group_evidence(
    database, mock_adapi
):
    """Requirements: REV-73; identity configuration 12; worker scope 4 (split coverage).

    Under split coverage only the evidence whose roots include the super-admin group
    carries the bootstrap effect: user-root evidence never opens human_auth, even with
    the flag; the group's evidence opens it only with the flag and an eligible member;
    no evidence lifts a restore-integrity gate.
    """
    assert gate(database) == {"ready": False, "reason": "directory_bootstrap_required"}
    user_only = save(database, mock_adapi, "local-admin", "user", bootstrap_qualified=True)
    assert user_only.saved and not user_only.publication.bootstrap_ready
    assert not gate(database)["ready"]
    unflagged = save(database, mock_adapi, SUPERADMIN, "team")
    assert unflagged.saved and not gate(database)["ready"]
    assert principal(database, "local-admin")["super_admin"]

    open_sessions(database, [principal(database, "local-admin")["id"]])
    summary = run_cycle(database, config(mock_adapi), SUPERADMIN, bootstrap_qualified=True)
    assert summary.ok and summary.bootstrap_ready
    by_kind = {unit.kind: unit for unit in summary.units}
    assert set(by_kind) == {"user", "team"}
    assert not by_kind["user"].publication.bootstrap_ready
    assert by_kind["team"].roots == (SUPERADMIN,) and by_kind["team"].publication.bootstrap_ready
    assert gate(database) == {"ready": True, "reason": "directory_bootstrap_verified"}

    with database.transaction() as conn:
        conn.execute(
            "UPDATE capability_gates SET ready=false,reason='restore_reconciliation_required' "
            "WHERE capability='human_auth'"
        )
    run_cycle(database, config(mock_adapi), SUPERADMIN, bootstrap_qualified=True)
    assert gate(database) == {"ready": False, "reason": "restore_reconciliation_required"}

    with database.transaction() as conn:
        conn.execute(
            "UPDATE capability_gates SET ready=false,reason='directory_bootstrap_required' "
            "WHERE capability='human_auth'"
        )
    mock_adapi.directory.remove_member(SUPERADMIN, "local-admin")
    emptied = run_cycle(database, config(mock_adapi), SUPERADMIN, bootstrap_qualified=True)
    assert emptied.ok and not emptied.bootstrap_ready and not gate(database)["ready"]
    assert not principal(database, "local-admin")["super_admin"]


def test_a13_refresh_disables_ends_sessions_audits_and_keeps_groups(database, mock_adapi):
    """Requirements: REV-73; identity configuration 7-8; worker scope 4; plan A13.

    Disabling a user in ADAPI makes the refresh mark them ineligible, end their
    sessions and record the access change; a granted group root stays a group (not a
    tombstone); a group whose members a /users batch omits still publishes, the
    omitted members keeping the members route's userAccountControl and their name.
    """
    assert run_cycle(database, config(mock_adapi), SUPERADMIN, bootstrap_qualified=True).ok
    developers = save(database, mock_adapi, "keep-developers", "team")
    assert developers.saved
    group = principal(database, "keep-developers")
    assert group["kind"] == "team" and group["display_name"] == "Developers"
    tenant = new_tenant(database)
    grant(database, tenant, group["id"], "admin")
    editor, hebrew = principal(database, "local-editor"), principal(database, "local-hebrew")
    assert editor["eligible"] and hebrew["display_name"] == "משתמש Local Hebrew"
    assert principal(database, "ws-0001$") is None
    open_sessions(database, [editor["id"], editor["id"], hebrew["id"]])
    with database.transaction() as conn:
        roots = tracked_roots(conn, "adapi", superadmin())
    assert roots[principal_key("keep-developers")] == "team"
    assert roots[principal_key("local-editor")] == "user"

    mock_adapi.disable_user("local-editor")
    before = checkpoint(database, "keep-developers")
    summary = run_cycle(database, config(mock_adapi), SUPERADMIN)
    assert summary.ok, summary.failed
    editor = principal(database, "local-editor")
    assert not editor["eligible"] and not editor["deleted"]
    assert live_sessions(database, editor["id"]) == 0
    assert live_sessions(database, hebrew["id"]) == 1
    with database.transaction() as conn:
        audit = conn.execute(
            """SELECT evidence FROM audit_events WHERE kind='directory_access_changed'
            AND tenant_id=%s AND evidence->>'principal_id'=%s""",
            (tenant, str(editor["id"])),
        ).fetchall()
        members = {
            row["user_id"]
            for row in conn.execute(
                "SELECT user_id FROM directory_memberships WHERE team_id=%s", (group["id"],)
            ).fetchall()
        }
    assert [row["evidence"]["after"] for row in audit][-1] is None
    assert editor["id"] not in members and hebrew["id"] in members
    group = principal(database, "keep-developers")
    assert group["kind"] == "team" and not group["deleted"] and group["eligible"]
    assert checkpoint(database, "keep-developers") > before

    # A /users batch that omits members (the search returns one row per call).
    mock_adapi.set_search_cap(1)
    before = checkpoint(database, "keep-developers")
    omitted = run_cycle(database, config(mock_adapi), SUPERADMIN)
    mock_adapi.set_search_cap(local_adapi.SEARCH_CAP)
    assert omitted.ok, omitted.failed
    assert checkpoint(database, "keep-developers") > before
    distribution = principal(database, "local-distribution")
    assert distribution["display_name"] == "local-distribution" and distribution["eligible"]
    disabled = principal(database, "local-disabled")
    assert not disabled["eligible"] and not disabled["deleted"]
    with database.transaction() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM directory_memberships WHERE team_id=%s AND user_id=%s",
                (group["id"], distribution["id"]),
            ).fetchone()
            is not None
        )


def test_a21_refresh_retries_after_a_concurrent_single_root_publication(
    database, adapi_factory
):
    """Requirements: REV-73; identity configuration 7; plan A21, "Concurrent publications".

    A single-root publication (save on pick, as keep_api) lands while the refresh is
    paused reading the same group: the refresh's evidence is fenced, retries once and
    publishes with a newer checkpoint generation. A single-root publication fenced on
    both attempts reports directory_busy. A member's newer row (a sign-in during a
    group read) is skipped while the group's edges still publish.
    """
    mock = adapi_factory()
    second = adapi_factory(mock.directory)  # same data, no pause gates
    assert run_cycle(database, config(mock), SUPERADMIN, bootstrap_qualified=True).ok
    assert save(database, mock, "keep-developers", "team").saved
    grant(database, new_tenant(database), principal(database, "keep-developers")["id"])

    mock.pause("members", name="keep-developers")
    result = {}
    refresh = threading.Thread(
        target=lambda: result.setdefault("summary", run_cycle(database, config(mock), SUPERADMIN))
    )
    refresh.start()
    try:
        assert mock.wait_for_pause("members", name="keep-developers", timeout=30)
        mock.rename("local-editor", "Editor Renamed")
        with database.transaction() as conn:
            conn.execute("SET LOCAL ROLE keep_api")
            concurrent_generation = allocate_generation(conn)
        evidence = verify_root(
            config(second),
            "keep-developers",
            "team",
            concurrent_generation,
            deadline_seconds=INLINE_VERIFY_DEADLINE,
            call_cap=REQUEST_CALL_CAP,
        )
        with database.transaction() as conn:
            conn.execute("SET LOCAL ROLE keep_api")
            lock_identity(conn)
            concurrent = publish(conn, evidence, superadmin_group=superadmin())
        assert concurrent.accepted and not concurrent.fenced
        assert checkpoint(database, "keep-developers") == concurrent_generation
    finally:
        mock.release("members", name="keep-developers")
        refresh.join(60)
    unit = next(u for u in result["summary"].units if u.roots == ("keep-developers",))
    assert unit.published and unit.outcome.attempts == 2
    assert unit.evidence.generation > concurrent_generation
    assert checkpoint(database, "keep-developers") == unit.evidence.generation
    assert principal(database, "local-editor")["display_name"] == "Editor Renamed"
    with database.transaction() as conn:
        fenced = conn.execute(
            """SELECT count(*) AS n FROM directory_verifications
            WHERE coverage->'roots' ? 'keep-developers' AND (coverage->>'fenced')::boolean"""
        ).fetchone()["n"]
    assert fenced == 1

    def racing_read(generation):
        own = verify_root(
            config(second), "keep-developers", "team", generation,
            deadline_seconds=INLINE_VERIFY_DEADLINE, call_cap=REQUEST_CALL_CAP,
        )
        with database.transaction() as conn:
            newer = allocate_generation(conn)
        other = verify_root(
            config(second), "keep-developers", "team", newer,
            deadline_seconds=INLINE_VERIFY_DEADLINE, call_cap=REQUEST_CALL_CAP,
        )
        with database.transaction() as conn:
            assert publish(conn, other, superadmin_group=superadmin()).accepted
        return own

    busy = publish_with_retry(
        database,
        racing_read,
        superadmin_group=superadmin(),
        authorize=lambda conn: conn.execute("SET LOCAL ROLE keep_api"),
    )
    assert not busy.saved and busy.reason == FAILURE_BUSY and busy.attempts == 2
    assert busy.publication.fenced and not busy.publication.accepted

    # A member signs in (newer single-person evidence) while the group is being read.
    with database.transaction() as conn:
        group_generation = allocate_generation(conn)
    group_evidence = verify_root(
        config(second), "keep-developers", "team", group_generation,
        deadline_seconds=INLINE_VERIFY_DEADLINE, call_cap=REQUEST_CALL_CAP,
    )
    mock.rename("local-editor", "Signed In Editor")
    with database.transaction() as conn:
        person_generation = allocate_generation(conn)
    started = datetime.now(UTC)
    with ADAPIClient(config(second), call_cap=REQUEST_CALL_CAP) as client:
        rows = client.lookup_user_rows("local-editor")
    assert len(rows) == 1
    with database.transaction() as conn:
        lock_identity(conn)
        signed_in = publish(
            conn, user_evidence("adapi", rows[0], person_generation, started),
            superadmin_group=superadmin(),
        )
    assert signed_in.accepted
    with database.transaction() as conn:
        lock_identity(conn)
        late_group = publish(conn, group_evidence, superadmin_group=superadmin())
    assert late_group.accepted and not late_group.fenced
    editor = principal(database, "local-editor")
    assert editor["display_name"] == "Signed In Editor"
    assert editor["evidence_generation"] == person_generation
    assert checkpoint(database, "keep-developers") == group_generation
    with database.transaction() as conn:
        assert conn.execute(
            """SELECT 1 FROM directory_memberships m JOIN principals t ON t.id=m.team_id
            WHERE t.object_id='keep-developers' AND m.user_id=%s""",
            (editor["id"],),
        ).fetchone()


def _scale_database(database, users, teams):
    ids = stub_principals(database, users, "user")
    open_sessions(database, ids.values())
    team_ids = stub_principals(database, teams, "team")
    tenant = new_tenant(database)
    for team_id in team_ids.values():
        grant(database, tenant, team_id, "view")
    return ids, team_ids


def test_a25_company_size_refresh_and_one_bad_team(database, adapi_factory):
    """Requirements: REV-73; REV-75; worker scope 4; identity configuration 9;
    observability 2 and 4; plan A25, A3 and A8.

    With 50 ms per ADAPI call, 3,000 users with open sessions and 20 granted groups of
    200 publish one evidence for the users and one per group, each within 60 s, using at
    most 60 + 20 x 6 calls. One group failing (500) or over its cap fails alone: the
    other groups and the users still publish, and the command exits non-zero naming it.
    Both entry points write the same REV-75 records (service api, role
    directory-refresh) and print neither the DSN, the ADAPI address nor the ClientId.
    """
    data = local_adapi.build_directory(users=3000, groups=20, members_per_group=200)
    names = [row["sAMAccountName"] for row in data["users"]]
    data["groups"].append({"sAMAccountName": "team-all", "members": names})
    mock = adapi_factory(data)
    teams = [f"team{i:02d}" for i in range(1, 21)]
    _scale_database(database, names, teams)
    mock.set_latency("*", 0.05)
    mock.reset_log()
    summary = run_cycle(database, config(mock), "team01", bootstrap_qualified=True)
    assert summary.ok, summary.failed
    assert mock.count() <= 60 + 20 * 6, mock.log()["counts"]
    assert [u.kind for u in summary.units] == ["user"] + ["team"] * 20
    for unit in summary.units:
        assert unit.outcome.elapsed_seconds < 60
        assert not unit.evidence.target_missed
    assert len(summary.units[0].roots) == 3000
    assert summary.bootstrap_ready and gate(database)["ready"]
    with database.transaction() as conn:
        assert conn.execute(
            """SELECT count(*) AS n FROM principals WHERE directory_source='adapi' AND kind='user'
            AND eligible AND verified_at IS NOT NULL AND display_name LIKE 'User %%'"""
        ).fetchone()["n"] == 3000
        assert conn.execute("SELECT count(*) AS n FROM directory_memberships").fetchone()["n"] == 4000
        assert conn.execute(
            "SELECT count(*) AS n FROM auth_sessions WHERE revoked_at IS NULL"
        ).fetchone()["n"] == 3000

    # One group answers 500: it fails alone, named in the summary and by the command.
    mock.fail("members", 500, name="team05")
    before = {team: checkpoint(database, team) for team in teams}
    failing = run_cycle(database, config(mock), "team01")
    assert failing.failed == {"team05": "directory_unavailable"}
    assert checkpoint(database, "team05") == before["team05"]
    assert all(checkpoint(database, t) > before[t] for t in teams if t != "team05")
    assert failing.units[0].published
    # Both entry points run the same refresh: keep-delivery's worker and the module
    # carried by every image with keep-domain (the Product API image included).
    printed = []
    for entry in (["keep.worker", "directory", "--once"], ["keep.identity.worker", "--once"]):
        command = subprocess.run(
            [sys.executable, "-m", *entry],
            cwd=REPOSITORY,
            env={
                **os.environ,
                "KEEP_DATABASE_URL": database.dsn,
                "KEEP_WRAPPING_KEY": base64.b64encode(b"k" * 32).decode(),
                "KEEP_DEPLOYMENT_EPOCH": "test",
                "KEEP_ADAPI_URL": mock.base_url,
                "KEEP_ADAPI_CLIENT_ID": mock.client_id,
                "KEEP_SUPERADMIN_GROUP": "TEAM01",
                "KEEP_LOG_FORMAT": "json",
                "KEEP_LOG_LEVEL": "INFO",
                "KEEP_LOG_SHIP_URL": "",
            },
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert command.returncode == 1
        assert "directory_root_failed root=team05 reason=directory_unavailable" in command.stdout
        assert command.stdout.count("directory_root_failed") == 1
        output = command.stdout + command.stderr
        assert mock.client_id not in output
        assert database.dsn not in output
        assert mock.base_url not in output
        records = [json.loads(line) for line in command.stderr.splitlines()
                   if line.startswith("{")]
        events = {}
        for record in records:
            events.setdefault(record.get("event", {}).get("action"), []).append(record)
        (failed,) = events["directory_root_failed"]
        assert failed["keep"]["root"] == "team05"
        assert failed["keep"]["reason"] == "directory_unavailable"
        (completed,) = events["directory_refresh_completed"]
        assert (completed["service"]["name"], completed["keep"]["role"]) == (
            "api", "directory-refresh")
        assert (completed["keep"]["units"], completed["keep"]["published"],
                completed["keep"]["failed_roots"]) == (21, 20, 1)
        printed.append([line for line in command.stdout.splitlines() if line.startswith("directory_")])
    assert printed[0] == printed[1]
    assert printed[1][-1].startswith("directory_refresh units=21 published=20 failed_roots=1 ")
    mock.clear_failures()

    # A group over its cap (here 2,999 objects: all 3,000 users) fails alone, before
    # any name batch; the real 10,000 cap is shown by test_real_cap_fails_only_that_team.
    stub = stub_principals(database, ["team-all"], "team")
    grant(database, new_tenant(database), stub["team-all"], "view")
    mock.reset_log()
    capped = run_cycle(database, config(mock), "team01", max_objects=2999)
    assert capped.failed == {"team-all": "directory_group_too_large"}
    assert [u.kind for u in capped.units] == ["user", "user"] + ["team"] * 21
    assert all(u.published for u in capped.units if u.roots != ("team-all",))
    assert principal(database, "team-all")["verified_at"] is None
    assert checkpoint(database, "team-all") is None


def test_real_cap_fails_only_that_team(database, adapi_factory):
    """Requirements: REV-73; worker scope 4; plan A25 (a team over 10,000 objects).

    A group reaching 10,001 users exceeds the 10,000-object evidence cap: it fails
    with directory_group_too_large after one members call, and the super-admin group
    still publishes in the same refresh.
    """
    data = local_adapi.merge_directories(
        local_adapi.sample_directory(),
        local_adapi.large_group("big-team", members=10001, distribution_members=100),
    )
    mock = adapi_factory(data)
    stub = stub_principals(database, ["big-team"], "team")
    grant(database, new_tenant(database), stub["big-team"], "view")
    mock.reset_log()
    summary = run_cycle(database, config(mock), SUPERADMIN, bootstrap_qualified=True)
    assert summary.failed == {"big-team": "directory_group_too_large"}
    assert summary.bootstrap_ready and gate(database)["ready"]
    log = [r for r in mock.log()["requests"] if "big-team" in str(r["query"])]
    assert [r["route"] for r in log] == ["groups", "members"]


def test_a27_large_publication_holds_the_identity_guard_briefly(database, adapi_factory):
    """Requirements: REV-73; identity configuration 7; plan A27, "Short guard hold".

    Publishing a group at the 10,000-object cap (the group and 9,999 members) holds the
    identity guard for under one second, measured from taking the guard to commit, both
    on first save and on a republication that revokes half the members; a concurrent
    loop of grant mutations (which take the same guard) sees no lock_timeout.
    """
    large = local_adapi.large_group("huge-team", members=9999, distribution_members=100)
    mock = adapi_factory(local_adapi.merge_directories(local_adapi.sample_directory(), large))
    assert run_cycle(database, config(mock), SUPERADMIN, bootstrap_qualified=True).ok
    tenants = [new_tenant(database) for _ in range(3)]
    admin = principal(database, "local-admin")
    stop, errors, mutations = threading.Event(), [], [0]

    def mutate():
        role = ["view", "edit"]
        while not stop.is_set():
            try:
                with database.transaction() as conn:
                    conn.execute(
                        "SELECT scope FROM authorization_guards WHERE scope='identity' FOR UPDATE"
                    )
                    conn.execute(
                        """INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,%s)
                        ON CONFLICT(tenant_id,principal_id) DO UPDATE SET role=EXCLUDED.role""",
                        (tenants[mutations[0] % 2], admin["id"], role[mutations[0] % 2]),
                    )
                mutations[0] += 1
            except psycopg.Error as error:  # lock_timeout is LockNotAvailable
                errors.append(type(error).__name__)
            time.sleep(0.01)

    def timed_publication():
        with database.transaction() as conn:
            generation = allocate_generation(conn)
        evidence = verify_root(
            config(mock), "huge-team", "team", generation,
            deadline_seconds=60, call_cap=5,
        )
        assert evidence.complete, evidence.failures
        assert len(evidence.objects) == 10000
        with database.transaction() as conn:
            lock_identity(conn)
            held = time.monotonic()
            result = publish(conn, evidence, superadmin_group=superadmin())
        held = time.monotonic() - held
        assert result.accepted
        return held, result

    loop = threading.Thread(target=mutate)
    loop.start()
    try:
        first, _ = timed_publication()
        team = principal(database, "huge-team")
        grant(database, tenants[2], team["id"], "edit")
        members = [row["sAMAccountName"] for row in large["users"]]
        with database.transaction() as conn:
            ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM principals WHERE directory_source='adapi' AND object_id=ANY(%s)",
                    ([m.lower() for m in members[::2]],),
                ).fetchall()
            ]
        open_sessions(database, ids)
        for row in large["users"][::2]:
            row["disabled"] = True
        mock.load(local_adapi.merge_directories(local_adapi.sample_directory(), large))
        second, result = timed_publication()
    finally:
        stop.set()
        loop.join(30)
    print(f"A27 guard hold: first save {first:.3f}s, revoking republication {second:.3f}s")
    assert first < 1.0 and second < 1.0
    assert len(result.revoked_principals) == 5000 and len(result.changed_access) == 5000
    assert errors == [] and mutations[0] > 0
    with database.transaction() as conn:
        assert conn.execute(
            "SELECT count(*) AS n FROM auth_sessions WHERE revoked_at IS NULL AND principal_id=ANY(%s)",
            (ids,),
        ).fetchone()["n"] == 0


def test_pending_saves_are_refreshed_and_removed_when_published(database, mock_adapi):
    """Requirements: REV-73; worker scope 4 (pending saves in the refresh); plan A26 (10).

    The refresh reads pending saves without a failure with their kind, and deletes the
    rows its publications covered; failed pending rows are skipped and kept.
    """
    with database.transaction() as conn:
        conn.execute(
            """INSERT INTO directory_verification_requests(source,object_id,kind,last_failure)
            VALUES('adapi','keep-observers','team',NULL),('adapi','local-hebrew','user',NULL),
                  ('adapi','keep-developers','team','directory_group_too_large')"""
        )
        roots = tracked_roots(conn, "adapi", superadmin())
    assert roots == {
        principal_key("keep-observers"): "team",
        principal_key("local-hebrew"): "user",
        superadmin(): "team",
    }
    summary = run_cycle(database, config(mock_adapi), SUPERADMIN)
    assert summary.ok
    assert principal(database, "keep-observers")["kind"] == "team"
    assert checkpoint(database, "keep-observers") is not None
    assert principal(database, "keep-developers") is None
    with database.transaction() as conn:
        left = conn.execute(
            "SELECT object_id FROM directory_verification_requests ORDER BY object_id"
        ).fetchall()
    assert [row["object_id"] for row in left] == ["keep-developers"]
    assert PUBLICATION_INCOMPLETE == "directory_publication_incomplete"
