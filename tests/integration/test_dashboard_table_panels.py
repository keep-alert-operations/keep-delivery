"""Alert table panels through the real Product API and PostgreSQL (REV-63)."""
# ruff: noqa: F811

from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401
from test_configuration_deletion import configuration, create, headers  # noqa: F401
from test_dashboard_panel_types import count_panel, html_panel, save

pytestmark = pytest.mark.integration


def table_panel(preset, x=6, **fields):
    return {"id": str(uuid4()), "type": "table", "preset_id": preset, "title": None, "x": x, "y": 0,
            "w": 6, "h": 4, "rows": 10, "link": None, **fields}


def test_table_panels_store_their_preset_and_rows(configuration):
    """Requirements: REV-63; dashboard operations 2 and 14."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    counted = count_panel(preset["id"])
    listed = table_panel(preset["id"], rows=25, title="Latest")
    noted = html_panel("<p>Hi</p>", x=3)
    response, _ = save(configuration, [counted, noted, listed])
    assert response.status_code == 201, response.text
    panels = {panel["id"]: panel for panel in response.json()["data"]["panels"]}
    assert panels[listed["id"]] == listed
    assert "thresholds" not in panels[listed["id"]]
    dashboard = response.json()["data"]
    read = client.get(base + "/dashboards/" + dashboard["id"])
    assert read.json()["data"]["panels"] == response.json()["data"]["panels"]
    history = client.get(base + "/dashboards/" + dashboard["id"] + "/history/1")
    assert history.status_code == 200, history.text
    assert history.json()["data"]["definition"]["panels"] == response.json()["data"]["panels"]


@pytest.mark.parametrize("rows, status", [(1, 201), (50, 201), (0, 422), (51, 422)])
def test_table_rows_are_bounded(configuration, rows, status):
    """Requirements: REV-63; dashboard operations 2."""
    preset, _ = create(configuration)
    response, _ = save(configuration, [table_panel(preset["id"], x=0, rows=rows)])
    assert response.status_code == status, response.text


def test_table_panels_reference_and_block_their_preset(configuration):
    """Requirements: REV-63; dashboard operations 2 and 13."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    listed = table_panel(preset["id"], x=0)
    created, _ = save(configuration, [listed])
    assert created.status_code == 201, created.text
    references = client.get(base + "/presets/" + preset["id"] + "/references")
    assert references.status_code == 200, references.text
    assert [item["panel_id"] for item in references.json()["data"]] == [listed["id"]]
    blocked = client.delete(base + "/presets/" + preset["id"], headers=headers())
    assert blocked.status_code == 409, blocked.text
    missing, _ = save(configuration, [table_panel(str(uuid4()), x=0)])
    assert missing.status_code == 404, missing.text


def test_a_table_panel_keeps_its_type(configuration):
    """Requirements: REV-63; dashboard operations 2 and 14."""
    preset, _ = create(configuration)
    listed = table_panel(preset["id"], x=0)
    created, _ = save(configuration, [listed])
    assert created.status_code == 201, created.text
    dashboard = created.json()["data"]
    changed, _ = save(
        configuration,
        [count_panel(preset["id"], id=listed["id"])],
        dashboard=dashboard,
        revision="1",
    )
    assert changed.status_code == 422, changed.text
    assert changed.json()["error"]["code"] == "panel_type_changed"
    resized, _ = save(configuration, [{**listed, "rows": 5}], dashboard=dashboard, revision="1")
    assert resized.status_code == 200, resized.text
    assert resized.json()["data"]["panels"][0]["rows"] == 5
