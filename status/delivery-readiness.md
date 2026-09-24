# Delivery readiness

Implementation, environment bindings and qualification evidence are separate from the selected system design. DEL records below remain open. Local implementation and executed correctness evidence now exist and are tracked in [Implementation status](./implementation-status.md); full release qualification is still unperformed. A closed design issue or passing local test does not establish production readiness.

## Service bindings

Require a validated, versioned environment manifest with each service's supported endpoint/version, trust/auth identity references, namespace/resource permissions, client limits/allocation, durability/failure/restore behavior, owner contact and evidence link. Secrets themselves stay outside the manifest. The following are required capability checks, not claims of existing infrastructure settings:

| Owner/service | Required binding and application response to incompatibility |
|---|---|
| PostgreSQL team | Supported SQL/locking/RLS/partition/index behavior, durable-commit and failover contract, connections/storage allocation, backup/restore handoff. No durable acknowledgement if its authority is unavailable. |
| Elasticsearch team | Strict/wildcard mappings, external versions, PIT/msearch/search_after, exact totals/refresh visibility, index/alias/rebuild interfaces and allocation. Failed capability gates search; no script/full-scan fallback. |
| Kafka team | Topic namespaces, tenant key/order semantics, idempotent producer support, message limits, consumer/replay/retention and allocation. PG pending scans remain the recovery path. |
| Redis team | Authenticated Pub/Sub namespace and connection limits. Failure selects PG reconciliation/polling, not data loss. |
| Kong/Grafana owners | Operator supplement's auth/private context, credential IDs, supported management APIs, exact Unicode routing-label round trip and external writer safety. Unsupported remote contract keeps operations pending. |
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

## Runbooks and support

The user's team is accountable for Appchi application behavior, releases, Keycloak configuration/operation and first-line application triage. Technology owners execute their own failover, storage expansion, backup restore, certificate/service maintenance and infrastructure policy. Record actual primary/secondary contacts in the environment manifest before launch. Existing company on-call tooling handles operational alerts; Appchi still has no product notifications.

Deliver runbooks with the following selected containment and exit checks:

| Situation | Application action and exit proof |
|---|---|
| Admission/PG authority failure | Return retryable rejection without acknowledgement; owner restores authority; verify durable inbox/order/credential gates before ready. |
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

| Delivery record | Owner | Required output / completion evidence |
|---|---|---|
| DEL-01 Service bindings | Appchi integration lead + each service owner | Service bindings manifest above with actual versions/endpoints/identity references, capabilities, allocation, owner contacts and recovery guarantees. |
| DEL-02 Implementation | Appchi engineering | APIs/UI/workers/DDL/adapters implementing the selected contracts, generated schema checks and traceability to the 55 IDs. |
| DEL-03 Capacity qualification | Appchi performance lead + service owners | Shared system section 23 measured combined profile, selected numeric budgets, actual pool/resource settings and sustained/recovery results. |
| DEL-04 Correctness/security/UX | Appchi engineering and reviewers | [Verification plan](./verification-plan.md) acceptance executions, isolation/replay/permission tests, accessibility and redaction evidence. |
| DEL-05 Recovery protection | Evidence/service owners + Appchi | Independent intent/fence contract, backup-restore handoff, credential/permission/deletion reconciliation and full rebuild exercise. |
| DEL-06 Identity and remote integration | Appchi Keycloak/integration owners + LDAP/Kong/Grafana owners | Installed-version configuration tests, nested membership/five-minute evidence, exact routing labels, safe external writer mode and credential lifecycle verification. |
| DEL-07 Release and support | Appchi release/support owners | Immutable release bundle, compatible rollout/rollback rehearsal, staffed contacts and exercised runbooks. |
| DEL-08 Operational observation | Appchi + observability owner | Live SLI measurement/coverage and ongoing rolling-window results after deployment; preproduction tests alone cannot prove a production 30-day SLO. |


### Local implementation checkpoint — 2026-09-23

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
