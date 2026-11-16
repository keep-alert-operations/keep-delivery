from keep.correlation.consumer import correlate_record
from keep.search.consumer import index_record
import argparse
import logging
import signal
import threading

from keep.config import Settings
from keep.db import Database
from keep.alerts.processor import process_one, record_failure
from keep.shared.errors import unavailable
from keep.service_clients import elasticsearch_options, kafka_options

log = logging.getLogger("keep.worker")


def source_turn(db, profile=None):
    with db.transaction() as conn:
        tenants = [
            row["tenant_id"]
            for row in conn.execute(
                "SELECT tenant_id FROM tenant_work_state WHERE NOT blocked AND next_attempt_at<=clock_timestamp() AND EXISTS(SELECT 1 FROM ingest_items i WHERE i.tenant_id=tenant_work_state.tenant_id AND disposition='pending') ORDER BY next_attempt_at,tenant_id LIMIT 100"
            )
        ]
    failed = False
    for tenant in tenants:
        attempt = {}
        try:
            process_one(db, tenant, attempt, profile=profile)
        except Exception:
            failed = True
            if "item_id" in attempt:
                record_failure(db, tenant, attempt["item_id"])
            log.warning("source_unit_failed tenant_id=%s", tenant)
    if failed:
        raise unavailable("source_unit_failed")


def relay_turn(db, settings):
    from confluent_kafka import Producer
    from keep.pipeline import relay_once

    if not settings.kafka_bootstrap:
        raise unavailable("kafka_configuration_required")
    producer = Producer(
        {
            **kafka_options(settings),
            "enable.idempotence": True,
            "acks": "all",
            "max.in.flight.requests.per.connection": 5,
            "delivery.timeout.ms": 3000,
        }
    )
    return relay_once(db, settings, producer)


def hint_client(settings, role):
    from confluent_kafka import Consumer

    if not settings.kafka_bootstrap:
        raise unavailable("kafka_configuration_required")
    consumer = {
        "source-hints": "processor",
        "indexer-hints": "projection",
        "correlation-hints": "correlation-wakeup",
    }[role]
    stream = "received" if consumer == "processor" else "changed"
    client = Consumer(
        {
            **kafka_options(settings),
            "group.id": f"keep.{settings.environment}.{consumer}",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
        }
    )
    client.subscribe([f"keep.{settings.environment}.alerts.{stream}"])
    return consumer, client


def maintenance_turn(db, profile):
    from keep.maintenance.runtime import worker_turn

    with db.transaction() as conn:
        tenants = [row["id"] for row in conn.execute("SELECT id FROM tenants ORDER BY id")]
    for tenant in tenants:
        worker_turn(db, tenant, profile)


def correlation_turn(db, profile):
    from keep.correlation.runtime import worker_turn

    with db.transaction() as conn:
        rows = conn.execute("""SELECT c.tenant_id FROM correlation_consumer_state c JOIN tenant_work_state w USING(tenant_id)
            WHERE c.pending OR w.change_order>c.change_order ORDER BY c.updated_at,c.tenant_id LIMIT 100""").fetchall()
    for row in rows:
        worker_turn(db, row["tenant_id"], profile)


def projection_turn(db, settings):
    from keep.search.control import writer_lease

    with writer_lease(db, settings) as physical_index:
        _project(db, settings, physical_index)


