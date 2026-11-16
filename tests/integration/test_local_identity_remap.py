"""One-time local remap of keycloak-local principals to ('adapi', samAccountName).

The remap is an UPDATE in place, so every grant, membership, edge, checkpoint and
session keeps pointing at the same principal id. It runs after migration 0070 and
before the first ADAPI refresh, and refuses rather than merging when an adapi row for
the same name already exists.

Requirements: REV-73; identity configuration 14 (local directory development
exception); plan "One-time local remap" and A18.
"""

import copy
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

from keep.migrate import migrate

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("local_identity_remap",
                                              ROOT / "scripts/local_identity.py")
local_identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_identity)

USERS = {
    "local-admin": ("0f6c8f9e-2b8a-4d8e-9a4f-7f8c3a1e0001", "Local Administrator",
                    "Keep Administrators"),
    "local-editor": ("0f6c8f9e-2b8a-4d8e-9a4f-7f8c3a1e0002", "Local Editor", "Developers"),
    "local-viewer": ("0f6c8f9e-2b8a-4d8e-9a4f-7f8c3a1e0003", "Local Viewer", "Observers"),
}
GROUPS = {
    "Keep Administrators": "5d1d3a52-8c11-4f0e-8a3b-1b2c3d4e0001",
    "Developers": "5d1d3a52-8c11-4f0e-8a3b-1b2c3d4e0002",
    "Observers": "5d1d3a52-8c11-4f0e-8a3b-1b2c3d4e0003",
}
# A group someone made by hand in the old local realm; nothing names it in credentials.
UNMAPPED = ("5d1d3a52-8c11-4f0e-8a3b-1b2c3d4e0099", "Platform Team")
LEGACY = {
    "users": {name: {"id": object_id, "password": f"{name}-pw", "label": label, "group": group}
              for name, (object_id, label, group) in USERS.items()},
    "groups": GROUPS,
    "wrapping_key": "d3JhcHBpbmcta2V5LWZvci10aGUtdGVzdC1vbmx5LTAwMDA=",
    "deployment_epoch": "7e0d2c3b-0a49-4a9f-8f2a-000000000001",
    "api_password": "api-role", "worker_password": "worker-role",
}
FK_TABLES = {
    "tenant_grants": "principal_id, tenant_id, role",
    "directory_memberships": "team_id, user_id",
    "directory_edges": "parent_id, child_id",
    "directory_root_checkpoints": "root_id, generation, semantic_digest",
    "auth_sessions": "handle_hash, principal_id, deployment_epoch",
}


def dsn_for(admin, name):
    params = conninfo_to_dict(admin)
    params["dbname"] = name
    return make_conninfo(**params)


@pytest.fixture(scope="module")
def cluster():
    admin = os.environ.get("KEEP_IDENTITY_TEST_ADMIN_URL")
    if not admin:
        pytest.skip("Set KEEP_IDENTITY_TEST_ADMIN_URL to an isolated local PostgreSQL cluster"
                    " with CREATE DATABASE permission")
    template = "keep_remap_template_" + uuid4().hex
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(template)))
    created = [template]
    try:
        migrate(dsn_for(admin, template))

        def database(migrated=True):
            name = "keep_remap_test_" + uuid4().hex
            statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
            if migrated:
                statement += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
            with psycopg.connect(admin, autocommit=True) as conn:
                conn.execute(statement)
            created.append(name)
            return dsn_for(admin, name)

        yield database
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            for name in reversed(created):
                conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)))


def files():
    creds = local_identity.complete(local_identity.moved_credentials(copy.deepcopy(LEGACY)))
    return creds, local_identity.directory_data(creds)


