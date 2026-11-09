"""ServiceNow ticket panels through the real Product API and PostgreSQL (REV-65)."""
# ruff: noqa: F811

import os
from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401
from test_configuration_deletion import configuration, create, headers  # noqa: F401
from test_dashboard_panel_types import count_panel, html_panel, save

pytestmark = pytest.mark.integration


def servicenow_panel(x=6, **fields):
    return {"id": str(uuid4()), "type": "servicenow", "title": None, "x": x, "y": 0,
            "w": 3, "h": 3, "team": "NOC", "ticket_state": "open", "detection": "hamal",
            "thresholds": [], "link": None, **fields}


def test_servicenow_panels_store_their_settings(configuration):
    """Requirements: REV-65; dashboard operations 2 and 14."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    counted = count_panel(preset["id"])
    noted = html_panel("<p>Hi</p>", x=3)
    tickets = servicenow_panel(
        team="DBA", ticket_state="both", detection="all", title="DBA load",
        thresholds=[{"lower_bound": "1", "color": "green"},
                     {"lower_bound": "20", "color": "#D92D20"}],
    )
    response, _ = save(configuration, [counted, noted, tickets])
    assert response.status_code == 201, response.text
    panels = {panel["id"]: panel for panel in response.json()["data"]["panels"]}
    assert panels[tickets["id"]] == tickets
    assert "preset_id" not in panels[tickets["id"]]
    dashboard = response.json()["data"]
    read = client.get(base + "/dashboards/" + dashboard["id"])
    assert read.json()["data"]["panels"] == response.json()["data"]["panels"]
    history = client.get(base + "/dashboards/" + dashboard["id"] + "/history/1")
    assert history.status_code == 200, history.text
    assert history.json()["data"]["definition"]["panels"] == response.json()["data"]["panels"]


@pytest.mark.parametrize(
    "fields",
    [
        {"team": ""},
        {"team": "x" * 201},
        {"ticket_state": "closed"},
        {"detection": "manual"},
        {"thresholds": [{"lower_bound": "5", "color": "red"},
                        {"lower_bound": "5", "color": "blue"}]},
        {"preset_id": str(uuid4())},
    ],
)
def test_invalid_servicenow_panels_are_rejected(configuration, fields):
    """Requirements: REV-65; dashboard operations 2 and 5."""
    response, _ = save(configuration, [servicenow_panel(x=0, **fields)])
    assert response.status_code == 422, response.text


def test_a_servicenow_panel_keeps_its_type(configuration):
    """Requirements: REV-65; dashboard operations 2 and 14."""
    preset, _ = create(configuration)
    tickets = servicenow_panel(x=0)
    created, _ = save(configuration, [tickets])
    assert created.status_code == 201, created.text
    dashboard = created.json()["data"]
    changed, _ = save(
        configuration,
        [count_panel(preset["id"], id=tickets["id"])],
        dashboard=dashboard,
        revision="1",
    )
    assert changed.status_code == 422, changed.text
    assert changed.json()["error"]["code"] == "panel_type_changed"
    renamed, _ = save(configuration, [{**tickets, "team": "Network"}], dashboard=dashboard,
                      revision="1")
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["panels"][0]["team"] == "Network"


def test_ticket_count_status_follows_configuration(configuration):
    """Requirements: REV-65; dashboard operations 14."""
    client, base, *_ = configuration
    status = client.get(base + "/ticket-counts/status")
    assert status.status_code == 200, status.text
    assert status.json()["data"]["configured"] is bool(os.environ.get("APPCHI_TICKET_COUNT_URL"))


def test_ticket_counts_answer_each_query(configuration):
    """Requirements: REV-65; dashboard operations 11 and 14."""
    client, base, *_ = configuration
    queries = [
        {"team": "NOC", "ticket_state": "open", "detection": "hamal"},
        {"team": "NOC", "ticket_state": "open", "detection": "hamal"},
        {"team": "unknown-team", "ticket_state": "both", "detection": "all"},
    ]
    response = client.post(base + "/ticket-counts", json={"queries": queries}, headers=headers())
    assert response.status_code == 200, response.text
    results = response.json()["data"]["results"]
    assert len(results) == 3
    if not os.environ.get("APPCHI_TICKET_COUNT_URL"):
        assert {item["status"] for item in results} == {"not_configured"}
        return
    assert results[0] == results[1]
    assert results[0]["status"] in {"count", "unavailable"}
    assert results[2]["status"] in {"not_found", "unavailable"}
    empty = client.post(base + "/ticket-counts", json={"queries": []}, headers=headers())
    assert empty.status_code == 422, empty.text
