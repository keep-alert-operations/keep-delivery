"""Real PostgreSQL configuration history, holds, fences and physical erasure."""
# ruff: noqa: F811

from pathlib import Path
from contextlib import contextmanager
from uuid import uuid4

import pytest
from psycopg import sql

from keep.configuration.cleanup import cleanup_configuration
from test_maintenance_repository import maintenance_db  # noqa: F401
from test_product import browser  # noqa: F401
from test_configuration_deletion import configuration, create, headers  # noqa: F401

pytestmark = pytest.mark.integration


def test_api_role_writes_and_reads_captured_history(configuration, real_db):
    from test_audit_browsing import RestrictedDatabase

    client, base, _, _, _ = configuration
    original = client.app.state.db
    client.app.state.db = RestrictedDatabase(real_db)
    try:
        preset, body = create(configuration)
        url = base + "/presets/" + preset["id"]
        result = client.put(
            url, json={**body, "command_id": str(uuid4()), "expected_revision": "1", "cel": "false"}
        )
        assert result.status_code == 200, result.text
        assert client.get(url + "/history/2").json()["data"]["before_definition"]["cel"] == "true"
        deleted = client.delete(url, headers=headers("2"))
        assert deleted.status_code == 200, deleted.text
        assert client.get(url + "/history/3").json()["data"]["activity"]["kind"] == "deleted"
    finally:
        client.app.state.db = original


@pytest.fixture(scope="module")
def real_db(maintenance_db):
    db, _ = maintenance_db
    draft = Path(__file__).resolve().parents[2] / ".local/0034_configuration_history_parents.sql"
    if draft.exists():
        with db.transaction() as conn:
            conn.execute(draft.read_text(encoding="utf-8"), prepare=False)
    return db


def age(conn, resource, table="presets"):
    conn.execute(
        sql.SQL("UPDATE {} SET deleted_at=deleted_at-interval '91 days' WHERE id=%s").format(
            sql.Identifier(table)
        ),
        (resource,),
    )
    # Synthetic clock movement is administrative fixture setup, never a runtime API.
    conn.execute(
        "ALTER TABLE configuration_identities DISABLE TRIGGER configuration_fence_immutable"
    )
    conn.execute(
        "UPDATE configuration_identities SET deleted_at=deleted_at-interval '91 days' WHERE resource_id=%s",
        (resource,),
    )
    conn.execute(
        "ALTER TABLE configuration_identities ENABLE TRIGGER configuration_fence_immutable"
    )
    conn.execute(
        "UPDATE configuration_activity SET recorded_at=recorded_at-interval '91 days' WHERE resource_id=%s",
        (resource,),
    )
    conn.execute(
        sql.SQL(
            "UPDATE {} SET recorded_at=recorded_at-interval '91 days',superseded_at=superseded_at-interval '91 days' WHERE id=%s"
        ).format(
            sql.Identifier("preset_revisions" if table == "presets" else "dashboard_revisions")
        ),
        (resource,),
    )


def test_events_replay_noop_actor_snapshot_and_before_definition(configuration, real_db):
    client, base, _, tenant, actor = configuration
    preset, body = create(configuration)
    url = base + "/presets/" + preset["id"]
    update = {**body, "command_id": str(uuid4()), "expected_revision": "1", "cel": "false"}
    result = client.put(url, json=update)
    assert result.status_code == 200, result.text
    assert client.put(url, json=update).json() == result.json()
    noop = client.put(url, json={**update, "command_id": str(uuid4()), "expected_revision": "2"})
    assert noop.status_code == 200
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET display_name='Renamed' WHERE id=%s", (actor,))
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM configuration_activity WHERE resource_id=%s",
                (preset["id"],),
            ).fetchone()["n"]
            == 2
        )
    detail = client.get(url + "/history/2").json()["data"]
    assert detail["definition"]["cel"] == "false"
    assert detail["before_definition"]["cel"] == "true"
    assert detail["activity"]["actor_label"] == "Synthetic user"
    assert detail["activity"]["changed_fields"] == ["cel"]
    assert detail["activity"]["command_id"] == update["command_id"]
    assert client.delete(url, headers=headers("2")).status_code == 200
    deletion = client.get(url + "/history/3").json()["data"]
    assert deletion["activity"]["kind"] == "deleted"
    assert deletion["before_definition"]["revision"] == "2"


