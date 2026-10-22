"""Isolated combined Kafka/PG/ES/API workload, using explicit synthetic fixtures.

Not a production qualification or an OIDC/gateway/browser benchmark. Reads use
the real HTTPS Product API, normal authorization, distinct fixture sessions and
bounded quick counts. Arrivals use production KafkaAdmission with a controlled
trusted-routing fixture. The existing real-browser suite tests OIDC separately.
Requires APPCHI_SCALE_ADMIN_URL for an explicitly isolated loopback PostgreSQL
cluster. All created database/index/topic names are unique and removed afterward.

``--phase pipeline`` skips the API and users and instead drains fixed workloads
through the consume()-based source, correlation and indexer workers; see
``scalability_pipeline.py`` and the development runbook for comparing revisions.
"""

import argparse
import asyncio
import base64
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import threading
from time import monotonic, sleep, time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def percentile(values, p):
    return round(sorted(values)[min(len(values) - 1, int(len(values) * p))], 3) if values else None


class SeedPacer:
    """Optional setup-only rate ceiling; slow setup never causes catch-up bursts."""

    def __init__(self, rate):
        self.rate = rate
        self.next_at = None
        self.lock = asyncio.Lock()

    async def wait(self):
        if self.rate is None:
            return
        async with self.lock:
            if self.next_at is not None:
                await asyncio.sleep(max(0, self.next_at - monotonic()))
            self.next_at = monotonic() + 1 / self.rate


async def diagnostic_read(client, method, url, **kwargs):
    """Measure complete client latency separately from response/header delivery."""
    started = monotonic()
    async with client.stream(method, url, **kwargs) as response:
        headers_ms = (monotonic() - started) * 1000
        await response.aread()
    complete_ms = (monotonic() - started) * 1000
    try:
        asgi_ms = float(response.headers["x-appchi-diagnostic-asgi-ms"])
        if not math.isfinite(asgi_ms) or asgi_ms < 0:
            asgi_ms = None
    except (KeyError, ValueError):
        asgi_ms = None
    return response, {
        "client_headers_ms": headers_ms,
        "client_complete_ms": complete_ms,
        "asgi_response_start_ms": asgi_ms,
    }


def publish_clock(path):
    temporary = path.with_suffix(".new")
    temporary.write_text(
        json.dumps(
            {
                "synchronized": True,
                "offset_seconds": 0,
                "measured_at": datetime.now(UTC).isoformat(),
            }
        )
    )
    # Windows readers can briefly deny replacement even though the fixture is
    # writable. Keep the old complete sample available while retrying; do not
    # turn a transient sharing conflict into an indefinitely dead clock thread.
    deadline = monotonic() + 5
    while True:
        try:
            temporary.replace(path)
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) not in (5, 32, 33) or monotonic() >= deadline:
                raise
            sleep(0.025)


