# ruff: noqa: F811 -- pytest fixtures imported by name
"""Real PostgreSQL protected-delete tests; only independent evidence is synthetic."""

from uuid import uuid4

import pytest

from keep.configuration.deletion import resume_delete, router
from keep.recovery.evidence import DurableAck
from keep.shared.errors import unavailable
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


class EvidenceFixture:
    def close(self):
        pass

    def __init__(self):
        self.calls = []
        self.fail = None
        self.callback = None
        self.bad_ack = False

    def append(self, evidence):
        self.calls.append(evidence)
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        if evidence.state == self.fail:
            raise unavailable("recovery_protection_unavailable")
        return DurableAck(
            evidence.id, "fixture", "checkpoint", "0" * 64 if self.bad_ack else evidence.digest
        )


@pytest.fixture
def configuration(browser, real_db):
    client, actor = browser
    if not any(getattr(route, "name", "") == "delete_presets" for route in client.app.routes):
        client.app.include_router(router)
    store = EvidenceFixture()
    client.app.state.recovery_store = store
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    return client, f"/api/tenants/{tenant}", store, tenant, actor


def create(configuration, kind="presets", *, preset=None, name=None):
    client, base, *_ = configuration
    body = {"command_id": str(uuid4()), "name": name or str(uuid4())}
    if kind == "presets":
        body.update(
            cel="true", sort=[{"field": "timestamp", "direction": "desc"}], columns=["message"]
        )
    else:
        body["panels"] = [
            {
                "id": str(uuid4()),
                "preset_id": preset,
                "title": None,
                "x": 0,
                "y": 0,
                "w": 3,
                "h": 2,
                "thresholds": [],
            }
        ]
    response = client.post(base + "/" + kind, json=body)
    assert response.status_code == 201, response.text
    return response.json()["data"], body


def headers(revision="1", command=None):
    return {"If-Match": '"' + revision + '"', "Idempotency-Key": command or str(uuid4())}


def job(real_db, resource):
    with real_db.transaction() as conn:
        return conn.execute(
            "SELECT * FROM configuration_delete_jobs WHERE resource_id=%s ORDER BY created_at DESC LIMIT 1",
            (resource,),
        ).fetchone()


def test_delete_is_protected_atomic_retained_and_replayable(configuration, real_db):
    client, base, store, tenant, actor = configuration
    preset, definition = create(configuration)
    path = base + "/presets/" + preset["id"]
    command = headers()
    result = client.delete(path, headers=command)
    assert result.status_code == 200, result.text
    assert result.headers["etag"] == '"2"'
    assert result.json()["data"] == {"id": preset["id"], "deleted": True, "revision": "2"}
    assert [call.state for call in store.calls] == ["intent", "final"]
    assert client.delete(path, headers=command).json() == result.json()
    assert len(store.calls) == 2
    assert client.get(path).status_code == 404
    history = client.get(path + "/history/2")
    assert history.status_code == 200, history.text
    assert history.json()["data"]["definition"]["cel"] == "true"
    assert history.json()["data"]["definition"]["deleted_at"]
    assert client.delete(path, headers={**command, "If-Match": '"2"'}).status_code == 409
    replacement, _ = create(configuration, name=definition["name"])
    assert replacement["id"] != preset["id"]
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM configuration_activity WHERE resource_id=%s AND actor_id=%s AND kind='deleted'",
                (preset["id"], actor),
            ).fetchone()["n"]
            == 1
        )
        assert conn.execute(
            "SELECT * FROM live_hint_outbox WHERE tenant_id=%s", (tenant,)
        ).fetchone()
    assert job(real_db, preset["id"])["state"] == "final_protected"


def test_background_resumption_protects_final_after_api_disconnect(configuration, real_db):
    from keep.recovery.deletions import deletion_turn

    client, base, store, *_ = configuration
    preset, _ = create(configuration)
    command = headers()
    store.fail = "final"
    path = base + "/presets/" + preset["id"]
    assert client.delete(path, headers=command).status_code == 503
    assert job(real_db, preset["id"])["state"] == "deleted"
    store.fail = None
    cursor = None
    for _ in range(100):
        page = deletion_turn(real_db, store, cursor=cursor)
        cursor = page.cursor
        if cursor is None:
            break
    assert cursor is None
    assert job(real_db, preset["id"])["state"] == "final_protected"
    assert client.delete(path, headers=command).status_code == 200


