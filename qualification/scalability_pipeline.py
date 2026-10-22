"""Pipeline phase of the scalability harness: fixed workloads through consume().

Admission publishes through production KafkaAdmission. Production worker
processes (``scalability_worker.py``) run the source, correlation and indexer
consumers; their client, batch publication, handler and transaction hooks are
timed without changing production code. Each scenario is drained to exact
PostgreSQL and Elasticsearch counts before the next one starts, and worker
diagnostics are differenced per scenario. The workload is fixed by
``WORKLOAD_VERSION`` and the recorded arguments, so a run on other package
revisions with the same arguments is directly comparable. Local resources are
not production pod qualification.
"""

import asyncio
from collections import Counter
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
import importlib.metadata
import inspect
import json
import math
import os
import platform
import subprocess
import threading
from time import monotonic, sleep
from urllib.parse import urlparse
from urllib.request import url2pathname
from uuid import uuid4

WORKLOAD_VERSION = "pipeline-v1"
# A scenario offers its alerts, then drains every stage before the next starts.
# "rate" paces arrivals; None offers as fast as admission acknowledges, with at
# most "concurrency" admissions in flight. Ordering is fixed.
SCENARIOS = (
    {"name": "steady", "version": "v2", "tenants": "all", "paced": True},
    {"name": "burst_spread", "version": "v2", "tenants": "all", "paced": False},
    {"name": "burst_one_tenant", "version": "v2", "tenants": "one", "paced": False},
    {"name": "burst_global_v1", "version": "v1", "tenants": "global", "paced": False},
)
PACKAGES = (
    "appchi-domain",
    "appchi-contracts",
    "appchi-ingestion",
    "appchi-processor",
    "appchi-correlation",
    "appchi-indexer",
    "appchi-migrations",
    "appchi-api",
)
ROLES = ("source", "correlation", "indexer")
# Harness probes carry this name so activity sampling counts only workers.
HARNESS = "appchi-scale-harness"


def percentiles(values, points=(0.5, 0.95, 0.99)):
    if not values:
        return {}
    ordered = sorted(values)
    return {
        f"p{round(point * 100)}": round(
            ordered[min(len(ordered) - 1, int(len(ordered) * point))], 3
        )
        for point in points
    } | {"max": round(ordered[-1], 3)}


def workload(args):
    """The fields that must match for two runs to be compared."""
    return {
        "version": WORKLOAD_VERSION,
        "scenarios": [scenario["name"] for scenario in SCENARIOS],
        "tenants": args.tenants,
        "warmup": args.warmup,
        "steady_rate": args.steady_rate,
        "steady_seconds": args.steady_seconds,
        "burst": args.burst,
        "burst_concurrency": args.burst_concurrency,
        "message_bytes": 3500,
        "partitions": args.partitions,
        "processors": args.processors,
        "indexers": args.indexers,
        "correlators": args.correlators if args.features else 0,
        "features": args.features,
    }


def package_provenance():
    packages = {}
    for name in PACKAGES:
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
            continue
        origin = json.loads(distribution.read_text("direct_url.json") or "{}")
        entry = {"version": distribution.version}
        if origin.get("vcs_info"):
            entry["commit"] = origin["vcs_info"].get("commit_id")
        elif origin.get("dir_info", {}).get("editable"):
            path = url2pathname(urlparse(origin["url"]).path)
            entry["editable"] = True
            entry.update(git_revision(path))
        packages[name] = entry
    return packages


