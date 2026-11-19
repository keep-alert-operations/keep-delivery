# Development and verification runbook

**2026-09-30 live updates (REV-36):** the Product API owns live hint publishing and SSE. No separate live worker is started. Apply migration 0047 before the updated API, and stop old live workers. See [worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md#6-live-updates-inside-the-product-api).

**2026-09-30 directory scope (REV-35):** the directory worker is nice-to-have and is not started by current launchers. Explicit trusted publication remains required for bootstrap and identity changes; automatic five-minute LDAP propagation is deferred. See [current worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md#4-directory-synchronization-is-nice-to-have). Historical runtime evidence below does not describe a rollout of this later change.

**2026-09-30 worker-scope update:** see [current worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md). Maintenance release, assignment cleanup, inactivity/retention sweeps and operator background orchestration are deferred. Operator management runs synchronously in the Product API; older worker commands below describe the deferred implementation.

Unless stated otherwise, commands below run from `keep-delivery`. The sibling
frontend is `../keep-frontend`. For the coordinated local stack and critical
smoke suite, start with [Repeatable verification workflow](#repeatable-verification-workflow).

These commands start the implemented Keep components from the repository root.
They do not create an authentication bypass, qualify a company integration, enable
capability gates, or authorize production deployment. Consult
[implementation status](../../status/implementation-status.md) for current coverage.

## Prerequisites and configuration

Install Python 3.13, `Poetry 2.3.1`, Node.js 22.17 or newer, npm, and PowerShell 7 through approved
development tooling. The scripts use the committed `poetry.lock` and
`../keep-frontend/package-lock.json`. They do not install or administer shared PostgreSQL,
Kafka, Elasticsearch, Redis, LDAP, ADAPI, Kong or Grafana services.

Inject configuration into the terminal environment through your approved secret
mechanism. Do not put credentials in source control, command arguments or captured
terminal transcripts. Scripts report missing variable names without echoing values.

| Variable | Input |
|---|---|
| `KEEP_DATABASE_URL` | Supplied application PostgreSQL DSN and application role. |
| `KEEP_MIGRATION_URL` | Supplied migration PostgreSQL DSN and dedicated migration role. Used only by the migration action. |
| `KEEP_WRAPPING_KEY` | Secret base64 encoding of exactly 32 bytes, with the environment's managed key lifecycle. |
| `KEEP_WRAPPING_KEY_ID` | Matching key identifier; persist the association while encrypted records use it. |
| `KEEP_DEPLOYMENT_EPOCH` | Supplied deployment/recovery epoch; do not casually change it on restart. |
| `KEEP_PUBLIC_ORIGIN` | Exact browser HTTPS origin. Local example: `https://localhost:3443`, without trailing slash. |
| `KEEP_CAPACITY_PROFILE` | Absolute path to the reviewed application capacity profile. |
| `KEEP_BACKEND_URL` | Trusted API origin for the Next.js development proxy; local example: `http://127.0.0.1:8000`. Never include credentials. |
| `KEEP_DEV_TLS_CERTIFICATE`, `KEEP_DEV_TLS_KEY` | Local HTTPS certificate and private-key paths for the frontend development server. The certificate must cover the selected local hostname and be trusted by the browser. |

`KEEP_ENVIRONMENT=development` and the absolute path to
`qualification/development-capacity.json` select the explicitly **unqualified**
local development capacity example. Its numbers are neither a production budget
nor measured service capacity. It sets no correlation or maintenance budgets, so
both run without limits (REV-39); they have no capability gate. Any other missing
or gated capability remains visibly unavailable; do not open its gate to make a
page look successful.

For real human sign-in (REV-73), supply the LDAP bind settings `KEEP_LDAP_HOST`,
`KEEP_LDAP_PORT`, `KEEP_LDAP_USE_TLS`, optional `KEEP_LDAP_CA_FILE` and
`KEEP_LDAP_BIND_FORMAT` (for example `{username}@corp.example`); the ADAPI binding
`KEEP_ADAPI_URL` and the secret `KEEP_ADAPI_CLIENT_ID`; and the super-admin group's
samAccountName in `KEEP_SUPERADMIN_GROUP`. `KEEP_ADAPI_LOOKUP_BATCH` and
`KEEP_ADAPI_SEARCH_CACHE_TTL_SECONDS` have defaults (50 and 7200). The settings are
defined in [identity configuration 3 and 6](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-identity-configuration.md#3-username-and-password-authentication).
A qualified bootstrap refresh must publish trusted directory evidence before
`human_auth` can become ready. The development launcher does not seed users or
sessions or set that gate. Directory polling and its five-minute propagation
qualification are nice-to-have under REV-35. Run the refresh explicitly with
`python -m keep.worker directory --once` (or `python -m keep.identity.worker --once`
from an API image); set `KEEP_DIRECTORY_BOOTSTRAP_QUALIFIED=true` only on the first,
bootstrap refresh. It prints one `directory_root_failed` line per failed root and a
`directory_refresh` totals line, and exits non-zero when any root failed. Both
commands also write the REV-75 log records (service `api`, role `directory-refresh`)
to stderr, and to `keep-logs-api` when `KEEP_LOG_SHIP_URL` is set; `--budget-seconds N`
stops reading ADAPI after N seconds. In OpenShift the same command runs daily from the
`keep-directory-refresh` CronJob ([Daily directory refresh](openshift.md#daily-directory-refresh-rev-75));
locally it stays manual (local Start or `local-identity.ps1 -Action Refresh`). The
development worker launcher excludes continuous directory startup. Other
implemented roles are listed in `scripts/dev.ps1` and the relevant domain runbooks;
each requires its own explicit binding, capacity and readiness prerequisites.

Company bindings and actual private-network endpoints are unavailable here. The
user/service owners must supply them and execute the corresponding nonproduction
qualification; no example hostname or development version represents that evidence.

## Clean install and checks

```powershell
./scripts/dev.ps1 -Action Install
./scripts/dev.ps1 -Action Check
./scripts/dev.ps1 -Action Check -CheckConfiguration
```

`Install` runs `python scripts/workspace.py install --editable`, which validates
the Poetry lock, syncs dependencies, applies local Python sources and runs `npm ci`
in the sibling frontend. `Check` verifies imports and runtime versions without
contacting services. `-CheckConfiguration` additionally validates the backend
configuration schema and DSN syntax without exposing values or claiming connectivity.
`-RequireIdentityConfiguration` checks that the real identity binding variables
are also present; presence alone is not integration qualification.

## Migrate before starting writers

With `KEEP_MIGRATION_URL` supplied for the intended isolated development database:

```powershell
./scripts/dev.ps1 -Action Migrate
```

The real migration runner takes its dedicated advisory lock, applies ordered SQL
and checks stored migration checksums. Do not edit already-applied SQL. The scripts
do not create a database, create shared roles, copy an application DSN into the
migration variable, or run migrations implicitly during API startup. Migration
errors report their exception class without printing the connection string.

## Start the implemented workloads

Open separate configured terminals. These commands run in the foreground so Ctrl+C
stops the selected workload. Nothing is registered as a background service.

```powershell
# Terminal 1: Python API, listening only on loopback; raw access logs disabled.
./scripts/dev.ps1 -Action Start -Role Api -ApiPort 8000 -RequireIdentityConfiguration

# Terminal 2: Kafka source consumer with PostgreSQL source application/replay proof.
./scripts/dev.ps1 -Action Start -Role Worker -WorkerRole source

# Terminal 3: HTTPS UI with supplied certificate/key and API origin.
./scripts/dev.ps1 -Action Start -Role Frontend -FrontendPort 3443
```

Open the exact `KEEP_PUBLIC_ORIGIN`. The UI signs in on Keep's `/sign-in` page,
which posts to the API's `POST /auth/login`, and uses opaque Secure cookies; HTTP
development pages are not an equivalent authentication test. TLS
files may instead be passed with `-TlsCertificate` and `-TlsKey`. The API port and
`KEEP_BACKEND_URL` must agree. Without verified identity/bootstrap, sign-in is
gated, rather than replaced with a local password or synthetic user.

Complete the [Kafka cutover](kafka-first.md) before starting new alert workers. Supply `KEEP_KAFKA_BOOTSTRAP` and `KEEP_ELASTICSEARCH_URL` for the separate source, correlation and indexer roles:

```powershell
./scripts/dev.ps1 -Action Start -Role Worker -WorkerRole source
./scripts/dev.ps1 -Action Start -Role Worker -WorkerRole correlation
./scripts/dev.ps1 -Action Start -Role Worker -WorkerRole indexer
```

Live hint publishing starts automatically inside Redis-configured Product API
processes; there is no `live` worker command. Supply the normal API Redis binding
and approved Redis egress. Apply migration 0047 with the migration identity before
new API processes start; it grants API SELECT/DELETE on the minimal hint outbox
without worker credentials or domain RLS changes. Stop old live workers before
rollout and include publishing in API pool/concurrency qualification.

`-Once` requests one bounded worker turn. The current worker intentionally records
dependency failure and preserves pending work; exit code zero alone does not mean
search, publication or any capability gate is ready. Check API health, durable
progress and the corresponding verification evidence.

For the isolated local dependency stack, use the single
[local Compose definition](../../deploy/local/compose.yaml) through
[`scripts/local-runtime.ps1`](../../scripts/local-runtime.ps1). It runs the local
PostgreSQL, Elasticsearch, Kafka, Redis, OpenLDAP, Kong and Grafana services;
the API, UI, workers and the mock ADAPI remain host development processes. This local profile
does not replace the separately supplied and qualified company services.

## Verification commands

```powershell
./scripts/verify.ps1 -Suite Unit
./scripts/verify.ps1 -Suite Build
./scripts/verify.ps1 -Suite Browser -BrowserChannel chrome
```

`Unit` runs the coordinated Python unit suites through `scripts/workspace.py test`. Tests requiring explicit external fixtures
remain skipped if their variables are absent. `Build` runs frontend formatting,
TypeScript and the optimized production build. `Browser` runs all controlled
browser regression files; it temporarily excludes any real-browser opt-in.
Omit `-BrowserChannel` to use the pinned installed Playwright browser. Installing
that browser is an explicit separate command: `npx playwright install chromium`
from `../keep-frontend`. Installed Chrome is a distinct compatibility environment, not a
claim that the pinned browser download succeeded.

For an explicitly isolated PostgreSQL test database, supply
`KEEP_TEST_DATABASE_URL` and run:

```powershell
./scripts/verify.ps1 -Suite Database
```

This applies migrations and writes synthetic records to that supplied test database.
Never point it at production or shared company data. The ordinary integration suite
does not automatically drop that caller-owned database.

Only `KEEP_TEST_DATABASE_URL` is required. Every case whose other binding is
absent is skipped silently, so a green run without them covers far less. Bind
each variable only to isolated local services:

| Variable | Enables |
|---|---|
| `KEEP_TEST_DATABASE_URL` | Required. The caller-owned migrated database shared by session-scoped cases. |
| `KEEP_IDENTITY_TEST_ADMIN_URL` | A role with CREATE DATABASE on an isolated cluster. Search, pipeline, correlation, visibility, cutover and identity-session cases (unit and integration) create and drop their own `keep_pipeline_test_*` database per test. |
| `KEEP_MAINTENANCE_TEST_ADMIN_URL` | The same privilege, for module-scoped `keep_maintenance_test_*` databases (maintenance, restore fencing, alert detail). Falls back to `KEEP_IDENTITY_TEST_ADMIN_URL`. |
| `KEEP_TEST_ELASTICSEARCH_URL` | An isolated unauthenticated Elasticsearch 9.0, for example `http://127.0.0.1:59201`. Cases create and delete their own uniquely prefixed indices. |
| `KEEP_TEST_KAFKA_BOOTSTRAP` | An isolated Kafka 4.0 configured like the [local Compose definition](../../deploy/local/compose.yaml), including its INTERNAL listener and `kafka` hostname. Cases use unique topic and group names. |
| `KEEP_TEST_LOCAL_KONG=1` | The Kong policy cases, against the configured [local v2](../local-v2.md) Kong. |

Before the run, prove the broker ready: an idempotent produce, a transactional
init/produce/commit and a consumer-group offset commit must all succeed. A broker
that is not ready returns `kafka_publish_unconfirmed` or
`cutover_group_state_unverified` on unmodified code too.

Other suites have their own bindings. The frontend BrowserReal smoke uses
`KEEP_REAL_BROWSER_DATABASE_ADMIN_URL`, plus `KEEP_REAL_BROWSER_PYTHON` when
delivery's environment is not `.venv`. The S1–S4 suites opt in with
`KEEP_LOCAL_IDENTITY_TEST`, `KEEP_LOCAL_RUNTIME_TEST`, `KEEP_LOCAL_V2_BROWSER_TEST`
and `KEEP_LOCAL_INCIDENT_TEST`; `scripts/smoke.py` sets them for its own run only. Repository suites
use `KEEP_TEST_DATABASE_URL` and, where they need a disposable cluster,
`KEEP_MIGRATION_TEST_ADMIN_URL` (migrations), `KEEP_PROOF_TEST_POSTGRES_ADMIN_DSN`
(api operator proof) and `KEEP_BOOTSTRAP_TEST_POSTGRES_ADMIN_DSN` (indexer, with
optional `KEEP_TEST_ELASTICSEARCH_CA` and `KEEP_TEST_ELASTICSEARCH_API_KEY`).

For the real HTTPS browser/API/PostgreSQL smoke, supply
`KEEP_REAL_BROWSER_DATABASE_ADMIN_URL` for a **local isolated cluster** whose role
can create/drop disposable databases:

```powershell
./scripts/verify.ps1 -Suite BrowserReal -BrowserChannel chrome
```

That fixture creates and later drops only its randomized `keep_browser_test_*`
database, starts/stops its own unique-port API/UI process trees and uses synthetic
database session evidence exclusively inside the test. It never adds a test login
route or enables authentication on the ordinary application database. See the
[frontend evidence](https://github.com/keep-alert-operations/keep-frontend/blob/main/docs/frontend.md#real-browserapipostgresql-smoke) for exact assertions
and the distinction from company LDAP and ADAPI qualification.

The fixture seeds its alert through the Kafka-first path: it builds the admission
envelope and applies it with the source processor's PostgreSQL step, with no broker
or receipt lookup (repaired 2026-10-22, with current note selectors). It is still
not the four-journey smoke suite or evidence for real sign-in or Kafka admission.

`-Suite All` runs Unit, Build and Browser, then includes Database and BrowserReal
only when their explicit environment variables are present. Every omitted suite is
printed as `SKIPPED`; an otherwise successful command does not qualify those gates.
Tests stop on the first failing suite. Scripts restore their temporary PYTHONPATH
and browser-channel changes and do not print secret configuration.

## Repeatable verification workflow

**Agreed on 2026-10-12:** use S1–S4 below as the small baseline for application
changes; check dashboards/presets when affected. The user also accepted retaining
clearly identified synthetic alerts in the isolated local environment with a
per-run ledger. Scenario outcomes come from existing specifications, not new
product decisions. Procedures and automated coverage below were inspected in
source; their presence is not an executed pass. The setup task ran only the
checks listed in its [validation report](../../qualification/agent-verification-setup-2026-10-12.md).

### One command and the finish gate

`scripts/smoke.py` runs S1–S4 below against the running local stack, in order, and
writes a report. Run it from the stack's own `keep-delivery`, the checkout whose
`.local/` and dev server form the running stack:

```powershell
& "<workspace>\keep-delivery\.venv\Scripts\python.exe" "<workspace>\keep-delivery\scripts\smoke.py" run --workspace "<workspace>"
```

- **Before the scenarios.** It takes the lock `.local/smoke/lock`: it waits up to
  120 seconds for a live holder and takes over a dead or stale one. It repairs what
  a killed run left: local-viewer's `keep-observers` membership (with a directory
  refresh) and the cleanup of an unfinished S4 incident. Then it checks readiness:
  Node and the frontend's Playwright, credentials, both `/livez`, the UI, the mock
  ADAPI and fresh worker heartbeats. A stack that isn't ready is reported
  `blocked`, and no scenario runs.
- **Order and results.** S1 not passing blocks S2–S4. S2 failing still runs S3, and
  S4 then sends its own alert. A skipped Playwright test makes the scenario
  `partial`, which counts as failed. The whole run, lock wait included, has 540
  seconds; the first full run on 2026-11-19 took 164.
- **Output.** Exit 0 means passed, 1 failed and 2 blocked. The report is
  `<workspace>/keep-delivery/.local/smoke/latest.json`, with a copy in
  `reports/<run_id>.json`. It holds per-scenario results, counts, IDs and evidence
  paths, the content fingerprint of every app repo checkout, and
  `stack_includes_change`. The run ledger is `.local/verification/<run_id>/` in the
  stack's delivery.
- **What it tests.** The stack as it runs: it never starts, stops or reloads
  services. `stack_includes_change` (`yes`, `no` or `unknown`) says whether the
  running processes load the changed checkouts; a change that lives only in a
  worktree is `no`. Reload the affected processes first when the change itself
  must be tested.
- **Data kept per run.** S2 keeps one alert, S3 four (three v2 and one through
  Grafana) and S4 one resolved incident, plus its own alert when the S2 alert
  already has an active incident: five or six alerts and one incident, each named
  with the run ID.

**The finish gate.** `python scripts/workspace.py bootstrap` installs three things
at the workspace root from `workspace/.claude/`:

- the `smoke-runner` subagent (`.claude/agents/smoke-runner.md`: Haiku, PowerShell
  and Read only, runs the command above once and returns its table);
- the `scripts/smoke_gate.py` hooks in `.claude/settings.json`;
- an allow rule for the exact command above in `.claude/settings.local.json`.

In a Claude Code session opened at the workspace root (not inside one repository):

- The gate records each app repo checkout's content fingerprint when the session
  starts (main checkouts) or when a tool call first names it (worktrees such as
  `.wt/<task>/<repo>`). Any content change counts as an app change except
  `*.md`, `docs/`, `.github/` and `.claude/`, and in delivery `workspace/`,
  `status/` and `qualification/`. `keep-design` is never an app repository.
- After an app change, finishing is blocked until a report covers the current
  content and its `run_id` appears in a finished `smoke-runner` subagent of the
  session that ran on Haiku. The block message names the subagent and the command.
- While the subagent runs, a stop is allowed. A passed report allows finishing; a
  failed, partial or blocked one sends the agent back once to handle it.
- A missing run blocks every stop, except once per code state when the agent is
  waiting for the user's answer. After five blocks in one code state the gate lets
  the stop through with a message, so a broken stack can't trap a session.
- Committing tested content keeps it covered. A change to the fingerprint
  algorithm (`ALGORITHM_VERSION` in `scripts/smoke_state.py`) invalidates older
  reports. A missing gate script never blocks.

### Environment, startup and readiness

Use the existing Windows/PowerShell 7, Docker Desktop and installed Chrome local
profile. Required versions and editable installation are in
[workspace setup](../repository-workspace.md#checkout-and-dependencies).
Run the prerequisite `dev.ps1 -Action Check` above; install only when needed.
Record each involved repository's HEAD and staged/unstaged/untracked changes,
dependency mode (release pins or editable siblings), browser/runtime versions and
nonsecret service versions. Existing branch names and old reports do not identify
the running code. Inventory using `python scripts/workspace.py inventory --output
.local/workspace-inventory.json`; process records must match this checkout and
its installed sources. After source changes, reload the affected owned processes
and record the restart before claiming the new code was tested.

Before Start or Stop, inspect existing process ownership, listeners and data:

```powershell
# From keep-delivery. Status/ps do not prove application readiness.
./scripts/local-runtime.ps1 -Action Status
docker compose -p keep-local-stack -f deploy/local/compose.yaml ps
Get-NetTCPConnection -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess
./scripts/dev.ps1 -Action Check
```

Match owned process IDs/start times to `.local/runtime/workers.json` and the
identity/operator process records; do not print their secret environment files.
Use the ports in [local runtime](../local-runtime.md#start-inspect-and-stop) and
any validated [v2 port overrides](../local-v2.md). Never start a duplicate stack,
stop another checkout's processes or replace credentials for existing volumes.
Fresh setup and retained-volume recovery differ; follow those runbooks first.

```powershell
./scripts/local-runtime.ps1 -Action Start
./scripts/local-runtime.ps1 -Action Status
.venv/Scripts/python.exe scripts/local_runtime.py wait-workers
(Invoke-WebRequest http://127.0.0.1:58000/livez).StatusCode
(Invoke-WebRequest http://127.0.0.1:58001/livez).StatusCode
```

Start orchestrates local dependencies, dedicated migrations, explicit identity
publication, API/UI, source, indexer and correlation workers. It requires the
documented Kafka cutover and real search bootstrap evidence. Correlation and
maintenance are always on (REV-39); do not launch deferred scheduler/operator/live
workers or clear a recovery fence. An existing stack upgraded from the gated
version follows the one-time steps in
[local runtime](../local-runtime.md#always-on-correlation-and-maintenance-rev-39).

Require healthy dependency containers, owned API/UI processes, both `/livez`
responses 200 and fresh ready worker heartbeats with matching process IDs.
`wait-workers` checks freshness (15 seconds) and identity; Status alone can show an
old last-turn result. Then open `https://localhost:3443`, sign in normally, and
require successful session/tenant access and searchable test alerts in S1/S2.
Liveness, an empty Feed or exit code zero alone does not establish readiness.
Record gated capabilities and exact failures without opening gates manually.
Use `-Action Start -Restart` only after confirming ownership when a coordinated
reload is needed; it can restart several processes and run migration/setup checks.

### Test access, isolation and run ledger

Use only synthetic data in the local `keep_local` database/loopback stack.
This is persistent development data, not an automatically disposable database.
Sign in on the Keep `/sign-in` page with the generated local-admin/editor/viewer
usernames and passwords from `.local/identity/credentials.json`, described in
[local sign-in and directory](../local-identity.md). Read credentials
locally without printing passwords, cookies, keys or complete configuration.
Do not inject a session for S1–S4. Browser test TLS exceptions are local to these
tests; they do not authorize changing Windows trust or backend TLS validation.

Assign a unique run UUID and prefix manually created names/notes with
`smoke-<run UUID>`. Store a ledger under ignored
`.local/verification/<run UUID>/`: tested snapshot, timestamp, scenario results,
tenant/resource IDs, generated fixture run IDs, pre-existing services, mutations
to restore, artifact paths and cleanup outcome. The v1 test generates its own
UUID; the v2 sender generates a distinct UUID in `node_name`. Capture those IDs
in the ledger instead of relying on shared names. Never select unrelated records
by title alone. Preserve generated reports before another run overwrites them.

S1 creates Local Demo and its group grants if missing and may temporarily remove
and restore the local viewer's `keep-observers` membership in the mock ADAPI. Run
serially against this dedicated local directory with no competing identity tests. S3 uses the existing sender that `.local/operators/demo.json` names (`Local Demo Sender` by default);
key Copy is audited, and gateway verification can bootstrap the initial local gate
and retry provisioning. It is not a read-only probe. On an unprepared stack, follow
[local v2 setup](../local-v2.md#synchronous-provisioning-startup-rev-34), including
prepare/API reload/Start; never point it at shared company Kong/Grafana.

### Critical smoke scenarios and expected results

| ID | Steps through actual entry points | Required observable results | Existing coverage / limits |
|---|---|---|---|
| S1 Identity and access | Run the local identity suite; on the Keep `/sign-in` page sign in as admin/editor/viewer with passwords from `.local/identity/credentials.json`, inspect Local Demo roles, remove/restore the viewer's group membership with a refresh after each change, try a wrong password, sign out. | Each username/password sign-in produces the expected principal; `local-admin` is a global super-admin. Local Demo roles are admin/edit/view. Non-admin permission management returns 403. Revoked tenant access returns 404, restoration returns 200. A wrong password gets 401 `authentication_rejected` with no session. After sign-out the old session returns 401. Global retains its separate edit baseline. | `local-identity.spec.ts` runs against the real local OpenLDAP, mock ADAPI, API and PostgreSQL and asserts every result in this row, including the 401 code, the old session replayed after sign-out and Global's edit baseline. A skipped test is a partial, not an S1 pass. |
| S2 Native v1 and alert work | Run the local-runtime browser suite: submit its unique native v1 alert, wait for search, open Global Feed/detail, Assign to me, add a note, reload. | Admission returns 202 only after Kafka acknowledgement; exactly the created alert becomes visible in Global, Feed appears and live delivery reports connected with no update notice (REV-43), assignment acknowledges it, note survives reload and acknowledged CEL search includes its ID. A 202 alone does not prove processing/search. | `local-runtime.spec.ts` uses real login, ingestion, workers, Elasticsearch and browser. It asserts the note and the assignee (the acting user) after a reload. The connected live status proves connection, not every SSE recovery path. |
| S3 Native v2 isolation | Run gateway `verify`, then `send`; open the resulting IDs in Local Demo Feed/detail through the browser. | Missing/invalid/forged credentials rejected; duplicate/unsupported key transport rejected, direct private listener requires mTLS. Valid batch has 3 accepted results with 202; all 3 become searchable with expected components and names (REV-54), detail works only in the owning tenant, the same IDs under Global return 404. Browser shows the matching records. | `local_v2_verify.py` automates gateway/API/search assertions, including 401/400/404/422 distinctions. `local-v2-browser.spec.ts` then opens each `alerts-evidence.json` ID in Local Demo Feed and detail as local-admin, and checks that the same ID under Global returns 404 and shows no detail. No receipt lookup. It does not test Grafana's scheduled delivery or fleet propagation. |
| S4 Incident lifecycle | As editor/admin in the S2 alert's tenant, open Incidents → New incident, enter a unique title and explicit severity, and create. Confirm open/unassigned state; use Link alerts on detail to select the run's unowned alert. Add a uniquely tagged note, reload, resolve, reload, reopen and reload. | Created incident, subsequent link and note persist after reload; severity remains immutable; resolve leaves alert status unchanged. With no competing owner, reopen succeeds, starts a new lifecycle cycle and retains the link/history. Inspect detail plus its normal API/history responses for results not visible in the UI. | `local-incident.spec.ts` runs it through the HTTPS UI as local-editor with Low severity. It links the S2 alert when that alert has no active incident, otherwise its own `smoke-<run>` v1 alert, and reports `blocked` with the owning incident when that one is owned too; it never takes another incident's alert. Cleanup runs in `finally` and touches only the incident in its ledger. Follow the authoritative [incident rules](../../../keep-design/specs/spec-incident-operations.md). |

For S4, record incident/alert IDs and statuses before/after every transition. If
linking or reopening conflicts, inspect active ownership and the authoritative
error, do not steal another incident's alert. Use another uniquely created fixture
or record a failure. Resolve/reopen is not alert resolution. This small successful
journey does not replace the incident conflict, permission, atomic membership or
retention cases in the [verification plan](../../../keep-design/verification/verification-plan.md).
For affected layouts, repeat the relevant journey at a narrow viewport and with
keyboard interaction, using the existing UI acceptance requirements.

Use [the one command](#one-command-and-the-finish-gate) for the baseline. To
diagnose one scenario, run its parts by hand, serially: S1, S2, the S3 browser
spec and S4 from the stack's `keep-frontend`, and the S3 helper from its
`keep-delivery` after the v2 setup prerequisites. Restore prior opt-in variable
values afterward (use a dedicated test terminal if simpler):

```powershell
# keep-frontend
$env:KEEP_LOCAL_IDENTITY_TEST='1'
node node_modules/@playwright/test/cli.js test --config playwright.local-identity.config.ts
# Inspect exit status and skipped tests before proceeding.
$env:KEEP_LOCAL_RUNTIME_TEST='1'
node node_modules/@playwright/test/cli.js test --config playwright.local-runtime.config.ts
# keep-delivery
.venv/Scripts/python.exe scripts/local_v2_verify.py verify
.venv/Scripts/python.exe scripts/local_v2_verify.py send
# keep-frontend, after send
$env:KEEP_LOCAL_V2_BROWSER_TEST='1'
node node_modules/@playwright/test/cli.js test --config playwright.local-v2.config.ts
# Without KEEP_SMOKE_S2_ALERT, S4 sends and links its own alert.
$env:KEEP_LOCAL_INCIDENT_TEST='1'
node node_modules/@playwright/test/cli.js test --config playwright.local-incident.config.ts
```

Stop on a failed scenario, preserve its evidence and diagnose before dependent
steps. S2 retains one synthetic alert; S3 retains four (three v2 and one through
Grafana). Do not run the senders in a retry loop or assume repeating client IDs
deduplicates admission. S4 works in the normal HTTPS UI, not by directly mutating
the database.

### Automated coverage and impact-based regressions

| Layer | Inspected automation | Selection and gap |
|---|---|---|
| Python repositories | Owned CI runs Poetry lock/install, Ruff, unit tests and wheel build; migrations CI additionally provisions PostgreSQL and runs its tests. | Run changed repository checks and affected shared-package consumers; service CI is not system E2E. |
| Delivery | CI bootstraps pinned siblings and runs contract, boundary and document checks; `verify.ps1` provides Unit/Build/Browser/Database/BrowserReal. | Use affected PG/Kafka/search integration cases, explicit isolated bindings and real entry points. `All` can skip Database/BrowserReal and does not establish S1–S4. |
| Frontend | CI runs generation/drift, format, typecheck and build; `npm test` has controlled-route browser scenarios for alerts, incidents, navigation, dashboards/presets and access loss. | CI currently does not run Playwright. Run affected browser files locally plus S1–S4; mocked routes cannot establish persistence or real authentication. Keep local opt-ins unset for the controlled suite. |
| Real local stack | `scripts/smoke.py` runs the opt-in S1–S4 suites and the S3 helper in order; the finish gate requires it after app changes. | Outside default CI. It tests the running stack as is, and the report says whether that includes the change. BrowserReal seeds through the source step without a broker or real sign-in. |
| Release qualification | Existing capability, replay, recovery, capacity and private-integration cases in design/delivery. | Smoke is not release qualification; preserve all existing gates, deferred-worker corrections and stronger regression requirements. |

When shared commands/domain/auth/contracts change, broaden to affected producers,
consumers, revision/idempotency, tenant isolation and failure paths. Pipeline changes
need real replay/order/reconciliation checks; schema changes need migration
history/privilege checks. UI-affecting backend changes also exercise the changed
browser journey. Dashboards/presets add save/reload, count correctness, draft/conflict
and relevant responsive checks when affected. Serialize heavy CEL, builds and
browser suites. Documentation-only work uses document/context/import checks.

Smallest follow-up: add a reviewed CI/browser environment with isolated data if
CI smoke is requested.
This setup does not install a framework, provision CI services or claim those gaps
are fixed. See [engineering policies](../agent-workflows.md#documentation-and-verification)
for the binding selection and repair rules.

### Pipeline load comparison (before/after)

Use the pipeline phase of `qualification/scalability_load.py` to compare pipeline
throughput and latency between two package sets, for example `origin/main`
before and after a performance merge. It is a local diagnostic, not release
qualification. It does not use the shared `keep-local-stack` containers or the
local runtime. It skips the API and users. Production KafkaAdmission publishes
fixed workload `pipeline-v1`. The production source, correlation and indexer worker
processes consume it through `consume()`. Each scenario drains completely
before the next one starts:

| Scenario | Offered alerts | Measures |
|---|---|---|
| `warmup` | `--warmup` (100), spread over all tenants | Not reported; warms caches and assignment |
| `steady` | `--steady-rate` × `--steady-seconds` (3/s × 60 s), all tenants | Stage latency at a fixed sub-saturation rate |
| `burst_spread` | `--burst` (300), all tenants, `--burst-concurrency` (50) in flight | Stage throughput across tenants |
| `burst_one_tenant` | `--burst` v2 alerts for one tenant | Per-tenant serialization ceiling (rule and maintenance tenant) |
| `burst_global_v1` | `--burst` native v1 alerts for Global | The same ceiling for all v1 traffic |

For each scenario the result JSON contains the following:

- Per-stage throughput and latency percentiles from PostgreSQL timestamps. The
  database clock offset is corrected. Stages: received→recorded (processor) and
  received→processed (correlation).
- The search drain time and a 100 ms sampled visibility latency.
- Worker diagnostics differenced over the scenario:
  - `consume()` batch sizes.
  - Source records per Kafka transaction.
  - Kafka transaction, partition-confirmation write and apply time per record.
  - The confirmation share of active source turns.
  - Changed-consumer records per handler call and Kafka commits per record.
  - PostgreSQL transactions per record by stage.
- `kafka_source_results` HOT update ratio and WAL bytes per alert.
- Sampled lock-waiting backends.

Two summaries feed the open design items. `pipeline.confirmation` covers the
partition-confirmation write cost. `pipeline.serialization` gives one-tenant and
Global throughput as ratios to the spread burst. The report also records
`workload`, the exact arguments, installed package commits (editable checkouts
add HEAD and a dirty flag), consumer batching settings, service versions and host.

Run both sides with the same arguments on the same idle host, under the
heavy-suite rules (one heavy run at a time). Run each side at least twice;
single runs vary. From `keep-delivery`, with an environment whose installed
packages are the revision under test: a non-editable `poetry sync` of delivery's
lock, or `scripts/workspace.py install --editable` for local siblings. On Windows,
keep that environment path short (see workspace setup):

```powershell
.venv/Scripts/python.exe qualification/scalability_services.py up
$env:KEEP_SCALE_ADMIN_URL = Get-Content .local/keep-load-admin-url
.venv/Scripts/python.exe qualification/scalability_load.py --phase pipeline --features `
  --kafka-bootstrap 127.0.0.1:19293 --elasticsearch-url http://127.0.0.1:59401 `
  --redis-url redis://127.0.0.1:56580/0 --drain-seconds 1800 --output .local/pipeline-before-1.json
# Repeat for -2, then install the other revision and write pipeline-after-1/-2.json.
.venv/Scripts/python.exe qualification/compare_pipeline_load.py `
  --before (Get-ChildItem .local/pipeline-before-*.json) `
  --after (Get-ChildItem .local/pipeline-after-*.json)
.venv/Scripts/python.exe qualification/scalability_services.py down
```

`scalability_services.py up` starts private `keep-load-*` PostgreSQL,
Elasticsearch, Redis and Kafka containers. Kafka is configured like
`deploy/local/compose.yaml`: INTERNAL inter-broker listener and hostname `kafka`.
Readiness is proven before it returns, including an idempotent produce and a
transactional init/produce/commit. `down` runs `docker rm -f -v` on exactly
those containers. The comparison refuses runs whose workloads differ, and runs
that are no measurement unless `--allow-invalid` is given. A run
exits non-zero (`passed` false) unless every scenario drained to exact
processor, correlation, search and watermark counts. Treat such a run as no
measurement. Retried inputs do not invalidate a run but set `clean` false. Each
scenario reports them as `faults` (consumer and reason). Each worker role reports
its `errors` and `handler_errors` by code, for example native CEL compile
deadlines on a busy host. Compare them like any other metric. The defaults keep `steady` below this workstation's measured capacity.
Do not change them between the runs you compare. Executed baselines belong
in `qualification/`, with their package commits.

### Evidence, cleanup and completion

Record each command with working directory, exit status, pass/fail/skip counts,
test snapshot and environment; `scripts/smoke.py` records these in its report and
ledger. For any manual check record steps, expected/observed outcomes and redacted
screenshots or API result fields. S2 writes
`.local/runtime/browser-evidence.json`, `alerts.png` and `alert-detail.png`; S3 writes
`.local/operators/verification.json` and `alerts-evidence.json`. Copy safe results
into the run ledger before rerunning. Standard Playwright uses `playwright-report/`
and `test-results/`; real identity configs disable automatic auth capture. Never
publish passwords, session tokens, operator keys, connection strings or raw auth
traces. Durable sanitized summaries belong in `qualification/`, with links from
status documents only when they establish new implementation evidence.

Restore any test-changed memberships/grants even after failure; S1 has a restoration
finally block, but an interrupted process still needs an explicit check/publication.
`scripts/smoke.py` does that check and the unfinished S4 cleanup at the start and
end of each run.
For S4 cleanup, while the test incident is active unlink only the ledger's alert,
then resolve the test incident and confirm the alert status is unchanged. This
releases its ownership while retaining history. Leave S2/S3 synthetic alerts and
their notes with recorded IDs as agreed. Do not delete tenants, purge unrelated
rows, erase history or remove `.local`/volumes as cleanup. Retained fixtures are
an explicit cleanup outcome, not a claim of zero residue.

Close test browser sessions and restore the test terminal's environment. If this
run exclusively started the full stack and no other work uses it, stop via
`./scripts/local-runtime.ps1 -Action Stop`; otherwise retain it and record why.
Stop preserves credentials/volumes. For disposable database tests, confirm fixture
cleanup removed only the generated database/processes; the Database suite leaves
its caller-owned database for its separately scoped cleanup. Never bulk-drop data.

Fix defects introduced by the change, repeat the failed scenarios and affected
regressions, and attach fresh evidence. Completion reports distinguish **documented**,
**executed passed**, **executed failed**, and **blocked/skipped/not run**. Name exact
missing tools, bindings, stale gates or fixture problems and the smallest next
action. Neither old passing evidence nor a documented procedure verifies new code.

## Frontend image and OpenShift artifact

The [frontend Dockerfile](https://github.com/keep-alert-operations/keep-frontend/blob/main/Dockerfile) builds standalone Next.js output.
Supply `KEEP_BACKEND_URL` as a **build argument** containing the trusted internal
API origin. Next.js resolves its rewrites during the build; changing a runtime
environment variable does not rewrite an already-built image's API routing.

```powershell
# From keep-frontend; supply private dependency access as described in workspace setup.
docker build --secret id=github_token,env=KEEP_REPOSITORIES_TOKEN --build-arg KEEP_BACKEND_URL='<supplied trusted internal API origin>' --tag '<local image name>' .
```

This is a local build command, not a publish or deployment instruction. The Node
image tag is a development selection. Release CI must verify the complete image,
record its immutable digest/SBOM/scan results, and bind it to the intended API origin.
If the approved gateway routes `/api` and `/auth` directly, review that routing
contract explicitly; do not silently rely on an unconfigured Next.js proxy.

The [frontend OpenShift template](../../deploy/openshift/frontend.template.yaml)
contains only a Deployment and ClusterIP Service. Every image, replica and resource
input is required; no company allocation is invented. `FRONTEND_IMAGE_REPOSITORY`
plus `FRONTEND_IMAGE_DIGEST` must resolve to an approved `repository@sha256:<64 hex>`
image. `API_INTERNAL_URL` must exactly match its `KEEP_BACKEND_URL` build argument;
the template uses that exact variable name and records the binding annotation.

Supply qualified replica count, CPU/memory/ephemeral-storage requests and limits,
and bounded temporary/cache volume sizes through a reviewed parameter file. Local
rendering, when the owning platform supplies `oc`, is:

```powershell
oc process --local --filename deploy/openshift/frontend.template.yaml --param-file '<supplied frontend parameter file>' --output yaml
```

Rendering is not deployment authorization. The template does not create public
routes, TLS certificates, network policy, registry credentials, infrastructure
retention or service-owner allocations. The non-sensitive shell probe checks only
the frontend process; it does not qualify backend capabilities or user sign-in.
Containers drop capabilities, use runtime-default seccomp, run as non-root and
mount bounded writable temporary/cache volumes under an otherwise read-only root.

## Executed evidence and limits

The following describes historical evidence, not checks executed by the
2026-10-12 agent setup update. See its separate
[setup validation report](../../qualification/agent-verification-setup-2026-10-12.md).

PowerShell parsing, `dev.ps1 -Action Check`, synthetic configuration validation,
locked dependency installation, generated-contract checks, TypeScript and the
optimized production build have passed locally. The frontend template contains
two objects and 13 required parameters; local structure checks are not OpenShift
server validation.

Actual PostgreSQL/Elasticsearch backend tests, controlled browser workflows and
real HTTPS browser/API/PostgreSQL smoke checks have passed locally. Current totals,
migration coverage and outstanding reruns are maintained in
[implementation status](../../status/implementation-status.md). Packaging checks do not
establish correctness of subsequent changes.

No OpenShift deployment, container build, shared-service change, company identity
login, sustained capacity or coordinated infrastructure restore has been
qualified. The private-environment runbook records the corresponding gates.

## Worker scope rollout

Stop deferred roles before applying migration 0046. Deploy matching Product API,
source/correlation/indexer code together: the immutable source-release cutover
preserves old history while later ends wait for source updates. Do not start old
workers against the new schema. No application worker is required solely to end a
maintenance window or age a resource out of this release.

The Product API CONFIG_SECRET supplies `KEEP_OPERATOR_DATABASE_URL`,
`KEEP_OPERATOR_GRAFANA_TOKEN`, `KEEP_OPERATOR_KONG_TOKEN` and
`KEEP_OPERATOR_PROBE_TOKEN`. `KEEP_OPERATOR_BINDING` references the mounted
verified binding. Use provisioning-role privileges on its separate bounded pool;
ordinary API RLS remains separate. Confirm operator-route proxy timeouts (100s),
per-replica pool allocation and required remote egress before rollout. The network policy template takes explicit egress rules; supplied
Product API policies must include the approved Kong/Grafana/proof destinations.

Failed operations need explicit Retry from their detail page. A deleted operator
with unfinished final verification remains visible to tenant admins for recovery;
it disappears after completion. Cancellation can resume initial setup in the same
request. No process restarts or shared service changes are implied by these steps.
