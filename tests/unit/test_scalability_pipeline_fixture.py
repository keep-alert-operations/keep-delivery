"""The benchmark clock remains complete through Windows sharing conflicts."""

import ast
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest


def load_fixture():
    path = Path(__file__).resolve().parents[2] / "qualification/scalability_load.py"
    spec = importlib.util.spec_from_file_location("scalability_load_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_pacing_limits_concurrent_offers_without_catching_up(monkeypatch):
    fixture = load_fixture()
    clock = [0.0]
    offers = []
    monkeypatch.setattr(fixture, "monotonic", lambda: clock[0])

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(fixture.asyncio, "sleep", sleep)

    async def run():
        pacer = fixture.SeedPacer(2)

        async def offer():
            await pacer.wait()
            offers.append(clock[0])

        await asyncio.gather(*(offer() for _ in range(3)))
        clock[0] += 5  # A slow setup stage must not release accumulated permits.
        await asyncio.gather(*(offer() for _ in range(2)))

    asyncio.run(run())
    assert offers == [0.0, 0.5, 1.0, 6.0, 6.5]


def test_seed_pacing_is_disabled_by_default(monkeypatch):
    fixture = load_fixture()
    monkeypatch.setattr(fixture, "monotonic", lambda: pytest.fail("unexpected pacing"))

    async def run():
        pacer = fixture.SeedPacer(None)
        await asyncio.gather(*(pacer.wait() for _ in range(10)))

    asyncio.run(run())


def load_api_fixture():
    path = Path(__file__).resolve().parents[2] / "qualification/scalability_api.py"
    spec = importlib.util.spec_from_file_location("scalability_api_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_asgi_timing_preserves_application_messages_and_only_adds_header(monkeypatch):
    fixture = load_api_fixture()
    ticks = iter((1.0, 1.012))
    monkeypatch.setattr(fixture, "perf_counter", lambda: next(ticks))
    original = {
        "type": "http.response.start",
        "status": 503,
        "headers": [(b"content-type", b"application/json")],
    }
    body = {"type": "http.response.body", "body": b'{"error":{"code":"request_timeout"}}'}
    receive = object()
    sent = []

    async def app(scope, incoming, send):
        assert incoming is receive
        assert scope == {"type": "http"}
        await send(original)
        await send(body)

    async def send(message):
        sent.append(message)

    asyncio.run(fixture.ResponseTiming(app)({"type": "http"}, receive, send))
    assert original["headers"] == [(b"content-type", b"application/json")]
    assert sent[0]["status"] == 503
    assert sent[0]["headers"] == [
        (b"content-type", b"application/json"),
        (b"x-keep-diagnostic-asgi-ms", b"12.000"),
    ]
    assert sent[1] is body


def test_asgi_timing_passes_lifespan_through_unchanged(monkeypatch):
    fixture = load_api_fixture()
    monkeypatch.setattr(fixture, "perf_counter", lambda: pytest.fail("lifespan must not be timed"))
    receive, send = object(), object()
    scope = {"type": "lifespan"}

    async def app(actual_scope, actual_receive, actual_send):
        assert (actual_scope, actual_receive, actual_send) == (scope, receive, send)
        return "completed"

    assert asyncio.run(fixture.ResponseTiming(app)(scope, receive, send)) == "completed"


def test_client_diagnostics_separate_headers_body_and_server_time(monkeypatch):
    fixture = load_fixture()
    clock = [2.0]
    monkeypatch.setattr(fixture, "monotonic", lambda: clock[0])

    class Response:
        headers = {"x-keep-diagnostic-asgi-ms": "8.000"}

        async def aread(self):
            clock[0] += 0.030

    response = Response()

    class Client:
        @asynccontextmanager
        async def stream(self, method, url, **kwargs):
            assert (method, url, kwargs) == ("POST", "/counts", {"json": {"queries": []}})
            clock[0] += 0.010
            yield response

    actual, timing = asyncio.run(
        fixture.diagnostic_read(Client(), "POST", "/counts", json={"queries": []})
    )
    assert actual is response
    assert timing == pytest.approx(
        {
            "client_headers_ms": 10,
            "client_complete_ms": 40,
            "asgi_response_start_ms": 8,
        }
    )


def test_clock_retries_sharing_conflict_without_emptying_published_sample(tmp_path, monkeypatch):
    fixture = load_fixture()
    clock = tmp_path / "clock.json"
    clock.write_text('{"previous": true}')
    original = Path.replace
    attempts = []

    def replace(path, target):
        attempts.append(1)
        if len(attempts) < 5:
            assert json.loads(clock.read_text()) == {"previous": True}
            error = PermissionError("synthetic sharing violation")
            error.winerror = 32
            raise error
        return original(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(fixture, "sleep", lambda _: None)
    fixture.publish_clock(clock)
    assert len(attempts) == 5
    assert json.loads(clock.read_text())["synchronized"] is True


def test_clock_does_not_retry_unrelated_permission_error(tmp_path, monkeypatch):
    fixture = load_fixture()

    def replace(*_):
        raise PermissionError("not a Windows sharing conflict")

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(fixture, "sleep", lambda _: pytest.fail("unexpected retry"))
    with pytest.raises(PermissionError):
        fixture.publish_clock(tmp_path / "clock.json")


def test_clock_sharing_retry_has_finite_deadline(tmp_path, monkeypatch):
    fixture = load_fixture()
    ticks = iter([0, 1, 6])
    monkeypatch.setattr(fixture, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(fixture, "sleep", lambda _: None)

    def replace(*_):
        error = PermissionError("persistent synthetic sharing violation")
        error.winerror = 5
        raise error

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(PermissionError):
        fixture.publish_clock(tmp_path / "clock.json")


def test_session_check_reads_the_session_without_the_removed_activity_call():
    """Requirements: REV-73; identity configuration 2 and 4 (no activity heartbeat)."""
    path = Path(__file__).resolve().parents[2] / "qualification/scalability_load.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    checks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "session_check"
    ]
    assert len(checks) == 1
    routes = {
        node.value
        for node in ast.walk(checks[0])
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "/auth/session" in routes
    every_string = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not [value for value in every_string if value.startswith("/auth/activity")]


def test_process_adapter_keeps_production_lifecycle_and_private_settings(tmp_path, monkeypatch):
    from keep import config, kafka_source, pipeline_runtime

    path = Path(__file__).resolve().parents[2] / "qualification/scalability_worker.py"
    spec = importlib.util.spec_from_file_location("scalability_worker_fixture", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    namespace = "scale-" + "a" * 32
    stop = tmp_path / "stop"
    stop.touch()
    output = tmp_path / "metrics.json"
    monkeypatch.setenv("KEEP_SCALE_NAMESPACE", namespace)
    monkeypatch.setenv("KEEP_SCALE_STOP_FILE", str(stop))
    monkeypatch.setenv("KEEP_SCALE_MEASUREMENT_FILE", str(tmp_path / "measurement"))
    monkeypatch.setenv("KEEP_SCALE_METRICS_FILE", str(output))
    monkeypatch.setattr(sys, "argv", [str(path), "source"])
    monkeypatch.setattr(
        config,
        "Settings",
        lambda **_: SimpleNamespace(
            model_dump=lambda: {
                "environment": "development",
                "private_test_setting": "never-in-diagnostics",
            }
        ),
    )
    # Register these originals before the adapter installs its process-local hooks.
    monkeypatch.setattr(pipeline_runtime, "Settings", pipeline_runtime.Settings)
    monkeypatch.setattr(
        pipeline_runtime, "require_clock_health", pipeline_runtime.require_clock_health
    )
    monkeypatch.setattr(kafka_source, "apply_record", kafka_source.apply_record)
    monkeypatch.setattr(
        pipeline_runtime, "WorkerHealth", type("Health", (), {"wait": lambda *_: None})
    )
    inner = SimpleNamespace(turn=lambda: 1, close=Mock())
    monkeypatch.setattr(kafka_source, "SourceConsumer", lambda *_: inner)

    def run(role, factory, **kwargs):
        assert role == "source"
        settings = pipeline_runtime.Settings(role=role)
        assert settings.environment == namespace
        assert settings.private_test_setting == "never-in-diagnostics"
        transport = factory(None, settings, None)
        assert transport.turn() == 1
        event = Event()
        pipeline_runtime.WorkerHealth().wait(event, 0)
        assert event.is_set()
        transport.close()

    lifecycle = Mock(side_effect=run)
    monkeypatch.setattr(pipeline_runtime, "run", lifecycle)
    worker.main()
    lifecycle.assert_called_once()
    inner.close.assert_called_once()
    report = json.loads(output.read_text())
    assert report["turns"] == {"source": 1}
    assert report["errors"] == {}
    assert "never-in-diagnostics" not in output.read_text()
