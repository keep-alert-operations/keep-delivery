"""Real PostgreSQL retention with controlled independent evidence failures."""
# ruff: noqa: F811

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from appchi.recovery.incidents import retire_incident
from appchi.recovery.cleanup import cleanup_incident
from appchi.recovery.evidence import DurableAck
from appchi.shared.errors import AppError, unavailable
from test_product import browser, admit_alert  # noqa: F401
from test_search_pipeline import real_db as isolated_db

pytestmark = pytest.mark.integration


@pytest.fixture
def real_db():
    for db in isolated_db.__wrapped__():
        draft = Path("migrations/0024_incident_retirement.sql.draft")
        if draft.exists():
            with db.transaction() as conn:
                conn.execute(draft.read_text(encoding="utf-8"))
        yield db


class Store:
    def __init__(self):
        self.events = []
        self.fail = None
        self.on_intent = None
        self.fail_kind = None

    def append(self, evidence):
        self.events.append(evidence)
        if evidence.state == "intent" and self.on_intent:
            callback, self.on_intent = self.on_intent, None
            callback()
        if evidence.state == self.fail and (
            self.fail_kind is None or evidence.resource_kind == self.fail_kind
        ):
            raise unavailable("controlled_evidence_outage")
        return DurableAck(evidence.id, "fixture", "checkpoint", evidence.digest)


@pytest.fixture
def incident(browser, real_db, cipher, profile, v1):
    client, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    body = {
        "command_id": str(uuid4()),
        "title": "Expired incident sensitive title",
        "severity": "high",
        "alert_ids": [alert],
    }
    path = f"/api/tenants/{tenant}/incidents"
    result = client.post(path, json=body)
    assert result.status_code == 201, result.text
    row = result.json()["data"]
    note_body = {
        "command_id": str(uuid4()),
        "expected_revision": "1",
        "text": "Expired incident note",
    }
    assert client.post(path + "/" + row["id"] + "/notes", json=note_body).status_code == 201
    result = client.post(
        path + "/" + row["id"] + "/status",
        json={"command_id": str(uuid4()), "expected_revision": "2", "status": "resolved"},
    )
    assert result.status_code == 200, result.text
    with real_db.transaction(tenant) as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
        conn.execute(
            "UPDATE incidents SET retention_at=clock_timestamp()-interval '1 second',resolved_at=clock_timestamp()-interval '91 days' WHERE id=%s",
            (row["id"],),
        )
        alert_before = conn.execute("SELECT * FROM alerts WHERE id=%s", (alert,)).fetchone()
    return client, tenant, row["id"], alert, path, body, note_body, alert_before


def test_retirement_hides_incident_and_receipt_content_without_changing_alert(incident, real_db):
    client, tenant, identity, alert, path, create_body, note_body, before = incident
    store = Store()
    assert retire_incident(real_db, store, tenant, identity)
    assert [event.state for event in store.events] == ["intent", "final"]
    assert client.get(path + "/" + identity).status_code == 404
    assert client.post(path, json=create_body).json()["error"]["code"] == "command_result_expired"
    replay = client.post(path + "/" + identity + "/notes", json=note_body)
    assert replay.status_code == 409 and "Expired incident note" not in replay.text
    with real_db.transaction(tenant) as conn:
        assert conn.execute("SELECT * FROM alerts WHERE id=%s", (alert,)).fetchone() == before
        assert conn.execute(
            "SELECT 1 FROM incident_alert_links WHERE incident_id=%s AND alert_id=%s",
            (identity, alert),
        ).fetchone()
        assert (
            conn.execute(
                "SELECT final_revision FROM incident_identities WHERE id=%s", (identity,)
            ).fetchone()["final_revision"]
            == 4
        )
        assert conn.execute("SELECT 1 FROM incidents WHERE id=%s", (identity,)).fetchone()


def test_final_outage_preserves_content_inaccessible_then_resumes_same_fence(incident, real_db):
    client, tenant, identity, _, path, *_ = incident
    store = Store()
    store.fail = "final"
    with pytest.raises(AppError):
        retire_incident(real_db, store, tenant, identity)
    assert client.get(path + "/" + identity).status_code == 404
    with real_db.transaction(tenant) as conn:
        assert conn.execute("SELECT title FROM incidents WHERE id=%s", (identity,)).fetchone()
        assert (
            conn.execute(
                "SELECT state FROM retirement_jobs WHERE resource_id=%s", (identity,)
            ).fetchone()["state"]
            == "retired"
        )
    store.fail = None
    assert retire_incident(real_db, store, tenant, identity)
    assert len({event.id for event in store.events if event.state == "final"}) == 1


