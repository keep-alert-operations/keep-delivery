"""Real PostgreSQL read-time display names; search responses are controlled, not ES parity."""
# ruff: noqa: F811 -- pytest fixtures imported by name

from uuid import uuid4

import pytest

from appchi import search_routes
from appchi.alerts import routes as alert_routes
from test_product import browser  # noqa: F401
from test_configuration_counts import environment  # noqa: F401
from test_configuration_deletion import configuration, create as create_definition  # noqa: F401
from test_correlation_configuration import (  # noqa: F401
    TEST_CAPACITY,
    create as create_rule,
    definition as rule_definition,
    rules,
)
from test_incident_listing import incident_list  # noqa: F401
from test_maintenance_repository import (  # noqa: F401
    create as create_window,
    maintenance_browser,
    maintenance_db,
)
from test_maintenance_processing import setup, publish_source

pytestmark = pytest.mark.integration


def principal(db, name, *, deleted=False):
    identifier = uuid4()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible,deleted,evidence_generation,verified_at) VALUES(%s,'synthetic',%s,'user',%s,%s,%s,1,clock_timestamp())",
            (identifier, str(identifier), name, not deleted, deleted),
        )
    return str(identifier)


def other_tenant(db):
    tenant = uuid4()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
            (tenant, str(tenant), str(tenant)),
        )
    return str(tenant)


def incident(db, tenant, title):
    identifier = uuid4()
    with db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO incident_identities(id,tenant_id) VALUES(%s,%s)", (identifier, tenant)
        )
        conn.execute(
            """INSERT INTO incidents(id,tenant_id,title,description,severity,status,origin,cycle_id,created_at,updated_at,last_activity)
            VALUES(%s,%s,%s,'','high','open','manual',%s,clock_timestamp(),clock_timestamp(),clock_timestamp())""",
            (identifier, tenant, title, uuid4()),
        )
    return str(identifier)


def test_feed_and_preset_preview_label_rows_without_crossing_tenants(
    environment, real_db, monkeypatch
):
    client, actor, tenant, preset, _, elastic = environment
    monkeypatch.setattr(search_routes, "Elasticsearch", lambda *args, **kwargs: elastic)
    departed = principal(real_db, "Departed operator", deleted=True)
    own = incident(real_db, str(tenant), "Checkout latency")
    foreign = incident(real_db, other_tenant(real_db), "Foreign tenant secret")
    alerts = [str(uuid4()) for _ in range(3)]
    with real_db.transaction(tenant) as conn:
        for alert in alerts:
            conn.execute(
                "INSERT INTO alert_identities(id,tenant_id) VALUES(%s,%s)", (alert, tenant)
            )
    displays = [
        {
            "id": alerts[0],
            "assignee_id": str(actor),
            "acknowledged_by": departed,
            "active_incident_id": own,
        },
        {
            "id": alerts[1],
            "assignee_id": None,
            "acknowledged_by": None,
            "active_incident_id": foreign,
        },
        {"id": alerts[2], "assignee_id": str(uuid4()), "active_incident_id": str(uuid4())},
    ]
    elastic.hits = [
        {
            "_id": item["id"],
            "sort": [index, item["id"]],
            "_source": {"tenant_id": str(tenant), "deleted": False, "display": item},
        }
        for index, item in enumerate(displays)
    ]
    expected = [
        ("Synthetic user", "Checkout latency"),
        (None, None),
        (None, None),
    ]

    def labels(response):
        assert response.status_code == 200, response.text
        return [
            (
                row["assignee_display_name"],
                row["active_incident_title"],
            )
            for row in response.json()["data"]
        ]

    feed = client.get(f"/api/tenants/{tenant}/alerts")
    assert labels(feed) == expected
    # Current responses omit retired fields; stored search snapshots remain untouched.
    assert [
        {k: v for k, v in row.items() if k in displays[i]}
        for i, row in enumerate(feed.json()["data"])
    ] == [{k: v for k, v in row.items() if k != "acknowledged_by"} for row in displays]
    preview = client.post(
        f"/api/tenants/{tenant}/presets/preview",
        json={"preset_id": preset["id"], "expected_revision": "1", "limit": 10},
    )
    assert labels(preview) == expected
    assert preview.headers["etag"] == '"1"'


