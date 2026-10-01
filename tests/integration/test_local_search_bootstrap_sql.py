"""Real worker SQL privileges in a disposable DB; Elasticsearch is controlled."""

from contextlib import contextmanager
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr
import pytest

from appchi.db import Database
from appchi.migrate import migrate
from appchi.search.local_bootstrap import ALIAS, PHYSICAL, REASON, bootstrap
from appchi.search.projection import index_mapping
from appchi.shared.errors import AppError

pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_database():
    admin = os.getenv("APPCHI_IDENTITY_TEST_ADMIN_URL")
    if not admin:
        pytest.skip("Requires isolated local PostgreSQL admin DSN")
    name = "appchi_local_bootstrap_test_" + uuid4().hex
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    parameters = conninfo_to_dict(admin)
    parameters["dbname"] = name
    dsn = make_conninfo(**parameters)
    database = None
    try:
        migrate(dsn, Path(__file__).resolve().parents[2] / "migrations")
        database = Database(dsn, 1)
        yield database
    finally:
        if database:
            database.close()
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_worker_bootstrap_and_restore_rejection_without_restore_write_privilege(isolated_database):
    database = isolated_database

    @contextmanager
    def transaction(tenant_id=None):
        with database.transaction(tenant_id) as conn:
            conn.execute("SET LOCAL ROLE appchi_worker")
            yield conn

    worker = SimpleNamespace(transaction=transaction)
    with transaction() as conn:
        privileges = conn.execute(
            "SELECT has_table_privilege(current_user,'restore_control','SELECT') AS can_read, "
            "has_table_privilege(current_user,'restore_control','UPDATE') AS can_write"
        ).fetchone()
        assert privileges == {"can_read": True, "can_write": False}
    # The injected DB is disposable; these settings only exercise the independent
    # local-scope validator and never connect to the interactive appchi_local DB.
    settings = SimpleNamespace(
        environment="development",
        elasticsearch_url="http://localhost:59200",
        elasticsearch_index=ALIAS,
        database_url=SecretStr("postgresql://worker@localhost/appchi_local"),
    )
    client = MagicMock()
    client.indices.exists_alias.return_value = False
    client.indices.exists.return_value = False
    client.indices.get_alias.return_value = {PHYSICAL: {"aliases": {ALIAS: {}}}}
    client.indices.get_mapping.return_value = {PHYSICAL: {"mappings": index_mapping()}}
    client.search.return_value = {"hits": {"total": {"value": 0, "relation": "eq"}}}
    evidence = {"junit_path": "controlled-fixture", "sha256": "a" * 64, "tests": 2}
    result = bootstrap(worker, client, settings, evidence)
    assert result["state"] == "active" and result["verified_empty_tenants"] == 1
    with transaction() as conn:
        gate = conn.execute(
            "SELECT ready,reason FROM capability_gates WHERE capability='search'"
        ).fetchone()
        assert gate == {"ready": True, "reason": REASON}
        original_epoch = conn.execute("SELECT index_epoch FROM search_index_control").fetchone()[
            "index_epoch"
        ]
    client.indices.exists_alias.return_value = True
    assert bootstrap(worker, client, settings, evidence)["state"] == "already_active"
    with transaction() as conn:
        assert (
            conn.execute("SELECT index_epoch FROM search_index_control").fetchone()["index_epoch"]
            == original_epoch
        )
    with database.transaction() as conn:
        conn.execute(
            "UPDATE capability_gates SET ready=false,reason='restore_reconciliation_required' WHERE capability='search'"
        )
        conn.execute("UPDATE restore_control SET fenced=true WHERE singleton")
    client.reset_mock()
    with pytest.raises(AppError) as error:
        bootstrap(worker, client, settings, evidence)
    assert error.value.code == "local_search_restore_not_supported"
    assert client.mock_calls == []
    with database.transaction() as conn:
        assert not conn.execute(
            "SELECT ready FROM capability_gates WHERE capability='search'"
        ).fetchone()["ready"]