def _project(db, settings, physical_index):
    from keep.search.control import check_writer
    from elasticsearch import Elasticsearch
    from keep.search import Projection
    from keep.search.writer import ProjectionWriter

    with db.transaction() as conn:
        gate = conn.execute(
            "SELECT ready FROM capability_gates WHERE capability='search'"
        ).fetchone()
        if not gate["ready"] or not settings.elasticsearch_url:
            raise unavailable("search_not_ready")
        rows = conn.execute(
            "SELECT p.*,v.index_epoch FROM projection_work p JOIN tenant_view_state v USING(tenant_id) WHERE p.seq>v.searchable_seq AND (NOT p.visible OR p.visibility_epoch IS DISTINCT FROM v.index_epoch) ORDER BY p.tenant_id,p.seq LIMIT 100"
        ).fetchall()
    if not rows:
        return
    with Elasticsearch(settings.elasticsearch_url, **elasticsearch_options(settings)) as client:
        for row in rows:
            with db.transaction() as conn:
                retired = conn.execute(
                    "SELECT final_version FROM alert_identities WHERE tenant_id=%s AND id=%s AND retired_at IS NOT NULL",
                    (row["tenant_id"], row["alert_id"]),
                ).fetchone()
                if retired:
                    snapshot_row = conn.execute(
                        "SELECT * FROM projection_work WHERE tenant_id=%s AND alert_id=%s AND version=%s AND (snapshot->>'live'='false' OR snapshot->>'deleted'='true')",
                        (row["tenant_id"], row["alert_id"], retired["final_version"]),
                    ).fetchone()
                    if not snapshot_row:
                        raise unavailable("retirement_projection_missing")
                else:
                    snapshot_row = row
            item = Projection(
                str(row["tenant_id"]),
                snapshot_row["seq"],
                str(row["alert_id"]),
                snapshot_row["version"],
                snapshot_row["snapshot"],
            )

            def guard(candidate, document, epoch):
                with db.transaction() as conn:
                    check_writer(conn, physical_index, epoch)
                    live = conn.execute(
                        "SELECT v.index_epoch,i.retired_at FROM tenant_view_state v JOIN alert_identities i ON i.tenant_id=v.tenant_id WHERE v.tenant_id=%s AND i.id=%s",
                        (candidate.tenant_id, candidate.alert_id),
                    ).fetchone()
                    durable = conn.execute(
                        "SELECT snapshot,version FROM projection_work WHERE tenant_id=%s AND seq=%s",
                        (candidate.tenant_id, candidate.seq),
                    ).fetchone()
                    if (
                        not durable
                        or durable["version"] != candidate.version
                        or durable["snapshot"] != candidate.snapshot
                    ):
                        raise unavailable("projection_snapshot_mismatch")
                    if (
                        not live
                        or live["index_epoch"] != row["index_epoch"]
                        or (live["retired_at"] and candidate.snapshot.get("live", True))
                    ):
                        raise unavailable("projection_fenced")

            def covered_by_newer(candidate, document):
                from keep.search.projection import build_document

                with db.transaction() as conn:
                    newer = conn.execute(
                        "SELECT * FROM projection_work WHERE tenant_id=%s AND alert_id=%s AND version=%s",
                        (
                            candidate.tenant_id,
                            candidate.alert_id,
                            document.get("projection_version"),
                        ),
                    ).fetchone()
                    if not newer or newer["version"] <= candidate.version:
                        return False
                    return (
                        build_document(
                            Projection(
                                candidate.tenant_id,
                                newer["seq"],
                                candidate.alert_id,
                                newer["version"],
                                newer["snapshot"],
                            )
                        )
                        == document
                    )

            ProjectionWriter(
                client, physical_index, row["index_epoch"], max_document_bytes=1048576
            ).publish([item], guard=guard, covered_by_newer=covered_by_newer)
            with db.transaction() as conn:
                check_writer(conn, physical_index, row["index_epoch"])
                current = conn.execute(
                    "SELECT * FROM tenant_view_state WHERE tenant_id=%s FOR UPDATE",
                    (row["tenant_id"],),
                ).fetchone()
                if current["index_epoch"] != row["index_epoch"]:
                    raise unavailable("index_changed")
                conn.execute(
                    "UPDATE projection_work SET visible=true,visibility_epoch=%s WHERE tenant_id=%s AND seq=%s",
                    (row["index_epoch"], row["tenant_id"], row["seq"]),
                )
                hole = conn.execute(
                    "SELECT min(seq) AS seq FROM projection_work WHERE tenant_id=%s AND seq>%s AND (NOT visible OR visibility_epoch IS DISTINCT FROM %s)",
                    (row["tenant_id"], current["searchable_seq"], row["index_epoch"]),
                ).fetchone()["seq"]
                max_seq = conn.execute(
                    "SELECT max(seq) AS seq FROM projection_work WHERE tenant_id=%s",
                    (row["tenant_id"],),
                ).fetchone()["seq"]
                contiguous = hole - 1 if hole else max_seq
                conn.execute(
                    "UPDATE tenant_view_state SET searchable_seq=greatest(searchable_seq,%s),verified_at=clock_timestamp(),view_generation=view_generation+1 WHERE tenant_id=%s",
                    (contiguous, row["tenant_id"]),
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "role",
        choices=[
            "source",
            "relay",
            "indexer",
            "inactivity",
            "directory",
            "maintenance",
            "maintenance-warmup",
            "maintenance-proof",
            "correlation",
            "operators",
            "live",
            "assignment-cleanup",
            "retention",
            "incident-retention",
            "audit-retention",
            "protected-deletions",
            "command-retention",
            "configuration-retention",
            "correlation-retention",
            "maintenance-retention",
            "operator-retention",
            "visibility-capture",
            "source-hints",
            "indexer-hints",
            "correlation-hints",
        ],
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--tenant")
    args = parser.parse_args()
    if args.role == "live":
        parser.error("live hint publishing runs in the Product API; no separate worker")
    from keep.release_scope import DEFERRED_WORKERS

    # Explicit trusted publication remains available for bootstrap and manual refresh.
    if args.role in DEFERRED_WORKERS and not (args.role == "directory" and args.once):
        parser.error(f"{args.role} automation is deferred; see spec-worker-scope.md")
    if args.role in ("maintenance-warmup", "maintenance-proof"):
        from uuid import UUID

        if not args.tenant or str(UUID(args.tenant)) != args.tenant:
            parser.error("Maintenance proof/warmup requires --tenant with a canonical UUID")
    elif args.tenant:
        parser.error("--tenant is used only for maintenance proof/warmup roles")
    settings = Settings(role=args.role)
    from keep.worker_health import WorkerHealth

    health = WorkerHealth(settings.worker_health_path, args.role)
    health.publish()
    db = Database(
        settings.database_url.get_secret_value(),
        settings.pool_size,
        deployment_epoch=settings.deployment_epoch,
    )
    import json

    profile = (
        json.loads(settings.capacity_profile.read_text()) if settings.capacity_profile else None
    )
    from keep.visibility.runtime import capture_profile

    capture_profile(profile, deployment_epoch=settings.deployment_epoch)
    hints = None
    transport = None
    from keep.alert_notifications import configure

    configure(settings)
    cleanup_cursor = None
    warmup_cursor = None
    retirement_cursor = None
    retirement_progress_pending = False
    incident_retirement_cursor = None
    incident_progress_pending = False
    deletion_cursor = None
    command_cursor = None
    configuration_cursor = None
    correlation_retention_cursor = None
    maintenance_retention_cursor = None
    operator_retention_cursor = None
    evidence_store = None
    operator_executor = None
    stopping = False
    stopped = threading.Event()

    def stop(*_):
        nonlocal stopping
        stopping = True
        health.publish(draining=True)
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping:
            transport_progress = 0
            turn_failed = False
            try:
                from keep.clock_health import require_clock_health

                require_clock_health(settings)
                if args.role == "relay" or args.role.endswith("-hints"):
                    with db.transaction() as conn:
                        if conn.execute(
                            "SELECT enabled FROM kafka_pipeline_control WHERE singleton"
                        ).fetchone()["enabled"]:
                            raise unavailable("legacy_pipeline_fenced")
                if args.role == "source":
                    from keep.kafka_source import SourceConsumer

                    if transport is None:
                        transport = SourceConsumer(db, settings, profile)
                    transport_progress = transport.turn()
                elif args.role == "visibility-capture":
                    from keep.visibility.runtime import capture_turn

                    capture_turn(db)
                elif args.role == "relay":
                    relay_turn(db, settings)
                elif args.role == "indexer":
                    from keep.kafka_changed import ChangedConsumer
                    from keep.search.reconciliation import reconciliation_turn

                    reconciliation_turn(db, settings)
                    try:
                        if transport is None:
                            transport = ChangedConsumer(
                                db, settings, profile, "indexer", index_record
                            )
                        transport_progress = transport.turn()
                    except Exception:
                        if transport:
                            transport.close()
                            transport = None
                        log.warning("search_kafka_degraded_reconciliation_active")
                elif args.role.endswith("-hints"):
                    from keep.pipeline import consume_once

                    if hints is None:
                        hints = hint_client(settings, args.role)
                    consume_once(db, settings, hints[0], hints[1])
                elif args.role == "assignment-cleanup":
                    from keep.identity.cleanup import cleanup_assignments

                    page = cleanup_assignments(db, profile=profile, cursor=cleanup_cursor)
                    cleanup_cursor = page.cursor
                elif args.role == "configuration-retention":
                    from keep.configuration.cleanup import configuration_cleanup_turn

                    page = configuration_cleanup_turn(db, cursor=configuration_cursor)
                    configuration_cursor = page.cursor
                elif args.role == "correlation-retention":
                    from keep.correlation.cleanup import correlation_cleanup_turn

                    page = correlation_cleanup_turn(db, cursor=correlation_retention_cursor)
                    correlation_retention_cursor = page.cursor
                elif args.role == "maintenance-retention":
                    from keep.maintenance.cleanup import cleanup_turn

                    page = cleanup_turn(db, cursor=maintenance_retention_cursor)
                    maintenance_retention_cursor = page.cursor
                elif args.role == "operator-retention":
                    from keep.operators.cleanup import cleanup_turn

                    page = cleanup_turn(db, cursor=operator_retention_cursor)
                    operator_retention_cursor = page.cursor
                elif args.role in (
                    "retention",
                    "incident-retention",
                    "audit-retention",
                    "protected-deletions",
                    "command-retention",
                ):
                    from keep.recovery.runtime import configured_store, retirement_turn
                    from keep.identity.cleanup import purge_audit_batch

                    if evidence_store is None:
                        evidence_store = configured_store(settings)
                    if args.role == "command-retention":
                        from keep.recovery.command_retention import command_retention_turn

                        page = command_retention_turn(db, evidence_store, cursor=command_cursor)
                        command_cursor = page.cursor
                    elif args.role == "incident-retention":
                        from keep.recovery.runtime import incident_retirement_turn

                        page = incident_retirement_turn(
                            db, evidence_store, cursor=incident_retirement_cursor
                        )
                        incident_retirement_cursor = page.cursor
                        incident_progress_pending = page.progress_pending
                    elif args.role == "protected-deletions":
                        from keep.recovery.deletions import deletion_turn

                        page = deletion_turn(db, evidence_store, cursor=deletion_cursor)
                        deletion_cursor = page.cursor
                    elif args.role == "retention":
                        page = retirement_turn(db, evidence_store, cursor=retirement_cursor)
                        retirement_cursor = page.cursor
                        retirement_progress_pending = page.progress_pending
                    else:
                        purge_audit_batch(db, evidence_store)
                elif args.role == "operators":
                    from keep.operators.worker import run_one

                    if operator_executor is None and settings.operator_binding:
                        from keep.operators.executor import (
                            configured_executor,
                            PlatformCredentials,
                        )

                        tokens = (
                            settings.operator_grafana_token,
                            settings.operator_kong_token,
                            settings.operator_probe_token,
                        )
                        if not all(tokens):
                            raise unavailable("operator_management_credentials_required")
                        operator_executor = configured_executor(
                            settings.operator_binding,
                            db,
                            PlatformCredentials(*(token.get_secret_value() for token in tokens)),
                            settings=settings,
                        )
                    run_one(db, settings.cipher(), operator_executor)
                elif args.role == "correlation":
                    from keep.kafka_changed import ChangedConsumer

                    if transport is None:
                        transport = ChangedConsumer(
                            db, settings, profile, "correlation", correlate_record
                        )
                    transport_progress = transport.turn()
                elif args.role in ("maintenance-warmup", "maintenance-proof"):
                    from dataclasses import asdict
                    from keep.maintenance.warmup import warmup_page, warmup_status

                    if args.role == "maintenance-proof":
                        print(json.dumps(warmup_status(db, args.tenant)))
                    else:
                        page = warmup_page(db, args.tenant, profile, cursor=warmup_cursor)
                        warmup_cursor = page.cursor
                        print(json.dumps(asdict(page)))
                elif args.role == "maintenance":
                    maintenance_turn(db, profile)
                elif args.role == "inactivity":
                    from keep.inactivity import sweep

                    sweep(db, profile=profile)
                else:
                    from keep.identity.worker import bootstrap_from_environment, refresh

                    # REV-73: the same refresh as `python -m keep.identity.worker --once`:
                    # KEEP_ADAPI_URL/KEEP_ADAPI_CLIENT_ID, KEEP_SUPERADMIN_GROUP (a
                    # samAccountName) and KEEP_DIRECTORY_BOOTSTRAP_QUALIFIED=true.
                    summary = refresh(
                        db, settings, bootstrap_qualified=bootstrap_from_environment()
                    )
                    if not summary.ok:
                        raise unavailable("directory_publication_incomplete")
                health.publish(ready=True)
            except Exception:
                turn_failed = True
                if transport is not None:
                    try:
                        transport.close()
                    except Exception:
                        pass
                    transport = None
                health.publish(ready=False)
                log.warning("worker_capability_unavailable")
                if args.once:
                    raise SystemExit(1)
            if args.once or args.role == "maintenance-proof":
                break
            delay = 30 if args.role == "directory" else 1
            if args.role in ("source", "correlation", "indexer"):
                # The consumers already bound idle polling. Do not cap a busy
                # tenant at 100 records/s with a fixed per-record sleep.
                delay = 1 if turn_failed else 0 if transport_progress else 0.01
            if args.role == "inactivity":
                delay = 3600
            if (
                args.role == "retention"
                and retirement_cursor is None
                and not retirement_progress_pending
            ):
                delay = 3600
            elif args.role == "audit-retention":
                delay = 3600
            elif args.role == "command-retention" and command_cursor is None:
                delay = 3600
            elif args.role == "configuration-retention" and configuration_cursor is None:
                delay = 3600
            elif args.role == "correlation-retention" and correlation_retention_cursor is None:
                delay = 3600
            elif args.role == "maintenance-retention" and maintenance_retention_cursor is None:
                delay = 3600
            elif args.role == "operator-retention" and operator_retention_cursor is None:
                delay = 3600
            elif (
                args.role == "incident-retention"
                and incident_retirement_cursor is None
                and not incident_progress_pending
            ):
                delay = 3600
            health.wait(stopped, delay)
    finally:
        health.publish(draining=True)
        if hints:
            hints[1].close()
        if transport:
            transport.close()
        if evidence_store:
            evidence_store.close()
        if operator_executor:
            operator_executor.close()
        db.close()


if __name__ == "__main__":
    main()