def main():
    # CEL compilation uses spawn with a strict deadline. A spawned compiler
    # must not import the entire HTTP/Kafka/load harness before doing its work.
    import httpx
    from scalability_worker import host_memory
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from pydantic import SecretStr
    from confluent_kafka import KafkaError, KafkaException
    from confluent_kafka.admin import AdminClient, NewTopic
    from elasticsearch import Elasticsearch
    from elastic_transport import ConnectionError as ESConnectionError
    from elastic_transport import ConnectionTimeout as ESConnectionTimeout

    from appchi.config import Settings
    from appchi.db import Database
    from appchi.ingestion.gateway import GatewayIdentity
    from appchi.ingestion.kafka_admission import KafkaAdmission, KafkaPublisher
    from appchi.ingestion.native import canonical, format_native_time
    from appchi.kafka_changed import ChangedConsumer
    from appchi import kafka_source
    from appchi.cel import compiler as cel_compiler
    from appchi.kafka_source import SourceConsumer
    from appchi.migrate import migrate
    from appchi.search.consumer import index_batch, index_record
    from appchi.search.projection import index_mapping
    from appchi.search.reconciliation import ReconciliationRunner

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--rate", type=float, default=5000 / 60)
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--tenants", type=int, default=50)
    parser.add_argument("--seed", type=int, default=5000)
    parser.add_argument("--seed-batch", type=int, default=10)
    parser.add_argument(
        "--seed-rate",
        type=float,
        default=None,
        help="Optional setup-only alert rate ceiling; omitted preserves unpaced batches",
    )
    parser.add_argument("--preload-seconds", type=int, default=600)
    parser.add_argument("--partitions", type=int, default=8)
    parser.add_argument("--processors", type=int, default=4)
    parser.add_argument("--indexers", type=int, default=4)
    parser.add_argument("--api-pool", type=int, default=16)
    parser.add_argument("--response-timings", action="store_true")
    parser.add_argument("--worker-mode", choices=("threads", "processes"), default="processes")
    parser.add_argument("--correlators", type=int, default=4)
    parser.add_argument(
        "--features",
        action="store_true",
        help="Enable explicitly synthetic correlation and maintenance fixtures",
    )
    parser.add_argument("--read-pause", type=float, default=2)
    parser.add_argument("--drain-seconds", type=int, default=180)
    parser.add_argument(
        "--phase",
        choices=("combined", "pipeline"),
        default="combined",
        help="combined: API reads with arrivals; pipeline: fixed drained pipeline workloads",
    )
    parser.add_argument("--kafka-bootstrap", default="127.0.0.1:19093")
    parser.add_argument("--elasticsearch-url", default="http://127.0.0.1:59201")
    parser.add_argument("--redis-url", default="redis://127.0.0.1:56380/0")
    pipeline = parser.add_argument_group("pipeline phase (fixed workload pipeline-v1)")
    pipeline.add_argument("--warmup", type=int, default=100)
    pipeline.add_argument("--steady-rate", type=float, default=3)
    pipeline.add_argument("--steady-seconds", type=int, default=60)
    pipeline.add_argument("--burst", type=int, default=600)
    pipeline.add_argument("--burst-concurrency", type=int, default=50)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (
        1 <= args.seconds <= 3600
        and 0 < args.rate <= 500
        and 1 <= args.users <= 100
        and 1 <= args.tenants <= 50
        and 0 <= args.seed <= 100000
        and 1 <= args.seed_batch <= 100
        and (args.seed_rate is None or 0 < args.seed_rate <= 500)
        and 1 <= args.preload_seconds <= 3600
        and 1 <= args.processors <= args.partitions <= 32
        and 1 <= args.indexers <= args.partitions
        and 1 <= args.correlators <= 16
        and 1 <= args.api_pool <= 32
        and args.read_pause >= 1
        and 1 <= args.drain_seconds <= 3600
        and 0 <= args.warmup <= 20000
        and 0 < args.steady_rate <= 500
        and 1 <= args.steady_seconds <= 600
        and 1 <= args.burst <= 20000
        and 1 <= args.burst_concurrency <= 200
    ):
        parser.error("Workload exceeds the explicit local diagnostic bounds")
    if args.phase == "pipeline" and args.worker_mode != "processes":
        parser.error("The pipeline phase measures production worker processes only")
    for endpoint in (args.kafka_bootstrap, args.elasticsearch_url, args.redis_url):
        if endpoint.split("//")[-1].split(":")[0] not in ("127.0.0.1", "localhost"):
            parser.error("Only isolated loopback Kafka, Elasticsearch and Redis are supported")
    admin = os.environ["APPCHI_SCALE_ADMIN_URL"]
    parameters = conninfo_to_dict(admin)
    if parameters.get("host") not in ("127.0.0.1", "localhost"):
        parser.error("Only an explicitly isolated loopback PostgreSQL cluster is supported")
    run = "scale-" + uuid4().hex
    name = "appchi_scale_" + run[6:]
    index = "appchi-" + run
    broker = args.kafka_bootstrap
    endpoint = args.elasticsearch_url
    topics = [f"appchi.{run}.alerts.{kind}" for kind in ("received", "changed")]
    db_url = make_conninfo(admin, dbname=name)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"https://localhost:{port}"
    clock = ROOT / ".local" / (run + "-clock.json")
    profile_file = ROOT / ".local" / (run + "-profile.json")
    worker_stop = ROOT / ".local" / (run + "-stop")
    measurement_file = ROOT / ".local" / (run + "-measurement")
    profile = {"id": run, "qualified": False, "search_max_document_bytes": 104857600}
    if args.features:
        from scalability_feature_fixture import synthetic_feature_profile

        if args.phase == "pipeline":
            # The one-tenant burst lands on the first tenant on top of its share.
            offered = args.warmup + round(args.steady_rate * args.steady_seconds) + args.burst
            per_tenant = math.ceil(offered / args.tenants) + args.burst + 10
        else:
            per_tenant = math.ceil((args.seed + args.seconds * args.rate) / args.tenants) + 10
        profile = synthetic_feature_profile(profile, max_alerts_per_tenant=per_tenant)
    # A fresh checkout or worktree has no ignored local state directory yet.
    profile_file.parent.mkdir(exist_ok=True)
    profile_file.write_text(json.dumps(profile))
    publish_clock(clock)
    settings = Settings(
        database_url=db_url,
        deployment_epoch=run,
        environment="development",
        public_origin=origin,
        pool_size=args.api_pool,
        capacity_profile=profile_file,
        wrapping_key=base64.b64encode(secrets.token_bytes(32)).decode(),
        elasticsearch_url=endpoint,
        elasticsearch_index=index,
        kafka_bootstrap=broker,
        redis_url=args.redis_url,
        clock_health_file=clock,
        database_clock_health_file=clock,
    )
    transport_settings = SimpleNamespace(
        **{
            **settings.model_dump(),
            "environment": run,
            "clock_health_file": clock,
            "database_clock_health_file": clock,
        }
    )
    stats = {
        "arrival_ms": [],
        "read_ms": [],
        "read_diagnostics": {},
        "errors": Counter(),
        "accepted": 0,
        "read_requests": 0,
        "offered": 0,
        "seed_offered": 0,
        "seed_accepted": 0,
        "generator_late_ms": [],
        "worker_turns": Counter(),
        "sse_connections": 0,
        "sse_messages": 0,
        "pit_closed": 0,
        "accepted_by_tenant": Counter(),
        "expected_suppressed": 0,
        "search_sample_arrivals": {},
        "search_visibility_ms": {},
        "source_phases_ms": {},
    }
    sample_lock = threading.Lock()
    sample_stride = max(1, math.ceil(args.seconds * args.rate / 1000))

    def http_failure(label, response):
        try:
            code = response.json().get("error", {}).get("code", "unknown")
        except (ValueError, AttributeError):
            code = "invalid_response"
        if (
            not isinstance(code, str)
            or len(code) > 100
            or not all(c.isascii() and (c.isalnum() or c == "_") for c in code)
        ):
            code = "invalid_error_code"
        stats["errors"][f"{label}_{response.status_code}_{code}"] += 1

    stop = threading.Event()
    threads = []
    databases = []
    server = None
    publisher = None
    worker_processes = []
    worker_health_files = []
    worker_reports = {}
    created_db = False
    roles = []
    result = {
        "host_memory_at_start": host_memory(),
        "run": run,
        "profile": vars(args) | {"output": str(args.output)},
        "scope": "Synthetic authorized sessions; HTTPS API, Kafka admission/library, real source and indexer; no OIDC/gateway/render or enabled rule/maintenance capacity claim",
        "feature_profile": profile if args.features else None,
        "source_sql_scope": (
            "Not instrumented in process mode; per-process postgres_apply and durable received-to-recorded latency include real SQL waits."
            if args.worker_mode == "processes"
            else "Explicit Connection.execute calls inside source apply; pipeline calls measure enqueue time, excluding sync/transaction boundaries. Overall postgres_apply includes those waits."
        ),
        "worker_topology": (
            "Separate production-lifecycle worker processes and uvicorn API process; delivery fixture adapts only private namespace, stop polling and diagnostics. Local process resources are not production pod qualification."
            if args.worker_mode == "processes"
            else "Independent consumer/database clients in harness threads; API runs as a separate uvicorn process. Consumer count is not a claim of equivalent production pod resources."
        ),
    }
    phase_lock = threading.Lock()
    source_thread = threading.local()
    sql_phases = {}
    original_execute = psycopg.Connection.execute

    def collect_worker_reports():
        for process, path in worker_processes:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            previous = worker_reports.get(process.pid, {})
            stats["errors"].update(Counter(data["errors"]) - Counter(previous.get("errors", {})))
            stats["worker_turns"].update(
                Counter(data["turns"]) - Counter(previous.get("turns", {}))
            )
            with phase_lock:
                for label, values in data["phases_ms"].items():
                    old_count = len(previous.get("phases_ms", {}).get(label, []))
                    stats["source_phases_ms"].setdefault(label, []).extend(values[old_count:])
            worker_reports[process.pid] = data
        result["worker_memory"] = [
            {
                "pid": report["pid"],
                "role": report["role"],
                "peak_sampled_rss_bytes": report["peak_sampled_rss_bytes"],
            }
            for report in worker_reports.values()
        ]

    def private_environment(config):
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("APPCHI_")
        }
        for key, value in config.model_dump().items():
            if value is not None:
                if isinstance(value, SecretStr):
                    value = value.get_secret_value()
                elif isinstance(value, (tuple, list, dict, bool)):
                    value = json.dumps(value)
                environment["APPCHI_" + key.upper()] = str(value)
        return environment

    def timed_execute(connection, query, *values, **kwargs):
        cohort = getattr(source_thread, "cohort", None)
        if cohort is None:
            return original_execute(connection, query, *values, **kwargs)
        statement = str(query).lower()
        label = next(
            (
                name
                for name, markers in (
                    (
                        "restore_guard",
                        (
                            "restore_control",
                            "pg_try_advisory_lock_shared",
                            "pg_advisory_unlock_shared",
                        ),
                    ),
                    ("session_settings", ("set local", "set_config(")),
                    ("source_receipt", ("kafka_source_results",)),
                    ("maintenance", ("maintenance_",)),
                    ("correlation", ("correlation_",)),
                    (
                        "alert_identity",
                        ("alert_identities", "alert_key_claims", "alert_retired_keys"),
                    ),
                    ("alert_state", ("alerts ", "into alerts(")),
                    ("tenant_order", ("tenant_work_state",)),
                    ("tenant_view", ("tenant_view_state",)),
                    ("clock", ("clock_timestamp()",)),
                    ("capability", ("capability_gates",)),
                    ("operator", ("operators ",)),
                    ("activity_history", ("alert_activity", "alert_occurrences")),
                    ("projection_work", ("projection_work",)),
                    (
                        "projection_relations",
                        ("alert_notes", "incident_alert_links", "alert_active_ownership"),
                    ),
                    ("transaction_scope", ("pg_current_xact_id", "current_setting(")),
                )
                if any(marker in statement for marker in markers)
            ),
            "other",
        )
        began = monotonic()
        try:
            return original_execute(connection, query, *values, **kwargs)
        finally:
            duration = (monotonic() - began) * 1000
            with phase_lock:
                aggregate = sql_phases.setdefault(
                    cohort + ":" + label, {"count": 0, "total_ms": 0.0, "max_ms": 0.0}
                )
                aggregate["count"] += 1
                aggregate["total_ms"] += duration
                aggregate["max_ms"] = max(aggregate["max_ms"], duration)

    psycopg.Connection.execute = timed_execute

    def record_phase(label, began):
        cohort = "measured" if result.get("measured_from") else "seed"
        with phase_lock:
            stats["source_phases_ms"].setdefault(cohort + ":" + label, []).append(
                (monotonic() - began) * 1000
            )

    original_apply_record = kafka_source.apply_record
    original_compile = cel_compiler._compile_checked

    def timed_compile(*values, **kwargs):
        began = monotonic()
        try:
            return original_compile(*values, **kwargs)
        except cel_compiler.CELUnavailableError as error:
            # Only classify fixed local diagnostics, never store source input.
            text = str(error)
            kind = (
                "deadline"
                if "deadline" in text
                else "crashed"
                if "crashed" in text
                else "runtime_unavailable"
            )
            stats["errors"]["cel_compile_" + kind] += 1
            raise
        finally:
            record_phase("cel_compile", began)

    cel_compiler._compile_checked = timed_compile

    def timed_apply_record(*values, **kwargs):
        began = monotonic()
        envelope = json.loads(kwargs["value"])
        arrival = datetime.fromisoformat(envelope["received_at"].replace("Z", "+00:00"))
        cohort = (
            "measured"
            if result.get("measured_from") and arrival >= result["measured_from"]
            else "seed"
        )
        with phase_lock:
            stats["source_phases_ms"].setdefault(cohort + ":queue_age", []).append(
                (datetime.now(UTC) - arrival).total_seconds() * 1000
            )
        source_thread.cohort = cohort
        try:
            return original_apply_record(*values, **kwargs)
        except Exception as error:
            stats["errors"]["source_apply_" + getattr(error, "code", type(error).__name__)] += 1
            raise
        finally:
            source_thread.cohort = None
            with phase_lock:
                stats["source_phases_ms"].setdefault(cohort + ":postgres_apply", []).append(
                    (monotonic() - began) * 1000
                )

    class TimedProducer:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            method = getattr(self.inner, name)
            if name not in {"send_offsets_to_transaction", "commit_transaction"}:
                return method

            def invoke(*values, **kwargs):
                began = monotonic()
                try:
                    result = method(*values, **kwargs)
                    if name == "commit_transaction":
                        source_thread.confirmed_at = monotonic()
                    return result
                finally:
                    record_phase(name, began)

            return invoke

    class TimedConsumerClient:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def poll(self, *values, **kwargs):
            began = monotonic()
            try:
                return self.inner.poll(*values, **kwargs)
            finally:
                record_phase("poll", began)

        def consume(self, *values, **kwargs):
            # Batched source turns fetch with consume(); poll() only when idle.
            began = monotonic()
            try:
                return self.inner.consume(*values, **kwargs)
            finally:
                record_phase("consume", began)

    class TimedSourceConsumer(SourceConsumer):
        def __init__(self, *values, **kwargs):
            super().__init__(*values, **kwargs)
            self.client = TimedConsumerClient(self.client)

        def _assigned(self, consumer, partitions):
            began = monotonic()
            super()._assigned(consumer, partitions)
            record_phase("assignment", began)
            for partition, producer in self.producers.items():
                if not isinstance(producer, TimedProducer):
                    self.producers[partition] = TimedProducer(producer)

        def turn(self):
            began = monotonic()
            source_thread.confirmed_at = None
            try:
                progress = super().turn()
            except Exception:
                record_phase("failed_turn", began)
                raise
            record_phase("applied_turn" if progress else "idle_turn", began)
            if source_thread.confirmed_at is not None:
                record_phase("postgres_confirmation", source_thread.confirmed_at)
            return progress

    kafka_source.apply_record = timed_apply_record

    def measured_index_handler(handler):
        def invoke(*values, **kwargs):
            began = monotonic()
            try:
                return handler(*values, **kwargs)
            except Exception as error:
                code = getattr(error, "code", type(error).__name__)
                stats["errors"]["index_handler_" + code] += 1
                raise
            finally:
                record_phase("index_handler", began)

        return invoke

    measured_index_record = measured_index_handler(index_record)
    measured_index_batch = measured_index_handler(index_batch)
    es = Elasticsearch(endpoint, request_timeout=15, max_retries=0)
    kafka = AdminClient({"bootstrap.servers": broker})
    try:
        with psycopg.connect(admin, autocommit=True) as conn:
            # Creation can succeed while its response is lost. This unique
            # fixture name still needs cleanup after an ambiguous connection loss.
            created_db = True
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        migrate(db_url)
        with psycopg.connect(db_url) as conn:
            migration_name, migration_checksum = conn.execute(
                "SELECT name, checksum FROM schema_migrations ORDER BY name DESC LIMIT 1"
            ).fetchone()
        result["migration_head"] = {"filename": migration_name, "sha256": migration_checksum}
        from appchi import worker_health

        result["worker_health_sha256"] = hashlib.sha256(
            Path(worker_health.__file__).read_bytes()
        ).hexdigest()
        for suffix, parent in (("api", "appchi_api"), ("worker", "appchi_worker")):
            role = name + "_" + suffix
            password = secrets.token_urlsafe(32)
            with psycopg.connect(admin, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(
                        sql.Identifier(role), sql.Literal(password), sql.Identifier(parent)
                    )
                )
            roles.append(role)
            role_url = make_conninfo(db_url, user=role, password=password)
            if suffix == "api":
                settings.database_url = SecretStr(role_url)
            else:
                worker_url = role_url
        with psycopg.connect(db_url) as conn:
            global_id = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()[0]
            conn.execute(
                "UPDATE capability_gates SET ready=true WHERE capability IN ('human_auth','search')"
            )
            conn.execute(
                "UPDATE kafka_pipeline_control SET enabled=true,cutover_at=clock_timestamp(),received_partitions=%s,changed_partitions=%s",
                (args.partitions, args.partitions),
            )
            tenants = [uuid4() for _ in range(args.tenants)]
            operators = [uuid4() for _ in tenants]
            for tenant, operator in zip(tenants, operators):
                conn.execute(
                    "INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
                    (tenant, str(tenant), str(tenant)),
                )
                for table in (
                    "tenant_work_state",
                    "tenant_view_state",
                    "tenant_configuration_guard",
                ):
                    conn.execute(
                        sql.SQL("INSERT INTO {}(tenant_id) VALUES(%s)").format(
                            sql.Identifier(table)
                        ),
                        (tenant,),
                    )
                conn.execute(
                    "INSERT INTO operators(id,tenant_id,name,name_key,lifecycle) VALUES(%s,%s,%s,%s,'ready')",
                    (operator, tenant, str(operator), str(operator)),
                )
            conn.execute(
                "UPDATE tenant_view_state SET index_epoch=%s,verified_at=clock_timestamp()", (run,)
            )
            conn.execute(
                "INSERT INTO search_index_control(singleton,alias_name,physical_index,index_epoch,binding_digest) VALUES(true,%s,%s,%s,%s)",
                (index, index, run, "synthetic-fixture-" + run),
            )
            sessions = []
            cipher = settings.cipher()
            for _ in range(args.users if args.phase == "combined" else 0):
                principal, handle, csrf = (
                    uuid4(),
                    secrets.token_urlsafe(32),
                    secrets.token_urlsafe(32),
                )
                digest = hashlib.sha256(handle.encode()).digest()
                conn.execute(
                    "INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible,evidence_generation,verified_at) VALUES(%s,'scale-fixture',%s,'user','Synthetic scale user',true,1,clock_timestamp())",
                    (principal, str(principal)),
                )
                conn.cursor().executemany(
                    "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
                    [(tenant, principal) for tenant in tenants],
                )
                conn.execute(
                    "INSERT INTO auth_sessions(handle_hash,principal_id,issuer,subject,provider_sid,csrf_hash,csrf_cipher,auth_time,activity_at,absolute_at,deployment_epoch) VALUES(%s,%s,'https://synthetic.invalid',%s,%s,%s,%s,clock_timestamp(),clock_timestamp(),clock_timestamp()+interval '2 hours',%s)",
                    (
                        digest,
                        principal,
                        str(principal),
                        str(principal),
                        hashlib.sha256(csrf.encode()).digest(),
                        cipher.seal(csrf.encode(), "session-csrf:" + digest.hex()),
                        run,
                    ),
                )
                sessions.append((handle, csrf))
        transport_settings.global_tenant_id = global_id
        if args.features:
            from scalability_feature_fixture import install_feature_fixture
            from appchi.shared.errors import AppError

            fixture_db = Database(db_url, size=1, deployment_epoch=run)
            try:
                try:
                    install_feature_fixture(fixture_db, tenants, profile=profile)
                except AppError as error:
                    if error.code != "cel_compilation_unavailable":
                        raise
                    stats["errors"]["feature_setup_compile_retry"] += 1
                    result["feature_setup_retry"] = {
                        "initial_failure": error.code,
                        "attempts": 1,
                        "succeeded": False,
                        "capacity_qualified": False,
                    }
                    # Native compilation precedes fixture rule/window writes.
                    # Retry once within this process to exercise correctness;
                    # the original failure still makes the run fail qualification.
                    install_feature_fixture(fixture_db, tenants, profile=profile)
                    result["feature_setup_retry"]["succeeded"] = True
            finally:
                fixture_db.close()
            result["scope"] = (
                "Synthetic authorized sessions and feature profile; HTTPS API, Kafka admission/library, source/indexer/correlation and mixed maintenance; no OIDC/gateway/render or production qualification"
            )
        for future in kafka.create_topics(
            [NewTopic(topic, args.partitions, 1) for topic in topics]
        ).values():
            future.result(20)
        index_setup_started = monotonic()
        es.options(request_timeout=30).indices.create(
            index=index,
            mappings=index_mapping(),
            settings={"number_of_shards": 1, "number_of_replicas": 0},
        )
        result["setup_index_seconds"] = round(monotonic() - index_setup_started, 3)
        publisher = KafkaPublisher(transport_settings)
        admission = KafkaAdmission(publisher, environment=run, global_tenant_id=global_id)

        def worker(role):
            db = Database(worker_url, size=2, deployment_epoch=run)
            databases.append(db)
            consumer = None
            runner = ReconciliationRunner() if role == "indexer" else None
            try:
                while not stop.is_set():
                    try:
                        if consumer is None:
                            if role == "correlation":
                                from appchi.correlation.consumer import correlate_record
                            consumer = (
                                TimedSourceConsumer(db, transport_settings, profile)
                                if role == "source"
                                else ChangedConsumer(
                                    db,
                                    transport_settings,
                                    profile,
                                    role,
                                    correlate_record
                                    if role == "correlation"
                                    else measured_index_record,
                                    batch_handler=measured_index_batch
                                    if role == "indexer"
                                    else None,
                                )
                            )
                        progress = consumer.turn()
                        if runner:
                            try:
                                progress += runner(db, transport_settings) or 0
                            except Exception as error:
                                stats["errors"][
                                    "reconciliation_" + getattr(error, "code", type(error).__name__)
                                ] += 1
                        stats["worker_turns"][role] += progress or 0
                        if not progress:
                            stop.wait(0.01)
                    except Exception as error:
                        stats["errors"][
                            "worker_" + role + "_" + getattr(error, "code", type(error).__name__)
                        ] += 1
                        if consumer:
                            consumer.close()
                            consumer = None
                        stop.wait(0.1)
            finally:
                if consumer:
                    consumer.close()
                if runner and hasattr(runner, "close"):
                    runner.close()

        def clock_refresh():
            while not stop.wait(10):
                try:
                    publish_clock(clock)
                except OSError as error:
                    stats["errors"]["fixture_clock_" + type(error).__name__] += 1
                    stop.set()
                    return
                if any(process.poll() is not None for process, _ in worker_processes):
                    stats["errors"]["fixture_worker_early_exit"] += 1
                    stop.set()
                    return

        def start_workers():
            started = monotonic()
            wave = []
            worker_roles = ["source"] * args.processors + ["indexer"] * args.indexers
            if args.features:
                worker_roles += ["correlation"] * args.correlators
            # Schema, fixture and index setup can exceed the evidence freshness
            # window. Workers must start with a newly published fixture sample.
            publish_clock(clock)
            result["host_memory_before_workers"] = host_memory()
            for ordinal, role in enumerate(worker_roles):
                if args.worker_mode == "threads":
                    thread = threading.Thread(target=worker, args=(role,), daemon=True)
                    thread.start()
                    threads.append(thread)
                else:
                    health = ROOT / ".local" / f"{run}-worker-{ordinal}-health.json"
                    worker_health_files.append(health)
                    config = settings.model_copy(
                        update={
                            "database_url": SecretStr(worker_url),
                            "pool_size": 2,
                            "global_tenant_id": global_id,
                            "role": role,
                            "worker_health_path": health,
                        }
                    )
                    environment = private_environment(config)
                    metrics = ROOT / ".local" / f"{run}-worker-{ordinal}.json"
                    environment.update(
                        {
                            "APPCHI_SCALE_NAMESPACE": run,
                            "APPCHI_SCALE_STOP_FILE": str(worker_stop),
                            "APPCHI_SCALE_MEASUREMENT_FILE": str(measurement_file),
                            "APPCHI_SCALE_METRICS_FILE": str(metrics),
                        }
                    )
                    process = subprocess.Popen(
                        [sys.executable, "qualification/scalability_worker.py", role],
                        env=environment,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    worker_processes.append((process, metrics))
                    wave.append((process, health))
                    if len(wave) == 2 or ordinal == len(worker_roles) - 1:
                        deadline = monotonic() + 60
                        for pending_process, pending_health in wave:
                            while monotonic() < deadline and pending_process.poll() is None:
                                try:
                                    state = json.loads(pending_health.read_text())
                                    if state.get("ready") and not state.get("draining"):
                                        break
                                except (OSError, ValueError):
                                    pass
                                sleep(0.1)
                            else:
                                result["worker_startup_seconds"] = round(monotonic() - started, 3)
                                raise RuntimeError("Worker startup readiness deadline exceeded")
                        wave.clear()
            result["worker_startup_seconds"] = round(monotonic() - started, 3)

        thread = threading.Thread(target=clock_refresh, daemon=True)
        thread.start()
        threads.append(thread)
        if args.phase == "combined":
            tls = ROOT / ".local/keycloak/tls"
            # Use the real process entrypoint. Embedding the API in the generator's
            # thread changes Windows CEL spawn startup and resource contention.
            api_environment = private_environment(settings)
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    *(
                        ["qualification.scalability_api:create_app", "--factory"]
                        if args.response_timings
                        else ["appchi.main:app"]
                    ),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "error",
                    "--no-access-log",
                    "--ssl-keyfile",
                    str(tls / "localhost-key.pem"),
                    "--ssl-certfile",
                    str(tls / "localhost.pem"),
                ],
                env=api_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            result["response_timing_scope"] = (
                "Test-only outer ASGI wrapper header measures scope entry to response start; "
                "client headers/complete durations also include transport, scheduling and body delivery. "
                "Production application, lifespan, authorization and budgets are unchanged."
                if args.response_timings
                else None
            )
            api_setup_started = monotonic()
            until = api_setup_started + 90
            with httpx.Client(
                verify=ssl.create_default_context(cafile=str(tls / "localhost.pem")), timeout=1
            ) as readiness:
                while monotonic() < until and server.poll() is None:
                    try:
                        if readiness.get(origin + "/health/live").status_code < 500:
                            break
                    except httpx.HTTPError:
                        pass
                    sleep(0.1)
                else:
                    result["api_exit_code"] = server.poll()
                    result["setup_api_seconds"] = round(monotonic() - api_setup_started, 3)
                    raise RuntimeError("HTTPS API did not start")
            result["setup_api_seconds"] = round(monotonic() - api_setup_started, 3)
        start_workers()

        async def send(ordinal, measured):
            tenant_index = ordinal % len(tenants)
            payload = {
                "application": run,
                "node_name": f"node-{ordinal}",
                "component": "scale",
                "severity": "warning"
                if args.features and (ordinal // len(tenants)) % 5 == 0
                else "critical",
                "status": "firing",
                "site": "synthetic",
                "impact": "qualification",
                "runbook_url": "https://example.invalid/runbook",
                "message": f"Scale synthetic {ordinal} " + "x" * 3500,
                "time_created": format_native_time(datetime.now(UTC)),
            }
            gateway = GatewayIdentity(
                str(uuid4()),
                str(uuid4()),
                operator_id=str(operators[tenant_index]),
                tenant_id=str(tenants[tenant_index]),
                policy_generation="1",
                authorized_at=time(),
            )
            started = monotonic()
            received_at = datetime.now(UTC)
            stats["offered" if measured else "seed_offered"] += 1
            try:
                status, response = await admission.admit(
                    canonical(payload), "v2", gateway=gateway, received_at=received_at
                )
                accepted = status == 202
                if accepted:
                    stats["accepted_by_tenant"][str(tenants[tenant_index])] += 1
                    if args.features and tenant_index < 20 and payload["severity"] == "warning":
                        stats["expected_suppressed"] += 1
                if measured:
                    stats["accepted"] += int(accepted)
                    stats["arrival_ms"].append((monotonic() - started) * 1000)
                    if not accepted:
                        stats["errors"]["admission_" + str(status)] += 1
                    elif (ordinal - args.seed) % sample_stride == 0:
                        with sample_lock:
                            stats["search_sample_arrivals"][f"node-{ordinal}"] = received_at
                else:
                    stats["seed_accepted"] += int(accepted)
                    if not accepted:
                        stats["errors"]["seed_admission_" + str(status)] += 1
            except Exception as error:
                stats["errors"]["admission_" + getattr(error, "code", type(error).__name__)] += 1

        async def run_workload():
            result["stage"] = "preload"
            print(f"Preload: offering {args.seed} alerts before user startup", flush=True)
            preload_started = monotonic()
            preload_deadline = preload_started + args.preload_seconds
            next_preload_report = preload_started + 30
            seed_pacer = SeedPacer(args.seed_rate)

            async def send_seed(ordinal):
                await seed_pacer.wait()
                await send(ordinal, False)

            try:
                # One setup budget includes offering and both drains. Per-stage
                # budgets could otherwise multiply under a saturated host.
                async with asyncio.timeout(args.preload_seconds):
                    for batch in range(0, args.seed, args.seed_batch):
                        await asyncio.gather(
                            *(
                                send_seed(i)
                                for i in range(batch, min(batch + args.seed_batch, args.seed))
                            )
                        )
                        if stats["seed_accepted"] != stats["seed_offered"]:
                            raise RuntimeError("Preload admission was not fully acknowledged")
                        if monotonic() >= next_preload_report:
                            print(
                                f"Preload acknowledged {stats['seed_accepted']}/{args.seed}; "
                                f"elapsed {monotonic() - preload_started:.1f}s",
                                flush=True,
                            )
                            next_preload_report = monotonic() + 30
                    await asyncio.to_thread(
                        wait_applied, args.seed, max(0, preload_deadline - monotonic())
                    )
                    await asyncio.to_thread(
                        wait_searchable, args.seed, max(0, preload_deadline - monotonic())
                    )
            finally:
                result["preload_elapsed_seconds"] = round(monotonic() - preload_started, 3)
            result["stage"] = "user_startup"
            print(f"Preload visible; establishing {args.users} active user sessions", flush=True)
            # Ordinary authorized requests establish a ready, warm steady-state
            # starting point; cold-start/real OIDC behavior is a separate suite.
            context = ssl.create_default_context(cafile=str(tls / "localhost.pem"))
            handle, csrf = sessions[0]
            async with httpx.AsyncClient(
                base_url=origin,
                verify=context,
                timeout=15,
                headers={
                    "Origin": origin,
                    "X-CSRF-Token": csrf,
                    "Cookie": "__Host-appchi_session=" + handle,
                },
            ) as client:
                response = await client.post(
                    f"/api/tenants/{tenants[0]}/alerts/counts",
                    json={
                        "queries": [
                            {"id": "all", "cel": "true", "include_suppressed": True},
                            {
                                "id": "active",
                                "cel": "status == 'firing'",
                                "include_suppressed": True,
                            },
                        ]
                    },
                )
                if response.status_code != 200:
                    http_failure("warmup_counts", response)
                    raise RuntimeError("Authorized count warmup failed")
            go = asyncio.Event()
            ready = asyncio.Queue()
            startup_slots = asyncio.Semaphore(min(10, args.users))
            finish = None
            stats["active_sse"] = 0
            stats["startup_read_ms"] = []
            stats["startup_pit_closed"] = 0
            stats["startup_sse_messages"] = 0
            stats["startup_ready_users"] = 0
            context = ssl.create_default_context(cafile=str(tls / "localhost.pem"))

            async def reader(user):
                tenant = tenants[user % len(tenants)]
                handle, csrf = sessions[user]
                headers = {
                    "Origin": origin,
                    "X-CSRF-Token": csrf,
                    "Cookie": "__Host-appchi_session=" + handle,
                }
                stream, reported, connected = None, False, False
                stream_ready = asyncio.Event()
                try:
                    async with httpx.AsyncClient(
                        base_url=origin, verify=context, headers=headers, timeout=15
                    ) as client:

                        async def events():
                            nonlocal connected
                            try:
                                async with client.stream(
                                    "GET", f"/api/tenants/{tenant}/events", timeout=None
                                ) as response:
                                    if response.status_code != 200:
                                        await response.aread()
                                        http_failure("sse", response)
                                        return
                                    connected = True
                                    stats["active_sse"] += 1
                                    stats["sse_connections"] += 1
                                    stream_ready.set()
                                    async for line in response.aiter_lines():
                                        if line.startswith("data:"):
                                            stats[
                                                "sse_messages"
                                                if go.is_set()
                                                else "startup_sse_messages"
                                            ] += 1
                                    if finish is None or monotonic() < finish:
                                        stats["errors"]["sse_closed_early"] += 1
                            except asyncio.CancelledError:
                                raise
                            except Exception as error:
                                stats["errors"]["sse_" + type(error).__name__] += 1
                            finally:
                                if connected:
                                    stats["active_sse"] -= 1
                                    connected = False
                                    if go.is_set() and monotonic() < finish:
                                        result["minimum_active_sse"] = min(
                                            result["minimum_active_sse"], stats["active_sse"]
                                        )
                                stream_ready.set()

                        async def session_check(label):
                            responses = (
                                await client.get("/auth/session"),
                                await client.post("/auth/activity", json={}),
                            )
                            for response in responses:
                                if response.status_code != 200:
                                    http_failure(label, response)
                            return all(response.status_code == 200 for response in responses)

                        async def read_once(startup=False):
                            label = "startup_read" if startup else "read"
                            timings = stats["startup_read_ms" if startup else "read_ms"]

                            async def endpoint_read(kind, method, path, **kwargs):
                                if not args.response_timings:
                                    began = monotonic()
                                    response = await client.request(method, path, **kwargs)
                                    timings.append((monotonic() - began) * 1000)
                                    return response
                                response, measured = await diagnostic_read(
                                    client, method, path, **kwargs
                                )
                                timings.append(measured["client_complete_ms"])
                                endpoint = stats["read_diagnostics"].setdefault(
                                    label + ":" + kind,
                                    {
                                        "statuses": Counter(),
                                        "errors": Counter(),
                                        "timings": {},
                                        "missing_asgi_header": 0,
                                    },
                                )
                                endpoint["statuses"][str(response.status_code)] += 1
                                endpoint["missing_asgi_header"] += int(
                                    measured["asgi_response_start_ms"] is None
                                )
                                for metric, value in measured.items():
                                    if value is not None:
                                        endpoint["timings"].setdefault(metric, []).append(value)
                                if response.status_code != 200:
                                    try:
                                        code = (
                                            response.json().get("error", {}).get("code", "unknown")
                                        )
                                    except (ValueError, AttributeError):
                                        code = "invalid_response"
                                    if (
                                        not isinstance(code, str)
                                        or len(code) > 100
                                        or not all(
                                            character.isascii()
                                            and (character.isalnum() or character == "_")
                                            for character in code
                                        )
                                    ):
                                        code = "invalid_error_code"
                                    endpoint["errors"][code] += 1
                                return response

                            feed = await endpoint_read(
                                "feed", "GET", f"/api/tenants/{tenant}/alerts", params={"limit": 50}
                            )
                            counts = await endpoint_read(
                                "count",
                                "POST",
                                f"/api/tenants/{tenant}/alerts/counts",
                                json={
                                    "queries": [
                                        {"id": "all", "cel": "true", "include_suppressed": True},
                                        {
                                            "id": "active",
                                            "cel": "status == 'firing'",
                                            "include_suppressed": True,
                                        },
                                    ]
                                },
                            )
                            if not startup:
                                stats["read_requests"] += 2
                            for response in (feed, counts):
                                if response.status_code != 200:
                                    http_failure(label, response)
                            valid = feed.status_code == counts.status_code == 200
                            if feed.status_code == 200:
                                token = feed.json().get("meta", {}).get("close_token")
                                if token:
                                    closed = await client.post(
                                        f"/api/tenants/{tenant}/alerts/snapshot/close",
                                        json={"token": token},
                                    )
                                    if closed.status_code not in (200, 204):
                                        http_failure(
                                            "startup_close" if startup else "close", closed
                                        )
                                        valid = False
                                    else:
                                        stats[
                                            "startup_pit_closed" if startup else "pit_closed"
                                        ] += 1
                            return valid

                        async with startup_slots:
                            stream = asyncio.create_task(events())
                            valid = await session_check("startup_session")
                            valid = await read_once(startup=True) and valid
                            await asyncio.wait_for(stream_ready.wait(), timeout=15)
                            stats["startup_ready_users"] += int(valid and connected)
                            await ready.put(valid and connected)
                            reported = True
                        await go.wait()
                        next_session = monotonic() + 30
                        while monotonic() < finish:
                            try:
                                if monotonic() >= next_session:
                                    await session_check("session")
                                    next_session = monotonic() + 30
                                await read_once()
                            except Exception as error:
                                stats["errors"]["reader_" + type(error).__name__] += 1
                            await asyncio.sleep(args.read_pause)
                        stream.cancel()
                        with suppress(asyncio.CancelledError):
                            await stream
                        stream = None
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    stats["errors"]["startup_" + type(error).__name__] += 1
                finally:
                    if not reported:
                        await ready.put(False)
                    if stream:
                        stream.cancel()
                        with suppress(asyncio.CancelledError):
                            await stream

            async def arrivals():
                begin = monotonic()
                tasks = set()
                for ordinal in range(round(args.seconds * args.rate)):
                    due = begin + ordinal / args.rate
                    await asyncio.sleep(max(0, due - monotonic()))
                    stats["generator_late_ms"].append(max(0, monotonic() - due) * 1000)
                    if len(tasks) >= 1000:
                        stats["errors"]["generator_concurrency_exhausted"] += 1
                        continue
                    task = asyncio.create_task(send(args.seed + ordinal, True))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                if tasks:
                    await asyncio.gather(*tasks)
                result["arrivals_complete_seconds"] = round(monotonic() - begin, 3)

                def applied_now():
                    with psycopg.connect(db_url) as conn:
                        return conn.execute(
                            "SELECT count(*) FROM kafka_source_results WHERE outcome='applied'"
                        ).fetchone()[0]

                result["applied_at_arrivals_complete"] = await asyncio.to_thread(applied_now)

            startup_started = monotonic()
            readers = [asyncio.create_task(reader(user)) for user in range(args.users)]
            try:
                async with asyncio.timeout(90):
                    statuses = [await ready.get() for _ in range(args.users)]
                result["startup_seconds"] = round(monotonic() - startup_started, 3)
                result["startup_read_p99_ms"] = percentile(stats["startup_read_ms"], 0.99)
                result["startup_ready_users"] = sum(statuses)
                result["startup_pit_closed"] = stats["startup_pit_closed"]
                if not all(statuses) or stats["active_sse"] != args.users:
                    raise RuntimeError("Not all users reached the active ready barrier")
                result["minimum_active_sse"] = stats["active_sse"]
                result["measured_from"] = datetime.now(UTC)
                result["host_memory_at_measurement"] = host_memory()
                measurement_temporary = measurement_file.with_suffix(".new")
                measurement_temporary.write_text(result["measured_from"].isoformat())
                measurement_temporary.replace(measurement_file)
                result["stage"] = "measured_workload"
                print(
                    f"All {args.users} users ready; measuring {args.seconds}s at {args.rate:.3f} alerts/s",
                    flush=True,
                )
                observer = threading.Thread(target=observe_search, daemon=True)
                observer.start()
                threads.append(observer)
                began = monotonic()
                finish = began + args.seconds
                go.set()
                await asyncio.gather(arrivals(), *readers)
            except TimeoutError:
                stats["errors"]["startup_barrier_timeout"] += 1
                raise
            finally:
                result.setdefault("startup_seconds", round(monotonic() - startup_started, 3))
                result.setdefault("startup_ready_users", stats["startup_ready_users"])
                result.setdefault("startup_read_p99_ms", percentile(stats["startup_read_ms"], 0.99))
                result.setdefault("startup_pit_closed", stats["startup_pit_closed"])
                for task in readers:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
            result["combined_elapsed_seconds"] = round(monotonic() - began, 3)
            with psycopg.connect(db_url) as conn:
                result["applied_at_combined_end"] = conn.execute(
                    "SELECT count(*) FROM kafka_source_results WHERE outcome='applied'"
                ).fetchone()[0]

        def wait_applied(expected, seconds):
            deadline = monotonic() + seconds
            next_report = monotonic() + 30
            applied = 0
            while monotonic() < deadline:
                if stop.is_set():
                    raise RuntimeError("Fixture worker stopped before processing drain completed")
                try:
                    with psycopg.connect(db_url) as conn:
                        applied = conn.execute(
                            "SELECT count(*) FROM kafka_source_results WHERE outcome='applied'"
                        ).fetchone()[0]
                    result["last_observed_applied"] = applied
                except psycopg.OperationalError as error:
                    stats["errors"]["authority_probe_" + type(error).__name__] += 1
                    sleep(1)
                    continue
                if applied >= expected:
                    return applied
                if monotonic() >= next_report:
                    print(
                        f"Authority drain ({result.get('stage')}): {applied}/{expected}", flush=True
                    )
                    next_report = monotonic() + 30
                sleep(1)
            raise RuntimeError(
                f"Processing drain exceeded {seconds}s; applied={applied}, expected={expected}"
            )

        def wait_searchable(expected, seconds):
            deadline = monotonic() + seconds
            next_report = monotonic() + 30
            visible = 0
            while monotonic() < deadline:
                if stop.is_set():
                    raise RuntimeError("Fixture worker stopped before search drain completed")
                try:
                    visible = es.count(index=index, query={"term": {"deleted": False}})["count"]
                    result["last_observed_searchable"] = visible
                except (ESConnectionError, ESConnectionTimeout) as error:
                    stats["errors"]["search_probe_" + type(error).__name__] += 1
                    sleep(1)
                    continue
                if visible >= expected:
                    return visible
                if monotonic() >= next_report:
                    print(f"Search drain ({result.get('stage')}): {visible}/{expected}", flush=True)
                    next_report = monotonic() + 30
                sleep(1)
            raise RuntimeError(
                f"Search drain exceeded {seconds}s; visible={visible}, expected={expected}"
            )

        def observe_search():
            # An explicitly sampled upper-bound observation (one-second probe
            # cadence), separate from actual browser render SLIs. Only confirmed
            # measured arrivals enter the denominator; no realtime GET shortcut.
            while not stop.is_set():
                with sample_lock:
                    pending_samples = {
                        key: value
                        for key, value in stats["search_sample_arrivals"].items()
                        if key not in stats["search_visibility_ms"]
                    }
                if pending_samples:
                    try:
                        response = es.search(
                            index=index,
                            size=len(pending_samples),
                            query={"terms": {"node_name": list(pending_samples)}},
                            source_includes=["node_name"],
                            track_total_hits=False,
                            allow_partial_search_results=False,
                        )
                        if response.get("timed_out") or response.get("_shards", {}).get("failed"):
                            stats["errors"]["visibility_sample_incomplete"] += 1
                        else:
                            observed_at = datetime.now(UTC)
                            with sample_lock:
                                for hit in response["hits"]["hits"]:
                                    node = hit["_source"]["node_name"]
                                    if node in pending_samples:
                                        stats["search_visibility_ms"].setdefault(
                                            node,
                                            (observed_at - pending_samples[node]).total_seconds()
                                            * 1000,
                                        )
                    except Exception as error:
                        stats["errors"]["visibility_sample_" + type(error).__name__] += 1
                stop.wait(1)

        async def verify_final_reads():
            context = ssl.create_default_context(cafile=str(tls / "localhost.pem"))
            handle, csrf = sessions[0]
            checked = 0
            async with httpx.AsyncClient(
                base_url=origin,
                verify=context,
                timeout=15,
                headers={
                    "Origin": origin,
                    "X-CSRF-Token": csrf,
                    "Cookie": "__Host-appchi_session=" + handle,
                },
            ) as client:
                for tenant in tenants:
                    response = await client.post(
                        f"/api/tenants/{tenant}/alerts/counts",
                        json={
                            "queries": [
                                {"id": "all", "cel": "true", "include_suppressed": True},
                                {
                                    "id": "active",
                                    "cel": "status == 'firing'",
                                    "include_suppressed": True,
                                },
                            ]
                        },
                    )
                    expected_count = stats["accepted_by_tenant"][str(tenant)]
                    if response.status_code != 200 or response.json().get("data", {}).get(
                        "counts"
                    ) != {"all": str(expected_count), "active": str(expected_count)}:
                        stats["errors"]["final_exact_counts_mismatch"] += 1
                        if response.status_code != 200:
                            http_failure("final_counts", response)
                    else:
                        checked += 1
            return checked

        def run_pipeline():
            from scalability_pipeline import PipelinePhase, environment, workload

            result["stage"] = "pipeline"
            result["scope"] = (
                "Fixed drained workloads through KafkaAdmission and production source, "
                "correlation and indexer worker processes; no API, OIDC or production qualification"
            )
            result["workload"] = workload(args)
            result["environment"] = environment(db_url, es, ROOT)

            def read_reports():
                reports = {}
                for process, path in worker_processes:
                    for _ in range(20):
                        try:
                            reports[process.pid] = json.loads(path.read_text())
                            break
                        except (OSError, ValueError):
                            sleep(0.05)
                return reports

            phase = PipelinePhase(
                args=args,
                run=run,
                admission=admission,
                tenants=tenants,
                operators=operators,
                global_id=global_id,
                conninfo=db_url,
                database=name,
                es=es,
                index=index,
                read_reports=read_reports,
                stop=stop,
                errors=stats["errors"],
                canonical=canonical,
                format_native_time=format_native_time,
                gateway=GatewayIdentity,
            )
            result["pipeline"] = phase.execute()
            collect_worker_reports()
            with psycopg.connect(db_url) as conn:
                result["pipeline_faults"] = dict(
                    conn.execute(
                        "SELECT consumer||':'||reason,count(*) FROM kafka_pipeline_faults GROUP BY consumer,reason"
                    ).fetchall()
                )
            # Retried inputs are measurements (see each scenario's faults); exact
            # drained counts, not a fault-free run, establish correctness.
            result["correctness_passed"] = (
                result["pipeline"]["correctness_passed"] and not stats["errors"]
            )
            result["faults_free"] = not result["pipeline_faults"]
            result["passed"] = result["correctness_passed"]

        if args.phase == "pipeline":
            run_pipeline()
        else:
            asyncio.run(run_workload())
            result["host_memory_after_measurement"] = host_memory()
            result["stage"] = "drain_and_verify"
            print(
                "Measured arrivals complete; draining and verifying exact authority/search counts",
                flush=True,
            )
            expected = args.seed + stats["accepted"]
            try:
                result["applied"] = wait_applied(expected, args.drain_seconds)
            except RuntimeError:
                stats["errors"]["processing_drain_timeout"] += 1
            deadline = monotonic() + args.drain_seconds
            while monotonic() < deadline:
                visible = es.count(index=index, query={"term": {"deleted": False}})["count"]
                if visible >= expected:
                    break
                sleep(1)
            result["searchable"] = visible
            watermark_deadline = monotonic() + args.drain_seconds
            while monotonic() < watermark_deadline:
                with psycopg.connect(db_url) as conn:
                    lagging = conn.execute("""SELECT count(*) FROM tenant_work_state w
                        JOIN tenant_view_state v USING(tenant_id)
                        WHERE v.searchable_seq<w.projection_seq""").fetchone()[0]
                if not lagging:
                    break
                sleep(1)
            else:
                stats["errors"]["watermark_drain_timeout"] += 1
            if args.features:
                deadline = monotonic() + args.drain_seconds
                while monotonic() < deadline:
                    with psycopg.connect(db_url) as conn:
                        consumed = conn.execute("""SELECT count(*) FROM kafka_source_results r
                            JOIN kafka_correlation_events e ON e.event_id=r.changed_event_id
                            WHERE r.outcome='applied'""").fetchone()[0]
                        lagging = conn.execute("""SELECT count(*) FROM tenant_work_state w
                            JOIN tenant_view_state v USING(tenant_id)
                            WHERE v.searchable_seq<w.projection_seq""").fetchone()[0]
                    if consumed == expected and not lagging:
                        break
                    sleep(1)
                else:
                    stats["errors"]["feature_drain_timeout"] += 1
                with psycopg.connect(db_url) as conn:
                    result["features"] = {
                        "source_events_consumed": consumed,
                        "suppressed": conn.execute(
                            "SELECT count(*) FROM alerts WHERE suppressed"
                        ).fetchone()[0],
                        "expected_suppressed": stats["expected_suppressed"],
                        "incidents": conn.execute("SELECT count(*) FROM incidents").fetchone()[0],
                        "active_owners": conn.execute(
                            "SELECT count(*) FROM alert_active_ownership"
                        ).fetchone()[0],
                    }
                if (
                    result["features"]["suppressed"] != stats["expected_suppressed"]
                    or not result["features"]["incidents"]
                    or not result["features"]["active_owners"]
                ):
                    stats["errors"]["feature_effects_mismatch"] += 1
            result["final_tenants_exact_counts"] = asyncio.run(verify_final_reads())
            observation_deadline = monotonic() + 3
            while monotonic() < observation_deadline:
                with sample_lock:
                    if len(stats["search_visibility_ms"]) == len(stats["search_sample_arrivals"]):
                        break
                sleep(0.1)
            with psycopg.connect(db_url) as conn:
                result["authority"] = dict(
                    zip(
                        ("receipts", "applied", "rejected"),
                        conn.execute(
                            "SELECT count(*),count(*) FILTER(WHERE outcome='applied'),count(*) FILTER(WHERE outcome='rejected') FROM kafka_source_results"
                        ).fetchone(),
                    )
                )
                result["processing_p99_ms"] = conn.execute(
                    "SELECT percentile_cont(0.99) WITHIN GROUP(ORDER BY extract(epoch FROM recorded_at-received_at)*1000) FROM kafka_source_results WHERE received_at>=%s",
                    (result["measured_from"],),
                ).fetchone()[0]
                result["pipeline_faults"] = dict(
                    conn.execute(
                        "SELECT consumer||':'||reason,count(*) FROM kafka_pipeline_faults GROUP BY consumer,reason"
                    ).fetchall()
                )
                result["watermark_lag_tenants"] = conn.execute(
                    "SELECT count(*) FROM tenant_work_state w JOIN tenant_view_state v USING(tenant_id) WHERE v.searchable_seq<w.projection_seq"
                ).fetchone()[0]
            with sample_lock:
                result["search_visibility_sample_p99_ms"] = percentile(
                    list(stats["search_visibility_ms"].values()), 0.99
                )
                result["search_visibility_sampled"] = len(stats["search_sample_arrivals"])
                result["search_visibility_observed"] = len(stats["search_visibility_ms"])
            collect_worker_reports()
            result.update(
                {
                    "accepted": stats["accepted"],
                    "offered": stats["offered"],
                    "read_requests": stats["read_requests"],
                    "admission_p99_ms": percentile(stats["arrival_ms"], 0.99),
                    "read_p99_ms": percentile(stats["read_ms"], 0.99),
                    "generator_late_p99_ms": percentile(stats["generator_late_ms"], 0.99),
                    "errors": dict(stats["errors"]),
                    "worker_turns": dict(stats["worker_turns"]),
                    "sse_connections": stats["sse_connections"],
                    "sse_messages": stats["sse_messages"],
                    "pit_closed": stats["pit_closed"],
                }
            )
            with phase_lock:
                result["source_phases_ms"] = {
                    label: {
                        "count": len(values),
                        "p50": percentile(values, 0.5),
                        "p99": percentile(values, 0.99),
                        "total_ms": round(sum(values), 3),
                    }
                    for label, values in stats["source_phases_ms"].items()
                }
            result["correctness_passed"] = (
                not stats["errors"]
                and visible == expected
                and result.get("applied") == expected
                and stats["accepted"] == round(args.rate * args.seconds)
                and not result["pipeline_faults"]
                and not result["watermark_lag_tenants"]
                and result["final_tenants_exact_counts"] == args.tenants
                and result["search_visibility_observed"] == result["search_visibility_sampled"]
            )
            result["latency_passed"] = (
                result["admission_p99_ms"] is not None
                and result["admission_p99_ms"] < 1000
                and result["read_p99_ms"] is not None
                and result["read_p99_ms"] < 2000
                and result["processing_p99_ms"] is not None
                and result["processing_p99_ms"] < 5000
                and stats["sse_connections"] == args.users
                and result["minimum_active_sse"] == args.users
                and result["generator_late_p99_ms"] < 1000
                and result["search_visibility_sample_p99_ms"] is not None
                and result["search_visibility_sample_p99_ms"] < 5000
            )
            result["passed"] = result["correctness_passed"] and result["latency_passed"]
    except Exception as error:
        result.update(
            {"passed": False, "failure_type": type(error).__name__, "errors": dict(stats["errors"])}
        )
        raise
    finally:
        result["read_diagnostics"] = {
            endpoint: {
                **{key: value for key, value in data.items() if key != "timings"},
                "timings": {
                    metric: {
                        "count": len(values),
                        "p50": percentile(values, 0.5),
                        "p99": percentile(values, 0.99),
                    }
                    for metric, values in data["timings"].items()
                },
            }
            for endpoint, data in stats["read_diagnostics"].items()
        }
        # Preserve measured evidence before bounded worker/resource cleanup, so
        # interruption during teardown cannot erase the completed workload.
        result.setdefault("errors", dict(stats["errors"]))
        result.setdefault("accepted", stats["accepted"])
        result.setdefault("offered", stats["offered"])
        result["seed_offered"] = stats["seed_offered"]
        result["seed_accepted"] = stats["seed_accepted"]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, default=str) + "\n")
        print("Workload finished; cleaning isolated fixtures", flush=True)
        stop.set()
        worker_stop.touch()
        worker_deadline = monotonic() + 20
        for process, _ in worker_processes:
            try:
                process.wait(timeout=max(0.1, worker_deadline - monotonic()))
            except subprocess.TimeoutExpired:
                stats["errors"]["fixture_worker_forced_stop"] += 1
                process.terminate()
                process.wait(timeout=5)
        collect_worker_reports()
        result["worker_turns"] = dict(stats["worker_turns"])
        if stats["errors"]:
            result["errors"] = dict(stats["errors"])
            result["correctness_passed"] = result["passed"] = False
        if server:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=20)
        kafka_source.apply_record = original_apply_record
        cel_compiler._compile_checked = original_compile
        psycopg.Connection.execute = original_execute
        with phase_lock:
            result["source_sql"] = sql_phases
            result["source_phases_ms"] = {
                label: {
                    "count": len(values),
                    "p50": percentile(values, 0.5),
                    "p99": percentile(values, 0.99),
                    "total_ms": round(sum(values), 3),
                }
                for label, values in stats["source_phases_ms"].items()
            }
        if publisher:
            publisher.close()
        for db in databases:
            db.close()
        try:
            es.options(request_timeout=30).indices.delete(index=index, ignore_unavailable=True)
            for future in kafka.delete_topics(topics).values():
                try:
                    future.result(20)
                except KafkaException as error:
                    if not error.args or error.args[0].code() != KafkaError.UNKNOWN_TOPIC_OR_PART:
                        raise
        finally:
            es.close()
            if created_db:
                with psycopg.connect(admin, autocommit=True) as conn:
                    conn.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            sql.Identifier(name)
                        )
                    )
                    for role in roles:
                        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            clock.unlink(missing_ok=True)
            clock.with_suffix(".new").unlink(missing_ok=True)
            profile_file.unlink(missing_ok=True)
            worker_stop.unlink(missing_ok=True)
            measurement_file.unlink(missing_ok=True)
            measurement_file.with_suffix(".new").unlink(missing_ok=True)
            for _, metrics in worker_processes:
                metrics.unlink(missing_ok=True)
                metrics.with_suffix(".new").unlink(missing_ok=True)
            for health in worker_health_files:
                health.unlink(missing_ok=True)
                health.with_suffix(".new").unlink(missing_ok=True)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, default=str) + "\n")
            print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