def seed(dsn, extra_adapi=()):
    """Legacy principals with references in every kind of table the remap must keep."""
    ids = {}
    with psycopg.connect(dsn) as conn:
        global_id = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()[0]

        def principal(source, object_id, kind, label, **flags):
            row = conn.execute(
                "INSERT INTO principals(directory_source, object_id, kind, display_name,"
                " eligible, super_admin, evidence_generation, verified_at)"
                " VALUES (%s,%s,%s,%s,true,%s,3,clock_timestamp()) RETURNING id",
                (source, object_id, kind, label, flags.get("super_admin", False)),
            ).fetchone()
            return row[0]

        for name, (object_id, label, _) in USERS.items():
            ids[name] = principal("keycloak-local", object_id, "user", label,
                                  super_admin=name == "local-admin")
        for label, object_id in GROUPS.items():
            ids[label] = principal("keycloak-local", object_id, "team", label)
        ids["unmapped"] = principal("keycloak-local", UNMAPPED[0], "team", UNMAPPED[1])
        # An unrelated person already published from ADAPI must not block the remap.
        ids["other"] = principal("adapi", "someone-else", "user", "Someone Else")
        for object_id, kind in extra_adapi:
            principal("adapi", object_id, kind, object_id)
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id, principal_id, role) VALUES"
            " (%s,%s,'admin'), (%s,%s,'view'), (%s,%s,'edit')",
            (global_id, ids["Keep Administrators"], global_id, ids["local-viewer"],
             global_id, ids["unmapped"]),
        )
        conn.execute(
            "INSERT INTO directory_memberships(team_id, user_id) VALUES (%s,%s), (%s,%s)",
            (ids["Keep Administrators"], ids["local-admin"], ids["Developers"],
             ids["local-editor"]),
        )
        conn.execute(
            "INSERT INTO directory_edges(parent_id, child_id) VALUES (%s,%s), (%s,%s)",
            (ids["Keep Administrators"], ids["local-admin"], ids["Observers"],
             ids["local-viewer"]),
        )
        conn.execute(
            "INSERT INTO directory_root_checkpoints(root_id, generation, verified_at,"
            " semantic_digest) VALUES (%s, 3, clock_timestamp(), 'digest-admins')",
            (ids["Keep Administrators"],),
        )
        conn.execute(
            "INSERT INTO auth_sessions(handle_hash, principal_id, csrf_hash, csrf_cipher,"
            " auth_time, absolute_at, deployment_epoch) VALUES (%s,%s,%s,%s,clock_timestamp(),"
            " clock_timestamp() + interval '24 hours', %s)",
            (b"handle-admin", ids["local-admin"], b"csrf-hash", b"csrf-cipher",
             LEGACY["deployment_epoch"]),
        )
    return ids


def snapshot(dsn):
    with psycopg.connect(dsn) as conn:
        state = {
            "principals": conn.execute(
                "SELECT id, directory_source, object_id, kind, display_name, eligible, deleted,"
                " super_admin, evidence_generation FROM principals ORDER BY id").fetchall(),
            "revision": conn.execute(
                "SELECT directory_revision FROM authorization_guards WHERE scope='identity'"
            ).fetchone()[0],
        }
        for table, columns in FK_TABLES.items():
            state[table] = conn.execute(
                sql.SQL("SELECT {} FROM {} ORDER BY 1, 2").format(
                    sql.SQL(columns), sql.Identifier(table))).fetchall()
    return state


def keys(dsn):
    with psycopg.connect(dsn) as conn:
        return {row[0]: (row[1], row[2]) for row in conn.execute(
            "SELECT id, directory_source, object_id FROM principals")}


def run(dsn, **kwargs):
    creds, directory = files()
    lines = []
    result = local_identity.remap(dsn=dsn, creds=kwargs.pop("creds", creds),
                                  directory=kwargs.pop("directory", directory),
                                  out=lines.append, **kwargs)
    return result, lines


def test_dry_run_lists_the_plan_and_changes_nothing(cluster):
    """Requirements: REV-73; plan "One-time local remap" (--dry-run)."""
    dsn = cluster()
    seed(dsn)
    before = snapshot(dsn)
    result, lines = run(dsn, dry_run=True)
    assert snapshot(dsn) == before
    assert not result["applied"] and len(result["planned"]) == 6
    assert lines[0] == ("Principal remap keycloak-local -> adapi (dry run): 6 planned,"
                        " 1 left unmapped.")
    assert f"  user {USERS['local-admin'][0]} -> local-admin" in lines
    assert f"  team {GROUPS['Developers']} -> keep-developers" in lines
    assert any(line.startswith(f"  left unmapped: team {UNMAPPED[0]} \"Platform Team\"")
               for line in lines)
    assert lines[-1] == "Dry run: nothing changed."


