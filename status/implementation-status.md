# Implementation status

## 2026-10-19 Pipeline round-trip reduction

A throughput/latency review led to these changes in domain 6d4cfa6, processor
f483832 and migrations c5f3e9c, pinned by api, correlation, indexer and ingestion:

- Tenant-scoped and opted-in READ COMMITTED units take the restore guard inside
  their own transaction, so they no longer open two extra transactions per unit.
- Each Kafka source alert takes the tenant coordinator once; its
  `tenant_work_state` statements drop from 9 to 3; new alerts are inserted once and
  `publish()` is pipelined.
- Maintenance/correlation preparation reads gates, catalog and checkpoints in one
  snapshot (4 → 2 transactions per alert), loads rule conditions in one query and
  reuses warmed CEL programs.
- The source processor publishes one Kafka transaction per partition batch. A
  receipt up to 1 s ahead of the tenant clock is waited out without a fault (two
  assistant-selected design-register entries).
- Migration 0051 indexes unconfirmed source results for assignment catch-up. It
  blocks writes to that table while building, so it runs in the stopped-writer
  window.

Executed on the final code: per-repository unit suites (domain 463 with native CEL,
processor 26, api 139, indexer 65, ingestion 5, contracts 16, migrations 26 with
PostgreSQL, delivery 222). The delivery integration suite ran against local
PostgreSQL 18.2, Kafka 4.0 and Elasticsearch 9.0. Its remaining failures also fail
on unmodified HEAD code, come from tests sharing one database (they pass on a fresh
one), or need the absent `.local/0032_correlation_retention.sql.draft` fixture. The
5 delivery unit failures need Poetry inside delivery's venv.

Not executed: the S1–S4 smoke on the full stack (PowerShell 7 was unavailable, and
the owner chose to skip it), before/after load measurement, a restore drill, and
real-broker rebalances under load. PRs were opened 2026-10-19 for merge by the
owner. Deployment is not recorded here.

## 2026-10-11 Alerts page renamed Feed

The frontend sidebar, page title and breadcrumbs now use Feed. Alert details have
one Feed return breadcrumb, while saved-preset context remains. Existing resource
URLs and API contracts are unchanged; design and verification guidance reflect
the user correction.

Five targeted synthetic Chrome checks, TypeScript, changed-file formatting and
the isolated production build passed. Desktop and narrow renders were inspected.
See [executed evidence](../qualification/feed-rename-2026-10-11/README.md).
No dependency, migration or deployment changed.

## 2026-10-09 dashboard Count provenance removal

Removed the Count provenance button, details panel and reserved action row from
ordinary, narrow and fullscreen dashboard views. Count context/revision checks,
automatic/manual refresh, accessible status and visible stale/error feedback remain.
This supersedes the provenance presentation in the earlier text-cleanup entry.

Forty-seven affected synthetic Chrome checks, TypeScript, changed-file formatting
and the isolated production build passed. Desktop, 800px and 390px renders were
inspected; local API/UI checks returned HTTP 200. See
[current evidence](../qualification/dashboard-provenance-removal-2026-10-09/README.md).
No backend contract, dependency or migration changed.

## 2026-10-09 dashboard helper/status text cleanup

Removed the requested visible draft reminder, panel-opening hint, drag/resize help
and tenant/live/capture/revision line. Accessible help and hidden count status
remain; metadata stays available through Count provenance. Save/Discard, live
refresh, errors and stable panel positions continue.

Forty-seven affected synthetic Chrome checks, TypeScript, changed-file formatting
and the isolated production build passed. Desktop/narrow screenshots were inspected.
See [current evidence](../qualification/dashboard-text-cleanup-2026-10-09/README.md).
No API contract, dependency or migration changed.

## 2026-10-09 dashboard toolbar correction

Discard changes replaces Pause updates in the dashboard toolbar, with existing
dirty/locked guards and confirmation. Duplicate discard entries and Preview draft
counts were removed, including preview requests/state. Fullscreen has no pause
control; automatic and manual count refresh remain. Backend contracts are unchanged.

The affected synthetic Chrome suites passed 57 checks, followed by three targeted
new/history draft checks after hiding the empty actions menu. TypeScript, changed-file
formatting and the isolated production build passed. Desktop/narrow renders were
inspected; API/UI health returned HTTP 200. See
[toolbar evidence](../qualification/dashboard-toolbar-2026-10-09/README.md).

## 2026-10-08 fluid dashboard motion and upward compaction

Panels now follow drag/resize pointer movement in pixels, with a compacted drop
placeholder and smooth settling. Vertical gaps close after movement, resizing,
creation, deletion and reordering. Older sparse layouts display compacted for
editors, viewers and fullscreen; opening remains clean, while the next explicit
changed Save persists compacted coordinates. This supersedes preserving empty
vertical gaps in the earlier layout entry below.

The final synthetic Chrome suite passed 57 affected tests. TypeScript, changed-file
formatting, generated contract drift and isolated production build passed; final
browser/type/build receipts have matching frontend content. Screenshots at desktop,
800 and 390 pixels were inspected. Full repository formatting still reports 62
files outside this task. API/UI health checks returned HTTP 200. See
[executed checks and rendered evidence](../qualification/dashboard-fluid-2026-10-08/README.md).
No API contract, dependency, migration or production rollout changed.

## 2026-10-08 inline dashboard editing correction

The regular dashboard now provides name-click editing with a pen icon, a single
panel Create/Edit dialog, panel options including Delete, and direct dragging and
resizing. Save enables only for changed complete drafts and disables after Save or
reverting all changes. Cancel preserves the existing dashboard draft. New and
retained-history drafts use the same composer.

The final synthetic Chrome suite passed 46 affected tests, with type, changed-file
formatting, isolated production build and contract generation/drift checks passing.
Final design/delivery document checks passed. Full repository formatting
still reports 62 files outside this task. Production build and final document checks
are recorded in [current evidence](../qualification/dashboard-inline-2026-10-08/README.md).
No backend contract, dependency, migration or production deployment changed.


## 2026-10-08 dashboard panel dragging and resizing

The frontend dashboard editor supports drag grips, corner resize handles,
grid snapping and valid downward displacement, with keyboard controls and explicit
Save. Metadata edits preserve manually arranged positions. The existing API
layout/revision contracts remain in use; no migration or package change is needed.
Thirty-three synthetic browser checks, types, changed-file formatting and an
isolated production build passed. Repository-wide formatting still reports
66 unchanged baseline files. See [executed scope and screenshots](../qualification/dashboard-layout-2026-10-08/README.md).
Real-database persistence and production deployment were not requalified by this work.

## 2026-10-04 local startup repair

The independent-repository development stack now starts successfully with its
preserved credentials and data. The offline Kafka cutover completed after a
backup and verified legacy drain. The public Ingestion API is launched separately
from the Product API. An operator rotation proof bug was corrected and the local
demo operation completed normally. Fresh ES compatibility checks, the real
browser workflow, 17 v2 gateway checks and a three-alert v2 tenant-isolation
check passed. See [revisions, commands and limitations](../qualification/local-startup-2026-10-04.md).
This establishes local core execution only; refreshed automatic correlation,
maintenance, protected recovery and production qualification remain outstanding.

## 2026-10-01 repository CI audit

Reviewed all twelve organization repositories, including the retained monorepo.
The design checker now accepts REV-38 and its repair branch passes GitHub Actions.
The legacy monorepo workflow was disabled at the user's request. Contracts and
the Alembic migrations branch already passed their hosted checks.

