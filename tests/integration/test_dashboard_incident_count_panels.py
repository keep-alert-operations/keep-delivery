"""Incident count panels through the real Product API and PostgreSQL (REV-69).

Elasticsearch replies are controlled, as in test_configuration_counts; incident
status, identity retirement, storage and references are real.
"""
# ruff: noqa: F811

from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401
from test_configuration_counts import SearchResponses, environment  # noqa: F401
from test_configuration_deletion import configuration, create, headers  # noqa: F401
from test_dashboard_panel_types import count_panel, save

pytestmark = pytest.mark.integration


def incident_panel(preset, x=3, **fields):
    return {"id": str(uuid4()), "type": "incident_count", "preset_id": preset, "title": None,
            "x": x, "y": 0, "w": 3, "h": 3, "incident_status": "both", "thresholds": [],
            "link": None, **fields}


def test_incident_count_panels_store_and_reference_their_preset(configuration):
    """Requirements: REV-69; dashboard operations 2, 13 and 14."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    counted = count_panel(preset["id"])
    incidents = incident_panel(
        preset["id"], incident_status="investigating",
        thresholds=[{"lower_bound": "2", "color": "red"}], title="Investigating",
    )
    response, _ = save(configuration, [counted, incidents])
    assert response.status_code == 201, response.text
    dashboard = response.json()["data"]
    panels = {panel["id"]: panel for panel in dashboard["panels"]}
    assert panels[incidents["id"]] == incidents
    read = client.get(base + "/dashboards/" + dashboard["id"])
    assert read.json()["data"]["panels"] == dashboard["panels"]
    references = client.get(base + "/presets/" + preset["id"] + "/references")
    assert references.status_code == 200, references.text
    assert {item["panel_id"] for item in references.json()["data"]} == {
        counted["id"], incidents["id"]
    }
    blocked = client.delete(base + "/presets/" + preset["id"], headers=headers())
    assert blocked.status_code == 409, blocked.text
    changed, _ = save(
        configuration, [count_panel(preset["id"], id=incidents["id"], x=3)],
        dashboard=dashboard, revision="1",
    )
    assert changed.status_code == 422, changed.text
    assert changed.json()["error"]["code"] == "panel_type_changed"
    for bad in ({"incident_status": "resolved"}, {"incident_status": None}, {"preset_id": None}):
        invalid, _ = save(configuration, [incident_panel(preset["id"], x=0, **bad)])
        assert invalid.status_code == 422, invalid.text


class IncidentSearch(SearchResponses):
    """Controlled replies that also report the active incident owning each alert."""

    def __init__(self, owners):
        super().__init__()
        self.owners = owners

    def msearch(self, **kwargs):
        self.calls.append(("msearch", kwargs))
        responses = []
        for body in kwargs["searches"][1::2]:
            reply = self._response()
            if "incidents" in body.get("aggs", {}):
                assert body["aggs"]["incidents"]["terms"]["field"] == "active_incident_id"
                reply["aggregations"] = {
                    "incidents": {
                        "doc_count_error_upper_bound": 0,
                        "sum_other_doc_count": 0,
                        "buckets": [
                            {"key": key, "doc_count": count} for key, count in self.owners.items()
                        ],
                    }
                }
            responses.append(reply)
        return {"responses": responses}


def incident(db, tenant, status, retired=False):
    identifier = uuid4()
    with db.transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO incident_identities(id,tenant_id,retired_at) VALUES(%s,%s,%s)",
            (identifier, tenant, None),
        )
        conn.execute(
            """INSERT INTO incidents(id,tenant_id,title,description,severity,status,origin,cycle_id,created_at,updated_at,last_activity,resolved_at)
            VALUES(%s,%s,%s,'','high',%s,'manual',%s,clock_timestamp(),clock_timestamp(),clock_timestamp(),
            CASE WHEN %s='resolved' THEN clock_timestamp() END)""",
            (identifier, tenant, f"{status} incident", status, uuid4(), status),
        )
        if retired:
            conn.execute(
                "UPDATE incident_identities SET retired_at=clock_timestamp() WHERE tenant_id=%s AND id=%s",
                (tenant, identifier),
            )
    return str(identifier)


def test_incident_counts_keep_each_matching_status_once(environment, real_db):
    """Requirements: REV-69; dashboard operations 3, 6, 10 and 11."""
    client, actor, tenant, preset, _, _ = environment
    firing = incident(real_db, tenant, "open")
    investigating = incident(real_db, tenant, "investigating")
    resolved = incident(real_db, tenant, "resolved")
    retired = incident(real_db, tenant, "open", retired=True)
    elastic = IncidentSearch(
        {firing: 3, investigating: 2, resolved: 4, retired: 1, str(uuid4()): 1}
    )
    client.app.state.search_client = elastic
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='edit' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, actor),
        )
    root = f"/api/tenants/{tenant}"
    panels = [
        count_panel(preset["id"], x=0),
        incident_panel(preset["id"], x=3, incident_status="open"),
        incident_panel(preset["id"], x=6, incident_status="investigating"),
        incident_panel(preset["id"], x=9, incident_status="both"),
    ]
    dashboard = client.post(
        root + "/dashboards",
        json={"command_id": str(uuid4()), "name": "Incidents", "panels": panels},
    )
    assert dashboard.status_code == 201, dashboard.text
    dashboard = dashboard.json()["data"]
    response = client.post(
        root + f"/dashboards/{dashboard['id']}/counts", json={"expected_revision": "1"}
    )
    assert response.status_code == 200, response.text
    results = {panel["panel_id"]: panel for panel in response.json()["data"]["panels"]}
    assert results[panels[0]["id"]]["count"] == "12"
    # Resolved, retired and unknown owners never count; each incident counts once.
    firing_only = results[panels[1]["id"]]
    assert (firing_only["count"], firing_only["alerts"], firing_only["linked_alerts"]) == (
        "1", "12", "3"
    )
    assert results[panels[2]["id"]]["count"] == "1"
    assert results[panels[2]["id"]]["linked_alerts"] == "2"
    assert results[panels[3]["id"]]["count"] == "2"
    assert results[panels[3]["id"]]["linked_alerts"] == "5"
    # One plain count and one incident query share the batch's point in time.
    searches = elastic.calls[1][1]["searches"][1::2]
    assert sorted("incidents" in body["aggs"] for body in searches) == [False, True]
    # The dashboard preview counts the same way before saving.
    preview = client.post(
        root + "/dashboards/preview",
        json={"name": "Draft", "panels": panels, "preset_revisions": {preset["id"]: "1"}},
    )
    assert preview.status_code == 200, preview.text
    assert [panel.get("count") for panel in preview.json()["data"]["panels"]] == ["12", "1", "1", "2"]
    # The dialog preview's quick count reports the same numbers.
    quick = client.post(
        root + "/alerts/counts",
        json={"queries": [{"id": "q", "cel": "true", "incident_status": "both"}]},
    )
    assert quick.status_code == 200, quick.text
    assert quick.json()["data"]["incidents"] == {
        "q": {"count": "2", "alerts": "12", "linked_alerts": "5"}
    }