def test_reopen_race_cancels_without_changing_lifecycle_or_old_links(incident, real_db):
    client, tenant, identity, _, path, *_ = incident
    store = Store()

    def reopen():
        response = client.post(
            path + "/" + identity + "/status",
            json={"command_id": str(uuid4()), "expected_revision": "3", "status": "open"},
        )
        assert response.status_code == 200, response.text

    store.on_intent = reopen
    assert not retire_incident(real_db, store, tenant, identity)
    assert [event.state for event in store.events] == ["intent", "cancel"]
    assert client.get(path + "/" + identity).json()["data"]["status"] == "open"


def test_late_retention_hold_cancels_and_active_ownership_is_never_released(incident, real_db):
    _, tenant, identity, alert, *_ = incident
    store = Store()

    def hold():
        with real_db.transaction(tenant) as conn:
            conn.execute(
                "INSERT INTO incident_retention_holds(tenant_id,incident_id,kind,reference) VALUES(%s,%s,'recovery','fixture')",
                (tenant, identity),
            )

    store.on_intent = hold
    assert not retire_incident(real_db, store, tenant, identity)
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE incident_retention_holds SET released_at=clock_timestamp() WHERE incident_id=%s",
            (identity,),
        )
        conn.execute(
            "INSERT INTO alert_active_ownership(tenant_id,alert_id,incident_id) VALUES(%s,%s,%s)",
            (tenant, alert, identity),
        )
    assert not retire_incident(real_db, store, tenant, identity)
    with real_db.transaction(tenant) as conn:
        assert conn.execute(
            "SELECT 1 FROM alert_active_ownership WHERE alert_id=%s", (alert,)
        ).fetchone()


def test_duplicate_workers_converge_on_one_final_identity(incident, real_db):
    _, tenant, identity, *_ = incident
    store = Store()
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert all(
            executor.map(lambda _: retire_incident(real_db, store, tenant, identity), range(2))
        )
    assert len({event.id for event in store.events if event.state == "final"}) == 1
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM retirement_jobs WHERE resource_id=%s", (identity,)
            ).fetchone()["n"]
            == 1
        )


def test_physical_cleanup_requires_command_fences_and_keeps_live_counterpart(incident, real_db):
    client, tenant, identity, alert, path, create_body, _, before = incident
    other = client.post(
        path, json={"command_id": str(uuid4()), "title": "Retained peer", "severity": "low"}
    ).json()["data"]
    with real_db.transaction(tenant) as conn:
        other_activity = conn.execute(
            "SELECT * FROM incident_activity WHERE incident_id=%s ORDER BY id", (other["id"],)
        ).fetchall()
    store = Store()
    assert retire_incident(real_db, store, tenant, identity)
    store.fail, store.fail_kind = "final", "command"
    with pytest.raises(AppError):
        cleanup_incident(real_db, store, tenant, identity)
    with real_db.transaction(tenant) as conn:
        assert conn.execute("SELECT 1 FROM incidents WHERE id=%s", (identity,)).fetchone()
        assert (
            conn.execute(
                "SELECT result FROM command_receipts WHERE command_id=%s",
                (create_body["command_id"],),
            ).fetchone()["result"]
            is not None
        )
    store.fail = None
    for _ in range(40):
        if cleanup_incident(real_db, store, tenant, identity, limit=2):
            break
    else:
        pytest.fail("Bounded cleanup did not make progress")
    assert client.post(path, json=create_body).json()["error"]["code"] == "command_result_expired"
    with real_db.transaction(tenant) as conn:
        assert not conn.execute("SELECT 1 FROM incidents WHERE id=%s", (identity,)).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM incident_notes WHERE incident_id=%s", (identity,)
        ).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM incident_activity WHERE incident_id=%s", (identity,)
        ).fetchone()
        assert conn.execute("SELECT * FROM alerts WHERE id=%s", (alert,)).fetchone() == before
        assert conn.execute(
            "SELECT 1 FROM incident_alert_links WHERE incident_id=%s AND alert_id=%s",
            (identity, alert),
        ).fetchone()
        assert conn.execute(
            "SELECT retired_at FROM incident_identities WHERE id=%s", (identity,)
        ).fetchone()["retired_at"]
        result = conn.execute(
            "SELECT fingerprint,result FROM command_receipts WHERE command_id=%s",
            (create_body["command_id"],),
        ).fetchone()
        assert result["result"] is None and len(result["fingerprint"]) == 32
        assert (
            conn.execute(
                "SELECT * FROM incident_activity WHERE incident_id=%s ORDER BY id", (other["id"],)
            ).fetchall()
            == other_activity
        )