def test_cleanup_keeps_held_event_references_then_erases_only_content(configuration, real_db):
    client, base, _, tenant, _ = configuration
    preset, _ = create(configuration)
    resource = preset["id"]
    url = base + "/presets/" + resource
    assert client.delete(url, headers=headers()).status_code == 200
    hold = uuid4()
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO configuration_retention_holds(id,tenant_id,resource_kind,resource_id,revision,reason) VALUES(%s,%s,'preset',%s,2,'controlled-pending-consumer')",
            (hold, tenant, resource),
        )
        age(conn, resource)
    assert not cleanup_configuration(real_db, tenant, "preset", resource, limit=1)
    assert client.get(url + "/history/1").status_code == 200
    held_detail = client.get(url + "/history/2").json()["data"]
    assert held_detail["activity"]["kind"] == "deleted"
    assert held_detail["before_definition"]["revision"] == "1"
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM preset_revisions WHERE id=%s", (resource,)
            ).fetchone()["n"]
            == 2
        )
        conn.execute(
            "UPDATE configuration_retention_holds SET released_at=clock_timestamp() WHERE id=%s",
            (hold,),
        )
    assert client.get(url + "/history/2").status_code == 404
    for _ in range(6):
        if cleanup_configuration(real_db, tenant, "preset", resource, limit=1):
            break
    else:
        pytest.fail("Bounded cleanup did not converge")
    with real_db.transaction() as conn:
        assert conn.execute("SELECT 1 FROM presets WHERE id=%s", (resource,)).fetchone() is None
        assert (
            conn.execute("SELECT 1 FROM preset_revisions WHERE id=%s", (resource,)).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT 1 FROM configuration_activity WHERE resource_id=%s", (resource,)
            ).fetchone()
            is None
        )
        fence = conn.execute(
            "SELECT * FROM configuration_identities WHERE resource_id=%s", (resource,)
        ).fetchone()
        assert fence["final_revision"] == 2
        assert set(fence) == {
            "tenant_id",
            "resource_kind",
            "resource_id",
            "final_revision",
            "deleted_at",
        }
    # Same retired UUID cannot be reinserted after content erasure.
    with pytest.raises(Exception, match="Retired configuration identity"):
        with real_db.transaction() as conn:
            conn.execute(
                "INSERT INTO presets(id,tenant_id,name,name_key,revision,cel,profile,sort,columns,created_at,updated_at,created_by,updated_by) VALUES(%s,%s,'reused','reused',1,'true','keep-cel-v1','[]','[]',clock_timestamp(),clock_timestamp(),%s,%s)",
                (resource, tenant, preset["created_by"], preset["updated_by"]),
            )


def test_cleanup_requires_final_proof_and_preserves_old_active_definition(configuration, real_db):
    client, base, store, tenant, _ = configuration
    preset, _ = create(configuration)
    resource = preset["id"]
    with real_db.transaction() as conn:
        age(conn, resource)
    assert cleanup_configuration(real_db, tenant, "preset", resource)
    assert client.get(base + "/presets/" + resource).status_code == 200
    store.fail = "final"
    assert client.delete(base + "/presets/" + resource, headers=headers()).status_code == 503
    with real_db.transaction() as conn:
        age(conn, resource)
    assert not cleanup_configuration(real_db, tenant, "preset", resource)
    # Unacknowledged deletion proof pins its exact base/final records and their
    # event references beyond the ordinary lifetime, consistently with cleanup.
    for revision in (1, 2):
        pending = client.get(base + "/presets/" + resource + f"/history/{revision}")
        assert pending.status_code == 200, pending.text
        if revision == 2:
            assert pending.json()["data"]["activity"]["kind"] == "deleted"
        else:
            # Its old creation activity was already erased before this command;
            # preserving the base definition never reconstructs old events.
            assert pending.json()["data"]["activity"] is None
    with real_db.transaction() as conn:
        assert conn.execute("SELECT 1 FROM presets WHERE id=%s", (resource,)).fetchone()


def test_list_cursor_rejects_collection_change_and_deleted_view_expires(configuration, real_db):
    client, base, _, _, _ = configuration
    a, _ = create(configuration, name="Cursor A")
    create(configuration, name="Cursor B")
    page = client.get(base + "/presets", params={"name": "Cursor", "limit": 1}).json()
    assert page["next_cursor"]
    create(configuration, name="Cursor C")
    changed = client.get(
        base + "/presets", params={"name": "Cursor", "limit": 1, "cursor": page["next_cursor"]}
    )
    assert changed.status_code == 409, changed.text
    assert changed.json()["error"]["code"] == "list_changed"
    assert client.delete(base + "/presets/" + a["id"], headers=headers()).status_code == 200
    deleted = client.get(base + "/presets", params={"state": "deleted", "name": "Cursor A"})
    assert [row["id"] for row in deleted.json()["data"]] == [a["id"]]
    with real_db.transaction() as conn:
        age(conn, a["id"])
    assert (
        client.get(base + "/presets", params={"state": "deleted", "name": "Cursor A"}).json()[
            "data"
        ]
        == []
    )


