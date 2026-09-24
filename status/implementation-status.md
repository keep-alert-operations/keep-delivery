# Implementation status

Implementation authorized 2026-09-22. **The entire initial release is not complete.** Local core workflows run and tests have executed; integration qualification and production readiness are separate. The user will deploy nonproduction in a private network unavailable to this agent. Missing code is distinguished below from unavailable environment evidence.

### User-selectable dark mode — 2026-09-24

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

### Persistent workspace during navigation — 2026-09-24

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

### Stable loading and refresh states — 2026-09-24

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

### Preset columns on creation — 2026-09-24

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

### Username/password sign-in — 2026-09-24

At the user's request, Appchi login now requires only username and password.
The running isolated Keycloak application flow and enrollment settings were
updated, and the API was restarted with protected `pwd` authentication evidence.
Existing accounts, passwords and groups were preserved. Fresh setup and the
saved import use the same policy; the separate Keycloak console retains MFA.
The current [identity contract](spec-identity-configuration.md) supersedes the
historical application TOTP behavior recorded in the September 23 results below.

Executed evidence: **5 real browser tests passed in 40.6 seconds**, covering all
three password-only account logins, tenant group roles, live membership changes
and signed provider logout. **29 OIDC unit tests passed** ([report](qualification/pytest-password-login.xml))
and **2 local setup tests passed** ([report](qualification/pytest-password-setup.xml)),
covering protected password claims, rejection of invalid claims, retained
configurable stronger policies, fresh realm setup and credential preservation.
Changed Python passed Ruff; TypeScript and changed browser-test formatting passed.
Company-service qualification and production deployment remain unperformed.

Sign-in recovery follow-up: a fresh local username/password sign-in succeeded
after the reported expired/consumed callback. HTML browser callbacks with an
expired, consumed or missing transaction now redirect to a public recovery page
with an explicit fresh sign-in link. The old code cannot create a session; JSON
clients retain 401. Stale authenticator-code text was removed from the login shell.
**11 real PostgreSQL session tests passed in 12.59 seconds** ([report](qualification/pytest-login-recovery.xml)),
including a regression that failed before the fix. Ruff, TypeScript and changed-file
Prettier checks passed. The local API was restarted with the fix; the actual browser
reached the recovery page and completed a new username/password login from it.

### Running local core stack — 2026-09-23

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
workers. The [local runtime guide](docs/local-runtime.md) gives credentials,
start/stop/status commands, ports and prerequisites. This uses the existing
workspace installations/data; it is not a verified fresh-machine installer.

Executed evidence:

- **1 real browser acceptance test passed (19.5 seconds)** through password/TOTP,
  native v1 admission, source processing, real Elasticsearch search, connected
  live events, acknowledgement, notes and updated search. Safe identifiers are
  in `.local/runtime/browser-evidence.json`; synthetic alerts remain in Global.
- **2 actual Elasticsearch search/count compatibility tests passed (15.73
  seconds)**: [report](qualification/pytest-local-runtime-es.xml).
- **24 focused backend tests passed (13.52 seconds)**, including restricted-role
  PostgreSQL search bootstrap and restore-fence preservation:
  [report](qualification/pytest-local-runtime.xml).
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

### Local identity delivery — 2026-09-23

The user requested local Keycloak with custom users/groups instead of external
LDAP. This development exception is implemented in the
[local identity guide](docs/local-keycloak.md). Docker 29.5.2 now runs isolated
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
[pytest-local-keycloak.xml](qualification/pytest-local-keycloak.xml). Four
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

Every row is **partial**; listed tests do not establish acceptance of the whole issue. Authoritative descriptions remain in [design register](design-register.md). Test evidence is in [backend tests](backend/tests) and [browser tests](frontend/tests).