def test_remap_keeps_ids_and_references_and_runs_once(cluster):
    """Requirements: REV-73; plan "One-time local remap" and A18 (ids and FKs unchanged)."""
    dsn = cluster()
    ids = seed(dsn)
    before = snapshot(dsn)
    result, lines = run(dsn)
    assert result["applied"]
    assert lines[-1] == "Remapped 6 principals; ids and references unchanged."
    after = snapshot(dsn)
    for table in FK_TABLES:
        assert after[table] == before[table], table
    assert after["revision"] == before["revision"] + 1
    assert [row[0] for row in after["principals"]] == [row[0] for row in before["principals"]]
    unchanged = {row[0]: row[3:] for row in before["principals"]}
    assert {row[0]: row[3:] for row in after["principals"]} == unchanged
    assert keys(dsn) == {
        ids["local-admin"]: ("adapi", "local-admin"),
        ids["local-editor"]: ("adapi", "local-editor"),
        ids["local-viewer"]: ("adapi", "local-viewer"),
        ids["Keep Administrators"]: ("adapi", "keep-administrators"),
        ids["Developers"]: ("adapi", "keep-developers"),
        ids["Observers"]: ("adapi", "keep-observers"),
        ids["unmapped"]: ("keycloak-local", UNMAPPED[0]),
        ids["other"]: ("adapi", "someone-else"),
    }
    # Only the unmapped group is left; a second run plans nothing and changes nothing.
    again, lines = run(dsn)
    assert not again["applied"] and again["planned"] == [] and len(again["unmapped"]) == 1
    assert lines[0] == "Principal remap keycloak-local -> adapi: 0 planned, 1 left unmapped."
    assert snapshot(dsn) == after


def test_a_finished_remap_ignores_later_edits_to_the_local_files(cluster):
    """Requirements: REV-73; plan "One-time local remap" (later Starts are no-ops).

    After the remap only the left-unmapped row keeps the old source. Removing a mapped
    person from adapi-directory.json or credentials.json afterwards must not stop Start.
    """
    dsn = cluster()
    seed(dsn)
    run(dsn)
    after = snapshot(dsn)
    creds, directory = files()
    directory["users"] = [u for u in directory["users"] if u["sAMAccountName"] != "local-viewer"]
    del creds["users"]["local-editor"]
    again, lines = run(dsn, creds=creds, directory=directory)
    assert not again["applied"] and again["planned"] == [] and len(again["unmapped"]) == 1
    assert lines[0] == "Principal remap keycloak-local -> adapi: 0 planned, 1 left unmapped."
    assert snapshot(dsn) == after


def test_an_existing_adapi_row_for_a_mapped_name_refuses_the_whole_remap(cluster):
    """Requirements: REV-73; plan "One-time local remap" (refuse, never merge)."""
    dsn = cluster()
    seed(dsn, extra_adapi=[("local-admin", "user")])
    before = snapshot(dsn)
    with pytest.raises(local_identity.RemapError, match="already exist for local-admin"):
        run(dsn)
    assert snapshot(dsn) == before


def test_a_label_without_a_samaccountname_stops_before_any_change(cluster):
    """Requirements: REV-73; plan "One-time local remap" (stop on missing labels)."""
    dsn = cluster()
    seed(dsn)
    before = snapshot(dsn)
    creds, directory = files()
    creds["remap"]["groups"]["Platform Team"] = UNMAPPED[0]
    with pytest.raises(local_identity.RemapError, match="Platform Team"):
        run(dsn, creds=creds, directory=directory)
    directory["users"] = [u for u in directory["users"] if u["sAMAccountName"] != "local-editor"]
    with pytest.raises(local_identity.RemapError, match="local-editor"):
        run(dsn, directory=directory)
    assert snapshot(dsn) == before


def test_no_legacy_rows_is_a_no_op(cluster):
    """Requirements: REV-73 (a fresh install has nothing to remap)."""
    dsn = cluster()
    before = snapshot(dsn)
    result, lines = run(dsn)
    assert result == {"planned": [], "unmapped": [], "applied": False}
    assert lines == ["No keycloak-local principals; nothing to remap."]
    assert snapshot(dsn) == before


def test_a_database_without_migration_0070_is_refused(cluster):
    """Requirements: REV-73 (remap runs after migration 0070)."""
    dsn = cluster(migrated=False)
    with pytest.raises(local_identity.RemapError, match="0070"):
        run(dsn)
