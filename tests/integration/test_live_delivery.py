"""Live coalescing and durable hint publication; no Redis compatibility claim."""

# ruff: noqa: F811
from pathlib import Path
from types import SimpleNamespace
import pytest
from appchi.live import LiveRegistry, BoundedStream, publish_hints
from test_search_pipeline import real_db  # noqa: F401


def test_connected_tenant_vectors_coalesce_and_release():
    registry = LiveRegistry()
    a = registry.register("tenant")
    assert registry.register("tenant") is a
    calls = []

    def load():
        calls.append(True)
        return {"view_generation": str(len(calls))}

    assert registry.read("tenant", load) == {"view_generation": "1"}
    assert registry.read("tenant", load) == {"view_generation": "1"}
    registry.invalidate("tenant")
    assert a.is_set()
    assert registry.read("tenant", load) == {"view_generation": "2"}
    registry.unregister("tenant")
    assert registry.entries
    registry.unregister("tenant")
    assert not registry.entries


@pytest.mark.asyncio
async def test_stream_rejects_oversized_buffer():
    async def body():
        yield b"x" * 65537

    async def send(message):
        pass

    with pytest.raises(RuntimeError, match="live_vector_exceeds_bound"):
        await BoundedStream(body()).stream_response(send)


@pytest.mark.integration
def test_hint_outbox_is_atomic_and_keeps_newer_commit(real_db):
    draft = Path("migrations/0020_live_hints.sql.draft")
    if draft.exists():
        with real_db.transaction() as conn:
            conn.execute(draft.read_text())
    with real_db.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
        conn.execute("DELETE FROM live_hint_outbox")
    with pytest.raises(ValueError):
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE tenant_view_state SET view_generation=view_generation+1 WHERE tenant_id=%s",
                (tenant,),
            )
            raise ValueError("rollback")
    with real_db.transaction() as conn:
        assert not conn.execute("SELECT 1 FROM live_hint_outbox").fetchone()
        conn.execute(
            "UPDATE tenant_view_state SET view_generation=view_generation+1 WHERE tenant_id=%s",
            (tenant,),
        )

    class Publisher:
        calls = []

        def publish(self, channel, data):
            self.calls.append((channel, data))
            with real_db.transaction() as conn:
                conn.execute(
                    "UPDATE tenant_view_state SET view_generation=view_generation+1 WHERE tenant_id=%s",
                    (tenant,),
                )

    publisher = Publisher()
    assert publish_hints(real_db, SimpleNamespace(environment="development"), publisher) == 1
    with real_db.transaction() as conn:
        row = conn.execute(
            "SELECT generation FROM live_hint_outbox WHERE tenant_id=%s", (tenant,)
        ).fetchone()
        assert row["generation"] > int(publisher.calls[0][1])

    class Failing:
        def publish(self, channel, data):
            raise ConnectionError()

    with pytest.raises(ConnectionError):
        publish_hints(real_db, SimpleNamespace(environment="development"), Failing())
    with real_db.transaction() as conn:
        assert conn.execute(
            "SELECT 1 FROM live_hint_outbox WHERE tenant_id=%s", (tenant,)
        ).fetchone()
