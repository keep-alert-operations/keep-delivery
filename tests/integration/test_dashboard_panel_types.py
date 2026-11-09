"""Dashboard panel types through the real Product API and PostgreSQL (REV-57)."""
# ruff: noqa: F811

from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401
from test_configuration_deletion import configuration, create, headers  # noqa: F401

pytestmark = pytest.mark.integration


def count_panel(preset, x=0, **fields):
    return {"id": str(uuid4()), "preset_id": preset, "title": None, "x": x, "y": 0, "w": 3, "h": 2,
            "thresholds": [], "link": None, **fields}


def html_panel(html, x=3, **fields):
    return {"id": str(uuid4()), "type": "html", "title": "Notes", "x": x, "y": 0, "w": 3, "h": 2,
            "html": html, "link": None, **fields}


def save(configuration, panels, *, dashboard=None, revision=None, command=None):
    client, base, *_ = configuration
    body = {"command_id": command or str(uuid4()), "name": str(uuid4()), "panels": panels}
    if dashboard:
        body.update(name=dashboard["name"], expected_revision=revision)
        return client.put(base + "/dashboards/" + dashboard["id"], json=body), body
    return client.post(base + "/dashboards", json=body), body


def test_mixed_panels_store_each_type_with_its_own_settings(configuration):
    """Requirements: REV-57; dashboard operations 2, 5 and 14."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    counted = count_panel(preset["id"])
    noted = html_panel('<p onclick="x()">Hi<script>t()</script></p>')
    response, body = save(configuration, [counted, noted])
    assert response.status_code == 201, response.text
    panels = {panel["id"]: panel for panel in response.json()["data"]["panels"]}
    assert panels[counted["id"]] == {**counted, "type": "count"}
    assert panels[noted["id"]] == {**noted, "html": "<p>Hi</p>"}
    assert "preset_id" not in panels[noted["id"]]
    dashboard = response.json()["data"]
    read = client.get(base + "/dashboards/" + dashboard["id"])
    assert read.json()["data"]["panels"] == response.json()["data"]["panels"]
    history = client.get(base + "/dashboards/" + dashboard["id"] + "/history/1")
    assert history.status_code == 200, history.text
    assert history.json()["data"]["definition"]["panels"] == response.json()["data"]["panels"]


def test_retries_match_with_or_without_the_count_type(configuration):
    """Requirements: REV-57; dashboard operations 2; shared commands idempotent retries."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    first, body = save(configuration, [count_panel(preset["id"])])
    assert first.status_code == 201, first.text
    retried = client.post(base + "/dashboards", json=body)
    assert retried.json() == first.json()
    typed = {**body, "panels": [{**body["panels"][0], "type": "count"}]}
    assert client.post(base + "/dashboards", json=typed).json() == first.json()


def test_a_panel_keeps_its_type(configuration):
    """Requirements: REV-57; dashboard operations 2 and 14."""
    preset, _ = create(configuration)
    noted = html_panel("<p>Hi</p>", x=0)
    created, _ = save(configuration, [noted])
    assert created.status_code == 201, created.text
    dashboard = created.json()["data"]
    changed, _ = save(
        configuration,
        [count_panel(preset["id"], id=noted["id"])],
        dashboard=dashboard,
        revision="1",
    )
    assert changed.status_code == 422, changed.text
    assert changed.json()["error"]["code"] == "panel_type_changed"
    same, _ = save(configuration, [{**noted, "html": "<p>Changed</p>"}], dashboard=dashboard, revision="1")
    assert same.status_code == 200, same.text


def test_html_size_is_bounded_after_sanitizing(configuration):
    """Requirements: REV-57; dashboard operations 5."""
    fits, _ = save(configuration, [html_panel("a" * 16_000 + "<script>" + "b" * 50 + "</script>")])
    assert fits.status_code == 201, fits.text
    large, _ = save(configuration, [html_panel("a" * 16_001)])
    assert large.status_code == 422, large.text


def test_validate_reports_what_sanitizing_removes(configuration):
    """Requirements: REV-57; dashboard operations 14."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    noted = html_panel('<p>\n<a href="javascript:x()">x</a>\n</p>')
    response = client.post(
        base + "/dashboards/validate",
        json={"name": "Check", "panels": [count_panel(preset["id"]), noted]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "valid": True,
        "removed": [{"panel_id": noted["id"], "line": 2, "what": "javascript: link"}],
    }


def test_html_panels_do_not_reference_or_block_presets(configuration):
    """Requirements: REV-57; dashboard operations 2 and 13."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    created, _ = save(configuration, [html_panel("<p>Hi</p>", x=0)])
    assert created.status_code == 201, created.text
    references = client.get(base + "/presets/" + preset["id"] + "/references")
    assert references.status_code == 200, references.text
    assert references.json()["data"] == []
    deleted = client.delete(base + "/presets/" + preset["id"], headers=headers())
    assert deleted.status_code == 200, deleted.text
    assert client.get(base + "/dashboards/" + created.json()["data"]["id"]).status_code == 200
