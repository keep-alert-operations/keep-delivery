# ruff: noqa: F811 -- imported reusable isolated database/config fixtures
"""Real PG publication with controlled HTTP; does not qualify a live Keycloak."""

from uuid import uuid4

import httpx
import pytest

from test_identity_sessions import identity_db  # noqa: F401
from local_keycloak_fixture import Realm, config, uid  # noqa: F401

from appchi.identity.local_keycloak_worker import run_cycle

pytestmark = pytest.mark.integration


def test_local_directory_bootstrap_disable_outage_delete_recreate_and_restore(identity_db, config):
    database, _ = identity_db
    realm = Realm()
    transport = httpx.MockTransport(realm)

    def read_user(object_id):
        with database.transaction() as conn:
            return conn.execute(
                "SELECT * FROM principals WHERE directory_source=%s AND object_id=%s",
                (config.source, object_id),
            ).fetchone()

    def gate():
        with database.transaction() as conn:
            return conn.execute(
                "SELECT * FROM capability_gates WHERE capability='human_auth'"
            ).fetchone()

    evidence, initial = run_cycle(database, config, bootstrap=True, transport=transport)
    assert evidence.complete and initial.accepted and gate()["ready"]
    admin = read_user(uid(1))
    assert admin["super_admin"] and admin["eligible"]
    tenant = uuid4()
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
            (tenant, str(tenant), str(tenant)),
        )
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'admin')",
            (tenant, admin["id"]),
        )
    _, unchanged = run_cycle(database, config, transport=transport)
    assert unchanged.semantic_revision == initial.semantic_revision

    realm.fail = "/groups"
    _, unavailable = run_cycle(database, config, transport=transport)
    assert not unavailable.accepted and read_user(uid(1))["super_admin"]
    realm.users[0]["enabled"] = False
    _, negative = run_cycle(database, config, transport=transport)
    assert not negative.accepted
    assert not read_user(uid(1))["eligible"] and not read_user(uid(1))["super_admin"]

    realm.fail = None
    realm.users[0]["enabled"] = True
    _, restored_identity = run_cycle(database, config, transport=transport)
    assert restored_identity.accepted and read_user(uid(1))["super_admin"]
    with database.transaction() as conn:
        conn.execute(
            "UPDATE capability_gates SET ready=false,reason='restore_reconciliation_required' "
            "WHERE capability='human_auth'"
        )
    run_cycle(database, config, bootstrap=True, transport=transport)
    assert not gate()["ready"] and gate()["reason"] == "restore_reconciliation_required"

    realm.users = [row for row in realm.users if row["id"] != uid(1)]
    realm.children[uid(10)] = []
    realm.members[uid(10)] = []
    replacement = {"id": uid(99), "username": "admin", "enabled": True}
    realm.users.append(replacement)
    evidence, deleted = run_cycle(database, config, transport=transport)
    assert evidence.complete and deleted.accepted
    old = read_user(uid(1))
    assert old["deleted"] and not old["eligible"] and not old["super_admin"]
    assert read_user(uid(11))["deleted"] and read_user(uid(11))["kind"] == "team"
    new = read_user(uid(99))
    assert new["id"] != old["id"] and not new["super_admin"]
    with database.transaction() as conn:
        assert (
            conn.execute(
                "SELECT principal_id FROM tenant_grants WHERE tenant_id=%s", (tenant,)
            ).fetchone()["principal_id"]
            == old["id"]
        )