| ID | Implemented entry point | Evidence and remaining acceptance |
|---|---|---|
| A1 | [Tenant create/rename and fixed Global](backend/appchi/tenant_routes.py) | Restricted API-role tenant creation/replay passed; global administration create UI and browser checks added |
| A2 | [OIDC, LDAP nested graph and publication](backend/appchi/identity/) | Adapter/session and real PG checks; actual local Keycloak native-user MFA/group/logout acceptance passed; company LDAP/Keycloak qualification pending |
| A3 | [Trusted evidence and session guards](backend/appchi/identity/) | Outage/negative evidence tests; real five-minute propagation and restore pending |
| A4 | [Grants and assignee authorization](backend/appchi/access.py) | PG role/continuity and assignment cleanup checks; actual directory propagation qualification pending |
| A5 | [Tenant isolation, CSRF and audit APIs](backend/appchi/tenant_routes.py) | Isolation/admin-read and protected audit cleanup checks; wider privilege review pending |
| L1 | [Strict native validation and raw bytes](backend/appchi/ingestion/) | Native/HTTP validation tests pass |
| L2 | [Immutable identity and occurrences](backend/appchi/alerts/lifecycle.py) | Real PG duplicate/conflict and lifecycle tests |
| L3 | [Durable admission, retry and capacity](backend/appchi/ingestion/service.py) | Real PG concurrent caps/replay; complete transport/temporal qualification pending |
| L4 | [Manual revisions, no-ops and pending guards](backend/appchi/alerts/routes.py) | Commands/bulk and maintenance-enabled command tests |
| L5 | [History, raw source and notes](backend/appchi/alerts/history.py) | PG history tests; full render/read-your-writes protocols pending |
| L6 | [Inactivity and protected alert retirement](backend/appchi/inactivity.py) | Expiry/intent/final/cancel and physical cleanup tests; rollback reconstruction remains |
| O1 | [Pinned mTLS and Grafana admission](backend/appchi/ingestion/) | Real TLS tests; Grafana provenance/partial batches/retry tests; installed Kong pending |
| O2 | [Operator SQL/API and durable jobs](backend/appchi/operators/) | Ten PG workflow tests; executable bound executor and HTTP/PG ledger tests; installed-service qualification pending |
| O3 | [Prepared/current/retiring keys and erasure](backend/appchi/operators/) | PG copy/rotate/revoke/generation tests; independent revocation restore fences incomplete |
| O4 | [Routing labels and remote capability/CAS adapter](backend/appchi/operators/) | Controlled remote facts only; executable controlled-writer profile connected; installed Kong/Grafana qualification pending |
| Q1 | [Checked native CEL and ES translator](backend/appchi/cel/) | 63 CEL checks and actual ES parity; measured cost budgets pending |
| Q2 | [Typed field/predicate profile](backend/appchi/cel/) | Null/presence/string/time and catalog/editor/span checks; upgrade/capacity qualification pending |
| Q3 | [Suppression and exact count batches](backend/appchi/configuration/count_routes.py) | Shared-PIT counts/preview and incident authoritative derived counts connected; private combined-load qualification pending |
| Q4 | [PIT pagination, sort and captured clock](backend/appchi/search/reader.py) | Real ES/API tests; all browser query workflows pending |
| C1 | [Condition evidence and persisted rules](backend/appchi/correlation/) | Native core, PG configuration and automatic SQL runtime tests; qualified capacity missing |
| C2 | [Expiry, repeats and resets](backend/appchi/correlation/evidence.py) | Durable historical replay and delayed maintenance qualifier tests; rollback restore still missing |
| C3 | [Eligibility, ownership and grouping](backend/appchi/correlation/) | Cross-resource runtime, concurrent consumer and context-boundary tests; private qualification pending |
| C4 | [Rule validation, diagnostics and budgets](backend/appchi/correlation/routes.py) | PG/native validation and tests; full editor/capacity qualification pending |
| C5 | [Deterministic complete winner selection](backend/appchi/correlation/evidence.py) | 150-alert pure core and 102-alert atomic PG attachment tests passed; sustained workload qualification missing |
| C6 | [Rule CRUD, epochs and histories](backend/appchi/correlation/routes.py) | Revision/group reset and protected deletion/physical cleanup tested; exact retained-name discovery and no-revival holds tested; private qualification pending |
| C7 | [Incident and alert boundaries](backend/appchi/incidents/routes.py) | Manual PG races and automatic context hooks, including deleted-origin resolve/reopen; restore qualification pending |
| C8 | [Durable journal and correlation state](backend/appchi/correlation/) | Automatic processing, exact replay and concurrent consumer tests; generalized recovery pending |
| M1 | [Maintenance SQL configuration and source bridge](backend/appchi/maintenance/) | Real source/manual and input-head-fenced warmup tests; atomic activation checks implemented; capacity qualification pending |
| M2 | [Temporal causes and delayed reconstruction](backend/appchi/maintenance/temporal.py) | Overlap/delay/fan-out and exact correlation replay tests; private qualification pending |
| M3 | [Pinned timezone conversion and revisions](backend/appchi/maintenance/) | Timezone/API and DST-aware schedule editor tests pass; private timezone/deployment qualification pending |
| M4 | [Suppression with unchanged lifecycle](backend/appchi/maintenance/runtime.py) | Source repeat/ack/no-op/note/inactivity tests; combined automatic incidents pending |
| M5 | [Durable bounded fan-out and qualifier effects](backend/appchi/maintenance/processing.py) | SQL job/materialization/correction tests; atomic baseline activation tested; rendered observations/capacity pending |
| I1 | [Atomic manual incident creation](backend/appchi/incidents/routes.py) | PG/API/browser and automatic creation/attachment checks; capacity qualification pending |
| I2 | [Incident status/cycles/assignment](backend/appchi/incidents/routes.py) | PG lifecycle/reopen, automatic context reopen and assignment cleanup checks; private qualification pending |
| I3 | [Link/unlink/move and retained reads](backend/appchi/incidents/) | PG ownership/move/retired-reference tests |
| I4 | [Qualifying activity and inactivity](backend/appchi/inactivity.py) | Real PG expiry plus ten protected retirement/cleanup tests; private recovery qualification remains |
| I5 | [Token search, counters, notes and history](backend/appchi/incidents/) | Note history and complete list filters/sorts implemented; 16 PG query tests and four focused browser cases pass; private qualification pending |
| D1 | [Immutable projections, journal and outbox](backend/appchi/alerts/repository.py) | PG atomicity and referenced content digest verification; strict received/changed schemas and durable rebuild hold; broader consumer cleanup qualification pending |
| D2 | [Leased relay, receipt checkpoints and workers](backend/appchi/pipeline.py) | Four real PG crash/fence/poison checks; actual Kafka transport test passed; Windows broker cleanup failed, full compatibility pending |
| D3 | [Independent evidence and alert retirement](backend/appchi/recovery/) | Failure-injection tests; real ES rebuild/cutover tested; restore fencing and checkpoint staging tested; evidence reconstruction/reopening missing |
| D4 | [Coordinator, locks, receipts and references](backend/appchi/shared/transactions.py) | Real PG races; complete cross-domain qualification pending |
| D5 | [SQL migrations, partitions and RLS](migrations/) | Migration/partition denial checks; least-privilege review and remaining families pending |
| R1 | [30-day inactivity and 90-day resolution lifetime](backend/appchi/inactivity.py) | PG lifecycle/expiry checks; all-resource retirement missing |
| R2 | [Logical retirement and replay fences](backend/appchi/recovery/retirement.py) | Alert/incident body and command cleanup tested; ordinary command and configuration/maintenance/rule cleanup implemented; rollback reconstruction remains |
| R3 | [Resource histories and audit APIs](backend/appchi/configuration/read_routes.py) | Preset/dashboard event references, revision holds and deletion fences tested; audit filters/UI connected; other families ongoing |
| R4 | [Stable intent/final/cancel evidence IDs](backend/appchi/recovery/) | Cancellation/retry/race tests; generalized cleanup/restore missing |
| U1 | [Tenant-scoped product UI](frontend/) | 79 controlled browser cases and fresh real HTTPS/PG browser flow passed; 11 later history/test cases passed; final integrated rerun pending; UX/accessibility qualification ongoing |
| U2 | [Shared native compiler and validation APIs](backend/appchi/cel/) | Native compiler/profile and connected autocomplete/span editor tests; remaining domain-test UX/qualification pending |
| U3 | [Shared presets, dashboards, references and counts](backend/appchi/configuration/) | PG definitions/references/protected delete/cleanup and real ES counts; history reuse UI and retained-only deleted discovery tested; full UX qualification pending |
| U4 | [Redis hints and coalesced PG/SSE reconciliation](backend/appchi/live.py) | Registry/outbox and fetch-stream heartbeat tests; actual Redis, rendered observations and combined-load qualification pending |
| U5 | [Strict HTTP, receipts and opaque cursors](backend/appchi/main.py) | HTTP/PG tests; complete response/event types and remaining endpoints pending |
| N1 | [Development capacity profile](qualification/development-capacity.json) | Explicitly unqualified; no sustained-load or allocation evidence |
| N2 | [Locked stack and workload artifacts](deploy/) | Local install/build checks; separate migration/API/worker/network templates structurally checked; actual OpenShift validation and service bindings pending |
| N3 | [Readiness and private qualification handoff](docs/runbooks/private-qualification.md) | Failure gates implemented; real outage/recovery/SLO evidence absent |
| N4 | [CI and executable verification](.github/workflows/checks.yml) | Local evidence below; actual CI/release/security/rollout acceptance not run |