Configured the approved read-only `APPCHI_REPOSITORIES_TOKEN` as a repository secret
in all eight private-dependency consumers. Frontend also required disabling
checkout credential persistence so npm uses the private contracts credential.
All eleven active repositories now have successful hosted runs at the recorded
revisions, including frontend's full build. Delivery main and its Alembic branch
also passed. Frontend/design fixes remain on their published repair branches.

Separate locked local environments passed 545 Python tests, with 65 delivery tests
skipped; lint, wheels, contracts, boundaries and documentation checks passed.
The credential expires 2026-12-30 and must be rotated in all eight consumers.
See [per-repository hosted evidence and changes](../qualification/ci-repair-2026-10-01.md).

## 2026-10-01 Alembic migration runner (REV-38)

`appchi-migrations` now executes its unchanged 44 SQL files through one Alembic
revision chain. Verified legacy adoption preserves checksum history/timestamps,
uses the original transaction/advisory lock, and rejects inconsistent histories.
Both migration ledgers deny API/worker access. Existing deployment entrypoints
remain; four delivery test fixtures now use the packaged migration directory.

Local verification passed 21 migration tests, 27 affected application SQL tests,
wheel execution outside the checkout, lint/build and a separate locked delivery
installation. The first consumer run's native-CEL availability failure and the
successful isolated rerun are recorded in [conversion evidence](../qualification/alembic-migrations.md).
Dependency/manifest pins identify the new migration and design commits. Publication,
image build and rollout remain separate; no existing application database was migrated.

## 2026-10-01 independent repositories (REV-37)

The application, packages, migrations, design and delivery tooling are split into
eleven independent repositories. See [split verification](../docs/repository-split-verification.md)
for executed evidence, the load-sensitive CEL rerun and remaining CI credential
setup. Production qualification remains unperformed.

## 2026-09-30 Product API live publishing (REV-36)

The Product API now owns background live hint publishing alongside its existing
Redis subscriber and SSE delivery. The separate live CLI role is rejected and
launchers/deployment guidance exclude it. Migration 0047 adds only minimal hint
outbox SELECT/DELETE policies for the API role; domain RLS remains unchanged.
Bounded publication runs outside the HTTP event loop and drains before pool
shutdown. Tests and remaining rollout qualification are recorded in
[worker-scope evidence](../docs/worker-scope-implementation.md). The running stack
was not restarted or migrated for this change.

## 2026-09-30 directory worker deferral (REV-35)

Automatic directory synchronization is nice-to-have. Continuous company/local
directory startup is rejected; local launchers start API/UI with a one-off trusted
identity bootstrap and provide an explicit Refresh action. The worker deployment
guidance, identity/access design and decision records now defer the automatic
five-minute LDAP-change propagation gate. Identity evidence, OIDC, current grants,
known revocations and session/restore safeguards remain in force. Executed checks
and limitations are recorded in [worker-scope evidence](../docs/worker-scope-implementation.md).
The running stack was not restarted and no company service was changed.

## 2026-09-30 worker scope correction

User-requested worker deferrals and synchronous operator provisioning are tracked
in [the implementation record](../docs/worker-scope-implementation.md), with the
normative correction in [worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md). Earlier Kafka-first
qualification remains historical evidence, not verification of this later change.
No shared service or running local application rollout was performed for this change.


### Kafka-first alert redesign â€” 2026-09-30

REV-33 implements stateless Kafka admission, per-receipt UUID/time preservation,
transactional changed-event/offset publication after PostgreSQL application,
Kafka-triggered correlation, inline maintenance effects, manual resolve invalidation,
versioned search reconciliation, packaged Kong policy enforcement and fenced offline
cutover. Forward migrations and generated HTTP/event/frontend contracts accompany
the code. All eight previously open technical decisions are explicitly labelled
assistant-selected in [the normative pipeline](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-kafka-alert-pipeline.md).

Executed evidence includes **530 backend unit passes (65 optional-service skips)**,
7 real Kafka/PG/ES protocol checks, 3 cutover checks, 8 search/rebuild checks,
69 operator/Kong checks, 57 recovery checks and 7 Chrome CEL checks. Maintenance
and API regression reports, later focused checks, actual failures/reruns and static
verification are listed in the [implementation record](../docs/kafka-first-implementation.md).
These suites overlap and are not a single aggregate test count.

Production qualification remains unperformed. Protected rollback reconstruction
was already incomplete and remains fenced, now explicitly requiring independent
Kafka delivery/retired-key evidence. Maintenance retention holds remain conservative
until replay boundaries are verified. Capacity, multi-node gateway propagation,
production clock/ACL/service bindings and the full fault matrix still need evidence.
The existing local application database was **not automatically cut over**; its
launcher requires the [offline cutover and renewed local evidence](../docs/runbooks/kafka-first.md).
Earlier dated runtime entries below describe their historical pipeline and must
not be read as current Kafka-first startup evidence. No production or shared company
service was changed.

### Quick sorting in alert and preset results â€” 2026-09-28

REV-32 adds a transient ascending/descending/regular-order cycle to server-sortable column headings in All alerts and saved preset results. The preset override sends a read-only candidate preview with one sort field and returns to the saved definition on the third click; neither path saves a preset. Sort changes restart pagination and clear selection. Derived and composite headings without a supported server sort remain static.

Frontend TypeScript and changed-file formatting passed. **13 focused Chrome browser checks passed** across preset columns and alert redesign, including request-cycle and cursor-reset regressions with controlled API responses. This is frontend verification; production qualification remains unperformed.

### Local correlation preview â€” 2026-09-27

The user asked to make correlation rule creation work in the isolated local stack. Assistant-selected REV-30 adds a finite, explicitly unqualified development preview profile. The API and correlation worker accept it only in `development`; source/evidence/epoch bindings and an audited local activation protect startup. The current local profile limits each tenant to two enabled rules, ten live groups per rule, 50 evidence rows per rule and 100 per tenant, plus finite CEL and digest ceilings. Maintenance remains gated and activation refuses any retained maintenance window or effect.

The focused disposable-PostgreSQL run passed **31 checks** covering configuration and automatic runtime, including a rule created and processed with the local-preview opt-in. Its JUnit report and scope/limit record are in `.local/runtime/correlation-preview-tests.xml` and `.local/runtime/correlation-preview-evidence.json`. Ruff and PowerShell parsing passed. The actual local database gate was activated with an audit entry; the API `/readyz` returned 200 and the source and correlation workers reported ready. No rule was created in the user's existing tenants during setup. The `indexer-hints` and `correlation-hints` Kafka consumers remain degraded on a quarantined historical `changed` offset whose authoritative outbox proof is missing; the PostgreSQL scan workers remain ready, and the local launcher reports these optional hints separately.

This preview is not DEL-03 capacity qualification: the 250/s 24-hour run, 500/s recovery, combined maintenance replay, supplied production allocation and service-failure evidence remain unperformed. Production/staging still reject the unqualified preview profile.

### Bare CEL field identifiers â€” 2026-09-27

CEL accepts allow-listed fields as bare identifiers such as `application` and `has(application)`. Assistant-selected compatibility keeps `alert.application` accepted for saved source; new editor suggestions, field chips, string-method signatures, `has()` completion, quick views and examples use bare names. Unknown identifiers and context-disallowed fields remain rejected. The change is additive within `appchi-cel-v1`; saved source is not rewritten and no data/index migration is needed (REV-29).

