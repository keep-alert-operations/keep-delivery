# Native v2 in local development

**2026-09-30 worker-scope update:** see [current worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md). Maintenance release, assignment cleanup, inactivity/retention sweeps and operator background orchestration are deferred. Operator management runs synchronously in the Product API; older worker commands below describe the deferred implementation.

The user authorized local v2 configuration on 2026-09-24. The assistant-selected
setup runs isolated Kong 3.9.1, its dedicated PostgreSQL 16 database and Grafana
11.6.0 as services in the unified local Compose project.
It uses the existing Appchi database, operator APIs, encrypted keys, provisioning
saga and pinned mTLS listener. It does not connect to company services.

Start the [local runtime](local-runtime.md) with the single Compose stack, then run:

```powershell
.\scripts\local-v2.ps1 -Action Start
.\scripts\local-v2.ps1 -Action Status
.\scripts\local-v2.ps1 -Action Start -Restart
.\scripts\local-v2.ps1 -Action Stop
```

The unified [`deploy/local/compose.yaml`](../deploy/local/compose.yaml) includes
these services alongside Appchi PostgreSQL, Elasticsearch, Kafka, Redis and
Keycloak. Start provisions `Local Demo Sender` in `Local Demo` using the normal local-admin
login and operator API. The normal worker provisions and verifies the Kong
Consumer/key, Grafana contact point, exact `Appchi - Local Demo Sender` policy,
and authenticated serving-member identity. The initial bootstrap checks transport
and authentication before opening only the initial local v2 gate; prepared keys
remain rejected until the normal worker activates them. Restore fences and other
gate reasons cannot be cleared by this launcher.

Repeated Start preserves keys, operator identity, data and running processes.
Restart reloads the managed helper processes. Once setup succeeds, the main
`local-runtime.ps1` launcher also starts/stops these services. Containers have
separate project-scoped volumes; Stop preserves them. Keep `.local/operators`
and the volumes together. Removing one without the other requires explicit
recovery; setup does not silently adopt or recreate an existing remote fleet.

| Service | Local address |
|---|---|
| Native v2 gateway | `https://localhost:58445/api/ingest/v2/alerts` |
| Grafana webhook gateway | `https://localhost:58445/api/ingest/v2/grafana` |
| Private Appchi listener (client certificate required) | `https://127.0.0.1:58444` |
| Worker-only readiness proof | `https://localhost:58446/operator-check` |
| Kong Admin API | `https://localhost:58447` |
| Dedicated Grafana | `https://localhost:58448` |

Published container ports are bound to loopback. Kong's community Admin API is
accessible to local processes; this is an isolated development control plane.
Grafana has a generated bootstrap password and a dedicated provisioning service
account. Keep this instance reserved for Appchi's provisioning worker: this
profile expects an empty rule inventory and preserves its initial fallback
policy. Use the regular company integration qualification for a shared instance.

Native clients send one `X-API-Key` header. Copy the current key from **Local Demo
→ Operators → Local Demo Sender**. Keys in a query/body and native Bearer auth
are rejected. Grafana uses one Bearer header on its dedicated route. Kong strips
client identity headers, authenticates the key, strips the secret and supplies
authenticated IDs over pinned mTLS; Appchi rechecks current credential state and
immutable tenant ownership in PostgreSQL. The ordinary port 58000 cannot
authenticate v2 from headers. Native v1 continues to route only to Global.

The local CA is `.local/operators/tls/ca.crt`; configure your sender to trust it
explicitly. This setup does not install certificates in Windows trust. The
Grafana webhook uses the Compose-internal `https://kong:8443` address and verifies
the same CA. Only Kong receives the private client certificate; neither container
receives the CA signing key.

To send three new synthetic alerts and verify processing, search, request retry
identity and cross-tenant concealment:

```powershell
.venv/Scripts/python.exe scripts/local_v2_verify.py send
```

This command obtains the current key through authenticated, audited Copy and
keeps it in memory. It deliberately leaves synthetic alerts in Local Demo. To
rerun gateway checks without creating alerts:

```powershell
.venv/Scripts/python.exe scripts/local_v2_verify.py verify
```

Secrets, certificates, generated bindings, process records and logs are under
ignored `.local/operators`. `verification.json` and `alerts-evidence.json` contain
only nonsecret result metadata. Focused automated evidence is recorded in
[`qualification/pytest-local-v2.xml`](../qualification/pytest-local-v2.xml).

This is development evidence for the observed versions and one gateway member.
It does not qualify production, shared-service compatibility, fleet propagation,
capacity, recovery, or Grafana's scheduled alert evaluation/delivery.

## Synchronous provisioning startup (REV-34)

The Product API now owns the management calls. For a fresh local v2 setup, run
`.venv/Scripts/python.exe scripts/local_v2.py prepare` before
`scripts/local-identity.ps1 -Action Start -Restart`, then run
`scripts/local-v2.ps1 -Action Start`. The first step prepares local credentials;
the API restart loads the new operator binding/token/DSN settings. Configuration
and proof listeners remain local; no operator worker is launched. Existing API
processes do not hot-load newly created credentials. All state and volumes remain.

After installing this revision, stop old scheduler/provisioning processes before
migration 0046. The local runtime restart detects the old removed-role records.
This document describes the startup path; it does not assert it was executed or
that the existing local Kafka cutover/readiness gaps have been resolved.