def test_worker_role_dashboard_cleanup_and_api_hold_denial(configuration, real_db):
    client, base, _, tenant, _ = configuration
    preset, _ = create(configuration)
    dashboard, _ = create(configuration, "dashboards", preset=preset["id"])
    assert (
        client.delete(base + "/dashboards/" + dashboard["id"], headers=headers()).status_code == 200
    )
    with real_db.transaction() as conn:
        age(conn, dashboard["id"], "dashboards")

    class Worker:
        @contextmanager
        def transaction(self, *args, **kwargs):
            with real_db.transaction(*args, **kwargs) as conn:
                conn.execute("SET LOCAL ROLE keep_worker")
                yield conn

    assert cleanup_configuration(Worker(), tenant, "dashboard", dashboard["id"])
    assert client.get(base + "/presets/" + preset["id"]).status_code == 200
    with pytest.raises(Exception, match="permission denied"):
        with real_db.transaction(tenant) as conn:
            conn.execute("SET LOCAL ROLE keep_api")
            conn.execute(
                "INSERT INTO configuration_retention_holds(id,tenant_id,resource_kind,resource_id,reason) VALUES(%s,%s,'preset',%s,'forbidden')",
                (uuid4(), tenant, preset["id"]),
            )


def test_removing_intermediate_revision_keeps_original_supersession_clock(configuration, real_db):
    client, base, _, tenant, _ = configuration
    preset, body = create(configuration)
    resource = preset["id"]
    url = base + "/presets/" + resource
    for rev in (1, 2):
        result = client.put(
            url,
            json={
                **body,
                "command_id": str(uuid4()),
                "expected_revision": str(rev),
                "name": body["name"] + str(rev),
            },
        )
        assert result.status_code == 200, result.text
    hold = uuid4()
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO configuration_retention_holds(id,tenant_id,resource_kind,resource_id,revision,reason) VALUES(%s,%s,'preset',%s,1,'revision-one')",
            (hold, tenant, resource),
        )
        conn.execute(
            "UPDATE preset_revisions SET recorded_at=recorded_at-interval '100 days',superseded_at=superseded_at-interval '100 days' WHERE id=%s",
            (resource,),
        )
        conn.execute(
            "UPDATE configuration_activity SET recorded_at=recorded_at-interval '100 days' WHERE resource_id=%s",
            (resource,),
        )
    assert cleanup_configuration(real_db, tenant, "preset", resource)
    with real_db.transaction() as conn:
        assert [
            r["revision"]
            for r in conn.execute(
                "SELECT revision FROM preset_revisions WHERE id=%s ORDER BY revision", (resource,)
            )
        ] == [1, 3]
        conn.execute(
            "UPDATE configuration_retention_holds SET released_at=clock_timestamp() WHERE id=%s",
            (hold,),
        )
    assert client.get(url + "/history/1").status_code == 404
    assert cleanup_configuration(real_db, tenant, "preset", resource)
    assert client.get(url + "/history/3").status_code == 200


