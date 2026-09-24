"""Real Elasticsearch/PG edge cases at rebuild snapshot and epoch boundaries."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from appchi.alerts.processor import process_one
from appchi.ingestion.gateway import GatewayIdentity
from appchi.ingestion.native import canonical
from appchi.ingestion.service import Admission
from appchi.search.rebuild import start_rebuild, rebuild_turn, cancel_rebuild
from appchi.shared.errors import AppError
from appchi.worker import projection_turn
from test_product import admit_alert, browser  # noqa: F401
from test_search_rebuild import index, real_db, state, until  # noqa: F401

pytestmark = pytest.mark.integration


def test_old_pit_cursor_rejected_after_real_alias_cutover(request, cipher, profile, v1):
    es, binding, source = request.getfixturevalue("index")
    database = request.getfixturevalue("real_db")
    api, _ = request.getfixturevalue("browser")
    tenant, first = admit_alert(database, cipher, profile, v1)
    _, second = admit_alert(database, cipher, profile, {**v1, "application": str(uuid4())})
    projection_turn(database, api.app.state.settings)
    path = f"/api/tenants/{tenant}/alerts"
    page = api.get(path, params={"limit": 1})
    assert page.status_code == 200, page.text
    cursor = page.json()["next_cursor"]
    assert cursor and page.json()["meta"]["total"] == "2"
    epoch = page.json()["meta"]["watermark"]["index_epoch"]
    job_id = start_rebuild(database, binding)
    until(database, es, binding, job_id, "complete")
    assert set(es.indices.get_alias(name=binding.alias)) == {
        state(database, job_id)["target_index"]
    }
    assert es.indices.exists(index=source)
    stale = api.get(path, params={"limit": 1, "cursor": cursor})
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "index_changed"
    fresh = api.get(path)
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["meta"]["watermark"]["index_epoch"] != epoch
    assert {row["id"] for row in fresh.json()["data"]} == {first, second}


def test_concurrent_start_and_rebuild_lease_are_fenced(request):
    es, binding, _ = request.getfixturevalue("index")
    database = request.getfixturevalue("real_db")
    job_id = start_rebuild(database, binding)
    entered, release = Event(), Event()

    class HeldIndices:
        def __getattr__(self, name):
            return getattr(es.indices, name)

        def exists(self, **kwargs):
            entered.set()
            assert release.wait(10), "Concurrent lease test failed to release external work"
            return es.indices.exists(**kwargs)

    class HeldClient:
        indices = HeldIndices()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(rebuild_turn, database, HeldClient(), binding, job_id)
        try:
            assert entered.wait(10), "Rebuild did not acquire its lease"
            for operation, code in (
                (lambda: start_rebuild(database, binding), "rebuild_in_progress"),
                (lambda: rebuild_turn(database, es, binding, job_id), "search_rebuild_busy"),
                (lambda: cancel_rebuild(database, es, binding, job_id), "search_rebuild_busy"),
            ):
                with pytest.raises(AppError) as failure:
                    operation()
                assert failure.value.code == code
        finally:
            release.set()
        assert pending.result(timeout=10) == "copying"
    job = state(database, job_id)
    assert job["hold_active"] and job["lease_token"] is None
    until(database, es, binding, job_id, "complete")


def test_tenant_created_after_snapshot_is_caught_up_with_real_native_admission(
    request, cipher, profile
):
    es, binding, _ = request.getfixturevalue("index")
    database = request.getfixturevalue("real_db")
    api, principal = request.getfixturevalue("browser")
    job_id = start_rebuild(database, binding)
    assert rebuild_turn(database, es, binding, job_id) == "copying"
    with database.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (principal,))
    created = api.post(
        "/api/tenants",
        json={
            "command_id": str(uuid4()),
            "name": "Post snapshot " + uuid4().hex,
            "initial_grants": [{"principal_id": str(principal), "role": "admin"}],
        },
    )
    assert created.status_code == 201, created.text
    tenant = created.json()["data"]["id"]
    operator, credential, consumer, external = (uuid4() for _ in range(4))
    with database.transaction(tenant) as conn:
        assert not conn.execute(
            "SELECT 1 FROM search_rebuild_positions WHERE job_id=%s AND tenant_id=%s",
            (job_id, tenant),
        ).fetchone()
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='v2_ingress'")
        conn.execute(
            "INSERT INTO operators(id,tenant_id,name,name_key,lifecycle) VALUES(%s,%s,%s,%s,'ready')",
            (operator, tenant, str(operator), str(operator)),
        )
        conn.execute(
            "INSERT INTO operator_credentials(id,tenant_id,operator_id,state,verifier,kong_consumer_id,kong_credential_id) VALUES(%s,%s,%s,'current',%s,%s,%s)",
            (credential, tenant, operator, credential.bytes, str(consumer), str(external)),
        )
    gateway = GatewayIdentity(str(consumer), str(external))
    admission = Admission(database, cipher, profile)
    code, receipt = admission.admit(
        canonical(
            {
                "application": "post-snapshot",
                "component": "worker",
                "message": "New tenant alert",
                "severity": "high",
                "status": "firing",
                "site": "local",
                "impact": "test",
                "runbook_url": "https://example.invalid/runbook",
            }
        ),
        "v2",
        gateway=gateway,
    )
    assert code == 202
    assert process_one(database, tenant)
    result = admission.receipt(receipt["request_id"], receipt["receipt_token"], "v2", gateway)
    assert result["items"][0]["processing_status"] == "applied"
    alert = result["items"][0]["alert_id"]
    until(database, es, binding, job_id, "complete")
    with database.transaction(tenant) as conn:
        progress = conn.execute(
            "SELECT * FROM search_rebuild_positions WHERE job_id=%s AND tenant_id=%s",
            (job_id, tenant),
        ).fetchone()
        assert progress["snapshot_seq"] == 0
        assert progress["copied_seq"] == progress["cutover_seq"] == 1
    found = api.get(f"/api/tenants/{tenant}/alerts")
    assert found.status_code == 200, found.text
    assert found.json()["meta"]["total"] == "1"
    assert found.json()["meta"]["updating"] is False
    assert found.json()["data"][0]["id"] == alert
    assert es.get(index=binding.alias, id=alert)["_source"]["tenant_id"] == tenant
