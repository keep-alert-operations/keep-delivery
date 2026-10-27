"""REV-44 grouping through the Product API: real PG/auth/native CEL, controlled ES responses.

Elasticsearch grouping semantics (exact composite counts, ordering, cursors) are
proven against a real two-shard index in appchi-domain's compatibility tests.
"""

from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401 -- shared authenticated session fixture

pytestmark = pytest.mark.integration

SEVERITY = {"critical": 0, "high": 0, "warning": 0, "clear": 0}


class GroupedSearch:
    def __init__(self):
        self.calls, self.closed = [], []
        self.buckets, self.groups, self.total = [], [], 0

    def options(self, **kwargs):
        return self

    def open_point_in_time(self, **kwargs):
        self.calls.append(("open", kwargs))
        return {"id": "pit-original", "_shards": {"total": 1, "successful": 1, "failed": 0}}

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        result = {
            "pit_id": "pit-replacement",
            "timed_out": False,
            "_shards": {"total": 1, "successful": 1, "failed": 0},
            "hits": {"total": {"relation": "eq", "value": self.total}, "hits": []},
        }
        if "aggs" in kwargs:
            result["aggregations"] = {"groups": {"buckets": self.buckets}}
        else:
            hits = next((hits for key, hits in self.groups), [])
            result["hits"] = {"total": {"relation": "eq", "value": len(hits)}, "hits": hits}
        return result

    def msearch(self, **kwargs):
        self.calls.append(("msearch", kwargs))
        return {
            "responses": [
                {
                    "timed_out": False,
                    "_shards": {"total": 1, "successful": 1, "failed": 0},
                    "hits": {"hits": hits},
                }
                for _, hits in self.groups[: len(kwargs["searches"]) // 2]
            ]
        }

    def close_point_in_time(self, *, id):
        self.closed.append(id)


def hit(tenant, alert, assignee, minute):
    return {
        "_id": alert,
        "sort": [False, 1, minute, alert],
        "_source": {
            "tenant_id": str(tenant),
            "deleted": False,
            "display": {
                "id": alert,
                "message": f"Row {minute}",
                "status": "firing",
                "assignee_id": assignee,
                "severity": "high",
            },
        },
    }


def bucket(key, count, **severity):
    return {
        "key": {"value": key},
        "doc_count": count,
        "severity": {
            "buckets": {name: {"doc_count": severity.get(name, 0)} for name in SEVERITY}
        },
    }


@pytest.fixture
def grouping(request, real_db, tenant):
    client, actor = request.getfixturevalue("browser")
    client.app.state.settings.elasticsearch_url = "https://response-controlled.invalid"
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='search'")
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, actor),
        )
        conn.execute(
            "UPDATE tenant_view_state SET index_epoch='group-test',verified_at=clock_timestamp() WHERE tenant_id=%s",
            (tenant,),
        )
    elastic = GroupedSearch()
    client.app.state.search_client = elastic
    return client, actor, tenant, elastic


def definition(**extra):
    return {
        "name": "Grouped " + uuid4().hex[:8],
        "cel": "true",
        "sort": [{"field": "timestamp", "direction": "desc"}],
        "columns": ["severity", "message"],
        **extra,
    }