Frontend TypeScript checking, scoped Python lint and Markdown link/fence plus REV-number checks passed. Runtime test suites were not run for this request.

### Current alert JSON viewer â€” 2026-09-27

Added **View alert** to alert detail. It opens a native modal with the already loaded alert detail response in formatted, scrollable JSON. Readers can inspect it without editing permissions. Close, Escape and backdrop dismissal use native dialog behavior; showing JSON performs no fetch or state change. Updated alert/system/UI design, verification plan, decision history/register (REV-28). Scoped TypeScript checking passed; visual/browser runtime checks have not been run for this addition.

Implementation authorized 2026-09-22. **The entire initial release is not complete.** Local core workflows run and tests have executed; integration qualification and production readiness are separate. The user will deploy nonproduction in a private network unavailable to this agent. Missing code is distinguished below from unavailable environment evidence.

### Assignment-based alert status â€” 2026-09-27

Implemented REV-27 across alert detail, feeds/presets, bulk actions, incident links and maintenance summaries. One detail Assign menu offers self, eligible other users and unassignment. Current Status is Firing when unassigned and firing, Acknowledged when assigned and firing, and Resolved whenever resolved. Separate acknowledgement commands/fields are retired from current presentation. New occurrences clear assignment; resolution preserves it. Current command/replay presentation applies eligibility masking without rewriting receipts.

The API exposes virtual `display_status`; feed CEL lowers it to the existing lifecycle/assignee index fields. Legacy snapshots, history, physical fields and saved CEL/sort meanings remain compatible. New preset defaults and current legacy-column adaptation use a single Status column. No schema migration or index rebuild is required. Updated authoritative specifications, UI mapping, verification plan, decision history/register and generated OpenAPI/TypeScript contracts. See [verification evidence](../qualification/alert-ownership-2026-09-27/README.md).

Executed checks: TypeScript and scoped Python lint passed; 12 design documents passed 82 local-link checks, fence checks and sequential REV-01â€“REV-27 checks. The initial focused backend run had **114 passed, 4 failed** in 66.89 seconds: four existing CEL expression cases exceeded the configured one-second compiler deadline. All 11 new ownership cases passed in that run. Later response eligibility masking and updated browser/integration cases received static review; they were not rerun as runtime tests. A final read-only review reported no remaining findings. The recorded local API was restarted and `/livez` succeeded; the running frontend uses development reload. Browser, real PostgreSQL/Elasticsearch end-to-end and production qualification are not established by this change.

### Return to a saved preset from alert detail â€” 2026-09-27

Saved preset results now record their alert-list context before opening a row. The alert detail shows a Presets breadcrumb and Back to preset action, while Previous/Next follows the visible result page (REV-25). No runtime tests or production qualification were performed for this correction.

### Clickable preset list rows â€” 2026-09-27

The existing primary link on each preset row now covers the full row. Edit, History and the actions menu retain independent controls (REV-26). No runtime tests were performed for this correction.

### Unified local dependency Compose stack â€” 2026-09-27

Consolidated PostgreSQL 18.2, Elasticsearch 9.0.0, Redis 7.4.2, Kafka 4.0.0, Keycloak 26.7.4, Kong 3.9.1, Kong's PostgreSQL 16 database and Grafana 11.6.0 under [`deploy/local/compose.yaml`](../deploy/local/compose.yaml). The Python API, Next.js UI and workers remain editable host processes started by the local launcher. All container ports bind to loopback.

The existing Appchi database was exported locally and restored into the Compose PostgreSQL volume; 24 documents were copied from the prior local Elasticsearch projection. The previous native database and Elasticsearch data files remain available as rollback copies. All eight long-running Compose services report healthy and the Kong migration job completed. The API `/livez` returned HTTP 200; an unauthenticated session check returned its expected HTTP 401 instead of `database_unavailable`, all nine workers completed healthy turns, and the existing 16 local v2 gateway checks passed. The Appchi login page is at https://localhost:3443. This local stack does not qualify production or the initial release.

### Local authenticated v2 ingress â€” 2026-09-24

User-authorized local v2 setup now runs dedicated Kong 3.9.1/Grafana 11.6.0 containers and the existing pinned mTLS ingestion/provisioning paths. The local launcher creates `Local Demo Sender` through authenticated operator APIs, verifies the remote resources and serving-member identity, and bootstraps only the initial development gate. Production/shared databases and restore fences are rejected. The [local v2 guide](../docs/local-v2.md) records commands and bindings (REV-22).

Executed: **17 focused tests passed** (15 local bootstrap/proof tests and two real TLS transport tests; two existing dependency deprecation warnings), with JUnit at `qualification/pytest-local-v2.xml`. Ruff passed. **16 live gateway checks passed** after restart, covering missing/invalid/duplicate keys, query/body credentials, forged identity, public listener rejection, client-certificate enforcement, worker-only proof and Grafana Bearer conversion. Initial activation also verified that a prepared key remained rejected until normal saga completion.

Five synthetic native v2 alerts reached Local Demo. The final three-item batch was checked through authenticated receipt polling, authoritative detail and Elasticsearch-backed feed APIs; repeated submission returned the same receipt, and Global detail access returned 404. An earlier third sample was correctly rejected for an unsupported severity before the sample was fixed. Repeated Start and restart preserved the operator/key and remote volumes. Nonsecret live results are under `.local/operators`; no company service was contacted. This does not qualify production, fleet propagation, capacity, recovery or scheduled Grafana notification delivery.

### Stable dashboard refresh â€” 2026-09-24

Reproduced the user's incoming-alert layout jump: the tenant search-lag banner moved panels down 49 pixels before the count batch could add another warning. Routine search/count/suppression progress now uses existing status areas, and an in-flight count request no longer gives every retained panel a stale pattern (REV-21). The narrow status layout keeps the label readable. Actual search failures and blocked source processing retain explicit errors.

**28 Chrome browser checks passed in 40.2 seconds**, covering dashboard counts, live recovery, loading states and themes with controlled API responses. The new regression failed before the fix and now checks unchanged panel bounds through held refreshes, indexing lag, suppression progress and recovery in desktop, narrow and fullscreen views. TypeScript and changed-file formatting passed.

### Clickable alert rows â€” 2026-09-24

Expanded existing alert links across their rows in feeds, preset results/previews, incident linked alerts and maintenance summaries. Selection, row actions and incident links remain independently clickable; keyboard focus outlines the row.

TypeScript and changed-file formatting passed. **22 existing Chrome browser checks passed in 43.6 seconds** across redesign, preset columns, navigation, incident filters and maintenance editing with controlled API responses. A direct browser interaction check also verified non-link cells and row padding, selection, middle-click opening a new tab, keyboard Enter, incident-link navigation and preset rows both with and without a Message column. The default bundled Chromium run could not launch; the successful run used installed Chrome.

### User-selectable dark mode â€” 2026-09-24

Added a Light/Dark switch to the account menu and Appchi sign-in screen (REV-20).
The reference light palette remains the first-use default even when the OS prefers
dark. An explicit choice is stored locally and applied before page content paints,
including on navigation, reload and signed-out Appchi pages. Every reference CSS
color now has a dark counterpart across the shared shell and feature styles;
the preference stores no tenant or identity data. External Keycloak pages retain
their separate appearance.

