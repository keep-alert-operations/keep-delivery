"""Real PostgreSQL HTTP tests; no Elasticsearch or authorization mocks."""

import base64
from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from test_product import browser  # noqa: F401 -- shared authenticated HTTP fixture

pytestmark = pytest.mark.integration


@pytest.fixture
def reader(request, real_db):
    client, principal = request.getfixturevalue("browser")
    with real_db.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
    return client, principal, tenant


def seed(real_db, reader, table="presets", ages=(0,)):
    _, actor, tenant = reader
    resource = uuid4()
    now = datetime.now(UTC)
    with real_db.transaction() as conn:
        common = (resource, tenant, str(resource), str(resource), len(ages), actor, actor)
        extra = ",cel,profile,sort,columns" if table == "presets" else ""
        values = ",%s,%s,%s,%s" if table == "presets" else ""
        args = common + (("true", "keep-cel-v1", Jsonb([]), Jsonb([])) if extra else ())
        conn.execute(
            sql.SQL(
                "INSERT INTO {}(id,tenant_id,name,name_key,revision,created_by,updated_by,created_at,updated_at"
                + extra
                + ") VALUES(%s,%s,%s,%s,%s,%s,%s,clock_timestamp(),clock_timestamp()"
                + values
                + ")"
            ).format(sql.Identifier(table)),
            args,
        )
        for index, age in enumerate(ages, 1):
            conn.execute(
                sql.SQL(
                    "INSERT INTO {}(tenant_id,id,revision,snapshot,recorded_at,actor_id) VALUES(%s,%s,%s,%s,%s,%s)"
                ).format(
                    sql.Identifier(
                        "preset_revisions" if table == "presets" else "dashboard_revisions"
                    )
                ),
                (
                    tenant,
                    resource,
                    index,
                    Jsonb(
                        {
                            "id": str(resource),
                            "revision": str(index),
                            "name": "Retained definition",
                            "cel": "true",
                        }
                    ),
                    now - timedelta(days=age),
                    actor,
                ),
            )
    return resource


def payload(cipher, token):
    return json.loads(cipher.open(base64.urlsafe_b64decode(token), "cursor:v1"))


@pytest.mark.parametrize("table", ["presets", "dashboards"])
def test_history_watermark_fixed_expiry_and_filters(reader, real_db, cipher, table):
    client, actor, tenant = reader
    resource = seed(real_db, reader, table, (5, 4, 3, 2, 1))
    base = f"/api/tenants/{tenant}/{table}/{resource}/history"
    first = client.get(base, params={"limit": 2})
    assert first.status_code == 200, first.text
    assert [row["revision"] for row in first.json()["data"]] == ["5", "4"]
    token = first.json()["next_cursor"]
    before = payload(cipher, token)
    # An ordinary newly recorded change is outside the first page's watermark.
    with real_db.transaction() as conn:
        conn.execute(
            sql.SQL("UPDATE {} SET revision=6 WHERE id=%s").format(sql.Identifier(table)),
            (resource,),
        )
        conn.execute(
            sql.SQL(
                "INSERT INTO {}(tenant_id,id,revision,snapshot,recorded_at,actor_id) VALUES(%s,%s,6,%s,clock_timestamp(),%s)"
            ).format(
                sql.Identifier("preset_revisions" if table == "presets" else "dashboard_revisions")
            ),
            (tenant, resource, Jsonb({"revision": "6"}), actor),
        )
    second = client.get(base, params={"limit": 2, "cursor": token})
    assert second.status_code == 200, second.text
    assert [row["revision"] for row in second.json()["data"]] == ["3", "2"]
    next_token = second.json()["next_cursor"]
    assert payload(cipher, next_token)["expires_at"] == before["expires_at"]
    third = client.get(base, params={"limit": 2, "cursor": next_token})
    assert [row["revision"] for row in third.json()["data"]] == ["1"]
    assert third.json()["next_cursor"] is None
    assert (
        client.get(base, params={"cursor": token, "from": "2000-01-01T00:00:00Z"}).status_code
        == 409
    )
    assert client.get(base, params={"from": "2026-01-01T00:00:00+01:00"}).status_code == 400
    assert (
        client.get(
            base, params={"from": "2026-01-01T00:00:00Z", "to": "2025-01-01T00:00:00Z"}
        ).status_code
        == 400
    )
    assert client.get(base, params={"limit": 201}).status_code == 400
    before["expires_at"] = "2000-01-01T00:00:00Z"
    expired = base64.urlsafe_b64encode(
        cipher.seal(json.dumps(before).encode(), "cursor:v1")
    ).decode()
    assert client.get(base, params={"cursor": expired}).status_code == 409