def git_revision(path):
    def git(*command):
        completed = subprocess.run(
            ["git", "-C", path, *command], capture_output=True, text=True, timeout=10
        )
        if completed.returncode:
            raise subprocess.SubprocessError(f"git exited {completed.returncode}")
        return completed.stdout.strip()

    # An unreadable checkout records no revision rather than a falsely clean one.
    try:
        return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def consumer_settings():
    """Batching facts of the installed consumers, recorded rather than assumed."""
    from appchi.kafka_changed import ChangedConsumer
    from appchi.kafka_source import SourceConsumer
    from appchi.search import consumer as search_consumer
    from appchi.search import reconciliation

    changed = {
        name: parameter.default
        for name, parameter in inspect.signature(ChangedConsumer.__init__).parameters.items()
        if parameter.default is not inspect.Parameter.empty and parameter.default is not None
    }
    return {
        "source_batch_records": getattr(SourceConsumer, "batch_records", None),
        "source_batch_seconds": getattr(SourceConsumer, "batch_seconds", None),
        "changed_defaults": changed,
        "index_groups_available": hasattr(search_consumer, "index_groups"),
        "search_bulk_documents": getattr(reconciliation, "BULK_DOCUMENTS", None),
    }


def environment(admin_conninfo, es, root):
    import psycopg
    from scalability_worker import host_memory

    with psycopg.connect(admin_conninfo) as conn:
        postgres = conn.execute("SHOW server_version").fetchone()[0]
    return {
        "harness": git_revision(str(root)),
        "packages": package_provenance(),
        "consumers": consumer_settings(),
        "postgres": postgres,
        "elasticsearch": es.info()["version"]["number"],
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "host_memory": host_memory(),
    }


class ActivitySampler:
    """Sample backend activity: how many writers run and how many wait on locks."""

    def __init__(self, conninfo, database):
        self.conninfo, self.database = conninfo, database
        self.samples, self.waits = [], Counter()
        self.halt = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        import psycopg

        try:
            with psycopg.connect(self.conninfo, autocommit=True) as conn:
                while not self.halt.wait(0.2):
                    rows = conn.execute(
                        """SELECT state, wait_event_type, wait_event FROM pg_stat_activity
                        WHERE datname=%s AND backend_type='client backend' AND application_name<>%s""",
                        (self.database, HARNESS),
                    ).fetchall()
                    active = [row for row in rows if row[0] == "active"]
                    locked = [row for row in active if row[1] == "Lock"]
                    self.waits.update(f"{row[1]}:{row[2]}" for row in active if row[1])
                    self.samples.append((len(active), len(locked)))
        except Exception as error:
            self.waits["sampler_" + type(error).__name__] += 1

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.halt.set()
        self.thread.join(5)

    def summary(self):
        if not self.samples:
            return {"samples": 0}
        active = [value for value, _ in self.samples]
        locked = [value for _, value in self.samples]
        return {
            "samples": len(self.samples),
            "active_backends_mean": round(sum(active) / len(active), 3),
            "lock_waiting_backends_mean": round(sum(locked) / len(locked), 3),
            "lock_waiting_backends_max": max(locked),
            "samples_with_lock_wait": round(sum(1 for value in locked if value) / len(locked), 3),
            "wait_events": dict(self.waits.most_common(12)),
        }


class VisibilityObserver:
    """Observe sampled alerts in search at a 100 ms cadence (an upper bound)."""

    def __init__(self, es, index):
        self.es, self.index = es, index
        self.pending, self.observed, self.errors = {}, {}, Counter()
        self.lock = threading.Lock()
        self.halt = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def add(self, node, received_at):
        with self.lock:
            self.pending[node] = received_at

    def run(self):
        while not self.halt.wait(0.1):
            with self.lock:
                waiting = {k: v for k, v in self.pending.items() if k not in self.observed}
            if not waiting:
                continue
            try:
                response = self.es.search(
                    index=self.index,
                    size=len(waiting),
                    query={"terms": {"node_name": list(waiting)}},
                    source_includes=["node_name"],
                    track_total_hits=False,
                    allow_partial_search_results=False,
                )
            except Exception as error:
                self.errors["visibility_" + type(error).__name__] += 1
                continue
            observed_at = datetime.now(UTC)
            with self.lock:
                for hit in response["hits"]["hits"]:
                    node = hit["_source"]["node_name"]
                    if node in waiting:
                        self.observed.setdefault(
                            node, (observed_at - waiting[node]).total_seconds() * 1000
                        )

    def complete(self):
        with self.lock:
            return len(self.observed) == len(self.pending)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        deadline = monotonic() + 3
        while monotonic() < deadline and not self.complete():
            sleep(0.1)
        self.halt.set()
        self.thread.join(5)


