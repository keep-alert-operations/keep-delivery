"""User-load regressions without external services or native CEL compilation."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event, Thread, get_ident
from types import SimpleNamespace

import httpx
import pytest

from keep.live import LiveRegistry
from keep.search.counts import CountQuery, CountReader, CountState
from keep.search.reader import SearchReader, SearchScope
from keep.shared.crypto import EnvelopeCipher
from keep.shared.errors import AppError


def test_live_invalidation_does_not_wait_for_slow_tenant_load():
    registry = LiveRegistry()
    registry.register("slow")
    registry.register("other")
    started, release = Event(), Event()

    def load():
        started.set()
        assert release.wait(2)
        return {"view_generation": 1}

    worker = Thread(target=lambda: registry.read("slow", load))
    worker.start()
    try:
        assert started.wait(1)
        registry.invalidate("slow")
        # Another tenant can load before releasing the first tenant's I/O.
        assert registry.read("other", lambda: 9) == 9
    finally:
        release.set()
        worker.join(2)
    # An invalidation during I/O cannot cache the old result as fresh.
    assert registry.read("slow", lambda: {"view_generation": 2})["view_generation"] == 2


@pytest.mark.asyncio
async def test_session_sql_runs_in_worker_while_event_loop_remains_responsive(monkeypatch):
    """Requirements: REV-73 (the activity heartbeat is gone; sign-out is the session write)."""
    from keep.identity import session

    event_loop_thread = get_ident()
    started, release = Event(), Event()

    class Database:
        @contextmanager
        def transaction(self):
            assert get_ident() != event_loop_thread
            started.set()
            assert release.wait(2)
            yield SimpleNamespace(execute=lambda *a: None)

    monkeypatch.setattr(session, "authenticate", lambda *a, **kw: ({}, {"handle_hash": b"test"}))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=Database())))
    task = asyncio.create_task(session.logout(request))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    assert (await asyncio.wait_for(task, 2)).status_code == 200


class Expression:
    source = "true"

    def scoped_query(self, tenant, evaluated_at, include_suppressed=False):
        filters = [{"term": {"tenant_id": tenant}}, {"term": {"deleted": False}}]
        if not include_suppressed:
            filters.append({"term": {"suppressed": False}})
        return {"bool": {"filter": filters}}


class Search:
    def __init__(self):
        self.opens, self.batches, self.closed = 0, [], []

    def options(self, **kwargs):
        return self

    def open_point_in_time(self, **kwargs):
        self.opens += 1
        return {"id": "pit", "_shards": {"total": 1, "successful": 1, "failed": 0}}

    def msearch(self, *, searches):
        self.batches.append(searches)
        return {
            "responses": [
                {
                    "timed_out": False,
                    "_shards": {"total": 1, "successful": 1, "failed": 0},
                    "hits": {"total": {"value": index + 1, "relation": "eq"}},
                }
                for index in range(len(searches) // 2)
            ]
        }

    def close_point_in_time(self, *, id):
        self.closed.append(id)


def test_count_batch_deduplicates_and_respects_text_suppression_then_closes_pit():
    search = Search()
    now = datetime.now(UTC)
    state = CountState("index", 1, 1, 0, now.isoformat(), 0, False, "none")
    normal = CountQuery(Expression(), "CPU", False)
    batch = CountReader(search, "alerts").read(
        {"all": normal, "duplicate": normal, "including": CountQuery(Expression(), "CPU", True)},
        "tenant",
        evaluated_at=now,
        before=state,
        revalidate=lambda: state,
    )
    assert search.opens == 1 and len(search.batches) == 1
    assert len(search.batches[0]) == 4
    assert batch.counts == {"all": 1, "duplicate": 1, "including": 2}
    normal_filters = search.batches[0][1]["query"]["bool"]["filter"]
    inclusive_filters = search.batches[0][3]["query"]["bool"]["filter"]
    assert {"term": {"search_tokens": "cpu"}} in normal_filters
    assert {"term": {"suppressed": False}} in normal_filters
    assert {"term": {"suppressed": False}} not in inclusive_filters
    assert search.closed == ["pit"]


def test_cleanup_token_is_bound_to_session_tenant_epoch_and_cleanup_purpose():
    search = Search()
    reader = SearchReader(
        search,
        "alerts",
        EnvelopeCipher({"test": b"k" * 32}, "test"),
        clock=lambda: None,
        state=lambda _: None,
        authorize=lambda _: None,
        retained_ids=lambda *_: set(),
    )
    scope = SearchScope("tenant", "session", "epoch")
    preview = SearchScope("tenant", "session:preview:query", "epoch")
    token = reader._seal_cursor({"purpose": "close", "pit": "pit"}, scope)
    reader.close_snapshot(token, preview)
    assert search.closed == ["pit"]

    for denied in [
        SearchScope("other", "session", "epoch"),
        SearchScope("tenant", "other", "epoch"),
        SearchScope("tenant", "session", "other"),
    ]:
        with pytest.raises(AppError, match="invalid_cursor"):
            reader.close_snapshot(token, denied)
    with pytest.raises(AppError):
        reader.close_snapshot(reader._seal_cursor({"pit": "pit"}, scope), scope)
    assert search.closed == ["pit"]


def test_search_client_reuses_transport_and_keeps_short_cleanup_budget():
    from keep.search_clients import search_client

    calls = []

    class Client:
        def options(self, **kwargs):
            calls.append(kwargs)
            return self

        def close_point_in_time(self, **kwargs):
            return kwargs

    client = Client()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(search_client=client)))
    assert search_client(request).client is client
    assert search_client(request).client is client
    search_client(request).options(request_timeout=0.25).close_point_in_time(id="pit")
    assert calls[-1]["request_timeout"] <= 0.25


def test_expired_database_budget_prevents_next_statement():
    from keep.db import BudgetConnection
    from keep.request_budget import deadline

    calls = []
    connection = BudgetConnection(SimpleNamespace(execute=lambda *a, **kw: calls.append(a)))
    token = deadline.set(0)
    try:
        with pytest.raises(AppError, match="request_timeout"):
            connection.execute("UPDATE alerts SET revision=revision+1")
    finally:
        deadline.reset(token)
    assert calls == []


def test_database_cursor_bounds_repeated_work_and_reads(monkeypatch):
    from keep.db import BudgetConnection
    from keep.request_budget import request_budget
    import keep.request_budget as budget

    clock = [100.0]
    monkeypatch.setattr(budget, "monotonic", lambda: clock[0])
    writes, statements = [], []

    class Cursor:
        def execute(self, query, params):
            writes.append(params)
            clock[0] += 0.06

        def __next__(self):
            clock[0] += 0.06
            return {"value": 1}

    raw = SimpleNamespace(cursor=Cursor, execute=lambda query: statements.append(query))
    with request_budget(0.1):
        connection = BudgetConnection(raw)
        with pytest.raises(AppError, match="request_timeout"):
            connection.cursor().executemany("UPDATE example", [(1,), (2,), (3,)])
    assert writes == [(1,), (2,)]
    assert len(statements) == 2
    with request_budget(0.1):
        cursor = BudgetConnection(raw).cursor()
        assert next(cursor) == {"value": 1}
        with pytest.raises(AppError, match="request_timeout"):
            next(cursor)


@pytest.mark.asyncio
async def test_product_handler_has_aggregate_deadline(monkeypatch):
    from keep.http import create_http_app
    import keep.http as module

    original_timeout = asyncio.timeout
    monkeypatch.setattr(module.asyncio, "timeout", lambda _: original_timeout(0.03))
    app = create_http_app(None, product=True)
    app.state.settings = SimpleNamespace(role="product-api", environment="development")

    @app.get("/api/slow")
    async def slow():
        await asyncio.sleep(1)
        return {"data": {}}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://test"
    ) as client:
        response = await client.get("/api/slow")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "request_timeout"