def test_retired_identity_is_immutable_to_api_and_job_label_scrubbing_is_held(
    configuration, real_db
):
    from keep.configuration.cleanup import configuration_cleanup_turn
    from keep.recovery.command_retention import compact_ordinary_command

    client, base, store, tenant, _ = configuration
    preset, _ = create(configuration)
    resource = preset["id"]
    assert client.delete(base + "/presets/" + resource, headers=headers()).status_code == 200
    with pytest.raises(Exception, match="fence is immutable"):
        with real_db.transaction(tenant) as conn:
            conn.execute("SET LOCAL ROLE keep_api")
            conn.execute(
                "UPDATE configuration_identities SET final_revision=NULL,deleted_at=NULL WHERE resource_id=%s",
                (resource,),
            )
    with real_db.transaction() as conn:
        age(conn, resource)
        conn.execute(
            "UPDATE configuration_delete_jobs SET updated_at=updated_at-interval '91 days' WHERE resource_id=%s",
            (resource,),
        )
        job = conn.execute(
            "SELECT * FROM configuration_delete_jobs WHERE resource_id=%s", (resource,)
        ).fetchone()
        conn.execute(
            "UPDATE command_receipts SET completed_at=completed_at-interval '91 days' WHERE scope=%s AND command_id=%s",
            (job["scope"], job["command_id"]),
        )
    # Content may be removed while an independently retained receipt still exists.
    assert cleanup_configuration(real_db, tenant, "preset", resource)
    with real_db.transaction() as conn:
        assert conn.execute(
            "SELECT actor_label FROM configuration_delete_jobs WHERE id=%s", (job["id"],)
        ).fetchone()["actor_label"]
    assert compact_ordinary_command(real_db, store, job["scope"], job["command_id"])
    # The scanner must revisit job metadata even after the parent content is gone.
    cursor = None
    for _ in range(20):
        page = configuration_cleanup_turn(real_db, cursor=cursor)
        cursor = page.cursor
        if cursor is None:
            break
    with real_db.transaction() as conn:
        remaining = conn.execute(
            "SELECT * FROM configuration_delete_jobs WHERE id=%s", (job["id"],)
        ).fetchone()
        assert remaining["actor_label"] is None
        assert remaining["final_ack"] is not None


@pytest.mark.parametrize("table", ["presets", "dashboards"])
def test_older_only_hold_erases_current_body_keeps_history_and_exact_deleted_name(
    configuration, real_db, table
):
    from keep.configuration.cleanup import configuration_cleanup_turn
    from test_audit_browsing import RestrictedDatabase

    client, base, _, tenant, _ = configuration
    token = uuid4().hex
    old_name, final_name = "Straße " + token, "Expired final " + token
    preset = create(configuration)[0]["id"] if table == "dashboards" else None
    definition, body = create(configuration, table, preset=preset, name=old_name)
    resource, kind = definition["id"], table[:-1]
    url = base + "/" + table + "/" + resource
    changed = client.put(
        url, json={**body, "name": final_name, "command_id": str(uuid4()), "expected_revision": "1"}
    )
    assert changed.status_code == 200, changed.text
    assert client.delete(url, headers=headers("2")).status_code == 200
    held, replacement_hold = uuid4(), uuid4()
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO configuration_retention_holds(id,tenant_id,resource_kind,resource_id,revision,reason) VALUES(%s,%s,%s,%s,1,'original-history')",
            (held, tenant, kind, resource),
        )
        age(conn, resource, table)
        # Exercise bounded exact Unicode backfill for a legacy historical row.
        conn.execute(
            sql.SQL("UPDATE {} SET name_key=NULL WHERE id=%s AND revision=1").format(
                sql.Identifier(table[:-1] + "_revisions")
            ),
            (resource,),
        )
    original_db = client.app.state.db
    client.app.state.db = RestrictedDatabase(real_db)
    try:
        before_cleanup = client.get(
            base + "/" + table, params={"state": "deleted", "name": "STRASSE " + token}
        )
        assert before_cleanup.status_code == 200, before_cleanup.text
        assert [r["id"] for r in before_cleanup.json()["data"]] == [resource]
        for _ in range(6):
            assert not cleanup_configuration(real_db, tenant, kind, resource, limit=1)
            assert client.get(url + "/history/1").status_code == 200
        with real_db.transaction() as conn:
            assert (
                conn.execute(
                    sql.SQL("SELECT 1 FROM {} WHERE id=%s").format(sql.Identifier(table)),
                    (resource,),
                ).fetchone()
                is None
            )
            assert [
                r["revision"]
                for r in conn.execute(
                    sql.SQL("SELECT revision FROM {} WHERE id=%s ORDER BY revision").format(
                        sql.Identifier(kind + "_revisions")
                    ),
                    (resource,),
                )
            ] == [1]
        assert client.get(url).status_code == 404
        assert client.get(url + "/history/3").status_code == 404
        historical = client.get(url + "/history/1")
        assert historical.status_code == 200, historical.text
        assert historical.json()["data"]["definition"]["name"] == old_name
        assert [r["revision"] for r in client.get(url + "/history").json()["data"]] == ["1"]
        listed = client.get(
            base + "/" + table, params={"state": "deleted", "name": "STRASSE " + token}
        ).json()["data"]
        assert listed[0]["name"] == old_name and listed[0]["history_revision"] == "1"
        assert listed[0]["final_revision"] == "3" and listed[0]["deleted_at"]
        assert (
            client.get(base + "/" + table, params={"state": "deleted", "name": final_name}).json()[
                "data"
            ]
            == []
        )
        # Another legitimate consumer may pin already-retained history after the
        # current body is gone; this must not require or reconstruct that body.
        with real_db.transaction() as conn:
            conn.execute(
                "INSERT INTO configuration_retention_holds(id,tenant_id,resource_kind,resource_id,revision,reason) VALUES(%s,%s,%s,%s,1,'second-consumer')",
                (replacement_hold, tenant, kind, resource),
            )
            conn.execute(
                "UPDATE configuration_retention_holds SET released_at=clock_timestamp() WHERE id=%s",
                (held,),
            )
        assert client.get(url + "/history/1").status_code == 200
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE configuration_retention_holds SET released_at=clock_timestamp() WHERE id=%s",
                (replacement_hold,),
            )
            conn.execute(
                "UPDATE configuration_delete_jobs SET actor_label=NULL WHERE resource_id=%s",
                (resource,),
            )
        assert client.get(url + "/history/1").status_code == 404
        assert (
            client.get(base + "/" + table, params={"state": "deleted", "name": token}).json()[
                "data"
            ]
            == []
        )
        # No current body or labelled job remains: the revision scan must still
        # find this resource and erase newly unheld history.
        cursor = None
        for _ in range(30):
            page = configuration_cleanup_turn(real_db, cursor=cursor, limit=100)
            cursor = page.cursor
            if cursor is None:
                break
        with real_db.transaction() as conn:
            assert (
                conn.execute(
                    sql.SQL("SELECT 1 FROM {} WHERE id=%s").format(
                        sql.Identifier(kind + "_revisions")
                    ),
                    (resource,),
                ).fetchone()
                is None
            )
            assert conn.execute(
                "SELECT 1 FROM configuration_identities WHERE resource_id=%s", (resource,)
            ).fetchone()
    finally:
        client.app.state.db = original_db