def test_dashboard_reference_blocks_preset_then_dashboard_delete_releases_only_live_refs(
    configuration, real_db
):
    client, base, store, *_ = configuration
    preset, _ = create(configuration)
    dashboard, _ = create(configuration, "dashboards", preset=preset["id"])
    blocked = client.delete(base + "/presets/" + preset["id"], headers=headers())
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "preset_in_use"
    assert store.calls == []
    response = client.delete(base + "/dashboards/" + dashboard["id"], headers=headers())
    assert response.status_code == 200, response.text
    historical = client.get(base + "/dashboards/" + dashboard["id"] + "/history/2")
    assert historical.json()["data"]["definition"]["panels"][0]["preset_id"] == preset["id"]
    assert client.get(base + "/presets/" + preset["id"]).status_code == 200
    assert client.delete(base + "/presets/" + preset["id"], headers=headers()).status_code == 200
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM dashboard_panels WHERE dashboard_id=%s",
                (dashboard["id"],),
            ).fetchone()["n"]
            == 0
        )


def test_intent_failure_preserves_name_and_retry_stable_ids(configuration, real_db):
    client, base, store, *_ = configuration
    preset, definition = create(configuration)
    path = base + "/presets/" + preset["id"]
    command = headers()
    store.fail = "intent"
    assert client.delete(path, headers=command).status_code == 503
    assert client.get(path).json()["data"]["revision"] == "1"
    duplicate = client.post(base + "/presets", json={**definition, "command_id": str(uuid4())})
    assert duplicate.status_code == 409
    pending = job(real_db, preset["id"])
    assert pending["state"] == "prepared" and pending["receipt"] is None
    store.fail = None
    assert client.delete(path, headers=command).status_code == 200
    assert store.calls[0] == store.calls[1]


def test_final_failure_resumes_without_second_revision_or_receipt(configuration, real_db):
    client, base, store, *_ = configuration
    preset, _ = create(configuration)
    path, command = base + "/presets/" + preset["id"], headers()
    store.fail = "final"
    assert client.delete(path, headers=command).status_code == 503
    assert client.get(path).status_code == 404
    pending = job(real_db, preset["id"])
    assert pending["state"] == "deleted" and pending["receipt"]["meta"]["revision"] == "2"
    store.fail = None
    assert client.delete(path, headers=command).status_code == 200
    assert [call.state for call in store.calls] == ["intent", "final", "final"]
    assert store.calls[1] == store.calls[2]
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM preset_revisions WHERE id=%s", (preset["id"],)
            ).fetchone()["n"]
            == 2
        )


@pytest.mark.parametrize("race", ["revision", "reference"])
def test_external_call_holds_no_locks_and_commit_rechecks_races(configuration, real_db, race):
    client, base, store, tenant, _ = configuration
    preset, definition = create(configuration)
    path, command = base + "/presets/" + preset["id"], headers()

    def change():
        if race == "revision":
            updated = client.put(
                path,
                json={
                    **definition,
                    "name": str(uuid4()),
                    "command_id": str(uuid4()),
                    "expected_revision": "1",
                },
            )
            assert updated.status_code == 200, updated.text
        else:
            create(configuration, "dashboards", preset=preset["id"])

    store.callback = change
    store.fail = "cancel"
    assert client.delete(path, headers=command).status_code == 503
    pending = job(real_db, preset["id"])
    assert pending["state"] == "cancelled" and pending["cancel_ack"] is None
    # Worker cancellation recovery must not try the active-resource/revision gate.
    store.fail = None
    with pytest.raises(Exception) as failure:
        resume_delete(real_db, store, tenant, pending["id"])
    assert getattr(failure.value, "code", None) == (
        "revision_conflict" if race == "revision" else "preset_in_use"
    )
    assert job(real_db, preset["id"])["cancel_ack"]
    assert client.get(path).status_code == 200
    assert client.delete(path, headers=command).status_code == 409
    assert store.calls[-1].state == "cancel"


