# Running the local Keep workspace

**2026-11-15 sign-in and directory (REV-73):** Keep's own `/sign-in` page checks
the password with one LDAP bind, and people and groups come from ADAPI. Locally,
OpenLDAP and a mock ADAPI stand in for them. The first Start after this change
moves the local credentials and remaps the local principals once; see
[local sign-in and directory](local-identity.md#moving-from-the-earlier-local-setup).

**2026-10-26 Kafka topic identity (REV-41):** the source processor refuses a received
topic that is not the incarnation recorded at cutover, and refuses while none is
recorded. This stack's cutover predates migration 0054: with source, correlation and
indexer stopped, apply 0054 and run `python -m keep.kafka_cutover
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
stack. See the [design register](https://github.com/keep-alert-operations/keep-design/blob/main/decisions/design-register.md#rev-39--always-on-correlation-and-maintenance-2026-10-24).

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

**2026-09-30 live updates (REV-36):** the Product API owns live hint publishing and SSE. No separate live worker is started. Apply migration 0047 before the updated API, and stop old live workers. See [worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md#6-live-updates-inside-the-product-api).

**2026-09-30 directory scope (REV-35):** the directory worker is nice-to-have and is not started by current launchers. Explicit trusted publication remains required for bootstrap and identity changes; automatic five-minute LDAP propagation is deferred. See [current worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md#4-directory-synchronization-is-nice-to-have). Historical runtime evidence below does not describe a rollout of this later change.

**2026-09-30 worker-scope update:** see [current worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md). Maintenance release, assignment cleanup, inactivity/retention sweeps and operator background orchestration are deferred. Operator management runs synchronously in the Product API; older worker commands below describe the deferred implementation.

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

Open **https://localhost:3443**. A signed-out visit opens the Keep `/sign-in`
page. Sign in as `local-admin`; its generated password is under
`users.local-admin.password` in `.local/identity/credentials.json`. Sign-in uses
only a username and password, with no one-time code.

The [local sign-in and directory guide](local-identity.md) covers the other test
accounts, adding users and groups, the mock ADAPI test hooks and the one-time move.
The launcher does not change Windows trust automatically. On this workstation, the
user explicitly approved trusting the generated certificate on 2026-09-23, so Keep
loads with normal browser certificate validation. If an old tab still displays the
certificate error, reopen the URL in a new tab. Fresh workstations need their own
explicit local certificate trust setup.

Select **Global → Alerts** to find the synthetic `Keep local demo` alert. The
live browser check received this alert through native v1, waited for processing
and Elasticsearch search, then acknowledged it and added a note through the UI.
`Local Demo` is a separate tenant with admin/editor/viewer group grants. Native
v1 always routes to Global. [Local v2 setup](local-v2.md) now provides authenticated
ingestion into Local Demo through its managed `Local Demo Sender` operator.

## Start, inspect and stop

From `keep-delivery` in PowerShell:

```powershell
.\scripts\local-runtime.ps1 -Action Start
.\scripts\local-runtime.ps1 -Action Status
.\scripts\local-runtime.ps1 -Action Start -Restart
.\scripts\local-runtime.ps1 -Action Stop
```

Start prepares local-only credentials, starts all dependency technologies from
one Docker Compose project, applies the Keep database migrations, then starts
the Product API, public Ingestion API, UI and workers on Windows. `-Restart` reloads the managed Keep
processes. Stop halts those processes and Compose containers without removing
their volumes. Nothing is installed as a Windows service.

The single dependency definition is [`deploy/local/compose.yaml`](../deploy/local/compose.yaml).
It includes PostgreSQL, Elasticsearch, Redis, Kafka, OpenLDAP, Kong, Kong's
dedicated PostgreSQL database, Grafana, the Fluent Bit log transport (see
[Logs in Elasticsearch](#logs-in-elasticsearch-rev-74)) and Prometheus (see
[Metrics](#metrics-rev-74)). The Python API, the mock ADAPI,
processing workers and Next.js development server still run as local processes
so the workspace's editable source and installed developer dependencies are used.
This is a local development setup, not the production OpenShift deployment.

Both local PostgreSQL containers use Compose `init: true` so orphaned container
exec or healthcheck processes are reaped by the init process instead of the
database postmaster. Recreating these containers to apply configuration changes
preserves their named data volumes; do not remove the volumes.

On this workspace, the first startup imports the existing Keep PostgreSQL
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
| Keep HTTPS UI | https://localhost:3443 |
| Product API | http://127.0.0.1:58000 |
| Public Ingestion API / native v1 | http://127.0.0.1:58001/api/ingest/v1/alerts |
| Private Ingestion API (mTLS) | https://127.0.0.1:58444 |
| OpenLDAP (Compose, plain LDAP) | ldap://127.0.0.1:58389 |
| Mock ADAPI | http://127.0.0.1:58092 |
| Keep PostgreSQL 18.2 (Compose) | 127.0.0.1:55433 |
| Elasticsearch 9.0.0 (Compose) | http://127.0.0.1:59201 |
| Kafka 4.0.0 (Compose) | 127.0.0.1:19093 |
| Redis 7.4.2 (Compose) | 127.0.0.1:56380 |
| Kong 3.9.1 (Compose) | https://localhost:58445 |
| Kong Admin API (Compose) | https://localhost:58447 |
| Grafana 11.6.0 (Compose) | https://localhost:58448 |
| Grafana for Grafana panels (Compose proxy) | https://localhost:58449 |
| Fluent Bit 5.1.2 log input (Compose) | http://127.0.0.1:59880 |
| Fluent Bit's own metrics and health (Compose) | http://127.0.0.1:59820 |
| Prometheus 2.54.1 (Compose) | http://127.0.0.1:59090 |

The previous native PostgreSQL and Elasticsearch remain on ports 55432 and
59200 as rollback sources; Keep uses the Compose-managed ports above. All
published dependency ports bind to loopback. Docker volumes persist Keep data,
Kong configuration, Kafka state and Grafana configuration. OpenLDAP has no volume;
Start seeds it again from `.local/identity/credentials.json`.
Kafka keeps its log in `kafka_data` through `KAFKA_LOG_DIRS`; before 2026-10-25 it
used the container layer, and recreating the container lost both topics. After the
recorded cutover, Start no longer creates missing application topics and refuses to
start workers when the broker ends below, or holds another delivery at, an offset
PostgreSQL recorded. That state needs the reviewed reset in
[Kafka failure and recovery](runbooks/kafka-first.md#failure-and-recovery).

Local startup publishes the mock ADAPI's users and groups once. Later changes require
`./scripts/local-identity.ps1 -Action Refresh`; directory polling is nice-to-have.
After cutover, the required workers handle Kafka source processing and search
consumption/reconciliation, and the correlation worker always starts. The Product API publishes live hints and serves SSE. The legacy alert relay and hint-consumer roles are retired.
Their logs, process records and heartbeats are in `.local/runtime`; mock ADAPI,
API and frontend process state/logs are in `.local/identity`.

## Verified scope and remaining gates

Working evidence covers native v1 admission, PostgreSQL processing, Elasticsearch
search, live events, browser acknowledgement and notes. Sign-in, group role and
sign-out evidence from before REV-73 does not cover the LDAP bind or the mock
ADAPI; see [implementation status](../status/implementation-status.md) for runs
on the current setup. The development search bootstrap requires the actual
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
the local binding, so rebuild with that CLI. From `keep-delivery` in PowerShell 7:

```powershell
./scripts/local-runtime.ps1 -Action Start -Restart   # migrates, then stops at search_mapping_mismatch
$values = Get-Content .local/identity/app-environment.json -Raw | ConvertFrom-Json
foreach ($item in $values.PSObject.Properties) { [Environment]::SetEnvironmentVariable($item.Name, [string]$item.Value, 'Process') }
$env:KEEP_DATABASE_URL = $env:KEEP_LOCAL_WORKER_DATABASE_URL
.venv/Scripts/python.exe -c "from keep.search.local_bootstrap import binding; print(binding().model_dump_json())" | Set-Content .local/runtime/search-binding.json -Encoding utf8NoBOM
.venv/Scripts/python.exe -m keep.search.rebuild_cli --binding .local/runtime/search-binding.json start
.venv/Scripts/python.exe -m keep.search.rebuild_cli --binding .local/runtime/search-binding.json resume <job-id>
./scripts/local-runtime.ps1 -Action Start
./scripts/local-v2.ps1 -Action Start -Restart       # when local v2 runs; plain Start does not restart it
```

The workers are stopped while the rebuild copies, so nothing else writes the index.
The previous physical index stays for cleanup, as in the [indexer runbook](https://github.com/keep-alert-operations/keep-indexer/blob/main/docs/runbooks/search-rebuild.md).
The CLI reports any failure as `search_rebuild_unavailable`; `status <job-id>` shows
the job's state, and `resume` continues the same job.

## Alert provider upgrade (REV-55)

REV-55 adds `provider` and `alert_rule_url` to every search document, so upgrade a
stack from before it with the rebuild above (Start applies migration 0060 first).
Then fill alerts stored before providers existed, in the same shell with the worker
environment loaded and the workers running:

```powershell
.venv/Scripts/python.exe -m keep.provider_backfill
```

It prints one JSON line per tenant with the number of alerts filled; a rerun fills
none. `unresolved` lists alerts whose latest source event is missing, which keep no
provider. Each filled alert gets a new projection version, so search shows its
provider once the indexer applies it.

## Always-on correlation and maintenance (REV-39)

Correlation rules and maintenance windows have no capability gate. Per-rule and
per-window Enable/Disable still apply. `scripts/local_identity.py` always points
`KEEP_CAPACITY_PROFILE` at `qualification/development-capacity.json`, which sets
no correlation or maintenance budgets, so both run without limits here. The
launcher always starts the `correlation` worker with `source` and `indexer`.
Window deletion still requires the `recovery` and `human_auth` gates and the
recovery evidence store, so it remains unavailable until protected recovery exists.
The REV-30 preview, `scripts/local_correlation.py` and its
`.local/runtime/correlation-preview-profile*.json` files are no longer used.

One-time upgrade of this stack, from `keep-delivery` in PowerShell:

1. Stop every writer: `.\scripts\local-runtime.ps1 -Action Stop`. Pre-0053 code
   reads the gate rows that migration 0053 deletes.
2. Back up the Keep database (`keep_local`) with only PostgreSQL running:

   ```powershell
   $compose = @('-p','keep-local-stack','-f','deploy/local/compose.yaml')
   docker compose @compose start postgres
   docker compose @compose exec -T postgres sh -ec 'PGPASSWORD="$(cat /run/secrets/postgres_password)" pg_dump -Fc -f /tmp/pre-0053.dump postgresql://keep_migrator@127.0.0.1:5432/keep_local'
   docker compose @compose cp postgres:/tmp/pre-0053.dump .local/runtime/pre-0053-always-on.dump
   ```

   Copy the file out rather than redirecting `pg_dump` output, which would
   corrupt a binary dump in PowerShell. Never name a backup
   `keep-local-migration.dump`: Start can restore that file over the database.
3. Start: `.\scripts\local-runtime.ps1 -Action Start`. Its migration step applies
   0053, which removes the `correlation` and `maintenance` gate rows and restarts
   rules saved as blocked with `consumer_not_integrated` at a new evaluation epoch,
   so changed events queued while the gate was closed never count.
4. Warm up maintenance proof once per tenant (Global and each other tenant). In the
   same shell, load the worker environment the launcher uses, then run the warmup
   and inspect the proof counts:

   ```powershell
   (Get-Content .local/identity/app-environment.json -Raw | ConvertFrom-Json).PSObject.Properties |
     ForEach-Object { [Environment]::SetEnvironmentVariable($_.Name, [string]$_.Value, 'Process') }
   $env:KEEP_DATABASE_URL = $env:KEEP_LOCAL_WORKER_DATABASE_URL
   .\.venv\Scripts\python.exe -m keep.worker maintenance-warmup --tenant <tenant UUID>
   .\.venv\Scripts\python.exe -m keep.worker maintenance-proof --tenant <tenant UUID>
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

The opt-in real browser check runs from `keep-frontend` and leaves a synthetic alert:

```powershell
$env:KEEP_LOCAL_RUNTIME_TEST='1'
node node_modules/@playwright/test/cli.js test --config playwright.local-runtime.config.ts
```

Its safe result identifiers are saved to `.local/runtime/browser-evidence.json`.
It uses real login and APIs without request mocks or injected sessions.

## ServiceNow ticket panels (REV-65)

The local API reads `KEEP_TICKET_COUNT_URL` from the generated environment, which
points at `http://127.0.0.1:58090/count` unless `KEEP_LOCAL_TICKET_COUNT_URL`
overrides it. Plain `http` is accepted only for loopback addresses. Start the fake
counting service next to the API:

```powershell
.venv/Scripts/python.exe scripts/local_ticket_count.py --port 58090
```

It answers stable synthetic counts per team. Teams starting with `unknown`, `nocount`,
`slow` or `error` give Team not found, No count, a timeout and an HTTP 500, so every
panel state can be checked. `POST /__set` with `{"team": "NOC", "count": 25}` pins a
count. Without the fake service running, ServiceNow panels show that the count is
unavailable; with the variable empty, the type is disabled.

## Grafana panels (REV-68)

The local API reads `KEEP_GRAFANA_PANEL_ORIGINS` from the generated environment, which
allows the local Grafana at `https://localhost:58449` and the fake Grafana at
`http://127.0.0.1:58091` unless `KEEP_LOCAL_GRAFANA_PANEL_ORIGINS` overrides it
(`KEEP_LOCAL_GRAFANA_PANEL_PORT` moves the first). Plain `http` is accepted only for
loopback addresses in development.

The Compose Grafana is set up for panels: embedding is allowed, anonymous visitors
are Viewers, and a read-only **Keep local / Keep local panels** dashboard
(`deploy/local/grafana/`) shows synthetic latency, error-rate and orders panels from
the built-in TestData source. Browsers do not trust the operators CA that signs
Grafana's certificate on 58448, so the `grafana-panels` Compose service serves Grafana
on 58449 with Keep's trusted localhost certificate, verifies Grafana with the operators
CA, and lets only `https://localhost:*` pages frame it. Keep's API keeps using 58448
for operator routing. Use an address such as
`https://localhost:58449/d-solo/keep-local-panels/keep-local-panels?orgId=1&panelId=1`,
or open the dashboard on 58449 and copy a panel's **Share › Embed** code. After
pulling this change, run
`docker compose -p keep-local-stack -f deploy/local/compose.yaml up -d grafana grafana-panels`
and regenerate the environment (`scripts/local_identity.py environment`) before
restarting the API.

Start the fake Grafana next to the API:

```powershell
.venv/Scripts/python.exe scripts/local_grafana.py --port 58091
```

It serves single-panel pages at `/d-solo/<uid>/<slug>?panelId=N` that follow the
`theme` parameter, and plain dashboard pages at `/d/…` for the panel link. Use an
address such as `http://127.0.0.1:58091/d-solo/local/checkout?orgId=1&panelId=4` in
the panel dialog. With the variable empty, the type is disabled.

## ServiceNow tickets on incidents (REV-70)

The local API reads `KEEP_SERVICENOW_URL` from the generated environment, which is
`https://servicenow.example` unless `KEEP_LOCAL_SERVICENOW_URL` overrides it. Keep
never calls it: incidents accept ticket links only into that address and Create in
ServiceNow opens its new incident form in a browser tab, which does not load for the
reserved `.example` name. Point it at a real ServiceNow instance you may use to check
the prefilled form. With the variable empty, incidents show no ticket row.

## Logs in Elasticsearch (REV-74)

The Compose stack runs one Fluent Bit. The launcher puts its address in the generated
environment as `KEEP_LOG_SHIP_URL` (`http://127.0.0.1:59880` unless
`KEEP_LOCAL_LOG_SHIP_URL` or `KEEP_LOCAL_LOG_SHIP_PORT` overrides it), so every Keep
process that sets up logging posts its records there, each to the path for its
service. Fluent Bit writes them to Elasticsearch, one index per service:
`keep-logs-ingestion`, `keep-logs-processor`, `keep-logs-correlation`,
`keep-logs-indexer` and `keep-logs-api`, and `keep-logs-frontend`,
`keep-logs-browser` and `keep-logs-migrations` once those services ship records. Every record is also
still written to the process's `.err.log` file. The configuration is
[`deploy/local/fluent-bit/fluent-bit.conf`](../deploy/local/fluent-bit/fluent-bit.conf).

The logs cluster is set separately from the alert-search cluster. The applications
read the alert-search cluster from `KEEP_ELASTICSEARCH_URL`. Fluent Bit reads the
logs cluster from `KEEP_LOCAL_LOGS_ELASTICSEARCH_HOST` and
`KEEP_LOCAL_LOGS_ELASTICSEARCH_PORT`, which default to the stack's own
Elasticsearch, so locally both are the same cluster. Keep creates no index
template, retention or role there. Elasticsearch therefore maps every string field
as text with a `.keyword` sub-field: use the `.keyword` name to sort or aggregate.

To follow one alert, send it and search by its application, then by the IDs the
records carry:

```powershell
$body = '{"application":"demo-app","object":"database","node_name":"db-1","message":"disk is full","operator":"legacy","time_created":"12-11-2026 14:00:00","severity":"error"}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:58001/api/ingest/v1/alerts -ContentType application/json -Body $body
$query = '{"size":100,"sort":[{"@timestamp":"asc"},{"event.sequence":"asc"}],"query":{"match_phrase":{"keep.alert.application":"demo-app"}}}'
(Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:59201/keep-logs-*/_search' -ContentType application/json -Body $query).hits.hits |
    ForEach-Object { '{0} {1,-22} {2} {3}' -f $_._source.'@timestamp', $_._index, $_._source.event.action, $_._source.message }
```

| To find | Query (`match_phrase` on) |
|---|---|
| One arrival in ingestion, the source processor and correlation | `keep.delivery_id` |
| The same arrival in the indexer | `keep.changed_event_id`, named on the processor's `alert_applied` record |
| Everything that happened to an alert after ingestion | `keep.alert.id` |
| Alerts by their own fields | `keep.alert.application`, `keep.alert.object` or `keep.alert.component`, `keep.alert.severity` and the rest |
| One HTTP request, including a rejected alert or a refused command | `http.request.id`: the `request_id` of an ingestion response, the `X-Request-ID` header of a Product API response |
| What one user did | `user.id`, the user's principal UUID |

Correlation's and the indexer's `alert_change_consumed` records are written before
the alert's content is loaded, so they carry the alert ID and the changed-event ID
but not its application or component. A search by application alone misses those
two steps; a search by either ID returns them.

Checked on 2026-11-12 against this stack: a new v1 alert, a repeat and a clear each
left eleven steps in order across the four indices (received, validated and
published; consumed, matched, applied and change published; consumed and correlated
or skipped; consumed and indexed), an alert with a missing and an unknown field left
`alert_rejected` naming both, and Fluent Bit reported every record written with no
error, retry or drop. With the Fluent Bit container stopped, ingestion still
answered 202 in under 10 ms and the alert was processed; once Fluent Bit was back
all eleven records arrived with their original `@timestamp`. Assigning and then resolving
that alert in the UI as the local editor left, for each action, `alert_changed`
with the cause and the user, `alert_notification_published` and the request record
in `keep-logs-api`, followed by correlation's and the indexer's steps for the new
changed event: 25 records for the alert from arrival to resolution. Kibana is not
part of the stack; any Elasticsearch client works against `http://127.0.0.1:59201`.

The API writes a successful read, search or session check
that the same user repeats for the same resource once, and then once a minute
with `keep.repeats`, so an open UI does not fill the index.

Fluent Bit serves its own counters at `http://127.0.0.1:59820/api/v1/metrics`.
While it is down, records queue in each process (10,000 records; beyond that the
oldest are dropped from shipping and counted). Fluent Bit is configured to buffer
on its `fluent_bit_data` volume while Elasticsearch is away; that case has not been
exercised here, because locally the logs cluster is also the alert-search cluster.

## Metrics (REV-74)

Each Keep process serves `GET /metrics` on its own loopback port, set by the
launcher that starts it. The stack's Prometheus scrapes them every five seconds,
together with Fluent Bit's own counters, and evaluates the Keep rules. The
application ports do not serve `/metrics`.

| Process | Metrics port | Prometheus job |
|---|---|---|
| Product API | 59101 | `keep-api` |
| Ingestion API | 59102 | `keep-ingestion` |
| Source processor | 59103 | `keep-processor` |
| Indexer | 59104 | `keep-indexer` |
| Correlation | 59105 | `keep-correlation` |
| Fluent Bit | container port 2020 | `keep-fluent-bit` |

| What | Where |
|---|---|
| Prometheus (queries, targets, alerts) | http://127.0.0.1:59090 |
| Dashboard "Keep pipeline" in the local Grafana, folder "Keep monitoring" | https://localhost:58449/d/keep-pipeline (the Grafana proxy under the trusted localhost certificate; https://localhost:58448 is the same Grafana under the operators' certificate) |
| Scrape configuration | [`deploy/local/prometheus/prometheus.yml`](../deploy/local/prometheus/prometheus.yml) |
| Rules and their unit tests | [`deploy/monitoring/prometheus`](../deploy/monitoring/prometheus) |
| Dashboard file | [`deploy/monitoring/grafana/keep-pipeline.json`](../deploy/monitoring/grafana/keep-pipeline.json) |

Every log event is also a counter of the same name: `keep_events_total` with the
labels `event`, `outcome` and `reason`. For example
`sum by (outcome) (rate(keep_events_total{event="alert_applied"}[1m]))` is the rate
of created, updated, unchanged, resolved and reopened alerts, and
`keep_events_total{outcome="failure"}` lists every failure with its reason. Stage
timings are `keep_event_delay_seconds` and `keep_event_duration_seconds` by event;
HTTP requests are `keep_http_requests_total` by route; Kafka lag is
`keep_kafka_consumer_lag_messages`.

A caught-up consumer of the changed topic shows a lag of 1: the last entry is the
transaction marker, which is never delivered as a message.

Checked on 2026-11-12: all six targets up; after a known mix of new, repeated,
cleared and rejected alerts, each of eleven counters moved by exactly the number
sent; the target-down alert fired while the processes were stopped and cleared
after they started.

Check the rule file after editing it:

```powershell
docker run --rm -v "${PWD}/deploy/monitoring/prometheus:/rules:ro" -w /rules --entrypoint promtool prom/prometheus:v2.54.1 test rules keep-rules.test.yml
```

## Real user monitoring (REV-74)

The local API accepts the browser's monitoring events: the generated environment
sets `KEEP_RUM_ENABLED=true` (`KEEP_LOCAL_RUM_ENABLED=false` turns it off). The UI
collects what users experience and posts a batch to `POST /api/rum-events` every
30 seconds and when the tab is hidden. With the setting off the endpoint answers
"not found" and the browser stops sending for that session.

The API turns the events into `keep_rum_*` metrics on its own metrics port, shown
in the row "What users experience (browser)" of the Keep pipeline dashboard: page
views by section, page load timings, API calls as the browser saw them, live
connection states and losses, and JavaScript errors. Unhandled JavaScript errors,
failed API calls and lost live connections are also written to
`keep-logs-browser`, with the user's ID and, for a failed call, the failed
request's ID in `http.request.id`. An error's own text is never recorded.

Checked on 2026-11-12: after signing in, moving between Feed and Incidents and
raising one deliberate error in the page, two batches were accepted and the page
views, timings, API call counts, live-connection time and the error record were
all present.

## Fresh start after the Keep rename (REV-67)

The rename to Keep changed every stored format tag, so a stack from before it cannot
be read and starts empty. The Compose volumes now have `keep-` names; the old
`appchi-local-*` volumes stay untouched as a rollback copy until you remove them
yourself. Move the old `.local/identity`, `.local/runtime` and `.local/operators`
aside (keep `.local/identity/tls` so the certificate stays the same), then:

A stack that still has `.local/keycloak` from the setup removed by REV-73 moves that folder aside too; otherwise Start copies its old keys back.


1. `.\scripts\local-runtime.ps1 -Action Start` migrates `keep_local` to the head and
   starts the API and UI. On an empty broker it stops before the workers because no
   Kafka cutover is recorded yet.
2. With `.local/identity/app-environment.json` loaded and `KEEP_DATABASE_URL` set to
   the worker URL, run `.venv/Scripts/python.exe scripts/local_runtime.py probe`
   (creates the two `keep.development.alerts.*` topics), then
   `.venv/Scripts/python.exe -m keep.kafka_cutover --ingress-and-legacy-workers-stopped --local-config .local/runtime/kafka-protocol.json`.
   A first attempt can report `cutover_group_state_unverified` while the new broker's
   group coordinator is still loading; run it again.
3. `.\scripts\local-runtime.ps1 -Action Start -Restart` starts ingestion and the workers.
4. For local v2: `.\scripts\local-v2.ps1 -Action Start` creates the Grafana token and
   then stops at operator provisioning (HTTP 503), because the API started without it.
   Run `.\scripts\local-identity.ps1 -Action Start -Restart` and then
   `.\scripts\local-v2.ps1 -Action Start` again.

If the workspace folder itself was renamed (`appchi-workspace` to `keep-workspace`),
two kinds of state can still point at the old path. Compose does not recreate the
`postgres` and `kong-db` containers for a moved secret file, so Start fails with
"bind source path does not exist"; recreate just those two with
`docker compose -p keep-local-stack -f deploy/local/compose.yaml up -d --no-deps --force-recreate postgres kong-db`
(their named volumes are kept). The `ca_file` entries in `.local/operators/binding.json`
also keep the old path, so calls that use them fail with `FileNotFoundError`; point
them at the same files under the new folder.
