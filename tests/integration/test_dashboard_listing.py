"""Dashboard ordering uses real PG keysets and retained definition boundaries."""
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401
from test_configuration_deletion import configuration, create, headers  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "sort,direction",
    [("name", "asc"), ("name", "desc"), ("updated_at", "asc"), ("updated_at", "desc")],
)
def test_dashboard_ordering_ties_filters_and_panel_metadata(
    configuration, real_db, sort, direction
):
    client, base, _, tenant, actor = configuration
    prefix = str(uuid4())
    preset, _ = create(configuration)
    rows = []
    for index, suffix in enumerate(("A", "C", "B")):
        row, _ = create(configuration, "dashboards", preset=preset["id"], name=prefix + suffix)
        updated = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index // 2)
        with real_db.transaction(tenant) as conn:
            conn.execute(
                "UPDATE dashboards SET updated_at=%s WHERE tenant_id=%s AND id=%s",
                (updated, tenant, row["id"]),
            )
        rows.append({**row, "updated_at": updated})
    expected = sorted(rows, key=lambda row: row["id"])
    expected.sort(key=lambda row: row[sort], reverse=direction == "desc")
    path = base + "/dashboards"
    params = {"name": prefix, "sort": sort, "direction": direction, "limit": 1}
    seen, cursor = [], None
    while True:
        response = client.get(path, params={**params, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        page = response.json()
        for row in page["data"]:
            seen.append(row["id"])
            assert row["panel_count"] == "1" and row["updated_by"] == str(actor)
        cursor = page["next_cursor"]
        if not cursor:
            break
        assert len(seen) <= len(rows)
        changed = client.get(
            path,
            params={
                **params,
                "cursor": cursor,
                "direction": "desc" if direction == "asc" else "asc",
            },
        )
        assert changed.status_code == 409
    assert seen == [row["id"] for row in expected]
    assert client.get(path, params={"sort": "arbitrary_sql"}).status_code == 422


def test_deleted_dashboard_sort_uses_retained_recording_time(configuration):
    client, base, *_ = configuration
    prefix = str(uuid4())
    preset, _ = create(configuration)
    dashboards = [
        create(configuration, "dashboards", preset=preset["id"], name=prefix + str(index))[0]
        for index in range(2)
    ]
    for row in dashboards:
        result = client.delete(base + "/dashboards/" + row["id"], headers=headers())
        assert result.status_code == 200, result.text
    path = base + "/dashboards"
    params = {
        "name": prefix,
        "state": "deleted",
        "sort": "updated_at",
        "direction": "desc",
        "limit": 1,
    }
    first = client.get(path, params=params)
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["data"][0]["id"] == dashboards[1]["id"]
    assert page["data"][0]["panel_count"] == "1"
    second = client.get(path, params={**params, "cursor": page["next_cursor"]})
    assert second.status_code == 200, second.text
    assert [row["id"] for row in second.json()["data"]] == [dashboards[0]["id"]]
    assert second.json()["next_cursor"] is None
