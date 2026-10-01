"""Activate the bounded, unqualified correlation preview on this local stack only."""

import argparse
import json
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / ".local/runtime/correlation-preview-profile.json"
EVIDENCE = ROOT / ".local/runtime/correlation-preview-evidence.json"
SOURCE_FILES = (
    "appchi-domain/src/appchi/pipeline_runtime.py",
    "appchi-processor/src/appchi/processor/main.py",
    "appchi-correlation/src/appchi/correlation/consumer.py",
    "appchi-correlation/src/appchi/correlation/main.py",
    "appchi-indexer/src/appchi/search/main.py",
    "appchi-processor/src/appchi/kafka_source.py",
    "appchi-domain/src/appchi/kafka_changed.py",
    "appchi-processor/src/appchi/kafka_cutover.py",
    "appchi-domain/src/appchi/maintenance/temporal.py",
    "appchi-domain/src/appchi/maintenance/processing.py",
    "appchi-domain/src/appchi/alerts/repository.py",
    "appchi-domain/src/appchi/correlation/repository.py",
    "appchi-domain/src/appchi/correlation/runtime.py",
    "appchi-domain/src/appchi/correlation/evidence.py",
    "appchi-api/src/appchi/correlation/routes.py",
    "appchi-domain/src/appchi/maintenance/runtime.py",
    "appchi-delivery/src/appchi/worker.py",
    "appchi-delivery/scripts/local_correlation.py",
    "appchi-delivery/scripts/local_identity.py",
    "appchi-delivery/scripts/local-runtime.ps1",
    "appchi-delivery/scripts/local_runtime.py",
)


def source_digest():
    digest = sha256()
    for name in SOURCE_FILES:
        digest.update(name.encode())
        digest.update((ROOT.parent / name).read_bytes())
    return digest.hexdigest()


def verified_profile(deployment_epoch):
    from appchi.correlation.repository import limits

    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    report = ROOT / ".local/runtime/correlation-preview-tests.xml"
    counts = evidence.get("test_counts", {})
    if (
        profile.get("qualified") is not False
        or profile.get("scope") != "local-development"
        or profile.get("correlation_preview") is not True
        or profile.get("deployment_epoch") != deployment_epoch
        or profile.get("code_sha256") != source_digest()
        or profile.get("evidence_sha256") != sha256(EVIDENCE.read_bytes()).hexdigest()
        or evidence.get("scope") != "local-development-preview"
        or evidence.get("qualified") is not False
        or evidence.get("code_sha256") != profile.get("code_sha256")
        or counts.get("tests", 0) < 31
        or any(counts.get(key) != 0 for key in ("failures", "errors", "skipped"))
        or evidence.get("test_report_sha256") != sha256(report.read_bytes()).hexdigest()
    ):
        raise RuntimeError("Local correlation preview proof is stale or invalid")
    limits(profile, allow_local_preview=True)
    return profile


def activate():
    credentials = json.loads(
        (ROOT / ".local/keycloak/credentials.json").read_text(encoding="utf-8")
    )
    profile = verified_profile(credentials["deployment_epoch"])
    digest = sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    reason = "local-preview:" + digest
    password = (ROOT / ".local/postgres-password.txt").read_text().strip()
    dsn = (
        "postgresql://appchi_migrator:"
        + quote(password, safe="")
        + "@127.0.0.1:55433/appchi_local?connect_timeout=5"
    )
    with psycopg.connect(dsn) as conn:
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("LOCK TABLE tenants IN SHARE MODE")
        tenants = [row[0] for row in conn.execute("SELECT id FROM tenants ORDER BY id")]
        states = conn.execute(
            "SELECT tenant_id,blocked,last_clock FROM tenant_work_state ORDER BY tenant_id FOR UPDATE"
        ).fetchall()
        now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        if {row[0] for row in states} != set(tenants) or any(
            row[1] or row[2] and now < row[2] for row in states
        ):
            raise RuntimeError("Local tenant processing or clock proof is incomplete")
        maintenance = conn.execute(
            "SELECT ready FROM capability_gates WHERE capability='maintenance' FOR SHARE"
        ).fetchone()
        if not maintenance or maintenance[0] or any(
            conn.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone()
            for table in (
                "maintenance_windows",
                "maintenance_segments",
                "maintenance_effect_outbox",
            )
        ):
            raise RuntimeError("Local maintenance effects require full correlation qualification")
        gate = conn.execute(
            "SELECT ready,reason FROM capability_gates WHERE capability='correlation' FOR UPDATE"
        ).fetchone()
        replacement = bool(
            gate
            and gate[0]
            and gate[1].startswith("local-preview:")
            and not conn.execute("SELECT 1 FROM correlation_rules LIMIT 1").fetchone()
        )
        if not gate or gate[1] not in (
            "correlation_consumer_and_capacity_qualification_required",
            reason,
        ) and not replacement:
            raise RuntimeError("Correlation gate has another active or recovery binding")
        if gate[0] and gate[1] == reason:
            result = "already_active"
        else:
            conn.execute(
                "UPDATE capability_gates SET ready=true,reason=%s WHERE capability='correlation'",
                (reason,),
            )
            conn.execute(
                """INSERT INTO audit_events(tenant_id,actor_id,actor_label,kind,evidence)
                VALUES(NULL,NULL,'local-runtime','correlation-local-preview-activated',%s)""",
                (
                    Jsonb(
                        {
                            "profile_id": profile["id"],
                            "profile_digest": digest,
                            "evidence_sha256": profile["evidence_sha256"],
                            "tenant_count": str(len(tenants)),
                            "scope": "local-development",
                        }
                    ),
                ),
            )
            result = "rebound" if replacement else "activated"
    print(json.dumps({"state": result, "scope": "local-development"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("activate",))
    args = parser.parse_args()
    if args.action == "activate":
        activate()
