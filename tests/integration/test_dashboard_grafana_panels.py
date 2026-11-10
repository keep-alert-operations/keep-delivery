"""Grafana panels through the real Product API and PostgreSQL (REV-68)."""
# ruff: noqa: F811

from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401
from test_configuration_deletion import configuration, create  # noqa: F401
from test_dashboard_panel_types import count_panel, save

pytestmark = pytest.mark.integration

ALLOWED = "https://grafana.example.com,https://tools.example.com/grafana/"
PANEL = "https://grafana.example.com/d-solo/ck81x/checkout?orgId=1&panelId=4"


@pytest.fixture
def browser_settings():
    # Bound before the test app starts, which parses the allowed Grafana once.
    return {"grafana_panel_origins": ALLOWED}


def grafana_panel(x=0, **fields):
    return {"id": str(uuid4()), "type": "grafana", "title": None, "x": x, "y": 0,
            "w": 6, "h": 6, "url": PANEL, "link": None, **fields}


def test_grafana_panels_store_their_address(configuration):
    """Requirements: REV-68; dashboard operations 2, 13 and 14."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    graph = grafana_panel(
        x=3, title="Latency",
        link={"url": "https://grafana.example.com/d/ck81x/checkout?orgId=1&viewPanel=4",
              "label": "Open in Grafana"},
    )
    nested = grafana_panel(x=0, y=6, w=3, url="https://tools.example.com/grafana/d-solo/a/b?panelId=2")
    response, _ = save(configuration, [count_panel(preset["id"]), graph, nested])
    assert response.status_code == 201, response.text
    panels = {panel["id"]: panel for panel in response.json()["data"]["panels"]}
    assert panels[graph["id"]] == graph
    assert panels[nested["id"]]["url"] == nested["url"]
    assert "preset_id" not in panels[graph["id"]] and "thresholds" not in panels[graph["id"]]
    dashboard = response.json()["data"]
    read = client.get(base + "/dashboards/" + dashboard["id"])
    assert read.json()["data"]["panels"] == dashboard["panels"]
    history = client.get(base + "/dashboards/" + dashboard["id"] + "/history/1")
    assert history.status_code == 200, history.text
    assert history.json()["data"]["definition"]["panels"] == dashboard["panels"]


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://grafana-old.example.com/d-solo/a/b?panelId=1", "isn't an allowed Grafana"),
        ("https://grafana.example.com/d/ck81x/checkout?viewPanel=4", "single-panel address"),
        ("https://tools.example.com/d-solo/a/b?panelId=1", "isn't an allowed Grafana"),
        ("http://grafana.example.com/d-solo/a/b?panelId=1", "must be https"),
        ("https://app.test/d-solo/a/b?panelId=1", "Keep's own pages"),
    ],
)
def test_grafana_addresses_that_are_not_allowed_are_rejected(configuration, url, message):
    """Requirements: REV-68; dashboard operations 3 and 14."""
    response, _ = save(configuration, [grafana_panel(url=url)])
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "grafana_url_invalid"
    assert message in error["message"]


@pytest.mark.parametrize(
    "fields",
    [
        {"url": ""},
        {"url": "https:grafana.example.com/d-solo/a"},
        {"url": "https://admin:pw@grafana.example.com/d-solo/a"},
        {"url": "https://grafana.example.com/" + "x" * 2030},
        {"thresholds": []},
        {"preset_id": str(uuid4())},
    ],
)
def test_invalid_grafana_panels_are_rejected(configuration, fields):
    """Requirements: REV-68; dashboard operations 2 and 3."""
    response, _ = save(configuration, [grafana_panel(**fields)])
    assert response.status_code == 422, response.text


def test_a_grafana_panel_keeps_its_type(configuration):
    """Requirements: REV-68; dashboard operations 2 and 14."""
    preset, _ = create(configuration)
    graph = grafana_panel()
    created, _ = save(configuration, [graph])
    assert created.status_code == 201, created.text
    dashboard = created.json()["data"]
    changed, _ = save(configuration, [count_panel(preset["id"], id=graph["id"])],
                      dashboard=dashboard, revision="1")
    assert changed.status_code == 422, changed.text
    assert changed.json()["error"]["code"] == "panel_type_changed"
    moved = "https://grafana.example.com/d-solo/ck81x/checkout?orgId=1&panelId=7"
    edited, _ = save(configuration, [{**graph, "url": moved}], dashboard=dashboard, revision="1")
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["panels"][0]["url"] == moved


def test_grafana_panel_status_lists_the_allowed_grafana(configuration):
    """Requirements: REV-68; dashboard operations 14."""
    client, base, *_ = configuration
    status = client.get(base + "/grafana-panels/status")
    assert status.status_code == 200, status.text
    assert status.json()["data"] == {
        "enabled": True,
        "origins": [
            {"origin": "https://grafana.example.com", "path_prefix": ""},
            {"origin": "https://tools.example.com", "path_prefix": "/grafana"},
        ],
    }
