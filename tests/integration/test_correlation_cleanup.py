"""Real isolated PG retention and API checks; evidence service is controlled."""

# ruff: noqa: F811 -- imported shared pytest fixtures

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
import psycopg

from appchi.correlation.cleanup import cleanup_rule, correlation_cleanup_turn, warm_history_names
from appchi.shared.errors import AppError
from appchi.shared.transactions import coordinator
from test_correlation_configuration import create, definition, edit, rules  # noqa: F401
from test_correlation_deletion import protected, remove  # noqa: F401
from test_product import browser  # noqa: F401
from test_search_pipeline import real_db as isolated_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def real_db(isolated_db):
    root = Path(__file__).resolve().parents[2]
    if not (root / "migrations/0032_correlation_retention.sql").exists():
        with isolated_db.transaction() as conn:
            conn.execute(
                (root / ".local/0032_correlation_retention.sql.draft").read_text(), prepare=False
            )
    return isolated_db


def settled(db, tenant):
    with db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
        conn.execute(
            """UPDATE correlation_consumer_state c SET health='healthy',pending=false,
            change_order=w.change_order FROM tenant_work_state w WHERE c.tenant_id=w.tenant_id AND c.tenant_id=%s""",
            (tenant,),
        )


def age(db, rule, *, deleted=False):
    with db.transaction() as conn:
        conn.execute(
            """UPDATE correlation_rule_revisions SET recorded_at=clock_timestamp()-interval '100 days',
            superseded_at=CASE WHEN superseded_at IS NOT NULL THEN clock_timestamp()-interval '99 days' END
            WHERE rule_id=%s""",
            (rule,),
        )
        if deleted:
            conn.execute(
                "UPDATE correlation_rules SET deleted_at=clock_timestamp()-interval '99 days' WHERE id=%s",
                (rule,),
            )
            conn.execute(
                "UPDATE correlation_rule_identities SET deleted_at=r.deleted_at FROM correlation_rules r WHERE correlation_rule_identities.id=r.id AND r.id=%s",
                (rule,),
            )


def hold(db, tenant, rule, revision):
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO correlation_revision_holds(tenant_id,rule_id,revision,consumer_kind,reference) VALUES(%s,%s,%s,'recovery','test-proof')",
            (tenant, rule, revision),
        )


def test_history_hidden_before_cleanup_and_durable_supersession(protected, real_db):
    fixture, _ = protected
    client, _, tenant, base = fixture
    row, command = create(fixture)
    updated = edit(fixture, row, {**command, "name": "second"}).json()["data"]
    third = edit(fixture, updated, {**command, "name": "third"})
    assert third.status_code == 200, third.text
    settled(real_db, tenant)
    hold(real_db, tenant, row["id"], 1)
    age(real_db, row["id"])
    assert client.get(base + "/" + row["id"] + "/history/2").status_code == 404
    assert cleanup_rule(real_db, tenant, row["id"])
    with real_db.transaction() as conn:
        kept = conn.execute(
            "SELECT revision,action,actor_label,superseded_at FROM correlation_rule_revisions WHERE rule_id=%s ORDER BY revision",
            (row["id"],),
        ).fetchall()
        assert [r["revision"] for r in kept] == [1, 3]
        assert (
            kept[0]["superseded_at"] is not None
            and kept[0]["action"] is None
            and kept[0]["actor_label"] is None
        )
        conn.execute(
            "UPDATE correlation_revision_holds SET released_at=clock_timestamp() WHERE rule_id=%s",
            (row["id"],),
        )
    # Removing revision 2 cannot accidentally extend revision 1 until revision 3.
    assert client.get(base + "/" + row["id"] + "/history/1").status_code == 404
    assert cleanup_rule(real_db, tenant, row["id"])
    assert client.get(base + "/" + row["id"]).status_code == 200
    assert client.get(base + "/" + row["id"] + "/history/3").json()["data"]["activity"] is None