## Executed evidence and current checkpoint

Migrations **0001–0039**, with intentionally unused 0010, are final and checksummed. Do not edit published SQL. Migration 0039 preserves alert note author, revision actor and human activity display-name snapshots; legacy absent names remain unknown. Visibility input capture and authority fences are integrated, but rendered measurement is unfinished.

| Check | Most recent completed result |
|---|---|
| Full backend with PostgreSQL/Elasticsearch | **843 passed, 1 failed, 1 skipped in 1371.97 s**, through migration 0039. The failure is a native CEL one-second compiler deadline on `true == true`; the isolated 63-case CEL rerun had 62 passed and one further deadline failure on a different expression (32.35 s). Compiler timing remains unresolved; see `qualification/pytest-cel-rerun.xml`. The limit is unchanged. Full-run JUnit is `qualification/pytest-results.xml`; this is not an all-green result. |
| Alert detail and saved maintenance tester | 15 passed in 18.80 s, real PostgreSQL. One repeatable-read detail snapshot; unavailable unprovable suppression; independent permitted CEL test inputs. |
| Alert authorship/history/product | 11 passed in 16.95 s through 0039, real PostgreSQL, including rename between note creation/edit/delete and later reads. |
| Configuration pagination and retention | 26 passed in 28.82 s, real PostgreSQL; dashboard sort/ties/deleted metadata and generation-bound pagination. |
| Shared cursor and history regressions | 58 passed in 42.25 s; context changes return specified 409, malformed encoding stays 400. |
| Controlled Chrome browser suite | **97 passed, 1 real-backend case skipped in 4.1 min**, including both null-body authorization-loss cases. This is the complete final controlled rerun. |
| Separate real HTTPS browser workflow | **1 passed in 34.5 s**: Chrome → Next.js → FastAPI → isolated PostgreSQL admission, acknowledgement and note persistence. Synthetic verified session exists only in the test fixture. |
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
- Restore begin/status/collect/assess remain fail-closed. Read-only assessment reports unresolved proof rather than opening gates. Strict permission/operator/credential authority descriptor and digest models have 64 unit checks, but are not connected producers; see the [authority integration plan](docs/recovery-authority-implementation-plan.md).

