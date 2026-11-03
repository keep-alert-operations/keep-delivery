# Delivery readiness

Implementation, environment bindings and qualification evidence are separate from the selected system design. DEL records below remain open. Local implementation and executed correctness evidence now exist and are tracked in [Implementation status](implementation-status.md); full release qualification is still unperformed. A closed design issue or passing local test does not establish production readiness.

The [2026-10-04 local startup repair](../qualification/local-startup-2026-10-04.md)
establishes working core development execution, local Kafka cutover and v1/v2
admission/search against the preserved local stores. It does not close DEL-01
through DEL-08 or qualify shared company services, capacity or recovery.

REV-38 adds [local Alembic conversion evidence](../qualification/alembic-migrations.md)
and its [migration runbook](../docs/alembic-migrations.md). Verified legacy adoption
and package/consumer checks contribute to DEL-02/04; no production gate is closed.
Publish the pinned prerequisite commits, build the dedicated migration image, and
execute the existing cutover prerequisites before the migration job/new writers.

## Service bindings

Require a validated, versioned environment manifest with each service's supported endpoint/version, trust/auth identity references, namespace/resource permissions, client limits/allocation, durability/failure/restore behavior, owner contact and evidence link. Secrets themselves stay outside the manifest. The following are required capability checks, not claims of existing infrastructure settings:

| Owner/service | Required binding and application response to incompatibility |
|---|---|
| PostgreSQL team | Supported SQL/locking/RLS/partition/index behavior, durable-commit and failover contract, connections/storage allocation, backup/restore handoff. Authority outage blocks application processing/commands; ingress Kafka admission remains independent. |
| Elasticsearch team | Strict/wildcard mappings, external versions, PIT/msearch/search_after, exact totals/refresh visibility, index/alias/rebuild interfaces and allocation. Failed capability gates search; no script/full-scan fallback. |
| Kafka team | Topic namespaces, logical-key received/tenant-key changed ordering, idempotent/transactional producer fencing, read_committed consumers, stable partition topology, message limits, offsets/replay/retention and allocation. No PG ingress queue; Kafka retention can lose accepted unprocessed inputs. |
| Redis team | Authenticated Pub/Sub namespace and connection limits. Failure selects PG reconciliation/polling, not data loss. |
| Kong/Grafana owners | Installed Appchi policy plugin, all-serving-node generation/digest/expiry proof, trusted host clocks, operator supplement's auth/private context, credential IDs, supported management APIs, exact Unicode routing-label round trip and external writer safety. Unsupported remote contract keeps operations pending. |
| LDAP owner / Appchi Keycloak team | Immutable identity schema, nested/primary memberships, paged completeness, upstream visibility within the five-minute budget; installed OIDC/password sign-in/console MFA/refresh/logout behavior. Unknown facts never become trusted memberships. |
| OpenShift/security/observability owners | Workload model/allocation, secret protection, trust, probes/grace, SSE flush/timeouts, metrics/traces/log interfaces and existing on-call routes. |
| Recovery-evidence owner | Independently protected ordered append/checkpoint interface and demonstrable rollback survival for minimal fences. Deletion/restore gates remain enforced until verified. |

Use adapter contract tests against a staging instance of the actual version. If a supplied service lacks a selected capability, the release stays gated and records a compatibility defect; no silent fallback that changes product behavior. Other teams choose infrastructure retention/backups/HA and perform service operations. Appchi chooses its schema/client usage and validates their offered guarantees. Do not claim a universal RPO/RTO or zero-loss service failover before those facts exist.

Collect the internal Keycloak/application hostnames, installed supported release, directory type/schema, TLS trust, LDAP read credentials/search bases, immutable-ID mapping and designated super-admin group ID. Confirm directory visibility and query capacity against section 7. These are integration inputs and qualification prerequisites; they do not reopen the selected configuration.

## CI and release artifacts

Define ordered CI gates: formatting/type/lint checks; unit/property tests of domain invariants; SQL migration/schema/constraint checks; OpenAPI/event compatibility checks; integration tests against supported real services; browser workflow/accessibility tests; security/dependency/container scans; and release qualification for changes affecting performance/recovery. Use pytest for Python tests and Playwright for end-to-end browser tests, with deterministic clock/IDs in test harnesses. Native CEL/ES equivalence tests must use the actual selected compiler and translator, not matching mocks.

Build immutable images once and promote the same digests. The release bundle includes migration checksums, OpenAPI/event/profile versions, CEL/Unicode/tzdata versions, capacity/service-manifest references, configuration diff, qualification results, runbooks and rollback compatibility range. Block production for failing correctness/isolation tests or unresolved exploitable dependency findings; record narrowly scoped reviewed exceptions only where the company's existing policy permits, never bypass a known tenant/credential safety failure. No unreviewed automatic major dependency or service upgrade.

