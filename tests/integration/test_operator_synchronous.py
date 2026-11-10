# ruff: noqa: F811
"""Real SQL/HTTP with verified remote fixtures, no provisioning worker."""
from contextlib import contextmanager
from uuid import uuid4

import pytest

from keep.operators import synchronous
from keep.shared.errors import unavailable
from test_product import browser  # noqa: F401
from test_search_pipeline import real_db  # noqa: F401
from test_operator_repository import VerifiedFixture

pytestmark = pytest.mark.integration


@pytest.fixture
def api(browser, real_db, tenant, monkeypatch):
    client, actor = browser
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (actor,))
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='v2_ingress'")
    adapter = VerifiedFixture()
    @contextmanager
    def runtime(request):
        yield real_db, adapter
    monkeypatch.setattr(synchronous, "runtime", runtime)
    return client, f"/api/tenants/{tenant}/operators", adapter


def test_create_finishes_both_gateway_phases_and_replay_creates_nothing(api, real_db):
    client, base, _ = api
    body = {"command_id": str(uuid4()), "name": "sync-" + str(uuid4())}
    response = client.post(base, json=body)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["operation"]["state"] == "completed"
    assert data["operator"]["lifecycle"] == "ready"
    replay = client.post(base, json=body)
    assert replay.status_code == 201
    assert replay.json()["data"]["operator"]["id"] == data["operator"]["id"]
    with real_db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM operator_credentials WHERE operator_id=%s",
                            (data["operator"]["id"],)).fetchone()["n"] == 1


def test_failed_remote_attempt_returns_failure_and_explicit_retry_resumes_same_key(api, monkeypatch):
    client, base, adapter = api
    original = adapter.execute
    def fail(work):
        raise unavailable("remote_outcome_uncertain")
    monkeypatch.setattr(adapter, "execute", fail)
    body = {"command_id": str(uuid4()), "name": "failed-" + str(uuid4())}
    response = client.post(base, json=body)
    assert response.status_code == 503, response.text
    fields = response.json()["error"]["fields"]
    assert fields["outcome_may_be_partial"]
    path = base + "/" + fields["operator_id"]
    pending = client.get(path).json()["data"]
    assert pending["lifecycle"] == "setup_pending"
    assert not pending["current_key_available"]
    monkeypatch.setattr(adapter, "execute", original)
    retry = client.post(path + "/operations/" + fields["operation_id"] + "/retry",
                        json={"command_id": str(uuid4()), "expected_revision": pending["revision"]})
    assert retry.status_code == 200, retry.text
    assert retry.json()["data"]["operation"]["state"] == "completed"
    assert retry.json()["data"]["operator"]["keys"][0]["id"] == pending["keys"][0]["id"]


def test_request_budget_exhaustion_reports_operation_without_background_work(api, monkeypatch):
    client, base, adapter = api
    monkeypatch.setattr(synchronous, "REQUEST_BUDGET_SECONDS", 0)
    monkeypatch.setattr(adapter, "execute", lambda _: pytest.fail("No time to start remote work"))
    response = client.post(base, json={"command_id": str(uuid4()), "name": str(uuid4())})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "operator_request_incomplete"


def test_deleted_operator_final_policy_failure_remains_retryable(api, monkeypatch, real_db):
    client, base, adapter = api
    created = client.post(base, json={"command_id": str(uuid4()), "name": str(uuid4())}).json()["data"]["operator"]
    path = base + "/" + created["id"]
    original = adapter.execute
    def fail_final(work):
        with real_db.transaction() as conn:
            row = conn.execute("SELECT lifecycle FROM operators WHERE id=%s", (created["id"],)).fetchone()
        if row["lifecycle"] == "deleted":
            raise unavailable("remote_outcome_uncertain")
        return original(work)
    monkeypatch.setattr(adapter, "execute", fail_final)
    response = client.delete(path, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"'+created["revision"]+'"'})
    assert response.status_code == 503, response.text
    current = client.get(path)
    assert current.status_code == 200, current.text
    pending = current.json()["data"]
    assert pending["lifecycle"] == "deleted"
    assert any(row["id"] == created["id"] for row in client.get(base).json()["data"])
    monkeypatch.setattr(adapter, "execute", original)
    retried = client.post(path + "/operations/" + pending["operation"]["id"] + "/retry",
                          json={"command_id": str(uuid4()), "expected_revision": pending["revision"]})
    assert retried.status_code == 200, retried.text
    assert client.get(path).status_code == 404


def test_cancel_initial_setup_deletion_finishes_resumed_setup_in_same_request(api, monkeypatch):
    client, base, adapter = api
    original = adapter.execute
    def fail(work):
        raise unavailable("remote_outcome_uncertain")
    monkeypatch.setattr(adapter, "execute", fail)
    failed = client.post(base, json={"command_id": str(uuid4()), "name": str(uuid4())})
    path = base + "/" + failed.json()["error"]["fields"]["operator_id"]
    current = client.get(path).json()["data"]
    deletion = client.delete(path, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"'+current["revision"]+'"'})
    assert deletion.status_code == 503
    current = client.get(path).json()["data"]
    monkeypatch.setattr(adapter, "execute", original)
    response = client.post(path + "/operations/" + current["operation"]["id"] + "/cancel",
                           json={"command_id": str(uuid4()), "expected_revision": current["revision"]})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["operator"]["lifecycle"] == "ready"
    assert response.json()["data"]["operation"]["state"] == "completed"