TypeScript and the optimized production build passed. **6 focused Chrome browser
checks passed** against controlled API responses: 2 theme checks cover both
switch directions, signed-out use, colors, navigation and reload; 4 existing
navigation checks cover session preservation, tenant isolation, expiry and
expired-sign-in recovery. This is frontend verification, not production
qualification or an accessibility audit.

### Persistent workspace during navigation â€” 2026-09-24

Moved the session-owning workspace from the dynamic catch-all page to the shared
operational route-group layout (REV-19). Internal navigation now preserves that
mounted workspace instead of flashing the initial session-check screen. Tenant
content still resets on tenant changes; session checks, expiry and sign-out keep
their existing implementation. The public expired-sign-in page stays outside
the operational layout.

Both navigation/session-retention regressions failed before the correction.
**All 4 focused browser checks passed** afterward, covering the main pages,
administration tabs, Home, Back/Forward, tenant-content isolation during loading,
periodic session expiry and independent expired-sign-in recovery. Route type
generation, TypeScript and changed-file formatting passed.

A broader 52-case browser run did not complete: the reused test server stalled
with page-load timeouts. It was stopped after verifying its workspace command
and port 3100; a clean retry reported slow filesystem access and stalled during
startup, so that optional run was interrupted. Those broader checks are not
reported as passing for this change. The app service on port 3443 was left running.

### Stable loading and refresh states â€” 2026-09-24

Fixed confirmed empty states being replaced by loading text during background
refreshes on the seven main lists, saved preset results, and related notes/history
views. The maintenance timeline retains its completed empty message. Routine
Alerts refreshes no longer insert an unavailable warning above the table; actual
failures still show it. Saved preset empty results retain their completed query
identity, so changed filters cannot reuse the old empty-state claim (REV-18).

All seven list regressions failed before the correction. **48 browser checks
passed in 1.7 minutes**, including the loading-state suite, notes, history, audit,
incident filters and redesign checks. After adding saved-preset query identity,
**25 final browser checks passed in 44.8 seconds**: all 11 loading-state cases,
6 preset-column cases and 8 configuration/history cases. These overlapping runs
use controlled API responses and the actual frontend. They cover initial loading,
same-query empty-state positions, empty-to-populated updates, changed filters,
failed initial/refresh requests, retained rows and access-loss behavior.
TypeScript and changed-file Prettier checks passed. No service restart or data
migration is needed for this frontend correction.

### Preset columns on creation â€” 2026-09-24

Fixed a renderer shortcut that replaced a preset's initial configured columns
with composite Alert/Ownership cells. Explicit preset and editor column lists
now render each field in order, including the default selection. New forms use
the default order specified in preset section 3; existing saved orders are preserved.
This supersedes the default-preset grouping adaptation recorded in REV-14.

The creation regression failed before the fix. **6 focused browser tests passed
in 18.0 seconds**, covering initial/customized creation, reload, editor preview,
original saved defaults, sorting, draft changes and navigation/suppression with
Message hidden. These tests use controlled API responses. TypeScript and changed-file
Prettier checks passed. No backend or stored preset migration was needed.

### Username/password sign-in â€” 2026-09-24

At the user's request, Appchi login now requires only username and password.
The running isolated Keycloak application flow and enrollment settings were
updated, and the API was restarted with protected `pwd` authentication evidence.
Existing accounts, passwords and groups were preserved. Fresh setup and the
saved import use the same policy; the separate Keycloak console retains MFA.
The current [identity contract](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-identity-configuration.md) supersedes the
historical application TOTP behavior recorded in the September 23 results below.

Executed evidence: **5 real browser tests passed in 40.6 seconds**, covering all
three password-only account logins, tenant group roles, live membership changes
and signed provider logout. **29 OIDC unit tests passed** ([report](../qualification/pytest-password-login.xml))
and **2 local setup tests passed** ([report](../qualification/pytest-password-setup.xml)),
covering protected password claims, rejection of invalid claims, retained
configurable stronger policies, fresh realm setup and credential preservation.
Changed Python passed Ruff; TypeScript and changed browser-test formatting passed.
Company-service qualification and production deployment remain unperformed.

Sign-in recovery follow-up: a fresh local username/password sign-in succeeded
after the reported expired/consumed callback. HTML browser callbacks with an
expired, consumed or missing transaction now redirect to a public recovery page
with an explicit fresh sign-in link. The old code cannot create a session; JSON
clients retain 401. Stale authenticator-code text was removed from the login shell.
**11 real PostgreSQL session tests passed in 12.59 seconds** ([report](../qualification/pytest-login-recovery.xml)),
including a regression that failed before the fix. Ruff, TypeScript and changed-file
Prettier checks passed. The local API was restarted with the fix; the actual browser
reached the recovery page and completed a new username/password login from it.

### Running local core stack â€” 2026-09-23

Follow-up access fix: the interactive browser initially rejected the generated
certificate with `ERR_CERT_AUTHORITY_INVALID`. After explicit user approval,
the exact development certificate was added to Windows `CurrentUser\Root`.
Both services passed normal Windows HTTPS validation, and the Codex browser
displayed Appchi and Keycloak sign-in without an exception. This later approval
supersedes the earlier observation below that Windows trust was unchanged.
The launcher itself still does not modify OS trust.

At the user's request, the core services are running at **https://localhost:3443**:
real Keycloak, HTTPS Next.js, FastAPI, PostgreSQL 18.2, Elasticsearch 9.0.0,
Kafka 4.0.0 and Redis 7.4.2, with the directory worker and nine processing/hint
workers. The [local runtime guide](../docs/local-runtime.md) gives credentials,
start/stop/status commands, ports and prerequisites. This uses the existing
workspace installations/data; it is not a verified fresh-machine installer.

Executed evidence:

- **1 real browser acceptance test passed (19.5 seconds)** through password/TOTP,
  native v1 admission, source processing, real Elasticsearch search, connected
  live events, acknowledgement, notes and updated search. Safe identifiers are
  in `.local/runtime/browser-evidence.json`; synthetic alerts remain in Global.
- **2 actual Elasticsearch search/count compatibility tests passed (15.73
  seconds)**: [report](../qualification/pytest-local-runtime-es.xml).
- **24 focused backend tests passed (13.52 seconds)**, including restricted-role
  PostgreSQL search bootstrap and restore-fence preservation:
  [report](../qualification/pytest-local-runtime.xml).
- Repeated startup and all nine worker readiness checks passed. Changed Python
  passed Ruff; frontend TypeScript and the new browser test's formatting passed.

The live browser check exposed buffering by development proxy compression;
SSE responses now retain `Cache-Control: private, no-store, no-transform` through
the API middleware. The browser test verifies connection before continuing.
Windows probe reader locks no longer terminate durable workers, and launcher
health checks identify the Python child behind the Windows virtualenv launcher.
An initial duplicate test-module filename was corrected before the passing
combined backend run.

Assistant-selected local setup choices: reuse the existing isolated stores,
run Kafka/Redis in a separate Compose project, use loopback Kafka port 19092
because the original port is reserved, and activate only the initial search
gate after successful real compatibility evidence and strict local binding
checks. No production contracts or required capacity profiles were relaxed.
Maintenance, automatic correlation, v2/Kong/Grafana and protected
retention/recovery remain gated. The full suites, cold start after reboot,
OpenShift and private company-service qualification were not run in this increment.

### Local identity delivery â€” 2026-09-23

