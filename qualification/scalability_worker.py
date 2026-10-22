"""Process-isolated adapter for the disposable scalability fixture.

Production worker lifecycle and transports remain unchanged. Private environment
provides normal settings; only topic namespace, stop polling and diagnostics are
fixture adapters. No settings or record payloads are written to diagnostics.
"""

from collections import Counter, defaultdict
from contextlib import contextmanager
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
    # Pipeline-phase diagnostics: exact cumulative totals the harness differences
    # per scenario, and capped [milliseconds, records] samples for percentiles.
    totals = defaultdict(lambda: {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "records": 0})
    samples = defaultdict(list)
    instrumented = set()
    # One turn runs at a time per process; this names the work a PostgreSQL
    # transaction belongs to.
    stage = {"name": "other"}

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

    def measure(label, started, records=None, *, sample=True, ended=None):
        duration = ((ended or monotonic()) - started) * 1000
        total = totals[label]
        total["count"] += 1
        total["total_ms"] += duration
        total["max_ms"] = max(total["max_ms"], duration)
        total["records"] += records or 0
        if not sample:
            return
        if len(samples[label]) < 50000:
            samples[label].append([round(duration, 3), records])
        else:
            totals["sample_capacity_exceeded"]["count"] += 1

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
                    "totals": totals,
                    "samples": samples,
                    "instrumented": sorted(instrumented),
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
        previous, stage["name"] = stage["name"], "apply"
        try:
            return original_apply(*args, **kwargs)
        except Exception as error:
            errors["source_apply_" + getattr(error, "code", type(error).__name__)] += 1
            raise
        finally:
            stage["name"] = previous
            record("postgres_apply", began, selected)
            measure("apply", began, 1)

    kafka_source.apply_record = apply_record

    class ObservedTransport:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            # Consumer methods a later runtime calls pass through unchanged.
            return getattr(self.inner, name)

        def turn(self):
            began = monotonic()
            try:
                progress = self.inner.turn()
                turns[role] += progress
                record(role + ("_active_turn" if progress else "_idle_turn"), began)
                measure("turn" if progress else "idle_turn", began, progress, sample=bool(progress))
                return progress
            except Exception as error:
                errors["worker_" + role + "_" + getattr(error, "code", type(error).__name__)] += 1
                raise

        def close(self):
            return self.inner.close()

    class TimedClient:
        """Time fetches and offset commits on the production consumer client."""

        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def consume(self, *args, **kwargs):
            began = monotonic()
            messages = self.inner.consume(*args, **kwargs)
            label = "consume" if messages else "consume_empty"
            measure(label, began, len(messages), sample=bool(messages))
            return messages

        def poll(self, *args, **kwargs):
            began = monotonic()
            message = self.inner.poll(*args, **kwargs)
            found = message is not None
            measure("poll" if found else "poll_empty", began, int(found), sample=found)
            return message

        def commit(self, *args, **kwargs):
            began = monotonic()
            try:
                return self.inner.commit(*args, **kwargs)
            finally:
                measure("kafka_commit", began)

    class CommitStamp:
        """Mark when a source batch's Kafka transaction commit returned."""

        def __init__(self, inner):
            self.inner, self.committed_at = inner, None

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def commit_transaction(self, *args, **kwargs):
            result = self.inner.commit_transaction(*args, **kwargs)
            self.committed_at = monotonic()
            return result

    def timed_transactions(db):
        if not hasattr(db, "transaction") or getattr(db, "_scale_timed", False):
            return
        original = db.transaction

        @contextmanager
        def transaction(*args, **kwargs):
            began, label = monotonic(), "pg_tx:" + stage["name"]
            try:
                with original(*args, **kwargs) as conn:
                    yield conn
            finally:
                measure(label, began, sample=False)

        db.transaction = transaction
        db._scale_timed = True
        instrumented.add("db.transaction")

    def staged(name, handler, count):
        def invoke(*args, **kwargs):
            began = monotonic()
            previous, stage["name"] = stage["name"], "handler"
            try:
                return handler(*args, **kwargs)
            except Exception as error:
                # The consumer retries the partition; keep the cause, not the text.
                code = getattr(error, "code", type(error).__name__)
                measure("handler_error:" + code, began, sample=False)
                raise
            finally:
                stage["name"] = previous
                measure(name, began, count(args[-1]))

        return invoke

    def instrument(consumer, db):
        """Wrap the constructed consumer's own hooks; production code is unchanged."""
        timed_transactions(db)
        if getattr(consumer, "client", None) is not None:
            consumer.client = TimedClient(consumer.client)
            instrumented.add("client")
        publish_batch = getattr(consumer, "_publish", None)
        if role == "source" and publish_batch is not None:

            def publish_timed(producer, applied):
                began, stamped = monotonic(), CommitStamp(producer)
                previous, stage["name"] = stage["name"], "confirmation"
                try:
                    return publish_batch(stamped, applied)
                finally:
                    stage["name"] = previous
                    if stamped.committed_at is not None:
                        ended = stamped.committed_at
                        measure("kafka_transaction", began, len(applied), ended=ended)
                        measure("partition_confirmation", ended, len(applied))

            consumer._publish = publish_timed
            instrumented.add("source._publish")
        if role == "source":
            return
        for name, count in (
            ("handler", lambda message: 1),
            ("batch_handler", len),
            ("groups_handler", lambda prefixes: sum(map(len, prefixes))),
        ):
            handler = getattr(consumer, name, None)
            if handler is not None:
                setattr(consumer, name, staged(name, handler, count))
                instrumented.add("changed." + name)

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
            transport = construct(db, config, profile)
        except Exception as error:
            errors["worker_start_" + getattr(error, "code", type(error).__name__)] += 1
            raise
        instrument(transport.inner, db)
        return transport

    class ObservedReconciliation:
        def __init__(self):
            from appchi.search.reconciliation import ReconciliationRunner

            self.runner = ReconciliationRunner()

        def __call__(self, *args):
            began = monotonic()
            previous, stage["name"] = stage["name"], "reconciliation"
            try:
                return self.runner(*args)
            except Exception as error:
                errors["reconciliation_" + getattr(error, "code", type(error).__name__)] += 1
                raise
            finally:
                stage["name"] = previous
                measure("reconciliation", began, sample=False)

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
