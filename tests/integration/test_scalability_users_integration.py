"""Real PostgreSQL checks for set-based authorization and handler budget rollback."""
# ruff: noqa: F811 -- shared authenticated/count fixtures are pytest dependencies

from time import sleep
from uuid import uuid4

import pytest

from appchi.access import accessible_tenants
from appchi.request_budget import request_budget
from appchi.shared.errors import AppError
from test_configuration_counts import environment  # noqa: F401, F811
from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


def test_quick_counts_endpoint_batches_duplicate_contexts_and_closes(environment):  # noqa: F811
    client, _, tenant, _, _, search = environment
    response = client.post(
        f"/api/tenants/{tenant}/alerts/counts",
        json={"queries": [{"id": str(index), "cel": "true", "q": "CPU"} for index in range(9)]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["counts"] == {str(index): "12" for index in range(9)}
    assert response.json()["data"]["relative"] is False
    assert len([call for call in search.calls if call[0] == "open"]) == 1
    batches = [call[1]["searches"] for call in search.calls if call[0] == "msearch"]
    assert len(batches) == 1 and len(batches[0]) == 2
    assert set(search.closed) == {"pit-original", "pit-replacement"}


def test_set_based_roles_preserve_precedence_revocations_and_global_access(real_db):
    user, team = uuid4(), uuid4()
    specific = [uuid4() for _ in range(3)]
    with real_db.transaction() as conn:
        for principal, kind in [(user, "user"), (team, "team")]:
            conn.execute(
                """INSERT INTO principals(id,directory_source,object_id,kind,display_name,
                eligible,evidence_generation,verified_at)
                VALUES(%s,'scalability-users',%s,%s,'Synthetic',true,1,clock_timestamp())""",
                (principal, str(principal), kind),
            )
        for tenant in specific:
            conn.execute(
                "INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
                (tenant, str(tenant), str(tenant)),
            )
        conn.execute(
            "INSERT INTO directory_memberships(team_id,user_id) VALUES(%s,%s)", (team, user)
        )
        for tenant, principal, role in [
            (specific[0], user, "view"),
            (specific[0], team, "admin"),
            (specific[1], user, "edit"),
        ]:
            conn.execute("INSERT INTO tenant_grants VALUES(%s,%s,%s)", (tenant, principal, role))

    principal = {"id": user, "eligible": True, "deleted": False, "super_admin": False}

    def read():
        with real_db.transaction() as conn:
            conn.execute("SET LOCAL ROLE appchi_api")
            return accessible_tenants(conn, principal)

    rows = read()
    roles = {row["id"]: row["role"] for row in rows}
    assert roles[specific[0]] == "admin" and roles[specific[1]] == "edit"
    assert specific[2] not in roles
    assert next(row for row in rows if row["kind"] == "global")["role"] == "edit"
    with real_db.transaction() as conn:
        conn.execute("DELETE FROM directory_memberships WHERE user_id=%s", (user,))
    assert {row["id"]: row["role"] for row in read()}[specific[0]] == "view"
    principal["super_admin"] = True
    assert all(row["role"] == "admin" for row in read())
    assert specific[2] in {row["id"] for row in read()}
    principal["eligible"] = False
    assert read() == []


def test_expired_request_rolls_back_local_transaction(real_db, tenant):
    with real_db.transaction() as conn:
        before = conn.execute("SELECT revision FROM tenants WHERE id=%s", (tenant,)).fetchone()[
            "revision"
        ]
    with pytest.raises(AppError, match="request_timeout"):
        with request_budget(0.1), real_db.transaction() as conn:
            conn.execute("UPDATE tenants SET revision=revision+1 WHERE id=%s", (tenant,))
            sleep(0.12)
    with real_db.transaction() as conn:
        after = conn.execute("SELECT revision FROM tenants WHERE id=%s", (tenant,)).fetchone()[
            "revision"
        ]
    assert after == before


def test_cursor_batch_deadline_stops_and_rolls_back(real_db, tenant):
    with real_db.transaction() as conn:
        before = conn.execute("SELECT revision FROM tenants WHERE id=%s", (tenant,)).fetchone()[
            "revision"
        ]

    consumed = []

    def parameters():
        for index in range(3):
            if index:
                sleep(0.12)
            consumed.append(index)
            yield (tenant,)

    with pytest.raises(AppError, match="request_timeout"):
        with request_budget(0.1), real_db.transaction() as conn:
            timeout = int(
                conn.execute(
                    "SELECT setting FROM pg_settings WHERE name='statement_timeout'"
                ).fetchone()["setting"]
            )
            assert 0 < timeout <= 100
            with conn.cursor() as cursor:
                cursor.executemany(
                    "UPDATE tenants SET revision=revision+1 WHERE id=%s", parameters()
                )
    assert consumed == [0, 1]
    with real_db.transaction() as conn:
        assert (
            conn.execute("SELECT revision FROM tenants WHERE id=%s", (tenant,)).fetchone()[
                "revision"
            ]
            == before
        )


def test_cursor_result_iteration_respects_aggregate_deadline(real_db):
    with pytest.raises(AppError, match="request_timeout"):
        with request_budget(0.1), real_db.transaction() as conn:
            cursor = conn.execute("SELECT generate_series(1,3) AS value")
            assert next(cursor)["value"] == 1
            sleep(0.12)
            next(cursor)


def test_production_restore_guard_preserves_statement_budget_across_transactions(real_db, tenant):
    from appchi.db import Database

    database = Database(real_db.pool.conninfo, deployment_epoch="scalability-users")
    try:
        with real_db.transaction() as conn:
            before = conn.execute("SELECT revision FROM tenants WHERE id=%s", (tenant,)).fetchone()[
                "revision"
            ]
        # A >4s aggregate deadline used to leave business transactions unbounded
        # after the guard transaction's SET LOCAL was discarded at commit.
        with request_budget(10), database.transaction() as conn:
            assert (
                int(
                    conn.execute(
                        "SELECT setting FROM pg_settings WHERE name='statement_timeout'"
                    ).fetchone()["setting"]
                )
                == 4000
            )
        # A short deadline must also survive the guard's explicit SET to 4000ms.
        with request_budget(0.5), database.transaction() as conn:
            assert (
                0
                < int(
                    conn.execute(
                        "SELECT setting FROM pg_settings WHERE name='statement_timeout'"
                    ).fetchone()["setting"]
                )
                <= 500
            )
            conn.execute("SET LOCAL statement_timeout = '4000ms'")
            assert (
                0
                < int(
                    conn.execute(
                        "SELECT setting FROM pg_settings WHERE name='statement_timeout'"
                    ).fetchone()["setting"]
                )
                <= 500
            )
        with pytest.raises(AppError, match="request_timeout"):
            with request_budget(0.1), database.transaction() as conn:
                conn.execute("UPDATE tenants SET revision=revision+1 WHERE id=%s", (tenant,))
                sleep(0.12)
        with real_db.transaction() as conn:
            assert (
                conn.execute("SELECT revision FROM tenants WHERE id=%s", (tenant,)).fetchone()[
                    "revision"
                ]
                == before
            )
    finally:
        database.close()


def test_incident_metadata_skips_maintenance_preparation_but_keeps_authorization(
    browser, real_db, tenant, monkeypatch
):  # noqa: F811
    import appchi.incidents.routes as routes

    client, actor = browser
    with real_db.transaction() as conn:
        conn.execute("INSERT INTO tenant_grants VALUES(%s,%s,'edit')", (tenant, actor))
    response = client.post(
        f"/api/tenants/{tenant}/incidents",
        json={
            "command_id": str(uuid4()),
            "title": "Metadata",
            "severity": "low",
        },
    )
    assert response.status_code == 201, response.text
    incident = response.json()["data"]["id"]
    path = f"/api/tenants/{tenant}/incidents/{incident}"
    locked = []
    original = routes.lock_catalog

    def lock(conn, catalog, **kwargs):
        locked.append(kwargs["incidents"])
        return original(conn, catalog, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("Incident metadata must not prepare alert maintenance history")

    monkeypatch.setattr(routes, "request_catalog", forbidden)
    monkeypatch.setattr(routes, "lock_catalog", lock)
    response = client.patch(
        path,
        json={
            "command_id": str(uuid4()),
            "expected_revision": "1",
            "title": "Updated",
            "description": "Description",
        },
    )
    assert response.status_code == 200, response.text
    response = client.post(
        path + "/assignment",
        json={"command_id": str(uuid4()), "expected_revision": "2", "assignee_id": str(actor)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["assignee_id"] == str(actor)
    assert locked == [[incident], [incident]]
    with real_db.transaction() as conn:
        conn.execute(
            "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, actor)
        )
    response = client.patch(
        path,
        json={
            "command_id": str(uuid4()),
            "expected_revision": "3",
            "title": "Denied",
            "description": "",
        },
    )
    assert response.status_code == 404
    assert locked == [[incident], [incident]]