def test_target_scopes_expired_results_and_auth_are_not_bypassed(configuration, real_db):
    client, base, store, tenant, actor = configuration
    a, _ = create(configuration)
    b, _ = create(configuration)
    command = headers()
    assert client.delete(base + "/presets/" + a["id"], headers=command).status_code == 200
    assert client.delete(base + "/presets/" + b["id"], headers=command).status_code == 200
    pending = job(real_db, a["id"])
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE command_receipts SET result=NULL WHERE scope=%s AND command_id=%s",
            (pending["scope"], pending["command_id"]),
        )
    expired = client.delete(base + "/presets/" + a["id"], headers=command)
    assert (
        expired.status_code == 409 and expired.json()["error"]["code"] == "command_result_expired"
    )
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET eligible=false WHERE id=%s", (actor,))
    assert client.delete(base + "/presets/" + b["id"], headers=command).status_code == 401


def test_rls_and_invalid_evidence_fail_closed(configuration, real_db, tenant):
    client, base, store, own, _ = configuration
    preset, _ = create(configuration)
    store.bad_ack = True
    response = client.delete(base + "/presets/" + preset["id"], headers=headers())
    assert (
        response.status_code == 503
        and response.json()["error"]["code"] == "recovery_protection_unproven"
    )
    assert client.get(base + "/presets/" + preset["id"]).status_code == 200
    with real_db.transaction(tenant) as conn:
        conn.execute("SET LOCAL ROLE keep_api")
        assert (
            conn.execute(
                "SELECT * FROM configuration_delete_jobs WHERE tenant_id=%s", (own,)
            ).fetchall()
            == []
        )
        assert (
            conn.execute(
                "SELECT * FROM configuration_activity WHERE tenant_id=%s", (own,)
            ).fetchall()
            == []
        )
    assert (
        client.delete(
            base + "/presets/" + preset["id"],
            headers={"If-Match": "1", "Idempotency-Key": str(uuid4())},
        ).status_code
        == 422
    )
    assert (
        client.delete(
            base + "/presets/" + preset["id"], headers={**headers(), "X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )


def test_authorization_revoked_during_external_intent_cancels_before_delete(configuration, real_db):
    client, base, store, tenant, actor = configuration
    preset, _ = create(configuration)

    def revoke():
        with real_db.transaction() as conn:
            conn.execute("SELECT scope FROM authorization_guards WHERE scope='identity' FOR UPDATE")
            conn.execute("UPDATE principals SET eligible=false WHERE id=%s", (actor,))

    store.callback = revoke
    response = client.delete(base + "/presets/" + preset["id"], headers=headers())
    assert response.status_code == 401, response.text
    pending = job(real_db, preset["id"])
    assert pending["state"] == "cancelled" and pending["cancel_ack"]
    with real_db.transaction() as conn:
        row = conn.execute(
            "SELECT revision,deleted_at FROM presets WHERE id=%s", (preset["id"],)
        ).fetchone()
    assert row["revision"] == 1 and row["deleted_at"] is None
    assert [e.state for e in store.calls] == ["intent", "cancel"]


@pytest.mark.parametrize("revoked", [False, True])
def test_background_resume_rechecks_accepted_actor_authority(configuration, real_db, revoked):
    client, base, store, tenant, actor = configuration
    preset, _ = create(configuration)
    store.fail = "intent"
    assert client.delete(base + "/presets/" + preset["id"], headers=headers()).status_code == 503
    pending = job(real_db, preset["id"])
    store.fail = None
    if revoked:
        with real_db.transaction() as conn:
            conn.execute("UPDATE principals SET eligible=false WHERE id=%s", (actor,))
        with pytest.raises(Exception) as failure:
            resume_delete(real_db, store, tenant, pending["id"])
        assert getattr(failure.value, "code", None) == "access_changed"
        assert job(real_db, preset["id"])["state"] == "cancelled"
    else:
        assert resume_delete(real_db, store, tenant, pending["id"])["data"]["deleted"] is True
        assert job(real_db, preset["id"])["state"] == "final_protected"