## Delivery sequence and rollout

Select six dependency-ordered milestones: (1) contracts/manifests and build/migration foundation; (2) identity/tenant authorization plus durable native ingress; (3) authoritative alerts/history and CEL/search; (4) maintenance, incidents/correlation and retention/recovery; (5) operators/Grafana plus presets/dashboards/live UI; (6) full qualification, support handoff and controlled production release. Parallel implementation is possible inside a milestone where transaction contracts are stable, but no incomplete safety boundary is enabled in production. Dates and named engineers are staffing inputs rather than design choices fabricated here.

Use separate development/staging/production namespaces, realms, databases/namespaces and service credentials under owner-provided infrastructure. Seed only synthetic qualification data. Bootstrap Global idempotently and validate the designated LDAP super-admin group before human administration. Support native v1/v2 simultaneously; no legacy enriched-state import, cross-version identity merge or automatic historical replay migration is included.

Roll out expand/read-compatible application changes, validate/backfill in bounded fenced jobs, then enable new writes/features only when old incompatible writers are drained. Canary stateless APIs by supported routing and workers by explicit tenant/role fencing; never let two incompatible worker generations concurrently write a tenant. Reconcile desired remote operator state rather than duplicate provisioning during rollout. Roll back application images only within declared schema/event compatibility, preserving monotonic versions/epochs and authoritative data; no database rewind as a deployment rollback. Follow the existing error-budget policy.

**Timed dismissal (REV-49):** apply migration 0056, deploy the indexer and every writer, rebuild the search index so its strict mapping has `dismissed_until`, then set `APPCHI_ALERT_DISMISSAL_ENABLED=true` on the API and deploy the UI. Turning the flag off is the rollback; older code ignores the columns, so dismissed alerts become visible again. Not yet qualified.

**Incident reports (REV-53):** apply migration 0058 in the stopped-writer migration window (its index builds block incident cycle and activity writes for one scan), then deploy the API and the UI. The report is read-only and has no flag; the API is correct without the indexes but slower. Rolling back the UI removes the Reports item; the indexes can stay. Not yet qualified.

**Alert name (REV-54):** order matters. Apply migration 0059 (a nullable `alerts.name`, no rewrite, no stopped-writer window), then deploy the processor, correlation and API, which accept and resolve names while nothing sends one. Rebuild the search index under the strict mapping that adds `name` and `missing_name`, switch the alias, then deploy the indexer; until the switch `name` predicates match nothing. Deploy ingestion last: an older processor stops at the first named record and retries it, stalling that partition without loss. Unnamed v2 keys are unchanged; named alerts get identity format 2. Grafana members now need the `alertname` label, which Grafana always sets. Roll back ingestion first; the column stays. Not yet qualified.

**Alert provider (REV-55):** apply migration 0060 (nullable `alerts.provider` and `alerts.alert_rule_url` with checks, no rewrite, no stopped-writer window), then deploy the processor, correlation and API. The processor derives both fields from the existing `adapter_version` and retained Grafana bytes, so Kafka envelopes and ingestion behavior are unchanged; ingestion only takes the new pins. Rebuild the search index under the strict mapping that adds `provider`, `alert_rule_url` and their missing flags, switch the alias, then deploy the indexer. Once every source processor runs this release, run `python -m appchi.provider_backfill` with the processor's bindings; writers keep running, and a rerun changes nothing. Rolling the processor back leaves its new rows without a provider; rerun the backfill after upgrading again. The columns stay. Not yet qualified.

**Incident list link counts (REV-56):** no migration and no new runtime role. Deploy the API with its domain pin; older UIs ignore the new `total_linked`, `suppressed` and `unavailable` list fields. Then deploy the frontend, whose Linked alerts column reads them. Roll back the API alone and the column shows "—" again. Not yet qualified.

**Dashboard panel types (REV-57):** apply migration 0062 first (adds `dashboard_panels.type` defaulting to `count` and `content`, makes `preset_id` nullable, no rewrite). Then deploy the API and the domain-pin-only services, then the frontend. An older frontend keeps working with count panels; HTML panels can be created only from the new frontend. Rolling the API back after HTML panels exist breaks reading those dashboards, so delete the HTML panels first or roll forward. Not yet qualified.

## Runbooks and support

The user's team is accountable for Appchi application behavior, releases, Keycloak configuration/operation and first-line application triage. Technology owners execute their own failover, storage expansion, backup restore, certificate/service maintenance and infrastructure policy. Record actual primary/secondary contacts in the environment manifest before launch. Existing company on-call tooling handles operational alerts; Appchi still has no product notifications.

Deliver runbooks with the following selected containment and exit checks:

| Situation | Application action and exit proof |
|---|---|
| Kafka admission / PG authority failure | Kafka admission requires broker confirmation and trusted gateway context; PG failure stops processing/commands but not healthy ingress. Verify Kafka offsets, retained proof and cutover/credential gates before reopening affected workers. |
| Backlog or blocked head/proof | Preserve accepted work, expose scope/lag, inspect trusted event/control proof; repair with audited preconditions; never skip an accepted predecessor. |
| Correlation blocked | Preserve incidents/alerts, show reason, correct validated configuration or use permitted fresh reset; never replay expired windows. |
| Maintenance lag/error | Preserve cause truth and historical boundaries, fence unsafe evaluation, repair reducer/jobs; verify overlap union and searchable/rendered generation. |
| Search unavailable/rebuild | Keep authoritative reads/ingestion where healthy, label stale views, snapshot/catch up/switch under the selected index epoch protocol. |
| Identity outage or access defect | Continue valid last-known-data policy for unavailable refresh; enforce known negative evidence/current grants; gate absent authority and restore sessions only through fresh SSO after rollback. |
| Key compromise/provisioning failure | Revoke authoritatively, erase ciphertext, reconcile remote resources with existing saga; verify no old generation can activate. |
| Retirement/evidence failure | Keep content inaccessible only once logical retirement commits; retain protected pending work, reconcile intents, gate unsafe physical deletion/restoration. |
| Clock anomaly | Fence timestamp-dependent actions, escalate owner repair, preserve recorded times and resume only with trusted monotonic decision time. |
| Application rollback | Drain incompatible writers, deploy compatible prior digest, verify schema/event/profile and fences; never restore a database as a shortcut. |

For each runbook record trigger, scope, current evidence, safe action, recovery verification, escalation route and audit. Controlled repair tooling uses a dedicated platform identity, explicit tenant/resource IDs, dry-run diff, expected versions and recorded execution reason; no arbitrary SQL console in the product UI. Repairs use normal invariants/append-only corrections and cannot delete required accepted work, forge successful remote verification or bypass current identity gates.

Link each technology team's authoritative service procedures and escalation route; do not duplicate their failover, backup, capacity or clock-repair instructions. Restored infrastructure availability does not establish application readiness. Operational incident handling does not change product incident behavior.

## Evidence register

The 2026-09-30 Kafka-first redesign contributes code and local execution evidence to
DEL-02/04/06; see [its implementation record](../docs/kafka-first-implementation.md).
It closes no production delivery gate. DEL-03 capacity, DEL-05 protected rollback
reconstruction (including new Kafka replay fences), production serving-pool/clock
bindings and DEL-08 observation remain open. Existing local application data requires
a separately executed offline cutover; the disposable test migration is not that cutover.