def test_recent_event_holds_its_before_snapshot(protected, real_db):
    fixture, _ = protected
    client, _, tenant, base = fixture
    row, command = create(fixture)
    assert edit(fixture, row, {**command, "name": "new"}).status_code == 200
    settled(real_db, tenant)
    with real_db.transaction() as conn:
        first = conn.execute(
            "UPDATE correlation_rule_revisions SET recorded_at=clock_timestamp()-interval '100 days' WHERE rule_id=%s AND revision=1 RETURNING superseded_at",
            (row["id"],),
        ).fetchone()
        assert first["superseded_at"] is not None
    assert cleanup_rule(real_db, tenant, row["id"])
    assert client.get(base + "/" + row["id"] + "/history/1").status_code == 200


@pytest.mark.parametrize("blocked", ["pending", "missing", "journal", "ahead"])
def test_unknown_or_pending_consumer_keeps_content(protected, real_db, blocked):
    fixture, _ = protected
    client, _, tenant, base = fixture
    row, _ = create(fixture)
    assert remove(fixture, row).status_code == 200
    settled(real_db, tenant)
    age(real_db, row["id"], deleted=True)
    with real_db.transaction() as conn:
        if blocked == "pending":
            conn.execute(
                "UPDATE correlation_consumer_state SET pending=true WHERE tenant_id=%s", (tenant,)
            )
        elif blocked == "missing":
            conn.execute("DELETE FROM correlation_consumer_state WHERE tenant_id=%s", (tenant,))
        elif blocked == "ahead":
            conn.execute(
                "UPDATE correlation_consumer_state SET change_order=43 WHERE tenant_id=%s",
                (tenant,),
            )
        else:
            conn.execute(
                "INSERT INTO correlation_journal(tenant_id,change_order,alert_id,after_snapshot,effective_at,cause) VALUES(%s,43,%s,'{}',clock_timestamp(),'source')",
                (tenant, uuid4()),
            )
    assert not cleanup_rule(real_db, tenant, row["id"])
    assert client.get(base + "/" + row["id"] + "/history/1").status_code == 200
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT action FROM correlation_rule_revisions WHERE rule_id=%s AND revision=1",
                (row["id"],),
            ).fetchone()["action"]
            is not None
        )


def test_final_proof_and_recovery_gate_precede_erasure(protected, real_db):
    fixture, _ = protected
    _, _, tenant, _ = fixture
    row, _ = create(fixture)
    assert remove(fixture, row).status_code == 200
    settled(real_db, tenant)
    age(real_db, row["id"], deleted=True)
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE correlation_delete_jobs SET final_ack=NULL WHERE rule_id=%s", (row["id"],)
        )
    assert not cleanup_rule(real_db, tenant, row["id"])
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) n FROM correlation_rule_revisions WHERE rule_id=%s", (row["id"],)
            ).fetchone()["n"]
            == 2
        )
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='recovery'")
    with pytest.raises(AppError, match="recovery_not_ready"):
        cleanup_rule(real_db, tenant, row["id"])


def test_deleted_current_erased_while_older_held_history_stays_readable(protected, real_db):
    fixture, _ = protected
    client, _, tenant, base = fixture
    row, command = create(fixture, definition("Straße"))
    changed = edit(fixture, row, {**command, "name": "Expired final name"}).json()["data"]
    assert remove(fixture, changed).status_code == 200
    settled(real_db, tenant)
    hold(real_db, tenant, row["id"], 1)
    age(real_db, row["id"], deleted=True)
    assert not cleanup_rule(real_db, tenant, row["id"])
    with real_db.transaction() as conn:
        assert not conn.execute(
            "SELECT 1 FROM correlation_rules WHERE id=%s", (row["id"],)
        ).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM correlation_conditions WHERE rule_id=%s", (row["id"],)
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM correlation_rule_identities WHERE id=%s", (row["id"],)
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM correlation_condition_identities WHERE rule_id=%s", (row["id"],)
        ).fetchone()
        conn.execute(
            "UPDATE correlation_rule_revisions SET name_key=NULL WHERE rule_id=%s", (row["id"],)
        )
    assert client.get(base + "/" + row["id"] + "/history/1").status_code == 200
    assert client.get(base + "/" + row["id"] + "/history/2").status_code == 404
    assert client.get(base + "/" + row["id"] + "/history").status_code == 200
    listing = client.get(base, params={"state": "deleted", "name": "STRASSE", "sort": "name"})
    assert listing.status_code == 200, listing.text
    assert [
        (x["name"], x["history_revision"], x["final_revision"]) for x in listing.json()["data"]
    ] == [("Straße", "1", "3")]
    assert listing.json()["data"][0]["deleted_at"] and not listing.json()["data"][0]["enabled"]
    assert client.get(base, params={"state": "deleted", "name": "Expired"}).json()["data"] == []
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT name_key FROM correlation_rule_revisions WHERE rule_id=%s", (row["id"],)
            ).fetchone()["name_key"]
            == "strasse"
        )
        conn.execute(
            "UPDATE correlation_revision_holds SET released_at=clock_timestamp() WHERE rule_id=%s",
            (row["id"],),
        )
    assert client.get(base, params={"state": "deleted"}).json()["data"] == []
    assert cleanup_rule(real_db, tenant, row["id"])
    assert client.get(base + "/" + row["id"] + "/history/1").status_code == 404