The user requested local Keycloak with custom users/groups instead of external
LDAP. This development exception is implemented in the
[local identity guide](../docs/local-keycloak.md). Docker 29.5.2 now runs isolated
Keycloak 26.7.4; its image is digest-pinned in the dedicated Compose file. A
native-user/group reader publishes through the existing guarded directory path
every 60 seconds. Three real password/TOTP accounts, separate API/worker database
logins, HTTPS startup/stop/status commands, persistent local credentials and a
`Local Demo` tenant are available. Production LDAP behavior remains unchanged.

Executed against the actual Keycloak, Next.js, FastAPI and PostgreSQL services:
**5 browser acceptance tests passed (59.8 seconds)**: three human sign-ins,
group-based admin/edit/view roles, live group removal/restoration and signed
provider logout through the container-to-host HTTPS callback. TOTP replay
protection remains enabled; tests wait for fresh codes. Backend and Keycloak
verify the explicitly supplied certificate. Browser tests use a test-only TLS
exception; Windows trust was not changed and interactive browsers may warn.

This supplies working local identity, not whole-application completion or
company-service qualification. Existing recovery, visibility, CEL timing and
other release limitations below remain open.

Focused backend verification: **22 passed (29.17 seconds)**, covering the new
adapter and real PostgreSQL publication; evidence is
[pytest-local-keycloak.xml](../qualification/pytest-local-keycloak.xml). Four
dependency deprecations and one pytest cache-permission warning were reported.
Changed Python files passed Ruff, and frontend TypeScript/Prettier checks passed.
The full application suites were not rerun for this identity-only increment.

## Dependency-ordered milestones

| Order | Acceptance criterion | Current status |
|---|---|---|
| 1 | Locked clean installs, startup, migrations, strict contracts, generated client and CI | Partial: local installs/build/migrations work; complete response/event contracts and release gates remain |
| 2 | Identity, tenancy, durable v1/v2 admission and operator credential isolation | Partial: SQL/API and executable remote adapter tests pass; actual SSO/Kong qualification pending |
| 3 | Alert lifecycle/history, native CEL and real ES parity, operational UI | Partial: real PG-to-ES-to-API flow passed; UX/observation completeness pending |
| 4 | Connected maintenance, incidents, correlation and protected recovery | Partial: automatic correlation, maintenance replay, protected alert/incident retirement and search rebuild tested; ordinary command/configuration cleanup implemented; rollback reconstruction remains |
| 5 | Complete operators, shared presets/dashboards and live/admin workflows | Partial: connected remote executor, definitions/history/counts/preview, audit and CEL editors implemented; complete UI/remote qualification ongoing |
| 6 | Full verification, measured profiles, release and support handoff | Local correctness evidence exists; external compatibility, security/load/recovery/rollout gates remain open |

## All 55 original issue IDs