def test_incident_list_and_detail_label_the_visible_assignee(incident_list, real_db):
    client, path, rows, actor, tenant = incident_list
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE incidents SET assignee_id=%s WHERE tenant_id=%s AND id=%s",
            (actor, tenant, rows[0]["id"]),
        )
    listed = client.get(path, params={"limit": 10})
    assert listed.status_code == 200, listed.text
    names = {row["id"]: row["assignee_display_name"] for row in listed.json()["data"]}
    assert names == {row["id"]: None for row in rows} | {rows[0]["id"]: "Synthetic user"}
    detail = client.get(path + "/" + rows[0]["id"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["assignee_display_name"] == "Synthetic user"
    assert detail.json()["meta"]["revision"] == rows[0]["revision"]
    # An assignee that no longer qualifies is hidden, so no label is presented.
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, actor),
        )
    hidden = client.get(path + "/" + rows[0]["id"]).json()["data"]
    assert hidden["assignee_id"] is None and hidden["assignee_display_name"] is None


def test_correlation_incidents_expose_origin_rule_and_current_name(incident_list, real_db):
    client, path, rows, _, tenant = incident_list
    client.app.state.profile = TEST_CAPACITY
    created = client.post(
        f"/api/tenants/{tenant}/correlation-rules",
        json={"command_id": str(uuid4()), **rule_definition("Checkout errors")},
    )
    assert created.status_code == 201, created.text
    rule = created.json()["data"]["id"]
    context = uuid4()
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO correlation_contexts(id,tenant_id,rule_id,grouping_generation,group_digest,group_key,created_at) VALUES(%s,%s,%s,1,%s,%s,clock_timestamp())",
            (context, tenant, rule, b"k" * 32, b"group"),
        )
        conn.execute(
            "UPDATE incidents SET origin='correlation',context_id=%s WHERE tenant_id=%s AND id=%s",
            (context, tenant, rows[0]["id"]),
        )

    def origins():
        listed = client.get(path, params={"limit": 10})
        assert listed.status_code == 200, listed.text
        detail = client.get(path + "/" + rows[0]["id"])
        assert detail.status_code == 200, detail.text
        by_id = {
            row["id"]: (row["origin_rule_id"], row["origin_rule_name"])
            for row in listed.json()["data"]
        }
        data = detail.json()["data"]
        assert by_id[rows[0]["id"]] == (data["origin_rule_id"], data["origin_rule_name"])
        assert {value for key, value in by_id.items() if key != rows[0]["id"]} == {(None, None)}
        return by_id[rows[0]["id"]]

    assert origins() == (rule, "Checkout errors")
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE correlation_rules SET name='Renamed rule' WHERE tenant_id=%s AND id=%s",
            (tenant, rule),
        )
    assert origins() == (rule, "Renamed rule")  # current name, not a snapshot
    with real_db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE correlation_rules SET deleted_at=clock_timestamp() WHERE tenant_id=%s AND id=%s",
            (tenant, rule),
        )
    assert origins() == (rule, None)


def test_preset_and_dashboard_reads_label_actors_including_deleted(configuration, real_db):
    client, base, _, tenant, actor = configuration
    preset, _ = create_definition(configuration)
    dashboard, _ = create_definition(configuration, "dashboards", preset=preset["id"])
    for kind, row in (("presets", preset), ("dashboards", dashboard)):
        detail = client.get(f"{base}/{kind}/{row['id']}")
        assert detail.status_code == 200, detail.text
        data = detail.json()["data"]
        assert data["created_by"] == data["updated_by"] == str(actor)
        assert (
            data["created_by_display_name"] == data["updated_by_display_name"] == ("Synthetic user")
        )
        listed = client.get(f"{base}/{kind}", params={"name": row["name"]})
        assert listed.status_code == 200, listed.text
        (item,) = listed.json()["data"]
        assert item["updated_by_display_name"] == "Synthetic user"
        history = client.get(f"{base}/{kind}/{row['id']}/history/1")
        assert history.status_code == 200, history.text
        # History keeps the recorded label; no current-directory name is added.
        revision = history.json()["data"]
        assert revision["activity"]["actor_label"] == "Synthetic user"
        assert "created_by_display_name" not in revision
        assert "created_by_display_name" not in revision["definition"]
    departed = principal(real_db, "Former editor", deleted=True)
    with real_db.transaction(tenant) as conn:
        conn.execute("UPDATE presets SET updated_by=%s WHERE id=%s", (departed, preset["id"]))
    data = client.get(f"{base}/presets/{preset['id']}").json()["data"]
    assert data["updated_by_display_name"] == "Former editor"
    assert data["created_by_display_name"] == "Synthetic user"


