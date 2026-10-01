"""API task lifecycle and Redis failure isolation, without external services."""

import asyncio
import base64
from threading import Event
from uuid import uuid4

import httpx
import pytest

from appchi.config import Settings
from appchi.live import hint_publisher
from appchi.main import create_app


@pytest.mark.asyncio
async def test_publisher_retries_failure_without_terminating_api_task(monkeypatch, caplog):
    stop = Event()
    calls = []

    def publish(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise ConnectionError("private dependency details")
        stop.set()

    monkeypatch.setattr("appchi.live.publish_hints", publish)
    await asyncio.wait_for(hint_publisher(None, None, stop, interval=0), 2)
    assert len(calls) == 2
    assert "live_hint_publication_unavailable" in caplog.text
    assert "private dependency details" not in caplog.text


def settings(role="product-api"):
    return Settings(
        database_url="unused",
        wrapping_key=base64.b64encode(b"k" * 32).decode(),
        deployment_epoch="test",
        redis_url="redis://unused",
        role=role,
        kafka_bootstrap="unused:9092" if role != "product-api" else None,
        global_tenant_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_api_keeps_serving_and_drains_publication_before_closing_sql(monkeypatch):
    started, release = Event(), Event()
    lifecycle = []

    class Database:
        def close(self):
            lifecycle.append("database_closed")

    def publish(*args, **kwargs):
        started.set()
        assert release.wait(5)
        lifecycle.append("publication_finished")

    async def listener(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr("appchi.main.Database", lambda *args, **kwargs: Database())
    monkeypatch.setattr("appchi.live.publish_hints", publish)
    monkeypatch.setattr("appchi.live.redis_listener", listener)
    app = create_app(settings())
    context = app.router.lifespan_context(app)
    await context.__aenter__()
    shutdown = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://test") as client:
            assert (await asyncio.wait_for(client.get("/livez"), 2)).status_code == 200
        shutdown = asyncio.create_task(context.__aexit__(None, None, None))
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert "database_closed" not in lifecycle
    finally:
        release.set()
        if shutdown is None:
            await context.__aexit__(None, None, None)
        else:
            await asyncio.wait_for(shutdown, 2)
    assert lifecycle == ["publication_finished", "database_closed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["ingestion-api", "private-ingestion"])
async def test_ingress_does_not_start_live_tasks_even_with_redis_configured(role, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Ingress must not start SQL/Redis live tasks")

    class Publisher:
        def close(self):
            pass

    monkeypatch.setattr("appchi.main.Database", forbidden)
    monkeypatch.setattr("appchi.main.KafkaPublisher", lambda settings: Publisher())
    monkeypatch.setattr("appchi.live.hint_publisher", forbidden)
    monkeypatch.setattr("appchi.live.redis_listener", forbidden)
    app = create_app(settings(role))
    async with app.router.lifespan_context(app):
        assert app.state.db is None


@pytest.mark.parametrize("once", [False, True])
def test_separate_live_worker_is_rejected_before_loading_settings(once, monkeypatch, capsys):
    from appchi.worker import main

    monkeypatch.setattr("sys.argv", ["worker", "live"] + (["--once"] if once else []))
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "runs in the Product API" in capsys.readouterr().err
