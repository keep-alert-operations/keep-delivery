"""Live local v2 authorization checks, initial-gate bootstrap and test-alert sender."""

import argparse
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import local_v2 as local
from psycopg.types.json import Jsonb


def demo_key(http, *, allow_prepared=False):
    demo = local.read(local.STATE / "demo.json")
    base = (
        local.ORIGIN
        + f"/api/tenants/{demo['tenant_id']}/operators/{demo['operator_id']}"
    )
    operator = local.checked(http.get(base))["data"]
    if operator["lifecycle"] == "ready":
        key = local.checked(
            http.post(
                base + "/keys/current/copy",
                json={
                    "command_id": str(uuid4()),
                    "expected_revision": operator["revision"],
                },
            )
        )["data"]["key"]
    elif (
        allow_prepared
        and operator.get("operation", {}).get("error_code") == "v2_activation_fenced"
    ):
        # The normal worker already verified every remote resource and serving-pool
        # identity, then refused activation. Provisioning alone may decrypt candidates.
        db = local.database()
        try:
            with db.transaction(demo["tenant_id"]) as conn:
                row = conn.execute(
                    "SELECT id,ciphertext FROM operator_credentials WHERE operator_id=%s AND state='prepared'",
                    (demo["operator_id"],),
                ).fetchone()
            if not row:
                raise RuntimeError("Verified local setup candidate required")
            key = (
                local.environment()
                .cipher()
                .open(
                    bytes(row["ciphertext"]),
                    f"operator-key:{demo['operator_id']}:{row['id']}:1",
                )
                .decode()
            )
        finally:
            db.close()
    else:
        raise RuntimeError("Local operator setup is not verified")
    return demo, key


def check_initial_gate(conn):
    from appchi.recovery.guard import RESTORE_LOCK

    conn.execute("SELECT pg_advisory_xact_lock_shared(%s)", (RESTORE_LOCK,))
    restore = conn.execute("SELECT * FROM restore_control WHERE singleton").fetchone()
    gate = conn.execute(
        "SELECT * FROM capability_gates WHERE capability='v2_ingress' FOR UPDATE"
    ).fetchone()
    if not restore or restore["fenced"] or restore["run_id"] is not None:
        raise RuntimeError("Local v2 setup cannot clear a restore fence")
    if not gate or (gate["ready"], gate["reason"]) not in {
        (False, "trusted_gateway_required"),
        (True, local.REASON),
    }:
        raise RuntimeError("Local v2 setup requires an initial local gate")
    return gate


def verify():
    local.environment()
    checks = []

    def expect(response, status, name):
        if response.status_code != status:
            raise RuntimeError(
                f"{name}: expected {status}, received {response.status_code}"
            )
        checks.append(name)

    db = local.database()
    try:
        with db.transaction() as conn:
            gate = check_initial_gate(conn)
        with local.client(app=True) as app, local.client() as http:
            local.login(app)
            demo, key = demo_key(app, allow_prepared=not gate["ready"])
            endpoint = local.GATEWAY + "/api/ingest/v2/alerts"
            forged = {
                "X-Consumer-Id": str(uuid4()),
                "X-Credential-Identifier": str(uuid4()),
            }
            expect(http.post(endpoint, json={}), 401, "missing_key_rejected")
            expect(
                http.post(endpoint, json={}, headers=forged),
                401,
                "forged_identity_rejected",
            )
            expect(
                http.post(endpoint, json={}, headers={"X-API-Key": "invalid"}),
                401,
                "invalid_key_rejected",
            )
            expect(
                http.post(
                    endpoint, json={}, headers=[("X-API-Key", key), ("X-API-Key", key)]
                ),
                401,
                "duplicate_native_key_rejected",
            )
            duplicate = http.post(
                local.GATEWAY + "/api/ingest/v2/grafana",
                json={},
                headers=[
                    ("Authorization", "Bearer " + key),
                    ("Authorization", "Bearer " + key),
                ],
            )
            # This Kong build rejects duplicate Authorization during HTTP parsing,
            # before plugins or Appchi's payload validator can run.
            expect(duplicate, 400, "duplicate_grafana_bearer_rejected")
            if "message" not in duplicate.json() or "error" in duplicate.json():
                raise RuntimeError("Duplicate Bearer headers reached the application")
            expect(
                http.post(endpoint, json={}, params={"X-API-Key": key}),
                401,
                "query_key_rejected",
            )
            expect(
                http.post(endpoint, json={"X-API-Key": key}), 401, "body_key_rejected"
            )
            expect(
                http.post(
                    endpoint, json={}, headers={"Authorization": "Bearer " + key}
                ),
                401,
                "native_bearer_rejected",
            )
            expect(
                http.post(
                    "http://127.0.0.1:58001/api/ingest/v2/alerts",
                    json={},
                    headers=forged,
                ),
                404,
                "public_listener_has_no_v2_route",
            )
            expect(
                app.post(local.ORIGIN + "/api/ingest/v2/alerts", json={}),
                404,
                "product_api_has_no_ingestion_route",
            )
            try:
                http.post(
                    "https://127.0.0.1:58444/api/ingest/v2/alerts",
                    json={},
                    headers=forged,
                )
            except httpx.TransportError:
                checks.append("private_listener_requires_client_certificate")
            else:
                raise RuntimeError(
                    "Private listener accepted a connection without a certificate"
                )
            response = http.post(
                endpoint, json={}, headers={"X-API-Key": key, **forged}
            )
            expect(
                response,
                422 if gate["ready"] else 503,
                "kong_mtls_identity_and_secret_stripping",
            )
            if (
                not gate["ready"]
                and response.json().get("error", {}).get("code")
                != "v2_ingest_not_ready"
            ):
                raise RuntimeError(
                    "The authenticated request did not reach the v2 readiness gate"
                )
            checks.append("normal_operator_saga_verified_remote_setup")
            expect(
                http.post(
                    local.GATEWAY + "/__local/operator-check",
                    headers={"X-API-Key": key},
                ),
                401,
                "readiness_probe_requires_worker_secret",
            )
            expect(
                http.post("https://localhost:58446/operator-check", json={}),
                401,
                "proof_service_requires_worker_secret",
            )
            binding = local.read(local.STATE / "binding.json")
            evidence = {
                "scope": "isolated-local-development",
                "production_qualified": False,
                "verified_at": datetime.now(UTC).isoformat(),
                "kong_version": binding["kong_version"],
                "grafana_version": binding["grafana_version"],
                "checks": checks,
                **demo,
            }
            with db.transaction() as conn:
                current = check_initial_gate(conn)
                if not current["ready"]:
                    conn.execute(
                        "UPDATE capability_gates SET ready=true,reason=%s WHERE capability='v2_ingress'",
                        (local.REASON,),
                    )
                    conn.execute(
                        "INSERT INTO audit_events(tenant_id,actor_id,actor_label,kind,evidence) VALUES(NULL,NULL,'local-development-bootstrap','local-v2-bootstrap',%s)",
                        (Jsonb(evidence),),
                    )
            if not gate["ready"]:
                expect(
                    http.post(endpoint, json={}, headers={"X-API-Key": key}),
                    401,
                    "prepared_key_rejected_before_activation",
                )
                local.setup_operator()
                for _ in range(45):
                    try:
                        demo, key = demo_key(app)
                        break
                    except RuntimeError:
                        time.sleep(1)
                else:
                    raise RuntimeError(
                        "Local operator did not activate after verified bootstrap"
                    )
            expect(
                http.post(endpoint, json={}, headers={"X-API-Key": key}),
                422,
                "authenticated_native_validation_after_enable",
            )
            expect(
                http.post(
                    local.GATEWAY + "/api/ingest/v2/grafana",
                    json={},
                    headers={"Authorization": "Bearer " + key},
                ),
                400,
                "grafana_bearer_adapter_authenticated",
            )
            local.save(local.STATE / "verification.json", evidence)
            print(
                f"Local v2 enabled; {len(checks)} live gateway and authorization checks passed."
            )
    finally:
        db.close()