class Worker:
    def __init__(self, db):
        self.db = db

    @contextmanager
    def transaction(self, *args, **kwargs):
        with self.db.transaction(*args, **kwargs) as conn:
            conn.execute("SET LOCAL ROLE appchi_worker")
            yield conn


def test_name_warmup_is_bounded_and_unknown_profile_is_explicitly_unavailable(protected, real_db):
    fixture, _ = protected
    client, _, tenant, base = fixture
    row, _ = create(fixture, definition("Straße"))
    assert remove(fixture, row).status_code == 200
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE correlation_rule_revisions SET name_key=NULL WHERE rule_id=%s", (row["id"],)
        )
    with Worker(real_db).transaction(tenant) as conn:
        assert warm_history_names(conn, tenant, limit=1) == 1
        assert (
            conn.execute(
                "SELECT count(*) n FROM correlation_rule_revisions WHERE rule_id=%s AND name_key IS NULL",
                (row["id"],),
            ).fetchone()["n"]
            == 1
        )
        conn.execute(
            "UPDATE correlation_rule_revisions SET snapshot=jsonb_set(snapshot,'{name_profile}','\"unsupported\"') WHERE rule_id=%s AND revision=2",
            (row["id"],),
        )
    unavailable = client.get(base, params={"state": "deleted", "sort": "name"})
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "correlation_history_name_index_pending"
    assert client.get(base, params={"state": "deleted"}).status_code == 200
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE correlation_rule_revisions SET snapshot=jsonb_set(snapshot,'{name_profile}','\"appchi-rule-name-unicode15.1-v1\"') WHERE rule_id=%s AND revision=2",
            (row["id"],),
        )
    assert (
        client.get(base, params={"state": "deleted", "name": "STRASSE", "sort": "name"}).status_code
        == 200
    )


def test_hold_committed_while_cleanup_waits_on_coordinator_is_preserved(protected, real_db):
    fixture, _ = protected
    _, _, tenant, _ = fixture
    row, _ = create(fixture)
    assert remove(fixture, row).status_code == 200
    settled(real_db, tenant)
    hold(real_db, tenant, row["id"], 1)
    age(real_db, row["id"], deleted=True)
    started = Event()

    class WaitingWorker(Worker):
        @contextmanager
        def transaction(self, *args, **kwargs):
            with super().transaction(*args, **kwargs) as conn:
                started.set()
                yield conn

    with ThreadPoolExecutor(max_workers=1) as pool:
        with real_db.transaction(tenant) as conn:
            coordinator(conn, tenant)
            future = pool.submit(cleanup_rule, WaitingWorker(real_db), tenant, row["id"])
            assert started.wait(1)
            conn.execute(
                "INSERT INTO correlation_revision_holds(tenant_id,rule_id,revision,consumer_kind,reference) VALUES(%s,%s,1,'recovery','concurrent-proof')",
                (tenant, row["id"]),
            )
            conn.execute(
                "UPDATE correlation_revision_holds SET released_at=clock_timestamp() WHERE rule_id=%s AND reference='test-proof'",
                (row["id"],),
            )
        assert not future.result(timeout=5)
    with real_db.transaction() as conn:
        assert conn.execute(
            "SELECT 1 FROM correlation_rule_revisions WHERE rule_id=%s AND revision=1", (row["id"],)
        ).fetchone()