def worker_delta(before, after):
    """Difference cumulative worker diagnostics into per-role scenario totals."""
    roles = {}
    for pid, report in after.items():
        prior = before.get(pid, {})
        role = roles.setdefault(
            report["role"], {"processes": 0, "totals": {}, "samples": {}, "errors": Counter()}
        )
        role["processes"] += 1
        role.setdefault("instrumented", sorted(report.get("instrumented", [])))
        for label, total in report.get("totals", {}).items():
            old = prior.get("totals", {}).get(label, {})
            merged = role["totals"].setdefault(label, {"count": 0, "total_ms": 0.0, "records": 0})
            for key in merged:
                merged[key] += total.get(key, 0) - old.get(key, 0)
        role["errors"].update(Counter(report.get("errors", {})) - Counter(prior.get("errors", {})))
        for label, values in report.get("samples", {}).items():
            start = len(prior.get("samples", {}).get(label, []))
            role["samples"].setdefault(label, []).extend(values[start:])
    return roles


def summarize_role(role):
    totals = {label: value for label, value in role["totals"].items() if value["count"]}
    operations = {}
    for label, value in totals.items():
        operations[label] = {
            "count": value["count"],
            "records": value["records"],
            "total_ms": round(value["total_ms"], 3),
            "mean_ms": round(value["total_ms"] / value["count"], 3),
        }
    for label, values in role["samples"].items():
        if label in operations and values:
            operations[label]["ms"] = percentiles([value[0] for value in values])
            records = [value[1] for value in values if value[1] is not None]
            if records:
                operations[label]["records_per_call"] = percentiles(records, (0.5,)) | {
                    "mean": round(sum(records) / len(records), 3)
                }

    def total(label, key="total_ms"):
        return totals.get(label, {}).get(key, 0)

    def ratio(numerator, denominator, digits=3):
        return round(numerator / denominator, digits) if denominator else None

    handled = sum(total(name, "records") for name in ("handler", "batch_handler", "groups_handler"))
    handler_ms = sum(total(name) for name in ("handler", "batch_handler", "groups_handler"))
    handler_calls = sum(
        total(name, "count") for name in ("handler", "batch_handler", "groups_handler")
    )
    transactions = {
        label.removeprefix("pg_tx:"): value["count"]
        for label, value in totals.items()
        if label.startswith("pg_tx:")
    }
    records = total("apply", "records") or handled
    derived = {
        "records": records,
        "records_per_consume": ratio(total("consume", "records"), total("consume", "count")),
        "active_turns": total("turn", "count"),
        "active_turn_ms": round(total("turn"), 3),
        "pg_transactions_per_record": {
            stage: ratio(count, records) for stage, count in transactions.items()
        },
    }
    if total("apply", "count"):
        derived |= {
            "apply_ms_per_record": ratio(total("apply"), total("apply", "records")),
            "publishes": total("partition_confirmation", "count"),
            "records_per_publish": ratio(
                total("partition_confirmation", "records"), total("partition_confirmation", "count")
            ),
            "kafka_transaction_ms_per_record": ratio(
                total("kafka_transaction"), total("kafka_transaction", "records")
            ),
            "confirmation_ms_per_record": ratio(
                total("partition_confirmation"), total("partition_confirmation", "records")
            ),
            "confirmation_share_of_active_turns": ratio(
                total("partition_confirmation"), total("turn"), 4
            ),
        }
    if handler_calls:
        derived |= {
            "handler_calls": handler_calls,
            "records_per_handler_call": ratio(handled, handler_calls),
            "handler_ms_per_record": ratio(handler_ms, handled),
            "kafka_commits_per_record": ratio(total("kafka_commit", "count"), handled),
            "kafka_commit_ms_per_record": ratio(total("kafka_commit"), handled),
        }
    return {
        "processes": role["processes"],
        "instrumented": role["instrumented"],
        "errors": dict(role.get("errors", {})),
        "handler_errors": {
            label.removeprefix("handler_error:"): value["count"]
            for label, value in totals.items()
            if label.startswith("handler_error:")
        },
        "derived": derived,
        "operations": operations,
    }


