"""The local launcher refuses workers until the cutover's topic identity is recorded."""

from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/local_runtime.py"
spec = importlib.util.spec_from_file_location("local_runtime", SOURCE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def control(monkeypatch, row):
    class Database:
        def __init__(self, *args, **kwargs):
            pass

        @contextmanager
        def transaction(self):
            yield Mock(execute=Mock(return_value=Mock(fetchone=lambda: row)))

        def close(self):
            pass

    settings = SimpleNamespace(
        database_url=SimpleNamespace(get_secret_value=lambda: "postgresql://"),
        deployment_epoch="local",
    )
    monkeypatch.setattr("keep.config.Settings", lambda: settings)
    monkeypatch.setattr("keep.db.Database", Database)


@pytest.mark.parametrize(
    "row",
    [
        # Before migration 0054 the column does not exist; after it, NULL until recorded.
        {"enabled": True},
        {"enabled": True, "received_topic_id": None},
    ],
)
def test_unrecorded_topic_identity_refuses_workers(monkeypatch, row):
    """Requirements: REV-41; Kafka pipeline 8."""
    control(monkeypatch, row)
    with pytest.raises(RuntimeError, match="--record-topic-identity"):
        runtime.require_kafka_cutover()


def test_recorded_topic_identity_starts_workers(monkeypatch):
    """Requirements: REV-41; Kafka pipeline 8."""
    control(monkeypatch, {"enabled": True, "received_topic_id": "YnmTZeuHT_iG2FXpjd3lUQ"})
    runtime.require_kafka_cutover()
