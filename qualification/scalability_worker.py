"""Process-isolated adapter for the disposable scalability fixture.

Production worker lifecycle and transports remain unchanged. Private environment
provides normal settings; only topic namespace, stop polling and diagnostics are
fixture adapters. No settings or record payloads are written to diagnostics.
"""

from collections import Counter, defaultdict
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
from time import monotonic
from types import SimpleNamespace


def host_memory():
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong)
            for name in (
                "total",
                "available",
                "page_total",
                "page_available",
                "virtual_total",
                "virtual_available",
                "extended",
            )
        ]

    state = MemoryStatus()
    state.length = ctypes.sizeof(state)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        return {"total_physical_bytes": state.total, "available_physical_bytes": state.available}
    return None


def resident_bytes():
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Memory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak",
                "working",
                "paged_peak",
                "paged",
                "nonpaged_peak",
                "nonpaged",
                "pagefile",
                "pagefile_peak",
                "private",
            )
        ]

    counters = Memory()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = wintypes.HANDLE
    read = ctypes.windll.psapi.GetProcessMemoryInfo
    read.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    return counters.working if read(process(), ctypes.byref(counters), counters.cb) else None


def main():
    from appchi.config import Settings
    from appchi import pipeline_runtime, kafka_source
    from appchi.kafka_changed import ChangedConsumer

    role = sys.argv[1]
    if role not in ("source", "indexer", "correlation"):
        raise ValueError("Unknown fixture worker role")
    sys.argv = [sys.argv[0]]
    namespace = os.environ["APPCHI_SCALE_NAMESPACE"]
    if not namespace.startswith("scale-") or len(namespace) != 38:
        raise ValueError("An isolated fixture namespace is required")
    settings = Settings(role=role)
    adapted = SimpleNamespace(**{**settings.model_dump(), "environment": namespace})
    pipeline_runtime.Settings = lambda **_: adapted
    stopped = Path(os.environ["APPCHI_SCALE_STOP_FILE"])
    measurement = Path(os.environ["APPCHI_SCALE_MEASUREMENT_FILE"])
    output = Path(os.environ["APPCHI_SCALE_METRICS_FILE"])
    phases = defaultdict(list)
    errors = Counter()
    turns = Counter()
    measured_from = None
    last_publish = 0
    peak_rss = 0

    def cohort(arrival=None):
        nonlocal measured_from
        if measured_from is None and measurement.exists():
            measured_from = datetime.fromisoformat(measurement.read_text())
        return (
            "measured"
            if measured_from and (arrival or datetime.now(UTC)) >= measured_from
            else "seed"
        )

    def record(label, started, selected=None):
        values = phases[(selected or cohort()) + ":" + label]
        if len(values) < 20000:
            values.append((monotonic() - started) * 1000)
        else:
            errors["diagnostic_sample_capacity"] += 1

    def publish(force=False):
        nonlocal last_publish, peak_rss
        if not force and monotonic() - last_publish < 1:
            return
        last_publish = monotonic()
        peak_rss = max(peak_rss, resident_bytes() or 0)
        temporary = output.with_suffix(".new")
        temporary.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "role": role,
                    "errors": errors,
                    "turns": turns,
                    "phases_ms": phases,
                    "peak_sampled_rss_bytes": peak_rss,
                }
            )
        )
        try:
            temporary.replace(output)
        except PermissionError:
            # Parent reads complete previous diagnostics; a missed sample does
            # not alter production worker progress or the final authority checks.
            pass

    original_wait = pipeline_runtime.WorkerHealth.wait

    def wait(health, event, seconds):
        publish()
        if stopped.exists():
            event.set()
        return original_wait(health, event, seconds)

    pipeline_runtime.WorkerHealth.wait = wait
    original_clock = pipeline_runtime.require_clock_health

    def clock(*args, **kwargs):
        try:
            return original_clock(*args, **kwargs)
        except Exception as error:
            errors["worker_" + role + "_" + getattr(error, "code", type(error).__name__)] += 1
            raise

    pipeline_runtime.require_clock_health = clock
    original_apply = kafka_source.apply_record

    def apply_record(*args, **kwargs):
        began = monotonic()
        arrival = datetime.fromisoformat(
            json.loads(kwargs["value"])["received_at"].replace("Z", "+00:00")
        )
        selected = cohort(arrival)
        phases[selected + ":queue_age"].append((datetime.now(UTC) - arrival).total_seconds() * 1000)
        try:
            return original_apply(*args, **kwargs)
        except Exception as error:
            errors["source_apply_" + getattr(error, "code", type(error).__name__)] += 1
            raise
        finally:
            record("postgres_apply", began, selected)

    kafka_source.apply_record = apply_record

    class ObservedTransport:
        def __init__(self, inner):
            self.inner = inner

        def turn(self):
            began = monotonic()
            try:
                progress = self.inner.turn()
                turns[role] += progress
                record(role + ("_active_turn" if progress else "_idle_turn"), began)
                return progress
            except Exception as error:
                errors["worker_" + role + "_" + getattr(error, "code", type(error).__name__)] += 1
                raise

        def close(self):
            return self.inner.close()

    def construct(db, config, profile):
        if role == "source":
            return ObservedTransport(kafka_source.SourceConsumer(db, config, profile))
        if role == "correlation":
            from appchi.correlation.consumer import correlate_record

            return ObservedTransport(ChangedConsumer(db, config, profile, role, correlate_record))
        from appchi.search.consumer import index_batch, index_record

        return ObservedTransport(
            ChangedConsumer(db, config, profile, role, index_record, batch_handler=index_batch)
        )

    def make(db, config, profile):
        try:
            return construct(db, config, profile)
        except Exception as error:
            errors["worker_start_" + getattr(error, "code", type(error).__name__)] += 1
            raise

    class ObservedReconciliation:
        def __init__(self):
            from appchi.search.reconciliation import ReconciliationRunner

            self.runner = ReconciliationRunner()

        def __call__(self, *args):
            try:
                return self.runner(*args)
            except Exception as error:
                errors["reconciliation_" + getattr(error, "code", type(error).__name__)] += 1
                raise

        def close(self):
            self.runner.close()

    try:
        pipeline_runtime.run(
            role,
            make,
            before_turn=ObservedReconciliation() if role == "indexer" else None,
            transport_optional=role == "indexer",
        )
    finally:
        publish(force=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        path = Path(os.environ["APPCHI_SCALE_METRICS_FILE"])
        if not path.exists():
            path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "role": sys.argv[1] if len(sys.argv) > 1 else "unknown",
                        "errors": {"fixture_worker_start_" + type(error).__name__: 1},
                        "turns": {},
                        "phases_ms": {},
                        "peak_sampled_rss_bytes": resident_bytes() or 0,
                    }
                )
            )
        raise SystemExit(1) from None
