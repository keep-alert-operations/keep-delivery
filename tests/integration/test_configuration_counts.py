"""Real PG/auth/native CEL with controlled ES responses (not ES parity evidence)."""

from uuid import uuid4

from psycopg.types.json import Jsonb
import pytest

from test_product import browser  # noqa: F401 -- shared authenticated session fixture

pytestmark = pytest.mark.integration


class SearchResponses:
    def __init__(self):
        self.calls, self.closed, self.after_search = [], [], None
        self.partial = False
        self.hits = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def options(self, **kwargs):
        return self

    def open_point_in_time(self, **kwargs):
        self.calls.append(("open", kwargs))
        return {"id": "pit-original", "_shards": {"total": 1, "successful": 1, "failed": 0}}

    def _response(self):
        return {
            "pit_id": "pit-replacement",
            "timed_out": self.partial,
            "_shards": {"total": 1, "successful": 1, "failed": 0},
            "hits": {"total": {"relation": "eq", "value": 12}, "hits": self.hits},
        }

    def msearch(self, **kwargs):
        self.calls.append(("msearch", kwargs))
        if self.after_search:
            self.after_search()
        return {"responses": [self._response() for _ in kwargs["searches"][1::2]]}

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        if self.after_search:
            self.after_search()
        return self._response()

    def close_point_in_time(self, *, id):
        self.closed.append(id)


@pytest.fixture
def environment(request, real_db, tenant, monkeypatch):
    client, actor = request.getfixturevalue("browser")
    client.app.state.settings.elasticsearch_url = "https://response-controlled.invalid"
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='search'")
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, actor),
        )
        conn.execute(
            "UPDATE tenant_view_state SET index_epoch='count-test',verified_at=clock_timestamp() WHERE tenant_id=%s",
            (tenant,),
        )
    root = f"/api/tenants/{tenant}"
    preset = client.post(
        root + "/presets",
        json={
            "command_id": str(uuid4()),
            "name": "Count preset",
            "cel": "true",
            "sort": [
                {"field": "severity", "direction": "asc"},
                {"field": "timestamp", "direction": "desc"},
            ],
            "columns": ["message", "severity"],
        },
    )
    assert preset.status_code == 201, preset.text
    preset = preset.json()["data"]
    panels = [
        {
            "id": str(uuid4()),
            "preset_id": preset["id"],
            "x": 0,
            "y": n * 2,
            "w": 3,
            "h": 2,
            "thresholds": [],
        }
        for n in range(2)
    ]
    dashboard = client.post(
        root + "/dashboards",
        json={"command_id": str(uuid4()), "name": "Count dashboard", "panels": panels},
    )
    assert dashboard.status_code == 201, dashboard.text
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, actor),
        )
    elastic = SearchResponses()
    client.app.state.search_client = elastic
    return client, actor, tenant, preset, dashboard.json()["data"], elastic


