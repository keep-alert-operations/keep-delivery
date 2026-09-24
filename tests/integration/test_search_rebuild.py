"""Real Elasticsearch alias cutover and PostgreSQL rebuild state transitions."""

# ruff: noqa: F811
import os
from pathlib import Path
from uuid import uuid4

from elasticsearch import Elasticsearch
import psycopg
import pytest

from appchi.search.rebuild import RebuildBinding, bind_serving_index, start_rebuild, rebuild_turn
from appchi.search.projection import index_mapping
from appchi.shared.errors import AppError
from appchi.worker import projection_turn
from test_product import browser, admit_alert  # noqa: F401
from test_search_pipeline import real_db as isolated_db

pytestmark = pytest.mark.integration


@pytest.fixture
def real_db():
    for db in isolated_db.__wrapped__():
        draft = Path("migrations/0025_search_rebuild.sql.draft")
        if draft.exists():
            with db.transaction() as conn:
                conn.execute(draft.read_text(encoding="utf-8-sig"))
        yield db


@pytest.fixture
def index(browser, real_db):
    url = os.getenv("APPCHI_TEST_ELASTICSEARCH_URL")
    if not url:
        pytest.skip("Requires real Elasticsearch")
    prefix = "appchi-rebuild-test-" + uuid4().hex + "-"
    alias, source = prefix + "alias", prefix + "source"
    binding = RebuildBinding(
        alias=alias,
        index_prefix=prefix,
        capability_evidence="isolated-local-ES9.0.0",
        atomic_alias_switch=True,
        exclusive_alias_writer=True,
        max_document_bytes=1048576,
        index_settings={"number_of_shards": 1, "number_of_replicas": 0},
    )
    with Elasticsearch(url, request_timeout=5, max_retries=0) as client:
        client.indices.create(
            index=source,
            mappings=index_mapping(),
            settings=binding.index_settings,
            aliases={alias: {"is_write_index": True}},
        )
        try:
            bind_serving_index(real_db, client, binding, str(uuid4()))
            browser[0].app.state.settings.elasticsearch_url = url
            browser[0].app.state.settings.elasticsearch_index = alias
            with real_db.transaction() as conn:
                conn.execute("UPDATE capability_gates SET ready=true WHERE capability='search'")
            yield client, binding, source
        finally:
            # Exact owned index names from this isolated unique namespace only.
            indices = client.indices.get(index=prefix + "*")
            for name in indices:
                assert name.startswith(prefix)
                client.indices.delete(index=name)


def state(db, identity):
    with db.transaction() as conn:
        return conn.execute("SELECT * FROM search_rebuild_jobs WHERE id=%s", (identity,)).fetchone()


def until(db, client, binding, identity, wanted):
    for _ in range(50):
        if state(db, identity)["state"] == wanted:
            return
        rebuild_turn(db, client, binding, identity)
    pytest.fail("Rebuild did not reach " + wanted)


def test_rebuild_copy_catchup_cutover_and_postcutover_queue(
    index, browser, real_db, cipher, profile, v1
):
    es, binding, source = index
    api, _ = browser
    settings = api.app.state.settings
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}/alerts"
    projection_turn(real_db, settings)
    initial = api.get(base)
    assert initial.status_code == 200 and initial.json()["meta"]["total"] == "1"
    identity = start_rebuild(real_db, binding)
    rebuild_turn(real_db, es, binding, identity)
    with pytest.raises(psycopg.errors.CheckViolation):
        with real_db.transaction() as conn:
            conn.execute("DELETE FROM projection_work WHERE tenant_id=%s", (tenant,))
    response = api.post(
        base + "/" + alert + "/commands",
        json={
            "command_id": str(uuid4()),
            "expected_revision": "1",
            "kind": "set_acknowledged",
            "value": True,
        },
    )
    assert response.status_code == 200, response.text
    until(real_db, es, binding, identity, "switching")
    assert api.get(base).status_code == 503
    with pytest.raises(AppError):
        projection_turn(real_db, settings)
    note = api.post(
        base + "/" + alert + "/notes",
        json={
            "command_id": str(uuid4()),
            "expected_revision": "2",
            "text": "After cutover snapshot",
        },
    )
    assert note.status_code == 201, note.text
    before = es.get(index=source, id=alert)["_source"]
    assert rebuild_turn(real_db, es, binding, identity) == "complete"
    job = state(real_db, identity)
    assert not job["hold_active"]
    assert set(es.indices.get_alias(name=binding.alias)) == {job["target_index"]}
    assert es.indices.exists(index=source)
    projected = api.get(base, params={"cel": "alert.acknowledged == true"})
    assert projected.status_code == 200, projected.text
    assert projected.json()["meta"]["updating"] is True
    assert projected.json()["meta"]["total"] == "1"
    projection_turn(real_db, settings)
    found = api.get(base, params={"q": "snapshot"})
    assert found.status_code == 200 and found.json()["meta"]["total"] == "1", found.text
    assert found.json()["meta"]["updating"] is False
    assert es.get(index=source, id=alert)["_source"] == before


def test_uncertain_switch_reconciles_alias_before_reopening_search(
    index, browser, real_db, cipher, profile, v1
):
    es, binding, _ = index
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    identity = start_rebuild(real_db, binding)
    until(real_db, es, binding, identity, "switching")

    class UncertainIndices:
        def __getattr__(self, name):
            return getattr(es.indices, name)

        def update_aliases(self, **kwargs):
            es.indices.update_aliases(**kwargs)
            raise TimeoutError("Response lost after atomic alias switch")

    class UncertainClient:
        indices = UncertainIndices()

    with pytest.raises(TimeoutError):
        rebuild_turn(real_db, UncertainClient(), binding, identity)
    assert state(real_db, identity)["state"] == "switching"
    assert browser[0].get(f"/api/tenants/{tenant}/alerts").status_code == 503
    assert rebuild_turn(real_db, es, binding, identity) == "complete"
    assert browser[0].get(f"/api/tenants/{tenant}/alerts").json()["data"][0]["id"] == alert