def test_new_holds_cannot_revive_logically_expired_content_or_activity(configuration, real_db):
    client, base, _, tenant, _ = configuration
    preset, body = create(configuration)
    resource = preset["id"]
    url = base + "/presets/" + resource
    assert (
        client.put(
            url,
            json={
                **body,
                "command_id": str(uuid4()),
                "expected_revision": "1",
                "name": body["name"] + " changed",
            },
        ).status_code
        == 200
    )
    with real_db.transaction() as conn:
        age(conn, resource)
    for revision in (1, 2, None):
        # Revision 2 is still the active definition, but its expired activity
        # cannot be revived by a revision-wide hold before activity cleanup.
        with pytest.raises(Exception, match="expired"):
            with real_db.transaction() as conn:
                conn.execute(
                    "INSERT INTO configuration_retention_holds(id,tenant_id,resource_kind,resource_id,revision,reason) VALUES(%s,%s,'preset',%s,%s,'too-late')",
                    (uuid4(), tenant, resource, revision),
                )
    assert cleanup_configuration(real_db, tenant, "preset", resource)
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO configuration_retention_holds(id,tenant_id,resource_kind,resource_id,revision,reason) VALUES(%s,%s,'preset',%s,2,'current-definition-only')",
            (uuid4(), tenant, resource),
        )
    assert client.get(url + "/history/2").json()["data"]["activity"] is None


def test_deleted_cursor_changes_when_hold_release_changes_retained_collection(
    configuration, real_db
):
    client, base, _, tenant, _ = configuration
    prefix = uuid4().hex
    resources, holds = [], []
    for suffix in ("A", "B"):
        preset, _ = create(configuration, name=prefix + suffix)
        resources.append(preset["id"])
        assert (
            client.delete(base + "/presets/" + preset["id"], headers=headers()).status_code == 200
        )
        with real_db.transaction() as conn:
            hold = uuid4()
            holds.append(hold)
            conn.execute(
                "INSERT INTO configuration_retention_holds(id,tenant_id,resource_kind,resource_id,revision,reason) VALUES(%s,%s,'preset',%s,1,'discovery')",
                (hold, tenant, preset["id"]),
            )
            age(conn, preset["id"])
    params = {"state": "deleted", "name": prefix, "limit": 1}
    first = client.get(base + "/presets", params=params)
    assert first.status_code == 200 and first.json()["next_cursor"], first.text
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE configuration_retention_holds SET released_at=clock_timestamp() WHERE id=%s",
            (holds[0],),
        )
    changed = client.get(
        base + "/presets", params={**params, "cursor": first.json()["next_cursor"]}
    )
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "list_changed"


