# Running the local Appchi workspace

**2026-10-26 Kafka topic identity (REV-41):** the source processor refuses a received
topic that is not the incarnation recorded at cutover, and refuses while none is
recorded. This stack's cutover predates migration 0054: with source, correlation and
indexer stopped, apply 0054 and run `python -m appchi.kafka_cutover
--record-topic-identity` before starting the updated processor; see the
[runbook](runbooks/kafka-first.md#failure-and-recovery).

**2026-10-25 Kafka storage and pipeline recovery (REV-40):** Kafka now persists its
log in the `kafka_data` volume. A container recreation on 2026-10-05 had lost both
topics and later stopped the source on a reused offset; the reviewed reset is recorded
in [local Kafka reset evidence](../qualification/local-kafka-reset-2026-10-25.md).

**2026-10-24 always-on correlation and maintenance (REV-39):** correlation rules and
maintenance windows are always on; there is no capability gate or local preview.
Older notes below that call them gated or preview-only are history. Follow the
[one-time upgrade steps](#always-on-correlation-and-maintenance-rev-39) once on this
stack. See the [design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md#rev-39--always-on-correlation-and-maintenance-2026-10-24).

**2026-10-04 startup repaired:** the core development system and local v2 gateway
are running from the independent repositories. Original local credentials and
persistent data were recovered, the legacy changed stream was drained after
reference verification, and the documented offline Kafka cutover completed.
Repeated Start succeeded. Real browser sign-in, v1 processing/search, assignment
and persisted notes passed; 17 gateway checks and a three-alert v2 batch passed.
See [executed repair evidence](../qualification/local-startup-2026-10-04.md).
Maintenance, protected recovery and refreshed automatic correlation preview
remain unqualified. The preserved historical correlation gate is not evidence
for running a new correlation worker.

**2026-09-30 live updates (REV-36):** the Product API owns live hint publishing and SSE. No separate live worker is started. Apply migration 0047 before the updated API, and stop old live workers. See [worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md#6-live-updates-inside-the-product-api).

**2026-09-30 directory scope (REV-35):** the directory worker is nice-to-have and is not started by current launchers. Explicit trusted publication remains required for bootstrap and identity changes; automatic five-minute LDAP propagation is deferred. See [current worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md#4-directory-synchronization-is-nice-to-have). Historical runtime evidence below does not describe a rollout of this later change.

**2026-09-30 worker-scope update:** see [current worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md). Maintenance release, assignment cleanup, inactivity/retention sweeps and operator background orchestration are deferred. Operator management runs synchronously in the Product API; older worker commands below describe the deferred implementation.

**2026-09-30 Kafka cutover required:** the historical local checks below describe the earlier pipeline. The updated launcher requires the [offline Kafka cutover](runbooks/kafka-first.md), a reconciled Kong binding, and refreshed local correlation preview evidence before the new workers run. This redesign was tested in disposable databases; the existing application database has not been cut over automatically.

**Historical 2026-09-30 partial startup (resolved by the 2026-10-04 repair):** the dependency containers, API, HTTPS UI and directory
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

From `appchi-delivery` in PowerShell:

```powershell
.\scripts\local-runtime.ps1 -Action Start
.\scripts\local-runtime.ps1 -Action Status
.\scripts\local-runtime.ps1 -Action Start -Restart
.\scripts\local-runtime.ps1 -Action Stop
```

Start prepares local-only credentials, starts all dependency technologies from
one Docker Compose project, applies the Appchi database migrations, then starts
the Product API, public Ingestion API, UI and workers on Windows. `-Restart` reloads the managed Appchi
processes. Stop halts those processes and Compose containers without removing
their volumes. Nothing is installed as a Windows service.

The single dependency definition is [`deploy/local/compose.yaml`](../deploy/local/compose.yaml).
It includes PostgreSQL, Elasticsearch, Redis, Kafka, Keycloak, Kong, Kong's
dedicated PostgreSQL database, and Grafana. The Python API,
processing workers and Next.js development server still run as local processes
so the workspace's editable source and installed developer dependencies are used.
This is a local development setup, not the production OpenShift deployment.

Both local PostgreSQL containers use Compose `init: true` so orphaned container
exec or healthcheck processes are reaped by the init process instead of the
database postmaster. Recreating these containers to apply configuration changes
preserves their named data volumes; do not remove the volumes.

On this workspace, the first startup imports the existing Appchi PostgreSQL
database from a local logical backup and copies its serving Elasticsearch
projection. The previous native stores and their files are retained as rollback
sources. Fresh workspaces start with new Compose volumes. Keep `.local` and the
Docker volumes together; do not remove volumes to troubleshoot startup.

When moving an existing local stack to a new checkout, recover its matching
password files, application encryption credentials, identities and TLS material
before Start. Check the stopped containers' mount paths to locate the original
ignored `.local` directory. Regenerate checkout-specific environment paths;
do not copy process records or reuse stale qualification/profile files. A new
password file does not change the password already stored in a PostgreSQL volume.

| Service | Local address |
|---|---|
| Appchi HTTPS UI | https://localhost:3443 |
| Product API | http://127.0.0.1:58000 |
| Public Ingestion API / native v1 | http://127.0.0.1:58001/api/ingest/v1/alerts |
| Private Ingestion API (mTLS) | https://127.0.0.1:58444 |
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
Kafka keeps its log in `kafka_data` through `KAFKA_LOG_DIRS`; before 2026-10-25 it
used the container layer, and recreating the container lost both topics. After the
recorded cutover, Start no longer creates missing application topics and refuses to
start workers when the broker ends below, or holds another delivery at, an offset
PostgreSQL recorded. That state needs the reviewed reset in
[Kafka failure and recovery](runbooks/kafka-first.md#failure-and-recovery).

Local startup publishes trusted Keycloak users/groups once. Later changes require
`./scripts/local-identity.ps1 -Action Refresh`; directory polling is nice-to-have.
After cutover, the required workers handle Kafka source processing and search
consumption/reconciliation, and the correlation worker always starts. The Product API publishes live hints and serves SSE. The legacy alert relay and hint-consumer roles are retired.
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

- Correlation and maintenance are always on (REV-39). Maintenance window
  deletion stays unavailable until protected recovery exists.
- Managed operators/native v2 are available with the optional
  [local Kong/Grafana setup](local-v2.md); company integration remains unqualified.
- Protected retention/recovery require independent evidence and remaining
  recovery implementation.

The development capacity profile remains unqualified and does not establish
release capacity. No production readiness or company-service compatibility is
claimed. See [implementation status](../status/implementation-status.md) for the
complete gaps.

## Search mapping changes (REV-54)

Start's search bootstrap requires the serving index's mapping to match the code
exactly. A change that adds a field to every document, as REV-54's `name` does, needs
a rebuild: the indexer refuses an existing document whose content differs at the same
version. The bootstrap accepts a serving index that the rebuild CLI completed under
the local binding, so rebuild with that CLI. From `appchi-delivery` in PowerShell 7:

```powershell
./scripts/local-runtime.ps1 -Action Start -Restart   # migrates, then stops at search_mapping_mismatch
$values = Get-Content .local/keycloak/app-environment.json -Raw | ConvertFrom-Json
foreach ($item in $values.PSObject.Properties) { [Environment]::SetEnvironmentVariable($item.Name, [string]$item.Value, 'Process') }
$env:APPCHI_DATABASE_URL = $env:APPCHI_LOCAL_WORKER_DATABASE_URL
.venv/Scripts/python.exe -c "from appchi.search.local_bootstrap import binding; print(binding().model_dump_json())" | Set-Content .local/runtime/search-binding.json -Encoding utf8NoBOM
.venv/Scripts/python.exe -m appchi.search.rebuild_cli --binding .local/runtime/search-binding.json start
.venv/Scripts/python.exe -m appchi.search.rebuild_cli --binding .local/runtime/search-binding.json resume <job-id>
./scripts/local-runtime.ps1 -Action Start
./scripts/local-v2.ps1 -Action Start -Restart       # when local v2 runs; plain Start does not restart it
```

The workers are stopped while the rebuild copies, so nothing else writes the index.
The previous physical index stays for cleanup, as in the [indexer runbook](https://github.com/appchi-alert-operations/appchi-indexer/blob/main/docs/runbooks/search-rebuild.md).
The CLI reports any failure as `search_rebuild_unavailable`; `status <job-id>` shows
the job's state, and `resume` continues the same job.

## Always-on correlation and maintenance (REV-39)

Correlation rules and maintenance windows have no capability gate. Per-rule and
per-window Enable/Disable still apply. `scripts/local_identity.py` always points
`APPCHI_CAPACITY_PROFILE` at `qualification/development-capacity.json`, which sets
no correlation or maintenance budgets, so both run without limits here. The
launcher always starts the `correlation` worker with `source` and `indexer`.
Window deletion still requires the `recovery` and `human_auth` gates and the
recovery evidence store, so it remains unavailable until protected recovery exists.
The REV-30 preview, `scripts/local_correlation.py` and its
`.local/runtime/correlation-preview-profile*.json` files are no longer used.

One-time upgrade of this stack, from `appchi-delivery` in PowerShell:

1. Stop every writer: `.\scripts\local-runtime.ps1 -Action Stop`. Pre-0053 code
   reads the gate rows that migration 0053 deletes.
2. Back up the Appchi database (`appchi_local`) with only PostgreSQL running:

   ```powershell
   $compose = @('-p','appchi-local-stack','-f','deploy/local/compose.yaml')
   docker compose @compose start postgres
   docker compose @compose exec -T postgres sh -ec 'PGPASSWORD="$(cat /run/secrets/postgres_password)" pg_dump -Fc -f /tmp/pre-0053.dump postgresql://appchi_migrator@127.0.0.1:5432/appchi_local'
   docker compose @compose cp postgres:/tmp/pre-0053.dump .local/runtime/pre-0053-always-on.dump
   ```

   Copy the file out rather than redirecting `pg_dump` output, which would
   corrupt a binary dump in PowerShell. Never name a backup
   `appchi-local-migration.dump`: Start can restore that file over the database.
3. Start: `.\scripts\local-runtime.ps1 -Action Start`. Its migration step applies
   0053, which removes the `correlation` and `maintenance` gate rows and restarts
   rules saved as blocked with `consumer_not_integrated` at a new evaluation epoch,
   so changed events queued while the gate was closed never count.
4. Warm up maintenance proof once per tenant (Global and each other tenant). In the
   same shell, load the worker environment the launcher uses, then run the warmup
   and inspect the proof counts:

   ```powershell
   (Get-Content .local/keycloak/app-environment.json -Raw | ConvertFrom-Json).PSObject.Properties |
     ForEach-Object { [Environment]::SetEnvironmentVariable($_.Name, [string]$_.Value, 'Process') }
   $env:APPCHI_DATABASE_URL = $env:APPCHI_LOCAL_WORKER_DATABASE_URL
   .\.venv\Scripts\python.exe -m appchi.worker maintenance-warmup --tenant <tenant UUID>
   .\.venv\Scripts\python.exe -m appchi.worker maintenance-proof --tenant <tenant UUID>
   ```

   Do not rely on `--once`: it runs one page (at most 100 alerts, and a page also
   yields after 100 ms, which here means about one alert) and the next run starts
   again from the beginning. Without `--once` the warmup carries its cursor between
   pages; stop it when `maintenance-proof` in a second shell reports `pending` 0. An
   alert still without a current checkpoint fails detail reads with
   `maintenance_proof_unavailable`. Alerts counted in `missing_baseline` cannot be
   proven; report them to the user before removing them from the local data.

**Executed on this stack, 2026-10-25:** backup
`.local/runtime/pre-always-on-20261025.dump`, Start applied 0051–0053, and a
cursor-paged warmup completed Global 22/22 and Local Demo 12/12 with no missing
baseline. The optional v2 private listener did not start because the delivery
`.venv` lacks `httptools`; this predates REV-39.

The opt-in real browser check runs from `appchi-frontend` and leaves a synthetic alert:

```powershell
$env:APPCHI_LOCAL_RUNTIME_TEST='1'
node node_modules/@playwright/test/cli.js test --config playwright.local-runtime.config.ts
```

Its safe result identifiers are saved to `.local/runtime/browser-evidence.json`.
It uses real login and APIs without request mocks or injected sessions.