def send():
    """Submit three synthetic alerts and verify authoritative and searchable tenancy."""
    local.environment()
    with local.client(app=True) as app, local.client() as http:
        session = local.login(app)
        demo, key = demo_key(app)
        run = str(uuid4())
        payloads = [
            {
                "application": "Appchi local demo v2",
                "component": component,
                "message": message,
                "severity": severity,
                "status": "firing",
                "site": "local-development",
                "impact": "Synthetic development test",
                "runbook_url": "https://example.invalid/local-demo",
                "node_name": run,
            }
            for component, message, severity in (
                ("cpu", "Local Demo: CPU usage is high", "warning"),
                ("latency", "Local Demo: API response time is elevated", "critical"),
                ("queue", "Local Demo: background queue needs review", "high"),
            )
        ]
        headers = {"X-API-Key": key}
        body = {
            "alerts": [
                {"payload": p, "event_id": f"{run}-{i}"} for i, p in enumerate(payloads)
            ]
        }
        response = http.post(
            local.GATEWAY + "/api/ingest/v2/alerts", headers=headers, json=body
        )
        admission = local.checked(response)
        if response.status_code != 202 or any(
            item["disposition"] != "accepted" for item in admission["results"]
        ) or len(admission["results"]) != len(payloads):
            raise RuntimeError("Test batch was not fully admitted")
        # Kafka acknowledgement is admission; application and indexing are asynchronous.
        # The current contract has no receipt lookup or client-key deduplication.
        base = local.ORIGIN + f"/api/tenants/{demo['tenant_id']}/alerts"
        ids = []
        for _ in range(45):
            response = app.get(
                base, params={"cel": 'alert.node_name == "' + run + '"'}
            )
            if response.status_code == 200:
                rows = response.json()["data"]
                if len(rows) == len(payloads) and {row["component"] for row in rows} == {
                    payload["component"] for payload in payloads
                }:
                    ids = [row["id"] for row in rows]
                    break
            time.sleep(1)
        else:
            raise RuntimeError("Test alerts did not become searchable")
        for identifier in ids:
            local.checked(app.get(base + "/" + identifier))
        global_id = next(t["id"] for t in session["tenants"] if t["kind"] == "global")
        for identifier in ids:
            if (
                app.get(
                    local.ORIGIN + f"/api/tenants/{global_id}/alerts/{identifier}"
                ).status_code
                != 404
            ):
                raise RuntimeError("Cross-tenant alert detail was not concealed")
        evidence = {
            "tenant_id": demo["tenant_id"],
            "operator_id": demo["operator_id"],
            "alert_ids": ids,
            "run": run,
            "count": len(ids),
            "applied": True,
            "searchable": True,
            "global_detail_denied": True,
            "kafka_admitted": True,
            "verified_at": datetime.now(UTC).isoformat(),
        }
        local.save(local.STATE / "alerts-evidence.json", evidence)
        print(
            f"Sent and verified {len(ids)} v2 alerts in Local Demo; Kafka admission, search and tenant isolation passed."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "send"))
    args = parser.parse_args()
    {"verify": verify, "send": send}[args.action]()