def test_group_by_saves_revisions_history_and_old_retries(grouping, real_db):
    """Requirements: REV-44; preset operations 3, 4, 10 and 16."""
    client, _, tenant, _ = grouping
    root = f"/api/tenants/{tenant}/presets"
    command = str(uuid4())
    body = {"command_id": command, **definition()}  # a client from before REV-44
    created = client.post(root, json=body)
    assert created.status_code == 201, created.text
    preset = created.json()["data"]
    assert preset["group_by"] is None
    # The same command with an explicit null retries the committed result.
    retried = client.post(root, json={**body, "group_by": None})
    assert retried.status_code == 201 and retried.json()["data"]["id"] == preset["id"]
    assert client.post(root, json={**body, "group_by": "site"}).status_code == 409
    replace = {
        **{key: body[key] for key in ("name", "cel", "sort", "columns")},
        "command_id": str(uuid4()),
        "expected_revision": "1",
        "group_by": "application",
    }
    updated = client.put(f"{root}/{preset['id']}", json=replace)
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["group_by"] == "application"
    assert updated.json()["meta"]["revision"] == "2"
    same = client.put(
        f"{root}/{preset['id']}",
        json={**replace, "command_id": str(uuid4()), "expected_revision": "2"},
    )
    assert same.status_code == 200 and same.json()["meta"]["revision"] == "2"
    assert client.get(f"{root}/{preset['id']}").json()["data"]["group_by"] == "application"
    with real_db.transaction() as conn:
        snapshot = conn.execute(
            "SELECT snapshot FROM preset_revisions WHERE id=%s AND revision=2", (preset["id"],)
        ).fetchone()["snapshot"]
        changed = conn.execute(
            "SELECT changed_fields FROM configuration_activity WHERE resource_id=%s AND revision=2",
            (preset["id"],),
        ).fetchone()["changed_fields"]
    assert snapshot["group_by"] == "application" and changed == ["group_by"]
    for value in ("message", "timestamp", "operator"):
        invalid = {**replace, "command_id": str(uuid4()), "expected_revision": "2", "group_by": value}
        assert client.put(f"{root}/{preset['id']}", json=invalid).status_code == 422


def test_grouped_preview_labels_order_and_group_rows(grouping, real_db):
    """Requirements: REV-44; preset operations 4 and 16; technical baseline 17."""
    client, actor, tenant, elastic = grouping
    root = f"/api/tenants/{tenant}"
    preset = client.post(
        root + "/presets",
        json={"command_id": str(uuid4()), **definition(group_by="assignee_id")},
    ).json()["data"]
    mine, unowned = str(uuid4()), str(uuid4())
    with real_db.transaction() as conn:
        for alert in (mine, unowned):
            conn.execute("INSERT INTO alert_identities(id,tenant_id) VALUES(%s,%s)", (alert, tenant))
    elastic.total = 2
    elastic.buckets = [bucket(None, 1, high=1), bucket(str(actor), 1, high=1)]
    elastic.groups = [
        (str(actor), [hit(tenant, mine, str(actor), 2)]),
        (None, [hit(tenant, unowned, None, 1)]),
    ]
    saved = {"preset_id": preset["id"], "expected_revision": "1", "rows_per_group": 2}
    response = client.post(root + "/presets/preview/groups", json=saved)
    assert response.status_code == 200, response.text
    assert response.headers["etag"] == '"1"'
    body = response.json()
    assert [group["key"] for group in body["data"]] == [str(actor), None]
    assert body["data"][0]["label"] == "Synthetic user" and body["data"][1]["label"] is None
    assert [group["total"] for group in body["data"]] == ["1", "1"]
    assert body["data"][0]["severity"]["high"] == "1"
    assert body["data"][0]["data"][0]["assignee_display_name"] == "Synthetic user"
    assert body["meta"]["total"] == "2" and body["meta"]["group_count"] == "2"
    assert body["meta"]["group_by"] == "assignee_id"
    assert body["meta"]["applied"]["definition"]["group_by"] == "assignee_id"
    aggregation = next(kwargs for kind, kwargs in elastic.calls if kind == "search")["aggs"]
    assert aggregation["groups"]["composite"]["sources"] == [
        {"value": {"terms": {"field": "assignee_id", "missing_bucket": True}}}
    ]
    searches = next(kwargs for kind, kwargs in elastic.calls if kind == "msearch")["searches"]
    assert {"term": {"missing_assignee_id": True}} in searches[3]["query"]["bool"]["filter"]
    # One group's rows through the ordinary preview, with the grouping filter.
    elastic.calls.clear()
    rows = client.post(
        root + "/presets/preview", json={**saved | {"limit": 5}, "group_empty": True}
    )
    assert rows.status_code == 422  # rows_per_group is not a preview field
    rows = client.post(
        root + "/presets/preview",
        json={"preset_id": preset["id"], "expected_revision": "1", "group_key": str(actor)},
    )
    assert rows.status_code == 200, rows.text
    search = next(kwargs for kind, kwargs in elastic.calls if kind == "search")
    assert {"term": {"assignee_id": str(actor)}} in search["query"]["bool"]["filter"]
    # A temporary override is a candidate; a definition without grouping is refused.
    candidate = {**definition(), "group_by": "severity"}
    del candidate["name"]
    elastic.buckets = [bucket("high", 2, high=2) | {"status": {"buckets": {
        "firing": {"doc_count": 2}, "acknowledged": {"doc_count": 0}, "resolved": {"doc_count": 0}
    }}}]
    elastic.groups = [("high", [hit(tenant, mine, None, 2), hit(tenant, unowned, None, 1)])]
    override = client.post(root + "/presets/preview/groups", json=candidate)
    assert override.status_code == 200, override.text
    assert override.json()["data"][0]["status"]["firing"] == "2"
    assert "etag" not in override.headers
    candidate.pop("group_by")
    refused = client.post(root + "/presets/preview/groups", json=candidate)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "grouping_required"
    unsaved_rows = client.post(root + "/presets/preview", json={**candidate, "group_key": "x"})
    assert unsaved_rows.status_code == 422


