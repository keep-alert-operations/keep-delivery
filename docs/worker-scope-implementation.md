# Worker scope implementation record

**Later correction — REV-35:** automatic directory synchronization is now
nice-to-have. The earlier REV-34 identity statements below are historical;
[current worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md#4-directory-synchronization-is-nice-to-have)
governs explicit publication and deferred propagation.

Implemented 2026-09-30 following the user's corrections and explicit clarification
that the next source update may clear ended maintenance suppression. This is
delivery evidence, not a runtime contract. [Worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md)
is authoritative and registered as REV-34. Existing uncommitted work was preserved.

## Delivered changes

- Maintenance timer release is deferred. End/control boundaries no longer clear
  suppression through reads or unrelated edits. The next qualifying source update,
  including an unchanged repeat, closes the ended cause at its receipt boundary.
  Overlapping causes, source suspension, delayed input, historical proof and
  no-backfill behavior remain covered by the reducer and transaction path.
- Migration 0046 records the source-release cutover and obsoletes pending maintenance
  jobs without deleting evidence. Controls no longer enqueue release fan-out.
- CLI and local launchers exclude maintenance, assignment cleanup, inactivity,
  retention and operator background workers. Their retained internal engines are
  future component references, not deployable current roles. Authorization, assignee
  masking, read-time access windows and previously retired-resource fences remain.
- Operator mutations now drive the recorded operation inside the HTTP request.
  Creation returns 201 and other provisioning mutations return 200 only after
  verification. Incomplete work returns explicit failure with recovery identifiers.
  Durable progress, leases, remote proof, Kafka drain and generation fencing remain.
  Follow-on setup/cleanup operations are completed within the same request budget.
  Failed final deletion remains visible to administrators for explicit retry.
- The backend uses a separate provisioning database pool and management credentials;
  ingress keeps its existing boundary. Contracts, UI timeouts/recovery, local setup
  and deployment templates reflect the request-owned flow. No backend retry loop
  replaces the removed worker.
- Nice-to-have components and assistant-selected choices are explicit in the scope
  supplement: cutover/source receipt semantics, preserved access windows/masking,
  partial-failure recovery, HTTP budgets, provisioning connection and expired-key
  ciphertext disposal on the next successful operator mutation.
- Identity synchronization is unchanged. Keycloak-only identity supply remains a
  discussed alternative requiring a separately accepted, qualified integration.

## Executed verification

PostgreSQL cases used disposable test databases. Operator remote adapters were
synthetic; browser cases used a temporary Next server and mocked HTTP responses.
Suites overlap and their counts must not be summed as unique coverage.

| Verification | Actual result | Evidence |
|---|---|---|
| Focused scope and synchronous HTTP checks | 20 passed | [Focused report](../qualification/worker-scope-focused.xml) |
| Backend unit directory | 548 passed, 65 optional-service cases skipped | [Unit report](../qualification/worker-scope-unit.xml) |
| Maintenance/source/cutover and HTTP regression | 49 passed, 2 deferred scheduler cases skipped | [Regression report](../qualification/worker-scope-regression.xml) |
| Operator request recovery, retained engine, remote ledger and Kafka drain regression | 35 passed | [Operator report](../qualification/worker-scope-operators.xml) |
| Edge browser: maintenance editor and synchronous operator UI | 6 passed | [Browser report](../qualification/worker-scope-browser.xml) |
| OpenAPI/event contracts | Generation and consistency checks passed | `qualification/check_contracts.py` |
| Frontend TypeScript | Typecheck passed | Local TypeScript CLI, `--noEmit` |
| Changed Python files | Scoped Ruff and compile checks passed | Local backend environment |
| Deployment and launchers | Product API/worker template JSON and local PowerShell syntax passed | Parsing only; no rollout |
| Documentation | Local links/anchors, fences and original numbering checked | [Document report](../qualification/worker-scope-doc-check.json) |
| Diff whitespace | Passed | `git diff --check` |

The full unit run preceded the final deleted-operation/cancellation recovery fixes;
the later operator and regression runs cover those changes. Two old scheduler
tests in the regression run are explicitly deferred. The former timer-visibility
suite is also marked deferred, rather than claimed as current behavior coverage.
Current replacement cases verify no fan-out, persisted suppression and release on
the next source update. Historical Kafka maintenance fixtures explicitly use the
pre-cutover rules; separate source-driven tests exercise the new cutover semantics.

Initial failures are retained for provenance:

- [Initial HTTP/source run](../qualification/worker-scope-integration.xml): 3 passed,
  3 failed. Fixed a deleted-operation retry lookup; corrected two new test assertions
  to read source receipt from PostgreSQL rather than a nonexistent event field.
- [Initial maintenance run](../qualification/worker-scope-maintenance.xml): 35 passed,
  8 failed against superseded scheduler expectations. Updated the current behavior
  checks and explicitly deferred scheduler-only cases. A fixture DSN password in
  that initial report was redacted.
- [Initial browser run](../qualification/worker-scope-browser-initial.xml): 5 passed,
  1 failed against old deletion copy. Corrected the expectation; all six passed on
  the final run.

The installed npm shim was unusable, so contract/typecheck/browser commands used
the installed Node package CLIs directly. The owned temporary browser-test server
was stopped. The user's running application stack was not restarted or migrated.

## Remaining qualification gaps

- No production deployment or shared company service changes occurred. Migration
  0046 was exercised only in disposable databases. Stop old schedulers before a
  future rollout; mixed old/new binaries and rollback are not qualified.
- Real Kong/Grafana provisioning through the new backend request path, supplied
  production database-role privileges, management bindings and network egress
  remain unqualified. Synthetic adapters verify orchestration, not those services.
- Qualify the complete proxy timeout chain, slow dependencies, disconnected callers,
  concurrent requests, replica shutdown and the extra provisioning connection per
  backend process. Configured budgets are not measured latency guarantees.
- Storage growth with inactivity/retention cleanup deferred has not been capacity
  qualified. Existing access/expiry rules do not imply physical reclamation.
- Keycloak-only nested membership, account disablement, five-minute propagation and
  last-trusted outage behavior have not been implemented or qualified. Existing
  identity guarantees still rely on the current directory integration.
- Earlier Kafka/recovery and infrastructure qualification gaps remain in the
  [Kafka-first record](kafka-first-implementation.md) and
  [delivery checklist](../status/delivery-readiness.md).

The original temporary Kafka redesign handoff was already removed after its
incorporation and verification recorded in the Kafka-first evidence. This later
scope correction neither recreates it nor deletes another project document.

## REV-35 — Directory worker deferred (2026-09-30)

User requested removing the directory worker and listing it as nice-to-have.
The release guard now rejects continuous company directory startup; `directory
--once` retains explicit complete trusted publication and returns failure when
publication is incomplete. The local Keycloak entry point likewise requires
`--once`. The developer launcher excludes directory polling; local identity Start
publishes once, starts only API/UI, and retires its previously recorded directory
process on a subsequent Start. The explicit Refresh action loads the existing
local bindings and publishes once without restarting applications or dependencies.

Identity/access specifications, the architecture diagram, deployment guidance,
readiness/verification scope and REV-35 decision records reflect this change.
Assistant-selected boundaries preserve existing trusted identity evidence,
bootstrap/restore gates and OIDC/grant/session checks. Automatic five-minute LDAP
propagation is deferred with the poller. This is not a Keycloak-only identity
implementation. New users/teams need explicitly published complete evidence.

Executed verification:

- Focused scope, local directory, verifier/session/selector and setup suites:
  **51 passed, 19 skipped**; the skipped cases require an explicit PostgreSQL
  fixture. [Report](../qualification/directory-scope-unit.xml). The initial narrower
  run passed 29 cases and skipped the same 19 before the final local CLI check.
- Changed Python files: scoped Ruff passed. PowerShell launcher syntax and worker
  template JSON parsed successfully; no launch or OpenShift rollout was performed.
- Documentation: 27 documents, 343 local links and 74 anchors checked; fences,
  contiguous REV-01–REV-35, all 55 original issue IDs and all 68 original decision
  numbers passed. [Report](../qualification/directory-scope-doc-check.json).
- `git diff --check` passed.

No database migration, running-stack restart or shared service change was performed.
Manual publication against real company LDAP/Keycloak and the updated local
Start/Refresh process lifecycle remain unqualified; synthetic reader and CLI checks
are not end-to-end deployment evidence. A currently running directory process is
not stopped merely by editing these files; the updated local launcher removes its
owned directory process on the next Start.

## REV-36 — Product API owns live publishing (2026-09-30)

The user requested moving the live publisher into the Product API. Each configured
API process starts one background task alongside its existing Redis subscriber.
SQL/Redis calls execute outside the HTTP event loop, use the existing bounded
SQL pool, and drain before pool shutdown. Turns start at most 100 publications
and stop starting rows after one second or shutdown; Redis uses bounded socket
waits without internal transport retries. Dependency failure preserves pending
work and logs a sanitized warning. PostgreSQL/SSE reconciliation remains intact.

The standalone live role is rejected, developer/local worker launchers exclude
it, and a future local Start retires recorded old live workers and restarts the
managed API. Migration 0047 grants the API cross-tenant SELECT/DELETE only on
minimal hint outbox metadata. Domain RLS and scoped outbox INSERT/UPDATE remain;
no worker credential or additional database pool is needed. Architecture,
workload/deployment guidance and REV-36 decision/readiness records were updated.

Executed verification:

- **32 unit checks passed:** API background failure isolation, responsive HTTP
  during blocked publishing, drained shutdown before SQL close, ingress exclusion,
  retired live startup, workload routing, current worker deferrals and health.
  [Report](../qualification/api-live-unit.xml).
- **4 live-delivery checks passed:** disposable real PostgreSQL with synthetic
  Redis transport, including generation-conditional completion/failure retention
  and API-role cross-tenant hint publication. A stored alert identity stays hidden
  without tenant scope and visible with its tenant scope, confirming domain RLS.
  [Report](../qualification/api-live-integration.xml). Migration 0047 was applied
  only inside these disposable test databases.
- Changed Python Ruff checks, PowerShell launcher parsing, Product API/worker
  template parsing and `git diff --check` passed.
- Documentation links/anchors/fences, contiguous REV-01–REV-36, original 55 issue
  IDs and original 68 decision numbers passed.
  [Report](../qualification/api-live-doc-check.json).

Earlier narrower runs passed 28 unit and 4 live-delivery cases; the final runs
above include strengthened shutdown and stored-identity RLS assertions. Suites
overlap and must not be summed as independent coverage.

No running-stack migration/restart or OpenShift/shared-service rollout occurred.
The updated local Start path, combined API resource/load profile, actual Redis
failure/fan-out/proxy behavior and full replica fault matrix remain deployment
qualification work. Production readiness is not established by these checks.