## Next implementation dependencies

1. Diagnose intermittent native CEL compilation deadline failures without weakening the one-second bound; the isolated rerun also failed on a different expression.
2. Finish protected permission/credential authority producers, independent baseline/epoch coverage and restore reconstruction/reconciliation. Hashes cannot reconstruct lost grants; unknown credentials must stay invalidated. Existing collection and assessment cannot reopen capabilities.
3. Complete eligibility classification independent of fetch success, response-bound offers, browser post-render acknowledgements and explicitly configured telemetry handoff. Preserve missing/unclassified outcomes. The [visibility integration plan](docs/visibility-observations-implementation-plan.md) records the next PostgreSQL detail slice and the separate search/count proof requirements.
4. Finish response-contract coverage and remaining diagnostics/accessibility review, then connect acceptance evidence to all specification scenarios. Existing browser checks are not a complete accessibility audit.
5. Run feasible release/security/container checks and supplied-environment compatibility, load, crash/rollback and rollout exercises. Sustained load, recovery guarantees and rolling production SLOs remain unqualified.

No capability is enabled merely to remove an unavailable response. Maintenance/correlation need complete temporal proof and qualified capacity; operator jobs retain pending/needs-attention without verified remote execution. Test fixtures opening gates are not deployment approval.

## Resume and run

