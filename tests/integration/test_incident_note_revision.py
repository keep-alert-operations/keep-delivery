# ruff: noqa: F811 -- shared synthetic authenticated fixture
"""Incident notes must pair displayed text with one coherent aggregate revision."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import base64
import json
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


class RestrictedDatabase:
    def __init__(self, database):
        self.database = database
        self.hook = None

    @contextmanager
    def transaction(self, *args, **kwargs):
        with self.database.transaction(*args, **kwargs) as conn:
            conn.execute("SET LOCAL ROLE appchi_api")
            yield HookedConnection(conn, self.hook) if self.hook else conn
            assert conn.execute("SELECT current_user AS role").fetchone()["role"] == "appchi_api"


class HookedConnection:
    def __init__(self, conn, hook):
        self.conn, self.hook = conn, hook

    def execute(self, query, *args, **kwargs):
        self.hook(self.conn, query if isinstance(query, str) else query.as_string(self.conn))
        return self.conn.execute(query, *args, **kwargs)


@pytest.fixture
def notes(browser, real_db, tenant):
    client, actor = browser
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, actor),
        )
    restricted = RestrictedDatabase(real_db)
    original = client.app.state.db
    client.app.state.db = restricted
    try:
        response = client.post(
            f"/api/tenants/{tenant}/incidents",
            json={"command_id": str(uuid4()), "title": "Note revision test", "severity": "low"},
        )
        assert response.status_code == 201, response.text
        path = f"/api/tenants/{tenant}/incidents/{response.json()['data']['id']}"
        note = client.post(path + "/notes", json=body("1", "Original note"))
        assert note.status_code == 201, note.text
        yield client, path, note.json()["data"]["id"], restricted
    finally:
        client.app.state.db = original


def body(revision, text):
    return {"command_id": str(uuid4()), "expected_revision": revision, "text": text}


def test_note_parent_revision_guards_edit_noop_delete_on_resolved_incident(notes, real_db):
    """Requirements: I5; incident operations 8 and 18."""
    client, path, note, _ = notes
    response = client.post(
        path + "/status",
        json={"command_id": str(uuid4()), "expected_revision": "2", "status": "resolved"},
    )
    assert response.status_code == 200, response.text
    listed = client.get(path + "/notes").json()
    assert listed["meta"]["revision"] == "3"
    assert (
        listed["data"][0]["revision"] == "1"
    )  # Note-local revision is not the write precondition.
    noop = client.patch(path + "/notes/" + note, json=body("3", "Original note"))
    assert noop.status_code == 200 and noop.json()["meta"]["revision"] == "3"
    changed = client.patch(path + "/notes/" + note, json=body("3", "Edited note"))
    assert changed.status_code == 200 and changed.json()["meta"]["revision"] == "4"
    stale = client.patch(path + "/notes/" + note, json=body("3", "Stale edit"))
    assert stale.status_code == 409
    current = client.get(path + "/notes").json()
    assert current["meta"]["revision"] == "4" and current["data"][0]["text"] == "Edited note"
    headers = {"Idempotency-Key": str(uuid4()), "If-Match": '"4"'}
    deleted = client.delete(path + "/notes/" + note, headers=headers)
    assert deleted.status_code == 200 and deleted.json()["meta"]["revision"] == "5"
    assert client.delete(path + "/notes/" + note, headers=headers).json() == deleted.json()
    assert client.get(path + "/notes").json()["data"] == []
    assert client.get(path).json()["data"]["status"] == "resolved"
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM incident_note_revisions WHERE note_id=%s", (note,)
            ).fetchone()["n"]
            == 3
        )


def test_note_read_holds_revision_until_text_read_finishes(notes, real_db):
    """Requirements: I5; incident operations 8 and 17."""
    client, path, note, restricted = notes
    reading, release, attempted = Event(), Event(), Event()
    writer_pid = []

    def hook(conn, query):
        if query.startswith('SELECT * FROM "incident_notes"'):
            reading.set()
            assert release.wait(5)
        if query.startswith("SELECT i.* FROM incidents i") and query.endswith("FOR UPDATE OF i"):
            writer_pid.append(conn.info.backend_pid)
            attempted.set()

    restricted.hook = hook
    with ThreadPoolExecutor(max_workers=2) as executor:
        read = executor.submit(client.get, path + "/notes")
        assert reading.wait(3)
        write = executor.submit(
            client.patch, path + "/notes/" + note, json=body("2", "Concurrent edit")
        )
        try:
            assert attempted.wait(1)
            blocked = False
            deadline = monotonic() + 0.75
            while monotonic() < deadline and not blocked:
                with real_db.transaction() as conn:
                    blocked = conn.execute(
                        "SELECT cardinality(pg_blocking_pids(%s))>0 AS blocked",
                        (writer_pid[0],),
                    ).fetchone()["blocked"]
                if not blocked:
                    sleep(0.01)
            assert blocked, "Concurrent note writer must wait for the coherent page read"
        finally:
            release.set()
        before = read.result(timeout=5)
        after = write.result(timeout=5)
    restricted.hook = None
    assert before.status_code == 200 and after.status_code == 200
    assert before.json()["meta"]["revision"] == "2"
    assert before.json()["data"][0]["text"] == "Original note"
    current = client.get(path + "/notes").json()
    assert current["meta"]["revision"] == "3"
    assert current["data"][0]["text"] == "Concurrent edit"


def test_history_keeps_actor_snapshots_watermark_and_deleted_text_out_of_current(notes, real_db):
    """Requirements: I5; incident operations 8 and 9."""
    client, path, note, _ = notes
    tenant = path.split("/")[3]
    with real_db.transaction(tenant) as conn:
        actor = conn.execute("SELECT actor_id FROM incident_notes WHERE id=%s", (note,)).fetchone()[
            "actor_id"
        ]
        conn.execute(
            "UPDATE principals SET display_name='Historical editor <label>' WHERE id=%s", (actor,)
        )
    assert (
        client.patch(
            path + "/notes/" + note, json=body("2", "Edited <b>plain text</b>\nLine two")
        ).status_code
        == 200
    )
    history_path = path + "/notes/" + note + "/history"
    first = client.get(history_path, params={"limit": 1})
    assert first.status_code == 200, first.text
    first = first.json()
    assert first["data"][0]["revision"] == "2"
    assert first["data"][0]["actor_display_name"] == "Historical editor <label>"
    assert first["meta"]["upper_revision"] == "2"
    assert first["next_cursor"] and "Historical editor" not in first["next_cursor"]
    with real_db.transaction(tenant) as conn:
        conn.execute("UPDATE principals SET display_name='Deletion actor' WHERE id=%s", (actor,))
    deleted = client.delete(
        path + "/notes/" + note, headers={"If-Match": '"3"', "Idempotency-Key": str(uuid4())}
    )
    assert deleted.status_code == 200, deleted.text
    older = client.get(history_path, params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert older["meta"]["upper_revision"] == "2" and older["next_cursor"] is None
    assert older["data"][0]["revision"] == "1"
    assert older["data"][0]["actor_display_name"] == "Synthetic user"
    current = client.get(history_path).json()["data"]
    assert [row["revision"] for row in current] == ["3", "2", "1"]
    assert current[0]["deleted"] and current[0]["actor_display_name"] == "Deletion actor"
    assert current[0]["text"] == current[1]["text"] == "Edited <b>plain text</b>\nLine two"
    assert client.get(path + "/notes").json()["data"] == []
    tombstone = client.get(path + "/notes", params={"include_deleted": "true"}).json()["data"][0]
    assert tombstone["deleted"] and tombstone["text"] is None
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, actor),
        )
    assert client.get(history_path).status_code == 200
    assert client.patch(path + "/notes/" + note, json=body("4", "Disallowed")).status_code == 403


def test_history_cursor_rechecks_scope_expiry_access_and_retained_parent(notes, real_db, cipher):
    """Requirements: I5; incident operations 8, 13 and 19."""
    client, path, note, _ = notes
    tenant = path.split("/")[3]
    incident = path.split("/")[5]
    assert client.patch(path + "/notes/" + note, json=body("2", "Second text")).status_code == 200
    history_path = path + "/notes/" + note + "/history"
    token = client.get(history_path, params={"limit": 1}).json()["next_cursor"]
    another = client.post(path + "/notes", json=body("3", "Another note")).json()["data"]["id"]
    wrong_note = client.get(path + "/notes/" + another + "/history", params={"cursor": token})
    assert wrong_note.status_code == 409 and wrong_note.json()["error"]["code"] == "invalid_cursor"
    assert client.get(history_path, params={"cursor": token[:-8] + "invalid"}).status_code == 400
    payload = json.loads(cipher.open(base64.urlsafe_b64decode(token), "cursor:v1"))
    payload["expires_at"] = "2000-01-01T00:00:00+00:00"
    expired = base64.urlsafe_b64encode(
        cipher.seal(json.dumps(payload).encode(), "cursor:v1")
    ).decode()
    failure = client.get(history_path, params={"cursor": expired})
    assert failure.status_code == 409 and failure.json()["error"]["code"] == "cursor_expired"
    assert client.get(history_path, params={"limit": 201}).status_code == 422
    assert client.get(path + "/notes/" + str(uuid4()) + "/history").status_code == 404
    with real_db.transaction(tenant) as conn:
        actor = conn.execute("SELECT actor_id FROM incident_notes WHERE id=%s", (note,)).fetchone()[
            "actor_id"
        ]
        conn.execute(
            "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, actor)
        )
    assert client.get(history_path, params={"cursor": token}).status_code == 404
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'view')",
            (tenant, actor),
        )
        conn.execute(
            "UPDATE incident_identities SET retired_at=clock_timestamp() WHERE tenant_id=%s AND id=%s",
            (tenant, incident),
        )
    assert client.get(history_path, params={"cursor": token}).status_code == 404
    assert client.get(path + "/notes", params={"include_deleted": "true"}).status_code == 404


def test_deleted_selector_cursor_is_bound_and_legacy_name_is_not_inferred(notes, real_db):
    """Requirements: I5; incident operations 8."""
    client, path, note, _ = notes
    tenant = path.split("/")[3]
    another = client.post(path + "/notes", json=body("2", "Another note")).json()["data"]["id"]
    first = client.get(path + "/notes", params={"include_deleted": "true", "limit": 1}).json()
    wrong_filter = client.get(
        path + "/notes",
        params={"include_deleted": "false", "limit": 1, "cursor": first["next_cursor"]},
    )
    assert wrong_filter.status_code == 409
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE incident_note_revisions SET actor_display_name=NULL WHERE note_id=%s",
            (another,),
        )
    legacy = client.get(path + "/notes/" + another + "/history").json()["data"][0]
    assert legacy["actor_display_name"] is None
    assert legacy["actor_id"] and legacy["text"] == "Another note"