def test_deleted_name_warmup_commits_bounded_progress_before_unavailable(
    configuration, real_db, monkeypatch
):
    import keep.configuration.retention as retention

    client, base, _, tenant, _ = configuration
    prefix = uuid4().hex
    resources = []
    for suffix in ("a", "b"):
        preset, _ = create(configuration, name=prefix + suffix)
        resources.append(preset["id"])
        assert (
            client.delete(base + "/presets/" + preset["id"], headers=headers()).status_code == 200
        )
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE preset_revisions SET name_key=NULL WHERE id=ANY(%s::uuid[]) AND revision=2",
            (resources,),
        )
    original = retention.warm_history_names
    monkeypatch.setattr(
        retention,
        "warm_history_names",
        lambda conn, tenant, kind: original(conn, tenant, kind, limit=1),
    )
    params = {"state": "deleted", "name": prefix}
    pending = client.get(base + "/presets", params=params)
    assert (
        pending.status_code == 503
        and pending.json()["error"]["code"] == "configuration_history_name_index_pending"
    )
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM preset_revisions WHERE id=ANY(%s::uuid[]) AND revision=2 AND name_key IS NOT NULL",
                (resources,),
            ).fetchone()["n"]
            == 1
        )
    completed = client.get(base + "/presets", params=params)
    assert completed.status_code == 200 and len(completed.json()["data"]) == 2


def test_deleted_name_filter_uses_latest_retained_snapshot_not_any_older_match(
    configuration, real_db
):
    client, base, _, tenant, _ = configuration
    prefix = uuid4().hex
    old_name, newer_name = prefix + " old", prefix + " newest"
    preset, body = create(configuration, name=old_name)
    resource = preset["id"]
    url = base + "/presets/" + resource
    assert (
        client.put(
            url,
            json={**body, "name": newer_name, "command_id": str(uuid4()), "expected_revision": "1"},
        ).status_code
        == 200
    )
    assert client.delete(url, headers=headers("2")).status_code == 200
    with real_db.transaction() as conn:
        for revision in (1, 2):
            conn.execute(
                "INSERT INTO configuration_retention_holds(id,tenant_id,resource_kind,resource_id,revision,reason) VALUES(%s,%s,'preset',%s,%s,'history-consumer')",
                (uuid4(), tenant, resource, revision),
            )
        age(conn, resource)
    assert not cleanup_configuration(real_db, tenant, "preset", resource)
    assert (
        client.get(base + "/presets", params={"state": "deleted", "name": old_name}).json()["data"]
        == []
    )
    latest = client.get(base + "/presets", params={"state": "deleted", "name": newer_name}).json()[
        "data"
    ]
    assert len(latest) == 1 and latest[0]["history_revision"] == "2"


def test_old_revision_hold_does_not_hold_later_deletion_actor_snapshot(configuration, real_db):
    from keep.recovery.command_retention import compact_ordinary_command

    client, base, store, tenant, _ = configuration
    preset, body = create(configuration)
    resource = preset["id"]
    url = base + "/presets/" + resource
    assert (
        client.put(
            url, json={**body, "command_id": str(uuid4()), "expected_revision": "1", "cel": "false"}
        ).status_code
        == 200
    )
    assert client.delete(url, headers=headers("2")).status_code == 200
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO configuration_retention_holds(id,tenant_id,resource_kind,resource_id,revision,reason) VALUES(%s,%s,'preset',%s,1,'only-old-revision')",
            (uuid4(), tenant, resource),
        )
        age(conn, resource)
        conn.execute(
            "UPDATE configuration_delete_jobs SET updated_at=updated_at-interval '91 days' WHERE resource_id=%s",
            (resource,),
        )
        job = conn.execute(
            "SELECT * FROM configuration_delete_jobs WHERE resource_id=%s", (resource,)
        ).fetchone()
        conn.execute(
            "UPDATE command_receipts SET completed_at=completed_at-interval '91 days' WHERE scope=%s AND command_id=%s",
            (job["scope"], job["command_id"]),
        )
    assert compact_ordinary_command(real_db, store, job["scope"], job["command_id"])
    assert not cleanup_configuration(real_db, tenant, "preset", resource)
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT actor_label FROM configuration_delete_jobs WHERE id=%s", (job["id"],)
            ).fetchone()["actor_label"]
            is None
        )
    assert client.get(url + "/history/1").status_code == 200