Use the [development runbook](docs/runbooks/development.md), [frontend verification](docs/frontend.md), [local Elasticsearch runbook](docs/runbooks/local-elasticsearch.md) and [private qualification](docs/runbooks/private-qualification.md). The user will supply bindings inside the private network; no private endpoint or credential needs to be sent here.

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

Assistant-selected implementation choices preserve the normative contracts: bounded child-process CEL compilation; immutable migration checksums; transport-derived private TLS identity; PostgreSQL leases/ordering/reconciliation; explicit service bindings and capacity allocations; historical names left unknown when no snapshot exists; and conservative unavailable states when proof is missing. [REV-06–REV-10](design-register.md) record the API/implementation clarifications. No owner service versions, capacity guarantees, infrastructure retention or backup policy were invented.

**Locally runnable:** implemented workflows verified with isolated services and test-only synthetic identity; ordinary sign-in needs real identity configuration. **Integration-qualified:** no. **Production-ready:** no. Actual service bindings, trust/schema/version compatibility, independent recovery guarantees, measured allocations, container/security evidence and OpenShift rollout remain required. Production deployment and changes to shared services are neither authorized nor performed.

## UI reference implementation — 2026-09-24

The frontend now uses the user's supplied Option B full-system visual reference across the application shell, alerts, details, shared lists/editors/history, incidents, rules, maintenance, dashboards and administration. The [UI mapping](docs/ui-design.md) records provenance and adaptations; REV-12 updates the visual contract. Warm light surfaces, local Geist fonts, icon navigation, compact tables and CEL controls, split editors/details, notes alongside history and threshold count cards replace the prior presentation. Quick views and inline alert commands use the existing API contracts.

Executed for this change with installed Microsoft Edge through Playwright 1.63.0: **100 passed, 7 skipped** in the complete 107-case browser run (3.0 minutes). The seven opt-in real-environment cases were not executed in that run. After the final action-column width and query-hint spacing adjustment, all **3 focused redesign checks passed** (10.7 seconds). `tsc --noEmit` passed, and the final isolated Next production build compiled, checked TypeScript and generated its routes successfully. These browser cases use intercepted synthetic data and do not qualify backend or identity integrations.

1440 × 900 browser review included alerts, alert detail, presets and dashboards; a 390-pixel viewport check verified the shell did not overflow horizontally. [Review screenshots](qualification/ui-redesign-2026-09-24/README.md) contain synthetic data from an isolated temporary API. The final alerts table measured equal scroll/client widths at desktop size. No claim of certified pixel equality across all 21 reference states, complete accessibility acceptance, production qualification or external Keycloak theme customization is made.

## UI redesign continuation — 2026-09-24

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

The browser suite reused the controlled server on loopback port 3100. Manual preset/editor review used the existing isolated synthetic API/frontend on port 3190 and the mock screenshots recovered from the attached session's scratch files. Fresh 1440 × 900 screenshots, the [browser output](qualification/ui-redesign-2026-09-24/browser-tests.txt) and [build output](qualification/ui-redesign-2026-09-24/production-build.txt) are retained with the [review evidence](qualification/ui-redesign-2026-09-24/README.md). This supersedes the earlier UI browser checkpoint above. These fixtures do not qualify real integrations or production behavior, and this continuation did not repeat a certified pixel comparison of every artboard. REV-13's incident-activity actor-name gap remains: captured historical names are not present and are not reconstructed from today's directory.