def clock_offset(conninfo):
    """Database clock minus this host's clock, from the lowest-latency probe."""
    import psycopg

    best = None
    with psycopg.connect(conninfo, autocommit=True) as conn:
        for _ in range(7):
            sent = datetime.now(UTC)
            database = conn.execute("SELECT clock_timestamp()").fetchone()[0]
            received = datetime.now(UTC)
            trip = received - sent
            if best is None or trip < best[0]:
                best = (trip, database - (sent + trip / 2))
    return best[1]


def write_counters(conninfo):
    import psycopg

    with psycopg.connect(conninfo, autocommit=True) as conn:
        inserted, updated, hot = conn.execute(
            "SELECT n_tup_ins,n_tup_upd,n_tup_hot_upd FROM pg_stat_user_tables WHERE relname='kafka_source_results'"
        ).fetchone()
        wal = conn.execute("SELECT pg_current_wal_lsn()-'0/0'::pg_lsn").fetchone()[0]
    return {"inserted": inserted, "updated": updated, "hot_updated": hot, "wal_bytes": int(wal)}


def stage_metrics(conninfo, start, end, offset, accepted, correlation):
    """Per-stage latency and throughput from authoritative PostgreSQL timestamps.

    Receipt times come from this host; recorded/processed times from the
    database clock, corrected by the measured clock offset.
    """
    import psycopg

    shift = offset.total_seconds() * 1000
    stages = {}
    with psycopg.connect(conninfo) as conn:
        source = conn.execute(
            """SELECT count(*) FILTER(WHERE outcome='applied'),count(*) FILTER(WHERE outcome='rejected'),
            min(received_at),max(recorded_at),
            array_agg(extract(epoch FROM recorded_at-received_at)*1000)
            FROM kafka_source_results WHERE received_at>=%s AND received_at<%s""",
            (start, end),
        ).fetchone()
        applied, rejected, first, last, latencies = source
        stages["processor"] = stage(
            applied,
            first,
            last,
            offset,
            [float(value) - shift for value in latencies or []],
            accepted,
        )
        stages["processor"]["rejected"] = rejected
        # Retried inputs: one row per failed offset, recorded on the database clock.
        stages["faults"] = dict(
            conn.execute(
                """SELECT consumer||':'||reason,count(*) FROM kafka_pipeline_faults
                WHERE recorded_at>=%s GROUP BY consumer,reason""",
                (start + offset,),
            ).fetchall()
        )
        if correlation:
            row = conn.execute(
                """SELECT count(*),max(e.processed_at),
                array_agg(extract(epoch FROM e.processed_at-r.received_at)*1000),
                array_agg(extract(epoch FROM e.processed_at-r.recorded_at)*1000)
                FROM kafka_source_results r JOIN kafka_correlation_events e ON e.event_id=r.changed_event_id
                WHERE r.received_at>=%s AND r.received_at<%s""",
                (start, end),
            ).fetchone()
            stages["correlation"] = stage(
                row[0],
                first,
                row[1],
                offset,
                [float(value) - shift for value in row[2] or []],
                accepted,
            )
            stages["correlation"]["after_processor_ms"] = percentiles(
                [float(value) for value in row[3] or []]
            )
    return stages


def stage(count, first, last, offset, latencies, accepted):
    elapsed = (last - offset - first).total_seconds() if first and last else None
    return {
        "count": count,
        "complete": count == accepted,
        "elapsed_seconds": round(elapsed, 3) if elapsed else None,
        "per_second": round(count / elapsed, 3) if elapsed else None,
        "latency_ms": percentiles(latencies),
    }