| Delivery record | Owner | Required output / completion evidence |
|---|---|---|
| DEL-01 Service bindings | Appchi integration lead + each service owner | Service bindings manifest above with actual versions/endpoints/identity references, capabilities, allocation, owner contacts and recovery guarantees. |
| DEL-02 Implementation | Appchi engineering | APIs/UI/workers/DDL/adapters implementing the selected contracts, generated schema checks and traceability to the 55 IDs. |
| DEL-03 Capacity qualification | Appchi performance lead + service owners | Shared system section 23 measured combined profile, selected numeric budgets, actual pool/resource settings and sustained/recovery results. |
| DEL-04 Correctness/security/UX | Appchi engineering and reviewers | [Verification plan](https://github.com/appchi-alert-operations/appchi-design/blob/main/verification/verification-plan.md) acceptance executions, isolation/replay/permission tests, accessibility and redaction evidence. |
| DEL-05 Recovery protection | Evidence/service owners + Appchi | Independent intent/fence contract, backup-restore handoff, credential/permission/deletion reconciliation and full rebuild exercise. |
| DEL-06 Identity and remote integration | Appchi Keycloak/integration owners + LDAP/Kong/Grafana owners | Installed-version configuration tests, nested membership/five-minute evidence, exact routing labels, safe external writer mode and credential lifecycle verification. |
| DEL-07 Release and support | Appchi release/support owners | Immutable release bundle, compatible rollout/rollback rehearsal, staffed contacts and exercised runbooks. |
| DEL-08 Operational observation | Appchi + observability owner | Live SLI measurement/coverage and ongoing rolling-window results after deployment; preproduction tests alone cannot prove a production 30-day SLO. |


### Local implementation checkpoint â€” 2026-09-23

The integrated backend run through migration 0030 passed 606 tests, with one
Kafka-dependent test skipped and six dependency deprecation warnings. A further
three real Elasticsearch/PostgreSQL search rebuild edge tests passed. Rebuild
copy/catchup, alias uncertainty, new-tenant inheritance and old cursor invalidation
now have executed local evidence. Incident retirement and protected command/body
cleanup are connected; alert, ordinary-command and preset/dashboard cleanup have
executed PostgreSQL evidence. Restore epoch/session fencing and bounded checkpoint
collection are implemented, while reconstruction and safe reopening remain missing. The executable
Kong/Grafana profile is connected to the worker and has controlled HTTP/PG tests,
but installed services and serving-pool proof remain unqualified. These are partial
DEL-02/04/05/06 inputs; no DEL entry is closed by this checkpoint. Refer to
[implementation status](implementation-status.md) for the current, evolving result.

## REV-34 worker-scope evidence

[Worker-scope implementation](../docs/worker-scope-implementation.md) records actual
local checks and outstanding qualification. Previous maintenance-expiry and
background-worker capacity gates apply only if those nice-to-have components are
reintroduced. Synchronous operator remote/proxy/privilege/concurrency qualification
remains required before deployment; no DEL item is closed by documentation alone.

## REV-35 directory scope

Directory polling and its five-minute propagation evidence in DEL-06 are deferred
with the nice-to-have worker. Explicit complete bootstrap/manual publication,
immutable identity binding, nested membership, unknown-user/team refusal,
last-trusted authorization and session/restore safeguards remain required. This
does not close any DEL item or qualify company LDAP/Keycloak. See
[worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md#4-directory-synchronization-is-nice-to-have).

## REV-36 live publishing scope

The Product API owns Redis hint publication and SSE; remove the separate live
worker allocation. Apply migration 0047, supply API Redis egress/bindings and
qualify combined API SQL/Redis load, replica duplication, failures and drained
shutdown. Existing two-second PostgreSQL reconciliation and tenant/session guards
remain. Local checks are in [worker-scope evidence](../docs/worker-scope-implementation.md);
no DEL item is closed by this scope change alone.

## Private OpenShift load-test preparation â€” 2026-10-05

The [isolated deployment and load-test handoff](../docs/runbooks/openshift-load-testing.md)
assumes supplied Elasticsearch, Kafka and Grafana, an internal image registry and
dedicated native Keycloak test users. It provides container/deployment assets,
starting allocations and executable workload scripts. See the
[local evidence](../qualification/openshift-load-test-handoff.md) for actual checks
and unbuilt/unexecuted coverage. The
[initialization tools](../qualification/cluster-initialization-2026-10-05.md) are now
implemented; actual search activation, operator proof integration, capability
evidence and target-cluster execution remain required. No DEL item or production
readiness gate is closed by this preparation.
## Scalability handoff â€” 2026-10-07

Application corrections and local verification for 5,000 deliveries/minute,
100 active users and 50 tenants are recorded in the
[implementation report](../qualification/scalability-implementation-2026-10-06.md).
The combination remains uncommitted editable source, with migrations 0048 through 0050
and additive API contracts. Release preparation must publish immutable artifacts
and update all affected consumer pins and the delivery manifest together.
Local unit/component/build evidence does not close DEL-03 or other production
gates. Testing stopped at the user's request. Requested-scale latency did not
pass; even the final low-arrival-rate 100-user diagnostic had read failures.
The final clock race correction has focused regression evidence but no later
load run. Controlled browser tests passed; planned final real-browser identity
and alert workflows were not run. Local startup and 17 gateway checks completed.
All three temporarily paused BI containers were restored. See the report for
exact suite counts, revision limits and remaining retained-data growth cases.

Source publication was subsequently authorized. Coordinated
`codex/scalability-hardening` branches, immutable dependency pins and the workspace
manifest are recorded in [publication evidence](evidence/scalability-publication.json).
Pre-existing unrelated work remains local. No additional tests were run; this is
source publication, not a merged or qualified deployment.

## Python tooling migration — 2026-10-12

Active Python setup, CI, containers and guidance use Poetry 2.3.1 following the
user's decision. [Local verification](../qualification/poetry-migration-2026-10-12.md)
covers nine locked installations/builds, 813 passing owned unit cases, 65
unbound-service skips, contract/import/document checks and static container checks.
Existing runtime Git pins are preserved. Source publication was subsequently
authorized; the workspace manifest records the coordinated sibling revisions. Complete image builds, company-service integration and production
rollout are unexecuted, so no DEL capability gate is closed by this tooling change.

## Performance improvements — 2026-10-23

The remaining performance-review items are merged into every repository's `main`.
Delivery integration tests passed on the merged revisions apart from two
known-flaky cases.

[Local evidence](../qualification/performance-improvements-2026-10-23.md) records
the scope, measured effects and gaps. Not run:

- the local-stack smoke baseline;
- the real-backend browser journeys;
- the before/after load comparison.

Migration 0052 must run in the stopped-writer window, because it blocks
`projection_work` inserts while its index builds. Image builds, company-service
integration and production rollout are unexecuted, so no DEL capability gate is
closed by this work. The CEL compile-process decision (#10) and the per-tenant
correlation ceiling remain open.