Every row is **partial**; listed tests do not establish acceptance of the whole issue. Authoritative descriptions remain in [design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md). Test evidence is in [backend tests](../tests) and [browser tests](https://github.com/appchi-alert-operations/appchi-frontend).

| ID | Implemented entry point | Evidence and remaining acceptance |
|---|---|---|
| A1 | [Tenant create/rename and fixed Global](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/tenant_routes.py) | Restricted API-role tenant creation/replay passed; global administration create UI and browser checks added |
| A2 | [OIDC, LDAP nested graph and publication](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/identity) | Adapter/session and real PG checks; actual local Keycloak native-user MFA/group/logout acceptance passed; company LDAP/Keycloak qualification pending |
| A3 | [Trusted evidence and session guards](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/identity) | Outage/negative evidence tests; real five-minute propagation and restore pending |
| A4 | [Grants and assignee authorization](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/access.py) | PG role/continuity and assignment cleanup checks; actual directory propagation qualification pending |
| A5 | [Tenant isolation, CSRF and audit APIs](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/tenant_routes.py) | Isolation/admin-read and protected audit cleanup checks; wider privilege review pending |
| L1 | [Strict native validation and raw bytes](https://github.com/appchi-alert-operations/appchi-ingestion/tree/main/src/appchi/ingestion) | Native/HTTP validation tests pass |
| L2 | [Immutable identity and occurrences](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/alerts/lifecycle.py) | Real PG duplicate/conflict and lifecycle tests |
| L3 | [Durable admission, retry and capacity](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/ingestion/service.py) | Real PG concurrent caps/replay; complete transport/temporal qualification pending |
| L4 | [Manual revisions, no-ops and pending guards](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/alerts/routes.py) | Commands/bulk and maintenance-enabled command tests |
| L5 | [History, raw source and notes](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/alerts/history.py) | PG history tests; full render/read-your-writes protocols pending |
| L6 | [Inactivity and protected alert retirement](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/inactivity.py) | Expiry/intent/final/cancel and physical cleanup tests; rollback reconstruction remains |
| O1 | [Pinned mTLS and Grafana admission](https://github.com/appchi-alert-operations/appchi-ingestion/tree/main/src/appchi/ingestion) | Real TLS tests; Grafana provenance/partial batches/retry tests; installed Kong pending |
| O2 | [Operator SQL/API and durable jobs](https://github.com/appchi-alert-operations/appchi-api/tree/main/src/appchi/operators) | Ten PG workflow tests; executable bound executor and HTTP/PG ledger tests; installed-service qualification pending |
| O3 | [Prepared/current/retiring keys and erasure](https://github.com/appchi-alert-operations/appchi-api/tree/main/src/appchi/operators) | PG copy/rotate/revoke/generation tests; independent revocation restore fences incomplete |
| O4 | [Routing labels and remote capability/CAS adapter](https://github.com/appchi-alert-operations/appchi-api/tree/main/src/appchi/operators) | Controlled remote facts only; executable controlled-writer profile connected; installed Kong/Grafana qualification pending |
| Q1 | [Checked native CEL and ES translator](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/cel) | 63 CEL checks and actual ES parity; measured cost budgets pending |
| Q2 | [Typed field/predicate profile](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/cel) | Null/presence/string/time and catalog/editor/span checks; upgrade/capacity qualification pending |
| Q3 | [Suppression and exact count batches](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/configuration/count_routes.py) | Shared-PIT counts/preview and incident authoritative derived counts connected; private combined-load qualification pending |
| Q4 | [PIT pagination, sort and captured clock](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/search/reader.py) | Real ES/API tests; all browser query workflows pending |
| C1 | [Condition evidence and persisted rules](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/correlation) | Native core, PG configuration and automatic SQL runtime tests; qualified capacity missing |
| C2 | [Expiry, repeats and resets](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/correlation/evidence.py) | Durable historical replay and delayed maintenance qualifier tests; rollback restore still missing |
| C3 | [Eligibility, ownership and grouping](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/correlation) | Cross-resource runtime, concurrent consumer and context-boundary tests; private qualification pending |
| C4 | [Rule validation, diagnostics and budgets](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/correlation/routes.py) | PG/native validation and tests; full editor/capacity qualification pending |
| C5 | [Deterministic complete winner selection](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/correlation/evidence.py) | 150-alert pure core and 102-alert atomic PG attachment tests passed; sustained workload qualification missing |
| C6 | [Rule CRUD, epochs and histories](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/correlation/routes.py) | Revision/group reset and protected deletion/physical cleanup tested; exact retained-name discovery and no-revival holds tested; private qualification pending |
| C7 | [Incident and alert boundaries](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/incidents/routes.py) | Manual PG races and automatic context hooks, including deleted-origin resolve/reopen; restore qualification pending |
| C8 | [Durable journal and correlation state](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/correlation) | Automatic processing, exact replay and concurrent consumer tests; generalized recovery pending |
| M1 | [Maintenance SQL configuration and source bridge](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/maintenance) | Real source/manual and input-head-fenced warmup tests; atomic activation checks implemented; capacity qualification pending |
| M2 | [Temporal causes and delayed reconstruction](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/maintenance/temporal.py) | Overlap/delay/fan-out and exact correlation replay tests; private qualification pending |
| M3 | [Pinned timezone conversion and revisions](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/maintenance) | Timezone/API and DST-aware schedule editor tests pass; private timezone/deployment qualification pending |
| M4 | [Suppression with unchanged lifecycle](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/maintenance/runtime.py) | Source repeat/ack/no-op/note/inactivity tests; combined automatic incidents pending |
| M5 | [Durable bounded fan-out and qualifier effects](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/maintenance/processing.py) | SQL job/materialization/correction tests; atomic baseline activation tested; rendered observations/capacity pending |
| I1 | [Atomic manual incident creation](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/incidents/routes.py) | PG/API/browser and automatic creation/attachment checks; capacity qualification pending |
| I2 | [Incident status/cycles/assignment](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/incidents/routes.py) | PG lifecycle/reopen, automatic context reopen and assignment cleanup checks; private qualification pending |
| I3 | [Link/unlink/move and retained reads](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/incidents) | PG ownership/move/retired-reference tests |
| I4 | [Qualifying activity and inactivity](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/inactivity.py) | Real PG expiry plus ten protected retirement/cleanup tests; private recovery qualification remains |
| I5 | [Token search, counters, notes and history](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/incidents) | Note history and complete list filters/sorts implemented; 16 PG query tests and four focused browser cases pass; private qualification pending |
| D1 | [Immutable projections, journal and outbox](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/alerts/repository.py) | PG atomicity and referenced content digest verification; strict received/changed schemas and durable rebuild hold; broader consumer cleanup qualification pending |
| D2 | [Leased relay, receipt checkpoints and workers](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/pipeline.py) | Four real PG crash/fence/poison checks; actual Kafka transport test passed; Windows broker cleanup failed, full compatibility pending |
| D3 | [Independent evidence and alert retirement](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/recovery) | Failure-injection tests; real ES rebuild/cutover tested; restore fencing and checkpoint staging tested; evidence reconstruction/reopening missing |
| D4 | [Coordinator, locks, receipts and references](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/shared/transactions.py) | Real PG races; complete cross-domain qualification pending |
| D5 | [SQL migrations, partitions and RLS](https://github.com/appchi-alert-operations/appchi-migrations) | Migration/partition denial checks; least-privilege review and remaining families pending |
| R1 | [30-day inactivity and 90-day resolution lifetime](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/inactivity.py) | PG lifecycle/expiry checks; all-resource retirement missing |
| R2 | [Logical retirement and replay fences](https://github.com/appchi-alert-operations/appchi-domain/blob/main/src/appchi/recovery/retirement.py) | Alert/incident body and command cleanup tested; ordinary command and configuration/maintenance/rule cleanup implemented; rollback reconstruction remains |
| R3 | [Resource histories and audit APIs](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/configuration/read_routes.py) | Preset/dashboard event references, revision holds and deletion fences tested; audit filters/UI connected; other families ongoing |
| R4 | [Stable intent/final/cancel evidence IDs](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/recovery) | Cancellation/retry/race tests; generalized cleanup/restore missing |
| U1 | [Tenant-scoped product UI](https://github.com/appchi-alert-operations/appchi-frontend) | 79 controlled browser cases and fresh real HTTPS/PG browser flow passed; 11 later history/test cases passed; final integrated rerun pending; UX/accessibility qualification ongoing |
| U2 | [Shared native compiler and validation APIs](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/cel) | Native compiler/profile and connected autocomplete/span editor tests; remaining domain-test UX/qualification pending |
| U3 | [Shared presets, dashboards, references and counts](https://github.com/appchi-alert-operations/appchi-domain/tree/main/src/appchi/configuration) | PG definitions/references/protected delete/cleanup and real ES counts; history reuse UI and retained-only deleted discovery tested; full UX qualification pending |
| U4 | [Redis hints and coalesced PG/SSE reconciliation](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/live.py) | Registry/outbox and fetch-stream heartbeat tests; actual Redis, rendered observations and combined-load qualification pending |
| U5 | [Strict HTTP, receipts and opaque cursors](https://github.com/appchi-alert-operations/appchi-api/blob/main/src/appchi/main.py) | HTTP/PG tests; complete response/event types and remaining endpoints pending |
| N1 | [Development capacity profile](../qualification/development-capacity.json) | Explicitly unqualified; no sustained-load or allocation evidence |
| N2 | [Locked stack and workload artifacts](../deploy/) | Local install/build checks; separate migration/API/worker/network templates structurally checked; actual OpenShift validation and service bindings pending |
| N3 | [Readiness and private qualification handoff](../docs/runbooks/private-qualification.md) | Failure gates implemented; real outage/recovery/SLO evidence absent |
| N4 | [CI and executable verification](../legacy/monorepo-checks.yml) | Local evidence below; actual CI/release/security/rollout acceptance not run |

## Executed evidence and current checkpoint

Migrations **0001â€“0039**, with intentionally unused 0010, are final and checksummed. Do not edit published SQL. Migration 0039 preserves alert note author, revision actor and human activity display-name snapshots; legacy absent names remain unknown. Visibility input capture and authority fences are integrated, but rendered measurement is unfinished.

| Check | Most recent completed result |
|---|---|
| Full backend with PostgreSQL/Elasticsearch | **843 passed, 1 failed, 1 skipped in 1371.97 s**, through migration 0039. The failure is a native CEL one-second compiler deadline on `true == true`; the isolated 63-case CEL rerun had 62 passed and one further deadline failure on a different expression (32.35 s). Compiler timing remains unresolved; see `qualification/pytest-cel-rerun.xml`. The limit is unchanged. Full-run JUnit is `qualification/pytest-results.xml`; this is not an all-green result. |
| Alert detail and saved maintenance tester | 15 passed in 18.80 s, real PostgreSQL. One repeatable-read detail snapshot; unavailable unprovable suppression; independent permitted CEL test inputs. |
| Alert authorship/history/product | 11 passed in 16.95 s through 0039, real PostgreSQL, including rename between note creation/edit/delete and later reads. |
| Configuration pagination and retention | 26 passed in 28.82 s, real PostgreSQL; dashboard sort/ties/deleted metadata and generation-bound pagination. |
| Shared cursor and history regressions | 58 passed in 42.25 s; context changes return specified 409, malformed encoding stays 400. |
| Controlled Chrome browser suite | **97 passed, 1 real-backend case skipped in 4.1 min**, including both null-body authorization-loss cases. This is the complete final controlled rerun. |
| Separate real HTTPS browser workflow | **1 passed in 34.5 s**: Chrome â†’ Next.js â†’ FastAPI â†’ isolated PostgreSQL admission, acknowledgement and note persistence. Synthetic verified session exists only in the test fixture. |
| Frontend | Formatting, TypeScript and optimized Next.js build passed after the final null-error-body correction and both browser regressions. |
| Backend static/generated contracts | Ruff lint and formatting passed for all 215 Python files; OpenAPI and internal event schema checks passed. |
| Release inventory/manifest checker | 25 passed, 1 Windows symlink privilege case skipped in 5.21 s. Integrity checks do not establish release qualification. |
| Documentation | 47 Markdown files: local links, fences and all 55 original issue IDs passed. |

Real local services are PostgreSQL 18.2 at loopback 55432 and Elasticsearch 9.0.0 at loopback 59200. PostgreSQL concurrency and native CEL/Elasticsearch parity use the actual services. Isolated database/index fixtures clean up their generated resources. Remote-adapter unit tests use controlled responses and do not establish private-service compatibility.

The Kafka transport/offset check passed once; a later native Windows log-directory rename failure stopped the broker. Its latest full-suite test is skipped without a broker. Six Python dependency deprecation warnings remain. Docker and the isolated Keycloak container have now been exercised as described above; application container builds, actual CI execution and OpenShift execution remain unverified. No company LDAP/Keycloak, Kong, Grafana, Redis or independent recovery-evidence service qualification has run.

## Implemented in this continuation

- Dashboard counts coalesce requests, use compiler-derived relative-clock refresh, distinguish unavailable/stale/partial results, retry a changed definition once, and support pause and modal fullscreen keyboard behavior. Lists expose panel/editor metadata and bound ordering. Preset selection pages/searches beyond the first 200 entries while preserving chosen references.
- Deleted alert notes and maintenance/correlation definitions have retained-history discovery and pagination. Source JSON renders as plain text. Saved maintenance tests evaluate persisted UTC schedules, including ended windows, without rescheduling or creating causes.
- The shared frontend request helper preserves 401/403 status even when an error body is JSON null, so denial still clears protected drafts; both new Chrome regression cases passed in the full run.
- Alert detail derives suppression from one PostgreSQL snapshot and refuses an unprovable retained maintenance timeline. Note and activity authorship survives directory renames. Configuration cursor generation comparison and shared cursor error status were corrected.
- Bounded maintenance/correlation/configuration/operator-diagnostics cleanup, directory semantic authority fences and visibility input capture have real PostgreSQL regressions. Capture is prerequisite evidence, not a rendered-latency measurement.
- Restore begin/status/collect/assess remain fail-closed. Read-only assessment reports unresolved proof rather than opening gates. Strict permission/operator/credential authority descriptor and digest models have 64 unit checks, but are not connected producers; see the [authority integration plan](../docs/recovery-authority-implementation-plan.md).

## Next implementation dependencies

1. Diagnose intermittent native CEL compilation deadline failures without weakening the one-second bound; the isolated rerun also failed on a different expression.
2. Finish protected permission/credential authority producers, independent baseline/epoch coverage and restore reconstruction/reconciliation. Hashes cannot reconstruct lost grants; unknown credentials must stay invalidated. Existing collection and assessment cannot reopen capabilities.
3. Complete eligibility classification independent of fetch success, response-bound offers, browser post-render acknowledgements and explicitly configured telemetry handoff. Preserve missing/unclassified outcomes. The [visibility integration plan](../docs/visibility-observations-implementation-plan.md) records the next PostgreSQL detail slice and the separate search/count proof requirements.
4. Finish response-contract coverage and remaining diagnostics/accessibility review, then connect acceptance evidence to all specification scenarios. Existing browser checks are not a complete accessibility audit.
5. Run feasible release/security/container checks and supplied-environment compatibility, load, crash/rollback and rollout exercises. Sustained load, recovery guarantees and rolling production SLOs remain unqualified.

No capability is enabled merely to remove an unavailable response. Maintenance/correlation need complete temporal proof and qualified capacity; operator jobs retain pending/needs-attention without verified remote execution. Test fixtures opening gates are not deployment approval.

## Resume and run

Use the [development runbook](../docs/runbooks/development.md), [frontend verification](https://github.com/appchi-alert-operations/appchi-frontend/blob/main/docs/frontend.md), [local Elasticsearch runbook](../docs/runbooks/local-elasticsearch.md) and [private qualification](../docs/runbooks/private-qualification.md). The user will supply bindings inside the private network; no private endpoint or credential needs to be sent here.

```powershell
scripts/dev.ps1 -Action Install
scripts/dev.ps1 -Action Check
$env:APPCHI_TEST_DATABASE_URL='postgresql://appchi_migrator@127.0.0.1:55432/postgres?connect_timeout=5'
$env:APPCHI_IDENTITY_TEST_ADMIN_URL=$env:APPCHI_TEST_DATABASE_URL
$env:APPCHI_TEST_ELASTICSEARCH_URL='http://127.0.0.1:59200'
backend/.venv/Scripts/python.exe -m pytest backend/tests -q --basetemp=.local/pytest-final -p no:cacheprovider --junitxml=qualification/pytest-results.xml
backend/.venv/Scripts/ruff.exe check backend/appchi backend/tests
backend/.venv/Scripts/python.exe qualification/check_contracts.py
scripts/verify.ps1 -Suite Browser -BrowserChannel chrome
scripts/verify.ps1 -Suite Build
$env:APPCHI_REAL_BROWSER_DATABASE_ADMIN_URL=$env:APPCHI_TEST_DATABASE_URL
scripts/verify.ps1 -Suite BrowserReal -BrowserChannel chrome
```

Serialize the full native-CEL backend suite, Next build and browser runs on this workstation: compilation has a deliberate one-second deadline. Use only disposable synthetic test data. Normal interactive sign-in requires configured Keycloak and trusted directory publication; no development login bypass was added.

## Consequential decisions and release state

Assistant-selected implementation choices preserve the normative contracts: bounded child-process CEL compilation; immutable migration checksums; transport-derived private TLS identity; PostgreSQL leases/ordering/reconciliation; explicit service bindings and capacity allocations; historical names left unknown when no snapshot exists; and conservative unavailable states when proof is missing. [REV-06â€“REV-10](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md) record the API/implementation clarifications. No owner service versions, capacity guarantees, infrastructure retention or backup policy were invented.

**Locally runnable:** implemented workflows verified with isolated services and test-only synthetic identity; ordinary sign-in needs real identity configuration. **Integration-qualified:** no. **Production-ready:** no. Actual service bindings, trust/schema/version compatibility, independent recovery guarantees, measured allocations, container/security evidence and OpenShift rollout remain required. Production deployment and changes to shared services are neither authorized nor performed.

## UI reference implementation â€” 2026-09-24

The frontend now uses the user's supplied Option B full-system visual reference across the application shell, alerts, details, shared lists/editors/history, incidents, rules, maintenance, dashboards and administration. The [UI mapping](https://github.com/appchi-alert-operations/appchi-frontend/blob/main/docs/ui-design.md) records provenance and adaptations; REV-12 updates the visual contract. Warm light surfaces, local Geist fonts, icon navigation, compact tables and CEL controls, split editors/details, notes alongside history and threshold count cards replace the prior presentation. Quick views and inline alert commands use the existing API contracts.

Executed for this change with installed Microsoft Edge through Playwright 1.63.0: **100 passed, 7 skipped** in the complete 107-case browser run (3.0 minutes). The seven opt-in real-environment cases were not executed in that run. After the final action-column width and query-hint spacing adjustment, all **3 focused redesign checks passed** (10.7 seconds). `tsc --noEmit` passed, and the final isolated Next production build compiled, checked TypeScript and generated its routes successfully. These browser cases use intercepted synthetic data and do not qualify backend or identity integrations.

1440 Ã— 900 browser review included alerts, alert detail, presets and dashboards; a 390-pixel viewport check verified the shell did not overflow horizontally. [Review screenshots](../qualification/ui-redesign-2026-09-24/README.md) contain synthetic data from an isolated temporary API. The final alerts table measured equal scroll/client widths at desktop size. No claim of certified pixel equality across all 21 reference states, complete accessibility acceptance, production qualification or external Keycloak theme customization is made.

## UI redesign continuation â€” 2026-09-24

Resumed the supplied Claude Code session after its rate-limit interruption. The screen rebuild and most test updates were already present; this continuation completed integration verification and corrected a remaining preset regression. Custom saved/draft column choices now control the displayed fields and their order, with accurate sort indicators and mandatory navigation/suppression when Message is omitted. The reference's compact default layout is retained (assistant-selected adaptation recorded as REV-14). The saved-preview pagination test now captures the cursor request independently of concurrent quick-view count requests. Formatting left unfinished in the CEL editor tests was completed.

Fresh executed evidence after the correction:

| Check | Result |
|---|---|
| Complete controlled browser suite, installed Chrome / Playwright 1.63.0 | **111 passed, 7 skipped, 118 total in 3.4 minutes**. Skips require opt-in real backend/local identity environments; they were not executed in this continuation. |
| Focused custom-column and redesign regressions | **7 passed in 23.9 seconds**, including saved field ordering, unsaved removal/reordering, narrow-screen navigation/suppression without Message, observed mutation revisions and the fixed palette. The first two column checks were observed failing before the implementation fix. |
| Frontend static checks | Prettier and `tsc --noEmit` passed. |
| Isolated optimized production build | Next.js 16.3.5 compiled, completed TypeScript validation and generated its routes in `.next-codex-ui-build`. The original `tsconfig.json` and `next-env.d.ts` bytes were restored after Next's automatic output-path edits. |
| Display-name enrichment unit checks | **5 passed in 8.35 seconds**; four existing dependency deprecation warnings. |
| Generated contracts | OpenAPI and internal event schemas match the current application models. |

The browser suite reused the controlled server on loopback port 3100. Manual preset/editor review used the existing isolated synthetic API/frontend on port 3190 and the mock screenshots recovered from the attached session's scratch files. Fresh 1440 Ã— 900 screenshots, the [browser output](../qualification/ui-redesign-2026-09-24/browser-tests.txt) and [build output](../qualification/ui-redesign-2026-09-24/production-build.txt) are retained with the [review evidence](../qualification/ui-redesign-2026-09-24/README.md). This supersedes the earlier UI browser checkpoint above. These fixtures do not qualify real integrations or production behavior, and this continuation did not repeat a certified pixel comparison of every artboard. REV-13's incident-activity actor-name gap remains: captured historical names are not present and are not reconstructed from today's directory.

## Approved login theme â€” 2026-09-27

Implemented the approved Appchi password form as a client-specific Keycloak 26.7.4
theme (REV-23), with local Geist, light/dark appearance, password visibility,
provider errors and directory-support help. The unified local Compose file mounts
the theme read-only; fresh imports and the existing-installation configuration
step select it only for `appchi-web`. Initial unauthenticated entry starts the
OIDC flow with the requested local path. Signed-out and expired-sign-in recovery
remain explicit; authenticated workspace navigation retains its mounted session.

Executed evidence for this change:

- Recreated only the isolated local Keycloak service and applied the client theme;
  its HTTPS discovery and configuration step completed successfully.
- Opened `/auth/login?fresh=true` in Chrome and observed the rendered branded
  Keycloak form. [Rendered form](../qualification/login-theme-2026-09-27/login.png).
- Frontend `tsc --noEmit --incremental false`, Python Ruff lint for
  `scripts/local_identity.py`, and changed-file diff whitespace checks passed.
- Formatted the changed frontend component, theme CSS/JavaScript and Python helper.

No automated tests were added or run for this change, and no credential submission
was performed during the visual preview. End-to-end authentication regression and
production qualification remain unperformed for this theme. Shared company
Keycloak infrastructure was not modified.

## Appearance switch â€” 2026-09-27

Replaced the signed-out and account-menu text appearance buttons with one
accessible sun/moon switch component. It retains the user's saved theme and the
light default. Automated tests were not run for this UI update.

## Private OpenShift load-test handoff â€” 2026-10-05

Added the [deployment and testing guide](../docs/runbooks/openshift-load-testing.md),
staged infrastructure/application rendering, native test-user preparation and k6
workloads. Service Dockerfiles now include runtime checks and required Linux CEL
libraries; Kong/theme and PostgreSQL/Redis/generator images have build recipes.
[Executed evidence and limitations](../qualification/openshift-load-test-handoff.md)
distinguish local checks from unexecuted private-cluster qualification. The
[subsequent initialization implementation](../qualification/cluster-initialization-2026-10-05.md)
adds explicit initial-development search activation and a private operator proof
helper, with Jobs/workloads and operating instructions. Execute them against the
actual bindings before search/UI/v2 load; capability and recovery gates remain.
## Scalability implementation handoff â€” 2026-10-07

The user requested implementation of the system review for 5,000 alert deliveries
per minute, 100 active users and 50 tenants. The current uncommitted editable
source combination adds bounded bulk indexing, delta reconciliation with separate
repair-audit progress, tenant retry isolation, incremental maintenance continuation,
scoped/differential correlation work, batched quick counts, PIT disposal, stable
activity/live behavior, off-thread authorization SQL and aggregate request budgets.
Forward migrations 0048 through 0050 are required before the updated writers. Original
published migration bytes remain unchanged.

The [implementation evidence](../qualification/scalability-implementation-2026-10-06.md)
records commands, successful checks, corrected regressions and measured limits.
Testing stopped at the user's request. Coordinated units passed 780 cases with
65 skips; the final narrow clock correction passed 14 focused cases. Migration
and real-search checks passed 26 cases, and controlled browser checks passed
144 cases with seven opt-in skips. The production frontend build passed. Final
local startup completed, including 17 live gateway/authorization checks. The
planned real-browser OIDC/alert/note scenarios were not run before testing stopped.
The requested-rate cohort failed latency, and the final 100-user/50-tenant
diagnostic at two alerts/second still returned 195 errors in 382 reads despite
exact final alert counts. No sustainable maximum or passing target capacity was
established. All temporarily paused BI containers were restored.
No DEL gate, immutable release, company-service qualification or production
readiness is claimed. The existing 250/second sustained and 500/second recovery
requirements and deferred worker scope remain.

Source publication was subsequently authorized. Coordinated
`codex/scalability-hardening` branches, immutable dependency pins and the workspace
manifest are recorded in [publication evidence](evidence/scalability-publication.json).
Pre-existing unrelated work remains local. No additional tests were run; this is
source publication, not a merged or qualified deployment.

## Python tooling migration — 2026-10-12

At the user's request, all nine Python repositories, active scripts, documentation,
CI and service container builds now use Poetry 2.3.1. Native-generated locks preserve
the prior versions, Git pins and original artifact hashes. All nine isolated installs,
wheel builds and Ruff checks passed; final owned units report 813 passed and 65
unbound real-service skips. Contracts, import boundaries, documents and Docker
static checks passed. Existing running environments and unrelated work were preserved.
The [migration evidence](../qualification/poetry-migration-2026-10-12.md) records
initial harness/environment failures and corrected reruns. Source publication was subsequently authorized; the workspace manifest identifies
the coordinated sibling revisions. Runtime package pins, DEL gates and production
qualification remain unchanged. The report distinguishes full working-tree tests
from exact staged-tree publication checks.
