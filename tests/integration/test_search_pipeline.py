"""Real PG/source/indexer/API and Elasticsearch; synthetic auth in a disposable DB."""

# ruff: noqa: F811
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from elasticsearch import Elasticsearch
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from appchi.db import Database
from appchi.migrate import migrate
from appchi.search.projection import index_mapping
from appchi.worker import projection_turn
from test_product import browser, admit_alert  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def real_db():
    admin = os.getenv("APPCHI_IDENTITY_TEST_ADMIN_URL")
    if not admin:
        pytest.skip("Requires isolated PostgreSQL admin DSN and real Elasticsearch URL")
    name = "appchi_pipeline_test_" + uuid4().hex
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    params = conninfo_to_dict(admin)
    params["dbname"] = name
    dsn = make_conninfo(**params)
    db = None
    try:
        migrate(dsn, Path(__file__).resolve().parents[3] / "migrations")
        db = Database(dsn)
        yield db
    finally:
        if db:
            db.close()
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_durable_source_projection_search_and_manual_refresh(browser, real_db, cipher, profile, v1):
    if not os.getenv("APPCHI_TEST_ELASTICSEARCH_URL"):
        pytest.skip("Requires real Elasticsearch URL")
    client, principal = browser
    settings = client.app.state.settings
    settings.elasticsearch_url = os.environ["APPCHI_TEST_ELASTICSEARCH_URL"]
    settings.elasticsearch_index = "appchi-pipeline-test-" + uuid4().hex
    with Elasticsearch(settings.elasticsearch_url, request_timeout=5, max_retries=0) as es:
        es.indices.create(
            index=settings.elasticsearch_index,
            mappings=index_mapping(),
            settings={"number_of_shards": 1, "number_of_replicas": 0},
        )
        try:
            tenant, alert = admit_alert(real_db, cipher, profile, v1)
            with real_db.transaction() as conn:
                conn.execute("UPDATE capability_gates SET ready=true WHERE capability='search'")
                conn.execute(
                    "UPDATE tenant_view_state SET index_epoch=%s WHERE tenant_id=%s",
                    (uuid4().hex, tenant),
                )
            projection_turn(real_db, settings)
            base = f"/api/tenants/{tenant}/alerts"
            initial = client.get(base)
            assert initial.status_code == 200, initial.text
            assert initial.json()["meta"]["total"] == "1"
            assert initial.json()["meta"]["updating"] is False
            assert initial.json()["data"][0]["id"] == alert
            assert initial.json()["data"][0]["display_status"] == "firing"
            response = client.post(
                base + "/" + alert + "/commands",
                json={
                    "command_id": str(uuid4()),
                    "expected_revision": "1",
                    "kind": "set_assignee",
                    "value": str(principal),
                },
            )
            assert response.status_code == 200, response.text
            lagging = client.get(base)
            assert lagging.status_code == 200, lagging.text
            assert lagging.json()["meta"]["updating"] is True
            projection_turn(real_db, settings)
            current = client.get(base, params={"cel": 'alert.display_status == "acknowledged"'})
            assert current.status_code == 200, current.text
            assert current.json()["meta"]["total"] == "1"
            assert current.json()["meta"]["updating"] is False
            assert current.json()["data"][0]["display_status"] == "acknowledged"
            note = client.post(
                base + "/" + alert + "/notes",
                json={
                    "command_id": str(uuid4()),
                    "expected_revision": "2",
                    "text": "Pipeline searchable note",
                },
            )
            assert note.status_code == 201, note.text
            projection_turn(real_db, settings)
            found = client.get(base, params={"q": "searchable"})
            assert found.status_code == 200, found.text
            assert found.json()["meta"]["total"] == "1"
            with real_db.transaction() as conn:
                progress = conn.execute(
                    "SELECT v.searchable_seq,w.projection_seq FROM tenant_view_state v JOIN tenant_work_state w USING(tenant_id) WHERE tenant_id=%s",
                    (tenant,),
                ).fetchone()
                assert progress["searchable_seq"] == progress["projection_seq"] == 3
        finally:
            es.indices.delete(index=settings.elasticsearch_index)