def test_corrupt_target_blocks_validation_without_mutating_source(
    index, browser, real_db, cipher, profile, v1
):
    es, binding, source = index
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    identity = start_rebuild(real_db, binding)
    until(real_db, es, binding, identity, "validating")
    job = state(real_db, identity)
    es.index(
        index=job["target_index"],
        id=str(uuid4()),
        document={
            "id": str(uuid4()),
            "tenant_id": tenant,
            "projection_version": 1,
            "deleted": True,
        },
        refresh="wait_for",
    )
    with pytest.raises(AppError):
        rebuild_turn(real_db, es, binding, identity)
    assert state(real_db, identity)["hold_active"]
    assert set(es.indices.get_alias(name=binding.alias)) == {source}
    assert browser[0].get(f"/api/tenants/{tenant}/alerts").status_code == 503


def test_retired_alert_before_indexing_is_covered_by_final_tombstone(
    index, browser, real_db, cipher, profile, v1
):
    from appchi.recovery.retirement import retire_alert
    from test_incident_retirement import Store

    es, binding, source = index
    api, _ = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    result = api.post(
        f"/api/tenants/{tenant}/alerts/{alert}/commands",
        json={
            "command_id": str(uuid4()),
            "expected_revision": "1",
            "kind": "set_status",
            "value": "resolved",
        },
    )
    assert result.status_code == 200, result.text
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
        conn.execute(
            "UPDATE alerts SET retention_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (alert,),
        )
    assert retire_alert(real_db, Store(), tenant, alert)
    projection_turn(real_db, api.app.state.settings)
    tombstone = es.get(index=source, id=alert)["_source"]
    assert tombstone["deleted"] is True and "display" not in tombstone
    with real_db.transaction() as conn:
        progress = conn.execute(
            "SELECT searchable_seq,projection_seq FROM tenant_view_state JOIN tenant_work_state USING(tenant_id) WHERE tenant_id=%s",
            (tenant,),
        ).fetchone()
        assert progress["searchable_seq"] == progress["projection_seq"] == 3
    identity = start_rebuild(real_db, binding)
    until(real_db, es, binding, identity, "complete")
    assert es.get(index=binding.alias, id=alert)["_source"] == tombstone
    assert api.get(f"/api/tenants/{tenant}/alerts").json()["meta"]["total"] == "0"


def test_rebuild_preserves_independent_gate_and_cancellation_resumes_old_epoch(
    index, browser, real_db, cipher, profile, v1
):
    from appchi.search.rebuild import cancel_rebuild

    es, binding, source = index
    tenant, _ = admit_alert(real_db, cipher, profile, v1)
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE capability_gates SET ready=false,reason='recovery_unreconciled' WHERE capability='search'"
        )
    identity = start_rebuild(real_db, binding)
    until(real_db, es, binding, identity, "validating")
    assert cancel_rebuild(real_db, es, binding, identity) == "cancelled"
    assert set(es.indices.get_alias(name=binding.alias)) == {source}
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT reason FROM capability_gates WHERE capability='search'"
            ).fetchone()["reason"]
            == "recovery_unreconciled"
        )
        assert not conn.execute("SELECT writers_paused FROM search_index_control").fetchone()[
            "writers_paused"
        ]
    identity = start_rebuild(real_db, binding)
    until(real_db, es, binding, identity, "complete")
    assert browser[0].get(f"/api/tenants/{tenant}/alerts").status_code == 503
    with real_db.transaction() as conn:
        gate = conn.execute("SELECT * FROM capability_gates WHERE capability='search'").fetchone()
        assert not gate["ready"] and gate["reason"] == "recovery_unreconciled"


def test_new_tenant_inherits_epoch_under_restricted_api_role(index, browser, real_db):
    from contextlib import contextmanager

    es, binding, _ = index
    api, principal = browser
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (principal,))
    identity = start_rebuild(real_db, binding)
    until(real_db, es, binding, identity, "switching")
    original = real_db.transaction

    @contextmanager
    def restricted(tenant_id=None):
        with original(tenant_id) as conn:
            conn.execute("SET LOCAL ROLE appchi_api")
            yield conn

    real_db.transaction = restricted
    try:
        created = api.post(
            "/api/tenants",
            json={
                "command_id": str(uuid4()),
                "name": "During cutover " + uuid4().hex,
                "initial_grants": [{"principal_id": str(principal), "role": "admin"}],
            },
        )
        assert created.status_code == 201, created.text
    finally:
        real_db.transaction = original
    tenant = created.json()["data"]["id"]
    assert rebuild_turn(real_db, es, binding, identity) == "complete"
    with real_db.transaction() as conn:
        inherited = conn.execute(
            "SELECT index_epoch,searchable_seq FROM tenant_view_state WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert inherited["index_epoch"] == state(real_db, identity)["target_epoch"]
        assert inherited["searchable_seq"] == 0


def test_missing_snapshot_fails_before_copy(index, real_db, cipher, profile, v1):
    es, binding, _ = index
    tenant, _ = admit_alert(real_db, cipher, profile, v1)
    with real_db.transaction() as conn:
        conn.execute("DELETE FROM projection_work WHERE tenant_id=%s", (tenant,))
    identity = start_rebuild(real_db, binding)
    with pytest.raises(AppError) as failure:
        rebuild_turn(real_db, es, binding, identity)
    assert failure.value.code == "search_rebuild_snapshot_gap"
    assert state(real_db, identity)["hold_active"]
