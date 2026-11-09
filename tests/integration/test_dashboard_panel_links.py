"""Panel links through the real Product API and PostgreSQL (REV-66)."""
# ruff: noqa: F811

from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401
from test_configuration_deletion import configuration, create, headers  # noqa: F401
from test_dashboard_panel_types import count_panel, html_panel, save
from test_dashboard_table_panels import table_panel
from test_dashboard_servicenow_panels import servicenow_panel

pytestmark = pytest.mark.integration

RUNBOOK = {"url": "https://runbooks.example/payments/critical", "label": "Runbook"}


def image_panel(x=0, y=4, **fields):
    return {"id": str(uuid4()), "type": "image", "title": None, "x": x, "y": y, "w": 3, "h": 2,
            "image_id": None, "url": "https://wiki.example.com/map.png", "fit": "contain",
            "alt": None, "link": None, **fields}


def test_every_panel_type_stores_and_reads_its_link(configuration):
    """Requirements: REV-66 (with REV-65); dashboard operations 2, 3, 14 and 15."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    panels = [
        count_panel(preset["id"], link=RUNBOOK),
        html_panel("<p>Hi</p>", link={"url": "http://calendar.internal:8080/changes", "label": None}),
        table_panel(preset["id"], link={"url": "https://wiki.example/escalation", "label": "Escalation"}),
        image_panel(link={"url": "https://grafana.example/d/abc?var=1", "label": None}),
        servicenow_panel(x=3, y=4, link={"url": "https://servicenow.example/q", "label": "Queue"}),
    ]
    response, _ = save(configuration, panels)
    assert response.status_code == 201, response.text
    dashboard = response.json()["data"]
    stored = {panel["id"]: panel for panel in dashboard["panels"]}
    for panel in panels:
        assert stored[panel["id"]]["link"] == panel["link"]
    read = client.get(base + "/dashboards/" + dashboard["id"])
    assert read.json()["data"]["panels"] == dashboard["panels"]
    history = client.get(base + "/dashboards/" + dashboard["id"] + "/history/1")
    assert history.status_code == 200, history.text
    assert history.json()["data"]["definition"]["panels"] == dashboard["panels"]


def test_button_text_is_trimmed_and_removing_the_link_saves(configuration):
    """Requirements: REV-66; dashboard operations 3 and 8."""
    preset, _ = create(configuration)
    panel = count_panel(preset["id"], link={"url": "https://x.example/", "label": "  Runbook  "})
    created, _ = save(configuration, [panel])
    assert created.status_code == 201, created.text
    dashboard = created.json()["data"]
    assert dashboard["panels"][0]["link"] == {"url": "https://x.example/", "label": "Runbook"}
    removed, _ = save(configuration, [{**panel, "link": None}], dashboard=dashboard, revision="1")
    assert removed.status_code == 200, removed.text
    assert removed.json()["meta"]["revision"] == "2"
    assert removed.json()["data"]["panels"][0]["link"] is None


def test_unchanged_link_is_a_same_value_save(configuration):
    """Requirements: REV-66; dashboard operations 8."""
    preset, _ = create(configuration)
    panel = count_panel(preset["id"], link=RUNBOOK)
    created, _ = save(configuration, [panel])
    dashboard = created.json()["data"]
    again, _ = save(configuration, [panel], dashboard=dashboard, revision="1")
    assert again.status_code == 200, again.text
    assert again.json()["meta"]["revision"] == "1"


def test_commands_sent_before_panel_links_still_match_their_retries(configuration):
    """Requirements: REV-66; dashboard operations 14; shared commands idempotent retries."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    legacy = {key: value for key, value in count_panel(preset["id"]).items() if key != "link"}
    first, body = save(configuration, [legacy])
    assert first.status_code == 201, first.text
    assert first.json()["data"]["panels"][0]["link"] is None
    assert client.post(base + "/dashboards", json=body).json() == first.json()
    explicit = {**body, "panels": [{**legacy, "link": None}]}
    assert client.post(base + "/dashboards", json=explicit).json() == first.json()


@pytest.mark.parametrize(
    "link",
    [
        {"url": "javascript:alert(1)"},
        {"url": "data:text/html,hi"},
        {"url": "ftp://files.example/report"},
        {"url": "/dashboards/other"},
        {"url": "https://user:secret@wiki.example/"},
        {"url": "https://wiki.example/a b"},
        {"url": "https://" + "a" * 2041},
        {"url": "https://x.example", "label": "a" * 41},
        {"url": "https://x.example", "label": "Two\nlines"},
        {"label": "Runbook"},
    ],
)
def test_invalid_links_are_rejected(configuration, link):
    """Requirements: REV-66; dashboard operations 2 and 14."""
    preset, _ = create(configuration)
    response, _ = save(configuration, [count_panel(preset["id"], link=link)])
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_fields"