def test_cleanup_is_fenced_until_final_ack_and_rejects_a_late_hold(incident, real_db):
    import psycopg

    _, tenant, identity, *_ = incident
    store = Store()
    store.fail = "final"
    with pytest.raises(AppError):
        retire_incident(real_db, store, tenant, identity)
    assert not cleanup_incident(real_db, store, tenant, identity)
    with pytest.raises(psycopg.errors.CheckViolation):
        with real_db.transaction(tenant) as conn:
            conn.execute(
                "INSERT INTO incident_retention_holds(tenant_id,incident_id,kind,reference) VALUES(%s,%s,'late','fixture')",
                (tenant, identity),
            )


def test_scheduled_worker_connects_retirement_and_cleanup(incident, real_db):
    from appchi.recovery.runtime import incident_retirement_turn

    _, tenant, identity, *_ = incident
    store = Store()
    for _ in range(20):
        page = incident_retirement_turn(real_db, store)
        if not page.progress_pending:
            break
    with real_db.transaction(tenant) as conn:
        assert (
            conn.execute(
                "SELECT state FROM retirement_jobs WHERE resource_id=%s", (identity,)
            ).fetchone()["state"]
            == "cleaned"
        )


def test_cancelled_hold_attempt_cannot_cancel_later_success(incident, real_db):
    _, tenant, identity, *_ = incident
    store = Store()

    def hold():
        with real_db.transaction(tenant) as conn:
            conn.execute(
                "INSERT INTO incident_retention_holds(tenant_id,incident_id,kind,reference) VALUES(%s,%s,'recovery','retry')",
                (tenant, identity),
            )

    store.on_intent = hold
    assert not retire_incident(real_db, store, tenant, identity)
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE incident_retention_holds SET released_at=clock_timestamp() WHERE incident_id=%s",
            (identity,),
        )
    assert retire_incident(real_db, store, tenant, identity)
    assert [event.state for event in store.events] == ["intent", "cancel", "intent", "final"]
    first, cancel, second, final = store.events
    assert first.digest == cancel.digest and second.digest == final.digest
    assert first.digest != second.digest
    assert first.revision == second.revision


def test_counterpart_link_expires_when_alert_retires_after_incident_cleanup(incident, real_db):
    from appchi.recovery.retirement import retire_alert
    from appchi.recovery.runtime import incident_retirement_turn

    client, tenant, identity, alert, *_ = incident
    store = Store()
    assert retire_incident(real_db, store, tenant, identity)
    for _ in range(20):
        if cleanup_incident(real_db, store, tenant, identity):
            break
    with real_db.transaction(tenant) as conn:
        assert not conn.execute("SELECT 1 FROM incidents WHERE id=%s", (identity,)).fetchone()
        assert conn.execute(
            "SELECT 1 FROM incident_alert_links WHERE incident_id=%s", (identity,)
        ).fetchone()
    alert_path = f"/api/tenants/{tenant}/alerts/{alert}"
    alert_revision = client.get(alert_path).json()["data"]["revision"]
    response = client.post(
        alert_path + "/commands",
        json={
            "command_id": str(uuid4()),
            "expected_revision": alert_revision,
            "kind": "set_status",
            "value": "resolved",
        },
    )
    assert response.status_code == 200, response.text
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE alerts SET retention_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (alert,),
        )
    store.fail, store.fail_kind = "final", "alert"
    with pytest.raises(AppError):
        retire_alert(real_db, store, tenant, alert)
    incident_retirement_turn(real_db, store)
    with real_db.transaction(tenant) as conn:
        assert conn.execute(
            "SELECT 1 FROM incident_alert_links WHERE incident_id=%s", (identity,)
        ).fetchone()
    store.fail = None
    assert retire_alert(real_db, store, tenant, alert)
    incident_retirement_turn(real_db, store)
    with real_db.transaction(tenant) as conn:
        assert not conn.execute(
            "SELECT 1 FROM incident_alert_links WHERE incident_id=%s", (identity,)
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM incident_identities WHERE id=%s AND retired_at IS NOT NULL", (identity,)
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM alert_identities WHERE id=%s AND retired_at IS NOT NULL", (alert,)
        ).fetchone()