def test_correlation_rule_reads_label_actors(rules):
    client, actor, _, base = rules
    row, _ = create_rule(rules)
    detail = client.get(base + "/" + row["id"])
    assert detail.status_code == 200, detail.text
    assert detail.headers["etag"] == '"' + row["revision"] + '"'
    data = detail.json()["data"]
    assert data["updated_by"] == str(actor)
    assert data["created_by_display_name"] == data["updated_by_display_name"] == "Synthetic user"
    listed = client.get(base)
    assert listed.status_code == 200, listed.text
    assert [item["created_by_display_name"] for item in listed.json()["data"]] == ["Synthetic user"]
    history = client.get(base + "/" + row["id"] + "/history/1")
    assert history.status_code == 200, history.text
    assert history.json()["data"]["activity"]["actor_label"] == "Synthetic user"
    assert "updated_by_display_name" not in history.json()["data"]


def test_maintenance_reads_label_stored_actor_ids(maintenance_browser):
    client, tenant, _, _ = maintenance_browser
    window = create_window(client, tenant)
    detail = client.get(f"/api/tenants/{tenant}/maintenance/{window['id']}")
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["created_by_display_name"] == data["updated_by_display_name"] == "Test editor"
    assert "created_by" not in data and "updated_by" not in data
    listed = client.get(f"/api/tenants/{tenant}/maintenance")
    assert listed.status_code == 200, listed.text
    (item,) = [row for row in listed.json()["data"] if row["id"] == window["id"]]
    assert item["updated_by_display_name"] == "Test editor"


def test_alert_detail_labels_assignee_and_owned_incident(maintenance_browser, maintenance_db):
    db, client, tenant, _, catalog, _ = setup(maintenance_browser, maintenance_db)
    _, _, editor, app = maintenance_browser
    app.include_router(alert_routes.router)
    alert, _, _ = publish_source(db, tenant, None, catalog)
    path = f"/api/tenants/{tenant}/alerts/{alert['id']}"
    plain = client.get(path)
    assert plain.status_code == 200, plain.text
    assert {
        key: plain.json()["data"][key]
        for key in (
            "assignee_display_name",
            "active_incident_title",
        )
    } == dict.fromkeys(("assignee_display_name", "active_incident_title"))
    assert plain.json()["data"]["active_incident_id"] is None
    # Labels are read-time only, so stored state is arranged directly here;
    # the detail revision and every stored field stay exactly as persisted.
    owner = incident(db, tenant, "Payments degraded")
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE alerts SET assignee_id=%s,acknowledged=true,acknowledged_by=%s WHERE tenant_id=%s AND id=%s",
            (editor, editor, tenant, alert["id"]),
        )
        conn.execute(
            "INSERT INTO incident_alert_links(tenant_id,incident_id,alert_id,linked_at) VALUES(%s,%s,%s,clock_timestamp())",
            (tenant, owner, alert["id"]),
        )
        conn.execute(
            "INSERT INTO alert_active_ownership(tenant_id,alert_id,incident_id) VALUES(%s,%s,%s)",
            (tenant, alert["id"], owner),
        )
    labelled = client.get(path)
    assert labelled.status_code == 200, labelled.text
    data = labelled.json()["data"]
    assert data["assignee_display_name"] == "Test editor"
    assert data["display_status"] == "acknowledged"
    assert "acknowledged_by_display_name" not in data
    assert data["active_incident_title"] == "Payments degraded"
    assert labelled.json()["meta"] == plain.json()["meta"]
    assert data["active_incident_id"] == owner
    # A retired owner is no longer live, so neither its id nor its title is exposed.
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE incident_identities SET retired_at=clock_timestamp() WHERE tenant_id=%s AND id=%s",
            (tenant, owner),
        )
    retired = client.get(path).json()["data"]
    assert retired["active_incident_id"] is None and retired["active_incident_title"] is None
    departed = principal(db, "Retired acknowledger", deleted=True)
    with db.transaction(tenant) as conn:
        conn.execute(
            "UPDATE alerts SET acknowledged_by=%s WHERE tenant_id=%s AND id=%s",
            (departed, tenant, alert["id"]),
        )
        conn.execute("DELETE FROM alert_active_ownership WHERE tenant_id=%s", (tenant,))
    data = client.get(path).json()["data"]
    assert "acknowledged_by_display_name" not in data
    assert "acknowledged_by" not in data
    assert data["active_incident_title"] is None and data["active_incident_id"] is None
