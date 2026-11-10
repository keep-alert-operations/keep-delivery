import pytest
import psycopg
from keep.ingestion.native import canonical
from keep.ingestion.service import Admission
from keep.alerts.processor import process_one

pytestmark = pytest.mark.integration


def test_rls_requires_scope_and_partition_is_not_a_bypass(real_db, cipher, profile, v1, tenant):
    service = Admission(real_db, cipher, profile)
    service.admit(canonical(v1), "v1")
    with real_db.transaction() as conn:
        global_id = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    while process_one(real_db, global_id):
        pass
    with real_db.transaction() as conn:
        conn.execute("SET LOCAL ROLE keep_api")
        conn.execute("SELECT set_config('keep.tenant_id',%s,true)", (str(tenant),))
        assert conn.execute("SELECT count(*) AS n FROM alerts").fetchone()["n"] == 0
        conn.execute("SELECT set_config('keep.tenant_id',%s,true)", (str(global_id),))
        assert conn.execute("SELECT count(*) AS n FROM alerts").fetchone()["n"] > 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with real_db.transaction() as conn:
            conn.execute("SET LOCAL ROLE keep_api")
            conn.execute("SELECT * FROM alert_source_events_0")


def test_migrations_are_idempotent_and_detect_tampering(real_db, tmp_path):
    from keep.migrate import migrate, migration_directory
    import os

    dsn = os.environ["KEEP_TEST_DATABASE_URL"]
    migrate(dsn, migration_directory())
    with pytest.raises(RuntimeError, match="absent"):
        migrate(dsn, tmp_path)
