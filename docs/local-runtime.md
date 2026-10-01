# Running the local Appchi workspace

**2026-09-30 live updates (REV-36):** the Product API owns live hint publishing and SSE. No separate live worker is started. Apply migration 0047 before the updated API, and stop old live workers. See [worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md#6-live-updates-inside-the-product-api).

**2026-09-30 directory scope (REV-35):** the directory worker is nice-to-have and is not started by current launchers. Explicit trusted publication remains required for bootstrap and identity changes; automatic five-minute LDAP propagation is deferred. See [current worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md#4-directory-synchronization-is-nice-to-have). Historical runtime evidence below does not describe a rollout of this later change.

**2026-09-30 worker-scope update:** see [current worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md). Maintenance release, assignment cleanup, inactivity/retention sweeps and operator background orchestration are deferred. Operator management runs synchronously in the Product API; older worker commands below describe the deferred implementation.

**2026-09-30 Kafka cutover required:** the historical local checks below describe the earlier pipeline. The updated launcher requires the [offline Kafka cutover](runbooks/kafka-first.md), a reconciled Kong binding, and refreshed local correlation preview evidence before the new workers run. This redesign was tested in disposable databases; the existing application database has not been cut over automatically.

**2026-09-30 partial startup:** the dependency containers, API, HTTPS UI and directory
worker are running. Real local-admin sign-in and the Global alerts feed returned
success. Alert admission and processing remain unavailable: five legacy changed
messages are behind an existing `pipeline_authoritative_proof_missing` fence, so
the Kafka cutover has not run. A backup is preserved in
`.local/runtime/pre-kafka-cutover-20260930.dump`. The stale correlation profile was
preserved as `correlation-preview-profile.stale-20260930.json`; the application now
uses the ordinary development capacity profile. Renewed preview evidence is saved
under `.local/runtime` with `renewed`/`pending` filenames and is not activated.
The 31 correlation checks and five relay checks passed in disposable databases.
The user's authorized deletion of the demo rule `fdsfa` was refused by the normal
API with `recovery_not_ready`; the rule and all readiness fences remain preserved.
The alert relay was corrected to ignore unrelated resource outbox streams.

Open **https://localhost:3443**. The core local services were started and verified
on 2026-09-23. Sign in as `local-admin`; its generated password is under
`users.local-admin.password` in `.local/keycloak/credentials.json`. Appchi now
uses only username and password, with no one-time code.

The [identity guide](local-keycloak.md) covers the editor/viewer accounts, custom
users/groups and separate Keycloak console authentication. The launcher
does not change Windows trust automatically. On this workstation, the user
explicitly approved trusting the generated certificate on 2026-09-23; both
Appchi and Keycloak now load with normal browser certificate validation. If an
old tab still displays the certificate error, reopen the URL in a new tab.
Fresh workstations need their own explicit local certificate trust setup.

Select **Global → Alerts** to find the synthetic `Appchi local demo` alert. The
live browser check received this alert through native v1, waited for processing
and Elasticsearch search, then acknowledged it and added a note through the UI.
`Local Demo` is a separate tenant with admin/editor/viewer group grants. Native
v1 always routes to Global. [Local v2 setup](local-v2.md) now provides authenticated
ingestion into Local Demo through its managed `Local Demo Sender` operator.

## Start, inspect and stop

From this workspace's root in PowerShell:

```powershell
.\scripts\local-runtime.ps1 -Action Start
.\scripts\local-runtime.ps1 -Action Status
.\scripts\local-runtime.ps1 -Action Start -Restart
.\scripts\local-runtime.ps1 -Action Stop
```

Start prepares local-only credentials, starts all dependency technologies from
one Docker Compose project, applies the Appchi database migrations, then starts
the API, UI and workers on Windows. `-Restart` reloads the managed Appchi
processes. Stop halts those processes and Compose containers without removing
their volumes. Nothing is installed as a Windows service.

The single dependency definition is [`deploy/local/compose.yaml`](../deploy/local/compose.yaml).
It includes PostgreSQL, Elasticsearch, Redis, Kafka, Keycloak, Kong, Kong's
dedicated PostgreSQL database, and Grafana. The Python API,
processing workers and Next.js development server still run as local processes
so the workspace's editable source and installed developer dependencies are used.
This is a local development setup, not the production OpenShift deployment.

On this workspace, the first startup imports the existing Appchi PostgreSQL
database from a local logical backup and copies its serving Elasticsearch
projection. The previous native stores and their files are retained as rollback
sources. Fresh workspaces start with new Compose volumes. Keep `.local` and the
Docker volumes together; do not remove volumes to troubleshoot startup.

| Service | Local address |
|---|---|
| Appchi HTTPS UI | https://localhost:3443 |
| FastAPI | http://127.0.0.1:58000 |
| Keycloak / console | https://localhost:58443/admin/ |
| Appchi PostgreSQL 18.2 (Compose) | 127.0.0.1:55433 |
| Elasticsearch 9.0.0 (Compose) | http://127.0.0.1:59201 |
| Kafka 4.0.0 (Compose) | 127.0.0.1:19093 |
| Redis 7.4.2 (Compose) | 127.0.0.1:56380 |
| Kong 3.9.1 (Compose) | https://localhost:58445 |
| Kong Admin API (Compose) | https://localhost:58447 |
| Grafana 11.6.0 (Compose) | https://localhost:58448 |

The previous native PostgreSQL and Elasticsearch remain on ports 55432 and
59200 as rollback sources; Appchi uses the Compose-managed ports above. All
published dependency ports bind to loopback. Docker volumes persist Appchi data,
Keycloak identities, Kong configuration, Kafka state and Grafana configuration.

Local startup publishes trusted Keycloak users/groups once. Later changes require
`./scripts/local-identity.ps1 -Action Refresh`; directory polling is nice-to-have.
After cutover, workers handle Kafka source processing, correlation and search
consumption/reconciliation. The Product API publishes live hints and serves SSE. The legacy alert relay and hint-consumer roles are retired.
Their logs, process records and heartbeats are in `.local/runtime`; identity,
API and frontend process state/logs are in `.local/keycloak`.

## Verified scope and remaining gates

Working evidence covers real sign-in, group roles and logout (password-only login
reverified on 2026-09-24),
native v1 admission, PostgreSQL processing, Elasticsearch search, live events, browser
acknowledgement and notes. The development search bootstrap requires the actual
passing search/count compatibility report at
`qualification/pytest-local-runtime-es.xml`, exact local store bindings and an
initial search gate. It cannot clear restore fences. Initial activation and
repeated Start were exercised against the actual services.

**This is a usable core development environment, not the complete initial
release.** Additional capability status:

- Maintenance remains gated. Automatic correlation is available through the
  bounded local preview described below.
- Managed operators/native v2 are available with the optional
  [local Kong/Grafana setup](local-v2.md); company integration remains unqualified.
- Protected retention/recovery require independent evidence and remaining
  recovery implementation.

The development capacity profile remains unqualified. The correlation preview
uses a separate local file and does not establish release capacity. No production
readiness or company-service compatibility is claimed. See
[implementation status](../status/implementation-status.md) for the complete gaps.

## Local correlation preview

The local preview permits creating correlation rules and running the PostgreSQL
correlation worker in this development stack. It uses at most two enabled rules
per tenant, ten live groups per rule, 50 live evidence rows per rule and 100 per
tenant, with finite CEL cost and digest limits in
`.local/runtime/correlation-preview-profile.json`. Its `qualified` value remains
`false`. The profile and check report in `.local/runtime` are bound to this
deployment epoch and the relevant backend source digest. A changed source or
missing evidence makes the next startup refuse the preview until it is checked
again.

`scripts/local_correlation.py activate` checks tenant source order, the
maintenance state, profile evidence and the existing gate binding before an
audited local activation. The launcher starts the correlation worker when the
profile exists. `scripts/local-runtime.ps1 -Action Start -Restart` rechecks the
binding on a normal restart. This preview was exercised in a disposable
PostgreSQL database; sustained release workloads, maintenance replay and
production allocations are still unqualified. Do not copy this profile into a
staging or production deployment.

Kafka hint workers are optional accelerators; the launcher reports their degraded
state separately while authoritative PostgreSQL workers continue to reconcile.

The opt-in real browser check runs from `frontend` and leaves a synthetic alert:

```powershell
$env:APPCHI_LOCAL_RUNTIME_TEST='1'
node node_modules/@playwright/test/cli.js test --config playwright.local-runtime.config.ts
```

Its safe result identifiers are saved to `.local/runtime/browser-evidence.json`.
It uses real login and APIs without request mocks or injected sessions.
