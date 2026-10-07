import json
from pathlib import Path
from time import monotonic

from appchi.worker_health import WorkerHealth, probe
from appchi import worker_health


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
    clock = [0.0]
    monkeypatch.setattr(worker_health, "monotonic", lambda: clock[0])
    monkeypatch.setattr(worker_health, "sleep", lambda _: None)
    path = tmp_path / "health.json"
    health = WorkerHealth(path, "relay")
    health.publish(ready=True)
    clock[0] = 121.0
    attempts = []

    def locked(*_):
        attempts.append(clock[0])
        raise PermissionError("sharing violation")

    monkeypatch.setattr(Path, "replace", locked)
    health.publish(ready=True)
    assert len(attempts) == 3
    assert not probe(path, "live", 120)
    clock[0] += 0.5
    health.publish(ready=True)
    assert len(attempts) == 3
    assert not probe(path, "live", 120)


def test_unchanged_publications_write_at_most_once_per_second(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(worker_health, "monotonic", lambda: clock[0])
    replace = Path.replace
    writes = []

    def capture(source, target):
        writes.append(clock[0])
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", capture)
    path = tmp_path / "health.json"
    health = WorkerHealth(path, "source")
    for tick in range(201):
        clock[0] = tick / 100
        health.publish(ready=True)
    assert writes == [0.0, 1.0, 2.0]
    assert json.loads(path.read_text())["heartbeat"] == 2.0


def test_ready_and_draining_changes_publish_inside_throttle_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_health, "monotonic", lambda: 10.0)
    path = tmp_path / "health.json"
    health = WorkerHealth(path, "source")
    health.publish()
    health.publish(ready=True)
    assert probe(path, "ready", 15)
    health.publish(ready=False)
    assert not probe(path, "ready", 15)
    health.publish(ready=True)
    assert probe(path, "ready", 15)
    health.publish(draining=True)
    assert not probe(path, "ready", 15)
    assert json.loads(path.read_text())["draining"] is True


def test_failed_transition_retries_next_heartbeat_without_claiming_success(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(worker_health, "monotonic", lambda: clock[0])
    monkeypatch.setattr(worker_health, "sleep", lambda _: None)
    path = tmp_path / "health.json"
    health = WorkerHealth(path, "source")
    health.publish()
    replace = Path.replace

    def locked(*_):
        raise PermissionError("sharing violation")

    monkeypatch.setattr(Path, "replace", locked)
    health.publish(ready=True)
    assert not probe(path, "ready", 15)
    monkeypatch.setattr(Path, "replace", replace)
    clock[0] = 1.0
    health.publish()
    assert probe(path, "ready", 15)


def test_idle_wait_keeps_existing_five_second_heartbeat_cadence(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(worker_health, "monotonic", lambda: clock[0])
    path = tmp_path / "health.json"
    health = WorkerHealth(path, "source")
    observed = []

    class Stop:
        def is_set(self):
            return False

        def wait(self, seconds):
            observed.append(json.loads(path.read_text())["heartbeat"])
            clock[0] += seconds

    health.wait(Stop(), 12)
    assert observed == [0.0, 5.0, 10.0]
    assert probe(path, "live", 15)