def test_new_hold_cannot_revive_expired_physical_revision(protected, real_db):
    fixture, _ = protected
    client, _, tenant, base = fixture
    row, _ = create(fixture)
    assert remove(fixture, row).status_code == 200
    settled(real_db, tenant)
    hold(real_db, tenant, row["id"], 1)
    age(real_db, row["id"], deleted=True)
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE correlation_revision_holds SET released_at=clock_timestamp() WHERE rule_id=%s",
            (row["id"],),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        with Worker(real_db).transaction(tenant) as conn:
            conn.execute(
                "UPDATE correlation_revision_holds SET released_at=NULL WHERE rule_id=%s",
                (row["id"],),
            )
    with pytest.raises(psycopg.errors.CheckViolation):
        with Worker(real_db).transaction(tenant) as conn:
            conn.execute(
                "INSERT INTO correlation_revision_holds(tenant_id,rule_id,revision,consumer_kind,reference) VALUES(%s,%s,2,'recovery','too-late')",
                (tenant, row["id"]),
            )
    assert client.get(base + "/" + row["id"] + "/history/1").status_code == 404
    assert client.get(base + "/" + row["id"] + "/history/2").status_code == 404
    assert cleanup_rule(real_db, tenant, row["id"])


def test_worker_bounded_cleanup_preserves_minimal_and_command_fences(protected, real_db):
    fixture, _ = protected
    client, _, tenant, base = fixture
    row, _ = create(fixture)
    assert remove(fixture, row).status_code == 200
    settled(real_db, tenant)
    age(real_db, row["id"], deleted=True)
    worker = Worker(real_db)
    finished = False
    for _ in range(15):
        finished = cleanup_rule(worker, tenant, row["id"], limit=1)
        if finished:
            break
    assert finished
    with real_db.transaction() as conn:
        for table in (
            "correlation_rules",
            "correlation_conditions",
            "correlation_rule_revisions",
            "correlation_rule_controls",
        ):
            key = "id" if table == "correlation_rules" else "rule_id"
            assert not conn.execute(
                f"SELECT 1 FROM {table} WHERE {key}=%s", (row["id"],)
            ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM correlation_delete_jobs WHERE rule_id=%s AND final_ack IS NOT NULL AND receipt IS NOT NULL",
            (row["id"],),
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM correlation_rule_identities WHERE id=%s AND final_revision=2",
            (row["id"],),
        ).fetchone()
    assert client.get(base + "/" + row["id"] + "/history").status_code == 404
    turn = correlation_cleanup_turn(worker)
    assert turn.attempted >= 1


def test_retained_incident_context_and_revision_dependency_prevent_cleanup(protected, real_db):
    fixture, _ = protected
    _, _, tenant, _ = fixture
    row, _ = create(fixture)
    assert remove(fixture, row).status_code == 200
    settled(real_db, tenant)
    age(real_db, row["id"], deleted=True)
    context, incident = uuid4(), uuid4()
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO correlation_contexts(id,tenant_id,rule_id,grouping_generation,group_digest,group_key,created_at,reset_at,reset_order) VALUES(%s,%s,%s,1,%s,%s,clock_timestamp(),clock_timestamp(),1)",
            (context, tenant, row["id"], b"a" * 32, b"group"),
        )
        conn.execute(
            "INSERT INTO incident_identities(id,tenant_id) VALUES(%s,%s)", (incident, tenant)
        )
        conn.execute(
            """INSERT INTO incidents(id,tenant_id,title,description,severity,status,origin,context_id,cycle_id,created_at,updated_at,last_activity,resolved_at,retention_at)
            VALUES(%s,%s,'retained','provenance','high','resolved','correlation',%s,%s,clock_timestamp(),clock_timestamp(),clock_timestamp(),clock_timestamp(),clock_timestamp()+interval '90 days')""",
            (incident, tenant, context, uuid4()),
        )
    assert not cleanup_rule(real_db, tenant, row["id"])
    with real_db.transaction() as conn:
        assert conn.execute(
            "SELECT 1 FROM correlation_contexts WHERE id=%s AND reset_order=1", (context,)
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM correlation_rule_revisions WHERE rule_id=%s AND revision=2", (row["id"],)
        ).fetchone()
        assert conn.execute("SELECT 1 FROM correlation_rules WHERE id=%s", (row["id"],)).fetchone()