@pytest.mark.parametrize("table", ["presets", "dashboards"])
def test_retention_uses_supersession_and_does_not_restart_on_deletion(reader, real_db, table):
    client, _, tenant = reader
    resource = seed(real_db, reader, table, (180, 120, 80, 1))
    base = f"/api/tenants/{tenant}/{table}/{resource}/history"
    response = client.get(base)
    assert [row["revision"] for row in response.json()["data"]] == ["4", "3", "2"]
    assert client.get(base + "/1").status_code == 404
    retained = client.get(base + "/2")
    assert retained.status_code == 200, retained.text
    assert retained.json()["data"]["definition"]["revision"] == "2"
    assert retained.json()["data"]["activity"] is None
    with real_db.transaction() as conn:
        conn.execute(
            sql.SQL(
                "UPDATE {} SET deleted_at=clock_timestamp()-interval '1 day' WHERE id=%s"
            ).format(sql.Identifier(table)),
            (resource,),
        )
    assert client.get(base + "/1").status_code == 404
    assert client.get(base + "/4").status_code == 200
    with real_db.transaction() as conn:
        from test_configuration_retention import age

        # Advance the complete synthetic timeline beyond its lifetime. Moving
        # only the current-row deletion time contradicts the immutable identity
        # and leaves legitimately young superseded snapshots behind.
        age(conn, resource, table)
    assert client.get(base).status_code == 404
    assert client.get(base + "/4").status_code == 404
    active = seed(real_db, reader, table, (365,))
    active_url = f"/api/tenants/{tenant}/{table}/{active}/history"
    assert client.get(active_url + "/1").status_code == 200
    assert client.get(active_url).json()["data"][0]["activity_available"] is False
    assert client.get(active_url + "/-1").status_code == 400
    assert client.get(active_url + "/9223372036854775808").status_code == 400


def references(real_db, tenant, dashboard, preset, count=3):
    with real_db.transaction() as conn:
        for index in range(count):
            conn.execute(
                "INSERT INTO dashboard_panels(id,tenant_id,dashboard_id,preset_id,x,y,w,h,thresholds) VALUES(%s,%s,%s,%s,0,%s,3,2,'[]')",
                (uuid4(), tenant, dashboard, preset, index * 2),
            )


def test_reference_pages_count_names_changed_set_and_fixed_expiry(reader, real_db, cipher):
    client, _, tenant = reader
    preset = seed(real_db, reader)
    dashboard = seed(real_db, reader, "dashboards")
    references(real_db, tenant, dashboard, preset)
    base = f"/api/tenants/{tenant}/presets/{preset}/references"
    first = client.get(base, params={"limit": 1})
    assert first.status_code == 200, first.text
    assert first.json()["meta"]["total"] == "3"
    assert first.json()["data"][0]["dashboard_id"] == str(dashboard)
    token = first.json()["next_cursor"]
    # Changes to unrelated configuration should not invalidate this reference set.
    seed(real_db, reader)
    second = client.get(base, params={"limit": 1, "cursor": token})
    assert second.status_code == 200, second.text
    assert second.json()["data"] != first.json()["data"]
    assert (
        payload(cipher, second.json()["next_cursor"])["expires_at"]
        == payload(cipher, token)["expires_at"]
    )
    with real_db.transaction() as conn:
        conn.execute("UPDATE dashboards SET name='Renamed dashboard' WHERE id=%s", (dashboard,))
    changed = client.get(base, params={"limit": 1, "cursor": token})
    assert changed.status_code == 409, changed.text
    assert changed.json()["error"]["code"] == "list_changed"
    refreshed = client.get(base)
    assert refreshed.json()["data"][0]["dashboard_name"] == "Renamed dashboard"
    with real_db.transaction() as conn:
        conn.execute("UPDATE dashboards SET deleted_at=clock_timestamp() WHERE id=%s", (dashboard,))
    assert client.get(base).json()["meta"]["total"] == "0"


def test_configuration_pages_reauthorize_and_hide_other_tenants(reader, real_db, tenant):
    client, actor, global_tenant = reader
    preset = seed(real_db, reader, ages=(2, 1))
    base = f"/api/tenants/{global_tenant}/presets/{preset}"
    token = client.get(base + "/history", params={"limit": 1}).json()["next_cursor"]
    other = f"/api/tenants/{tenant}/presets/{preset}"
    assert client.get(other + "/history").status_code == 404
    assert client.get(other + "/history/1").status_code == 404
    assert client.get(other + "/references").status_code == 404
    another = seed(real_db, reader, ages=(2, 1))
    assert (
        client.get(
            f"/api/tenants/{global_tenant}/presets/{another}/history", params={"cursor": token}
        ).status_code
        == 409
    )
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET eligible=false WHERE id=%s", (actor,))
    assert client.get(base + "/history", params={"cursor": token}).status_code == 401
    assert client.get(base + "/history/1").status_code == 401
    assert client.get(base + "/references").status_code == 401