def test_feed_groups_and_group_rows(grouping, real_db):
    """Requirements: REV-44; alert operations 24; technical baseline 17."""
    client, _, tenant, elastic = grouping
    root = f"/api/tenants/{tenant}/alerts"
    first, second = str(uuid4()), str(uuid4())
    with real_db.transaction() as conn:
        for alert in (first, second):
            conn.execute("INSERT INTO alert_identities(id,tenant_id) VALUES(%s,%s)", (alert, tenant))
    elastic.total = 3
    elastic.buckets = [bucket("eu", 2, high=2), bucket(None, 1, high=1)]
    elastic.groups = [
        ("eu", [hit(tenant, first, None, 3)]),
        (None, [hit(tenant, second, None, 1)]),
    ]
    response = client.post(
        root + "/groups", json={"group_by": "site", "rows_per_group": 1, "group_limit": 1}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [group["key"] for group in body["data"]] == ["eu"]
    assert body["data"][0]["total"] == "2" and body["data"][0]["next_cursor"]
    assert body["next_group_cursor"] and body["meta"]["group_count"] == "2"
    assert body["meta"]["close_token"] and body["meta"]["relative"] is False
    elastic.calls.clear()
    rows = client.get(
        root,
        params={"group_by": "site", "group_key": "eu", "cursor": body["data"][0]["next_cursor"]},
    )
    assert rows.status_code == 200, rows.text
    search = next(kwargs for kind, kwargs in elastic.calls if kind == "search")
    assert search["search_after"] == [False, 1, 3, first]
    assert {"term": {"site": "eu"}} in search["query"]["bool"]["filter"]
    assert not any(kind == "open" for kind, _ in elastic.calls)
    mismatched = client.get(
        root,
        params={"group_by": "site", "group_empty": "true", "cursor": body["data"][0]["next_cursor"]},
    )
    assert mismatched.status_code == 409
    elastic.groups = [(None, [hit(tenant, second, None, 1)])]
    following = client.post(
        root + "/groups",
        json={
            "group_by": "site",
            "rows_per_group": 1,
            "group_limit": 1,
            "group_cursor": body["next_group_cursor"],
        },
    )
    assert following.status_code == 200, following.text
    assert [group["key"] for group in following.json()["data"]] == [None]
    for params in (
        {"group_key": "eu"},
        {"group_by": "site"},
        {"group_by": "site", "group_key": "eu", "group_empty": "true"},
        {"group_by": "message", "group_key": "x"},
    ):
        assert client.get(root, params=params).status_code == 422, params
    assert client.post(root + "/groups", json={"group_by": "timestamp"}).status_code == 422
    too_many = client.post(
        root + "/groups", json={"group_by": "site", "group_limit": 50, "rows_per_group": 5}
    )
    assert too_many.status_code == 422