@pytest.mark.parametrize("relative", [False, True])
def test_viewer_counts_duplicate_presets_and_unsaved_dashboard(environment, real_db, relative):
    client, _, tenant, preset, dashboard, elastic = environment
    if relative:
        with real_db.transaction(tenant) as conn:
            conn.execute(
                "UPDATE presets SET cel=%s WHERE tenant_id=%s AND id=%s",
                ('alert.time_created >= now() - duration("1h")', tenant, preset["id"]),
            )
    root = f"/api/tenants/{tenant}"
    response = client.post(
        root + f"/dashboards/{dashboard['id']}/counts", json={"expected_revision": "1"}
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["relative"] is relative
    assert response.headers["etag"] == '"1"'
    assert data["preset_revisions"] == {preset["id"]: "1"}
    assert [panel["count"] for panel in data["panels"]] == ["12", "12"]
    assert len(elastic.calls[1][1]["searches"]) == 2
    assert set(elastic.closed) == {"pit-original", "pit-replacement"}
    preview = client.post(
        root + "/dashboards/preview",
        json={
            "name": "Draft",
            "panels": dashboard["panels"],
            "preset_revisions": {preset["id"]: "1"},
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["relative"] is relative
    assert preview.json()["data"]["dashboard_revision"] is None
    stale = client.post(
        root + "/dashboards/preview",
        json={
            "name": "Draft",
            "panels": dashboard["panels"],
            "preset_revisions": {preset["id"]: "2"},
        },
    )
    assert stale.status_code == 409, stale.text


@pytest.mark.parametrize(
    "change,code",
    [
        ("preset", "preset_changed"),
        ("dashboard", "dashboard_changed"),
        ("preset_deleted", "preset_changed"),
        ("dashboard_deleted", "dashboard_changed"),
        ("purge", "count_snapshot_changed"),
        ("access", "not_found"),
    ],
)
def test_post_es_authority_changes_discard_counts(environment, real_db, change, code):
    client, actor, tenant, preset, dashboard, elastic = environment

    def changed():
        with real_db.transaction() as conn:
            if change == "preset":
                conn.execute("UPDATE presets SET revision=revision+1 WHERE id=%s", (preset["id"],))
            elif change == "dashboard":
                conn.execute(
                    "UPDATE dashboards SET revision=revision+1 WHERE id=%s", (dashboard["id"],)
                )
            elif change == "preset_deleted":
                conn.execute(
                    "UPDATE presets SET deleted_at=clock_timestamp() WHERE id=%s", (preset["id"],)
                )
            elif change == "dashboard_deleted":
                conn.execute(
                    "UPDATE dashboards SET deleted_at=clock_timestamp() WHERE id=%s",
                    (dashboard["id"],),
                )
            elif change == "purge":
                conn.execute(
                    "UPDATE tenant_view_state SET purge_generation=purge_generation+1 WHERE tenant_id=%s",
                    (tenant,),
                )
            else:
                conn.execute(
                    "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s",
                    (tenant, actor),
                )

    elastic.after_search = changed
    response = client.post(
        f"/api/tenants/{tenant}/dashboards/{dashboard['id']}/counts",
        json={"expected_revision": "1"},
    )
    assert response.status_code == (404 if change == "access" else 409), response.text
    assert response.json()["error"]["code"] == code
    assert "data" not in response.json() and elastic.closed


def test_partial_counts_never_zero_and_retirement_pending_never_counted(environment, real_db):
    client, _, tenant, _, dashboard, elastic = environment
    url = f"/api/tenants/{tenant}/dashboards/{dashboard['id']}/counts"
    elastic.partial = True
    response = client.post(url, json={"expected_revision": "1"})
    assert response.status_code == 503 and "data" not in response.json()
    elastic.calls.clear()
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO projection_work(tenant_id,seq,alert_id,version,snapshot) VALUES(%s,1,%s,1,%s)",
            (tenant, uuid4(), Jsonb({"live": False})),
        )
    response = client.post(url, json={"expected_revision": "1"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retention_projection_pending"
    assert elastic.calls == []


def test_saved_and_candidate_preset_previews_use_validated_multisort(environment):
    client, _, tenant, preset, _, elastic = environment
    url = f"/api/tenants/{tenant}/presets/preview"
    saved = {"preset_id": preset["id"], "expected_revision": "1", "q": "needle", "limit": 10}
    response = client.post(url, json=saved)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["total"] == "12"
    assert response.headers["etag"] == '"1"'
    search = next(arguments for kind, arguments in elastic.calls if kind == "search")
    assert search["sort"] == [
        {"severity_clear_last": "asc"},
        {"severity_rank": "asc"},
        {"last_received_at": {"order": "desc", "missing": "_last"}},
        {"id": "asc"},
    ]
    assert {"term": {"search_tokens": "needle"}} in search["query"]["bool"]["filter"]
    assert client.post(url, json={**saved, "expected_revision": "2"}).status_code == 409
    assert client.post(url, json={**saved, "cel": "true"}).status_code == 422
    candidate = {
        "cel": "true",
        "sort": [{"field": "timestamp", "direction": "desc"}],
        "columns": ["message"],
        "include_suppressed": True,
    }
    elastic.calls.clear()
    response = client.post(url, json=candidate)
    assert response.status_code == 200, response.text
    search = next(arguments for kind, arguments in elastic.calls if kind == "search")
    assert {"term": {"suppressed": False}} not in search["query"]["bool"]["filter"]
    assert "etag" not in response.headers
    assert client.post(url, json={**candidate, "cel": "alert.unknown == 1"}).status_code == 422
    assert client.post(url, json=candidate, headers={"X-CSRF-Token": "invalid"}).status_code == 403


def test_preview_cursor_keeps_clock_and_rejects_changed_saved_definition(environment, real_db):
    client, _, tenant, preset, _, elastic = environment
    alert = str(uuid4())
    with real_db.transaction() as conn:
        conn.execute("INSERT INTO alert_identities(id,tenant_id) VALUES(%s,%s)", (alert, tenant))
    elastic.hits = [
        {
            "_id": alert,
            "sort": [False, 1, 12345, alert],
            "_source": {
                "tenant_id": str(tenant),
                "deleted": False,
                "display": {"id": alert, "message": "Authorized row"},
            },
        }
    ]
    url = f"/api/tenants/{tenant}/presets/preview"
    body = {"preset_id": preset["id"], "expected_revision": "1", "limit": 1}
    first = client.post(url, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["data"][0]["id"] == alert
    cursor = first.json()["next_cursor"]
    assert cursor
    elastic.calls.clear()
    second = client.post(url, json={**body, "cursor": cursor})
    assert second.status_code == 200, second.text
    assert first.json()["meta"]["evaluated_at"] == second.json()["meta"]["evaluated_at"]
    assert not any(kind == "open" for kind, _ in elastic.calls)
    search = next(arguments for kind, arguments in elastic.calls if kind == "search")
    assert search["search_after"] == [False, 1, 12345, alert]
    with real_db.transaction() as conn:
        conn.execute("UPDATE presets SET revision=2 WHERE id=%s", (preset["id"],))
    elastic.calls.clear()
    stale = client.post(url, json={**body, "cursor": cursor})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "preset_changed"
    assert elastic.calls == []
