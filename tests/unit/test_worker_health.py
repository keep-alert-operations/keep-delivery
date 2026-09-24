import json
from pathlib import Path
from time import monotonic

from appchi.worker_health import WorkerHealth, probe


def test_process_liveness_is_independent_of_dependencies_and_draining(tmp_path):
    path = tmp_path / "health.json"
    assert not probe(path, "live", 120)
    health = WorkerHealth(path, "source")
    health.publish()
    assert probe(path, "live", 120) and not probe(path, "ready", 120)
    health.publish(ready=True)
    assert probe(path, "ready", 120)
    health.publish(ready=False)
    assert probe(path, "live", 120) and not probe(path, "ready", 120)
    health.publish(ready=True, draining=True)
    assert probe(path, "live", 120) and not probe(path, "ready", 120)


def test_stalled_or_malformed_worker_is_not_reported_live(tmp_path):
    path = tmp_path / "health.json"
    health = WorkerHealth(path, "retention")
    health.publish(ready=True)
    state = json.loads(path.read_text())
    state["heartbeat"] = monotonic() - 121
    path.write_text(json.dumps(state))
    assert not probe(path, "live", 120)
    path.write_text("{")
    assert not probe(path, "live", 120)


def test_windows_reader_lock_retries_without_stopping_worker(tmp_path, monkeypatch):
    path = tmp_path / "health.json"
    health = WorkerHealth(path, "relay")
    replace = Path.replace
    attempts = []

    def temporarily_locked(source, target):
        attempts.append(target)
        if len(attempts) < 3:
            raise PermissionError("sharing violation")
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", temporarily_locked)
    health.publish(ready=True)
    assert len(attempts) == 3
    assert probe(path, "ready", 120)


def test_persistent_reader_lock_does_not_claim_a_new_heartbeat(tmp_path, monkeypatch):
    path = tmp_path / "health.json"
    health = WorkerHealth(path, "relay")
    health.publish(ready=True)
    state = json.loads(path.read_text())
    state["heartbeat"] = monotonic() - 121
    path.write_text(json.dumps(state))

    def locked(*_):
        raise PermissionError("sharing violation")

    monkeypatch.setattr(Path, "replace", locked)
    health.publish(ready=True)
    assert not probe(path, "live", 120)