class PipelinePhase:
    def __init__(
        self,
        *,
        args,
        run,
        admission,
        tenants,
        operators,
        global_id,
        conninfo,
        database,
        es,
        index,
        read_reports,
        stop,
        errors,
        canonical,
        format_native_time,
        gateway,
    ):
        self.args, self.run = args, run
        self.admission, self.tenants, self.operators = admission, tenants, operators
        from psycopg.conninfo import make_conninfo

        self.global_id, self.database = global_id, database
        self.conninfo = make_conninfo(conninfo, application_name=HARNESS)
        self.es, self.index, self.read_reports = es, index, read_reports
        self.stop, self.errors = stop, errors
        self.canonical, self.format_native_time, self.gateway = (
            canonical,
            format_native_time,
            gateway,
        )
        self.correlation = args.features and args.correlators > 0

    def payload(self, scenario, ordinal):
        application = f"{self.run}-{scenario['name']}"
        node = f"{scenario['name']}-{ordinal}"
        if scenario["version"] == "v1":
            return (
                application,
                node,
                {
                    "application": application,
                    "object": "scale",
                    "node_name": node,
                    "message": f"Pipeline synthetic {ordinal} " + "x" * 3500,
                    "operator": "legacy",
                    "time_created": self.format_native_time(datetime.now(UTC)),
                    "severity": "error",
                },
            )
        # Same mix as the combined phase: suppression and correlation both run.
        warning = self.args.features and (ordinal // len(self.tenants)) % 5 == 0
        return (
            application,
            node,
            {
                "application": application,
                "node_name": node,
                "component": "scale",
                "severity": "warning" if warning else "critical",
                "status": "firing",
                "site": "synthetic",
                "impact": "qualification",
                "runbook_url": "https://example.invalid/runbook",
                "message": f"Pipeline synthetic {ordinal} " + "x" * 3500,
                "time_created": self.format_native_time(datetime.now(UTC)),
            },
        )

    async def offer(self, scenario, count, observer):
        state = {"accepted": 0, "admission_ms": [], "late_ms": [], "first": None, "last": None}
        stride = max(1, math.ceil(count / 200))
        slots = asyncio.Semaphore(self.args.burst_concurrency)

        async def send(ordinal):
            _, node, payload = self.payload(scenario, ordinal)
            gateway = None
            if scenario["version"] == "v2":
                index = 0 if scenario["tenants"] == "one" else ordinal % len(self.tenants)
                gateway = self.gateway(
                    str(uuid4()),
                    str(uuid4()),
                    operator_id=str(self.operators[index]),
                    tenant_id=str(self.tenants[index]),
                    policy_generation="1",
                    authorized_at=datetime.now(UTC).timestamp(),
                )
            began, received_at = monotonic(), datetime.now(UTC)
            state["first"] = state["first"] or began
            try:
                status, _ = await self.admission.admit(
                    self.canonical(payload),
                    scenario["version"],
                    gateway=gateway,
                    received_at=received_at,
                )
            except Exception as error:
                self.errors[
                    "pipeline_admission_" + getattr(error, "code", type(error).__name__)
                ] += 1
                return
            state["last"] = monotonic()
            state["admission_ms"].append((state["last"] - began) * 1000)
            if status != 202:
                self.errors[f"pipeline_admission_{status}"] += 1
                return
            state["accepted"] += 1
            if observer and ordinal % stride == 0:
                observer.add(node, received_at)

        async def bounded(ordinal):
            async with slots:
                await send(ordinal)

        if scenario.get("paced"):
            begin, tasks = monotonic(), set()
            for ordinal in range(count):
                due = begin + ordinal / self.args.steady_rate
                await asyncio.sleep(max(0, due - monotonic()))
                state["late_ms"].append(max(0, monotonic() - due) * 1000)
                task = asyncio.create_task(send(ordinal))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            await asyncio.gather(*tasks)
        else:
            await asyncio.gather(*(bounded(ordinal) for ordinal in range(count)))
        return state

    def drain(self, application, start, expected, began):
        import psycopg

        reached, deadline = {}, monotonic() + self.args.drain_seconds
        stages = ("processor", "correlation", "search", "watermark")
        wanted = [name for name in stages if name != "correlation" or self.correlation]
        last = {}
        while len(reached) < len(wanted):
            if self.stop.is_set():
                raise RuntimeError("Fixture worker stopped before the scenario drained")
            if monotonic() >= deadline:
                # Keep the partial measurement; the caller stops the sequence.
                return reached, last, True
            with psycopg.connect(self.conninfo) as conn:
                last["processor"] = conn.execute(
                    "SELECT count(*) FROM kafka_source_results WHERE received_at>=%s", (start,)
                ).fetchone()[0]
                if self.correlation:
                    last["correlation"] = conn.execute(
                        """SELECT count(*) FROM kafka_source_results r
                        JOIN kafka_correlation_events e ON e.event_id=r.changed_event_id
                        WHERE r.received_at>=%s""",
                        (start,),
                    ).fetchone()[0]
                lagging = conn.execute(
                    """SELECT count(*) FROM tenant_work_state w JOIN tenant_view_state v USING(tenant_id)
                    WHERE v.searchable_seq<w.projection_seq"""
                ).fetchone()[0]
            last["search"] = self.es.count(
                index=self.index,
                query={
                    "bool": {
                        "filter": [
                            {"term": {"application": application}},
                            {"term": {"deleted": False}},
                        ]
                    }
                },
            )["count"]
            elapsed = round(monotonic() - began, 3)
            for name in wanted:
                done = (
                    last.get(name, 0) >= expected
                    if name != "watermark"
                    else not lagging and "search" in reached
                )
                if done and name not in reached:
                    reached[name] = elapsed
            if len(reached) < len(wanted):
                sleep(0.25)
        return reached, last, False

    def settled_reports(self):
        # Workers publish diagnostics at most once a second; after a drain they
        # are idle, so a short settle yields their complete cumulative totals.
        sleep(2.5)
        return self.read_reports()

    def scenario(self, scenario, count, *, measure=True):
        name = scenario["name"]
        application = f"{self.run}-{name}"
        offset = clock_offset(self.conninfo)
        before = self.settled_reports()
        counters = write_counters(self.conninfo)
        start = datetime.now(UTC)
        print(f"Pipeline scenario {name}: offering {count} alerts", flush=True)
        observer = VisibilityObserver(self.es, self.index) if measure else None
        sampler = ActivitySampler(self.conninfo, self.database)
        began = monotonic()
        with sampler, observer or nullcontext():
            offered = asyncio.run(self.offer(scenario, count, observer))
            end = datetime.now(UTC) + timedelta(milliseconds=1)
            reached, last, timed_out = self.drain(application, start, offered["accepted"], began)
        after = self.settled_reports()
        if not measure:
            return {
                "name": name,
                "offered": count,
                "accepted": offered["accepted"],
                "drained_seconds": reached,
                "drain_timed_out": timed_out,
                "drain_counts": last,
            }
        written = write_counters(self.conninfo)
        written = {key: written[key] - counters[key] for key in written}
        accepted = offered["accepted"]
        offer_seconds = (offered["last"] - offered["first"]) if offered["last"] else None
        stages = stage_metrics(self.conninfo, start, end, offset, accepted, self.correlation)
        search_seconds = reached.get("search")
        stages["search"] = {
            "count": last.get("search"),
            "complete": last.get("search") == accepted,
            "drained_seconds": search_seconds,
            "per_second": round(accepted / search_seconds, 3) if search_seconds else None,
            "visibility_sample_ms": percentiles(list(observer.observed.values())),
            "visibility_sampled": len(observer.pending),
            "visibility_observed": len(observer.observed),
            "resolution_ms": 100,
        }
        self.errors.update(observer.errors)
        roles = worker_delta(before, after)
        return {
            "name": name,
            "version": scenario["version"],
            "tenants": {"all": len(self.tenants), "one": 1, "global": 1}[scenario["tenants"]],
            "offered": count,
            "accepted": accepted,
            "offer_seconds": round(offer_seconds, 3) if offer_seconds else None,
            "offered_per_second": round(accepted / offer_seconds, 3) if offer_seconds else None,
            "admission_ms": percentiles(offered["admission_ms"]),
            "generator_late_ms": percentiles(offered["late_ms"]),
            "db_clock_offset_ms": round(offset.total_seconds() * 1000, 3),
            "drained_seconds": reached,
            "drain_timed_out": timed_out,
            "drain_counts": last,
            "faults": stages.pop("faults"),
            "stages": stages,
            "workers": {role: summarize_role(roles[role]) for role in ROLES if role in roles},
            "database": sampler.summary(),
            "kafka_source_results_writes": written
            | {
                "hot_update_ratio": round(written["hot_updated"] / written["updated"], 4)
                if written["updated"]
                else None,
                "wal_bytes_per_alert": round(written["wal_bytes"] / accepted) if accepted else None,
            },
        }

    def execute(self):
        result = {"scenarios": []}
        if self.args.warmup:
            # Warm connections, CEL caches and consumer assignment; not reported
            # as a measurement and excluded from comparisons.
            result["warmup"] = self.scenario(
                {"name": "warmup", "version": "v2", "tenants": "all", "paced": False},
                self.args.warmup,
                measure=False,
            )
            warmup = result["warmup"]
            if warmup["drain_timed_out"] or warmup["accepted"] != warmup["offered"]:
                # Its backlog would be measured as part of the first scenario.
                result["stopped_after"] = "warmup"
                result["correctness_passed"] = False
                return result
        for scenario in SCENARIOS:
            count = (
                round(self.args.steady_rate * self.args.steady_seconds)
                if scenario.get("paced")
                else self.args.burst
            )
            result["scenarios"].append(self.scenario(scenario, count))
            if result["scenarios"][-1]["drain_timed_out"]:
                # A backlog would leak into the next scenario's measurement.
                result["stopped_after"] = scenario["name"]
                break
        by_name = {item["name"]: item for item in result["scenarios"]}

        def rate(name, stage_name="processor"):
            return by_name.get(name, {}).get("stages", {}).get(stage_name, {}).get("per_second")

        def ratio(value, base):
            return round(value / base, 3) if value and base else None

        spread = rate("burst_spread")
        result["serialization"] = {
            "processor_per_second": {
                name: rate(name) for name in ("burst_spread", "burst_one_tenant", "burst_global_v1")
            },
            "one_tenant_vs_spread": ratio(rate("burst_one_tenant"), spread),
            "global_v1_vs_spread": ratio(rate("burst_global_v1"), spread),
            "correlation_per_second": {
                name: rate(name, "correlation")
                for name in ("burst_spread", "burst_one_tenant", "burst_global_v1")
            },
        }
        result["confirmation"] = {
            name: {
                key: item["workers"].get("source", {}).get("derived", {}).get(key)
                for key in (
                    "records_per_publish",
                    "confirmation_ms_per_record",
                    "confirmation_share_of_active_turns",
                    "kafka_transaction_ms_per_record",
                    "apply_ms_per_record",
                )
            }
            | {
                "hot_update_ratio": item["kafka_source_results_writes"]["hot_update_ratio"],
                "wal_bytes_per_alert": item["kafka_source_results_writes"]["wal_bytes_per_alert"],
            }
            for name, item in by_name.items()
        }
        result["correctness_passed"] = len(result["scenarios"]) == len(SCENARIOS) and all(
            item["accepted"] == item["offered"]
            and item["stages"]["processor"]["complete"]
            and not item["stages"]["processor"]["rejected"]
            and item["stages"]["search"]["complete"]
            and (not self.correlation or item["stages"]["correlation"]["complete"])
            and "watermark" in item["drained_seconds"]
            for item in result["scenarios"]
        )
        return result
