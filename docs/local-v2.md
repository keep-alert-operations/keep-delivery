# Native v2 in local development

**2026-10-04 verified:** local startup, normal demo-operator key rotation,
17 gateway authorization checks, and Kafka admission/search/tenant isolation
for three synthetic alerts passed. See [startup repair evidence](../qualification/local-startup-2026-10-04.md).

**2026-09-30 worker-scope update:** see [current worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md). Maintenance release, assignment cleanup, inactivity/retention sweeps and operator background orchestration are deferred. Operator management runs synchronously in the Product API; older worker commands below describe the deferred implementation.

The user authorized local v2 configuration on 2026-09-24. The assistant-selected
setup runs isolated Kong 3.9.1, its dedicated PostgreSQL 16 database and Grafana
11.6.0 as services in the unified local Compose project.
It uses the existing Keep database, operator APIs, encrypted keys, provisioning
saga and pinned mTLS listener. It does not connect to company services.

Start the [local runtime](local-runtime.md) with the single Compose stack, then run:

```powershell
.\scripts\local-v2.ps1 -Action Start
.\scripts\local-v2.ps1 -Action Status
.\scripts\local-v2.ps1 -Action Start -Restart
.\scripts\local-v2.ps1 -Action Stop
```

The unified [`deploy/local/compose.yaml`](../deploy/local/compose.yaml) includes
these services alongside Keep PostgreSQL, Elasticsearch, Kafka, Redis and
OpenLDAP. Start provisions `Local Demo Sender` in `Local Demo` using the normal local-admin
sign-in (`POST /auth/login`) and operator API. The normal Product API request provisions and verifies the Kong
Consumer/key, Grafana contact point, exact `Keep - Local Demo Sender` policy,
and authenticated serving-member identity. The initial bootstrap runs the
key-rejection and listener checks before opening only the initial local v2 gate.
Prepared keys stay rejected until normal activation, which the gate fences. Kong
denies a prepared key before any upstream call, so the check that Kong replaces
forged identity headers and reaches private ingestion over pinned mTLS runs once
the sender is active. Restore fences and other gate reasons cannot be cleared by
this launcher.

Repeated Start preserves keys, operator identity, data and running processes.
The first Start records the sender's tenant and operator IDs in
`.local/operators/demo.json`. Later Starts, `verify` and `send` use the sender
that file names, whatever its name. To use another sender in `Local Demo`, set its
`operator_id` there. If the file names an operator or tenant the database does not
have, Start stops instead of creating a new sender.
Restart reloads the managed helper processes. Once setup succeeds, the main
`local-runtime.ps1` launcher also starts/stops these services. Containers have
separate project-scoped volumes; Stop preserves them. Keep `.local/operators`
and the volumes together. Removing one without the other requires explicit
recovery; setup does not silently adopt or recreate an existing remote fleet.

| Service | Local address |
|---|---|
| Native v2 gateway | `https://localhost:58445/api/ingest/v2/alerts` |
| Grafana webhook gateway | `https://localhost:58445/api/ingest/v2/grafana` |
| Private Keep listener (client certificate required) | `https://127.0.0.1:58444` |
| Worker-only readiness proof | `https://localhost:58446/operator-check` |
| Kong Admin API | `https://localhost:58447` |
| Dedicated Grafana | `https://localhost:58448` |

The table shows the defaults. If Windows reserves either host port, choose two
unused loopback ports outside `netsh interface ipv4 show excludedportrange protocol=tcp`
and the corresponding IPv6 ranges. Persist only the needed overrides in ignored
`.local/operators/ports.json`, for example:

```json
{"kong_admin":58647,"grafana":58648}
```

Only these two keys are accepted, with distinct integer ports from 1024 through
65535 that do not collide with the other fixed local listeners. Both launchers
load the file before any service action; Python configuration and verification
read the same values. Missing keys retain the defaults. TLS and loopback binding
remain unchanged. For direct Compose commands, load the validated environment
first in the same PowerShell session:

```powershell
$ports = & .venv/Scripts/python.exe scripts/local_v2.py ports
if ($LASTEXITCODE -ne 0) { throw 'Invalid local port configuration' }
($ports | ConvertFrom-Json).PSObject.Properties | ForEach-Object {
    [Environment]::SetEnvironmentVariable($_.Name, [string]$_.Value, 'Process')
}
```

After changing ports on an existing installation, start the containers with those
values, run `.venv/Scripts/python.exe scripts/local_v2.py configure` to refresh
the verified binding, then restart the managed API with
`scripts/local-identity.ps1 -Action Start -Restart` before
`scripts/local-v2.ps1 -Action Start -Restart`. Existing API processes and helper
processes do not hot-load the binding or ports file. Preserve credentials and
volumes throughout; deleting the overrides restores the documented defaults on
the next coordinated restart.

Published container ports are bound to loopback. Kong's community Admin API is
accessible to local processes; this is an isolated development control plane.
Grafana has a generated bootstrap password and a dedicated provisioning service
account. Keep this instance reserved for Keep's provisioning worker: this
profile expects an empty rule inventory and preserves its initial fallback
policy. Use the regular company integration qualification for a shared instance.

Native clients send one `X-API-Key` header. Copy the current key from **Local Demo
→ Operators**, on the sender that `demo.json` names (`Local Demo Sender` unless you
changed it). Keys in a query/body and native Bearer auth
are rejected. Grafana uses one Bearer header on its dedicated route. Kong strips
client identity headers, authenticates the key, strips the secret and supplies
policy-bound operator/tenant context over pinned mTLS. Private ingestion validates
that trusted context and publishes to Kafka without Keep PostgreSQL access.
The Product API on port 58000 has no ingestion routes; public ingestion on 58001
accepts native v1 only, routed to Global.

The local CA is `.local/operators/tls/ca.crt`; configure your sender to trust it
explicitly. This setup does not install certificates in Windows trust. The
Grafana webhook uses the Compose-internal `https://kong:8443` address and verifies
the same CA. Only Kong receives the private client certificate; neither container
receives the CA signing key.

From `keep-delivery`, send three new synthetic native alerts and one through the
Grafana webhook, and verify Kafka admission, processing, search, each alert's
provider and rule URL (REV-55) and cross-tenant concealment:

```powershell
.venv/Scripts/python.exe scripts/local_v2_verify.py send
```

This command obtains the current key through authenticated, audited Copy and
keeps it in memory. A 202 confirms Kafka admission; it polls tenant search and
detail APIs for subsequent application/indexing. The current admission contract
has no receipt lookup or client-key deduplication. It deliberately leaves
synthetic alerts in Local Demo. To
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
The 2026-10-04 evidence records execution of this startup path and the local Kafka
cutover. Company integration and the remaining capability gates stay unqualified.
