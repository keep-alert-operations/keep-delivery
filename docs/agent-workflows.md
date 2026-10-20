# Workspace engineering policies

These are the detailed workspace policies linked from the small agent entrypoint.
They preserve the prior workspace guidance; read the sections relevant to the task.
All applicable requirements remain binding. A route is a starting reading plan,
not a waiver of a requirement omitted by its index. Product specifications remain
authoritative in design; this document governs engineering work and ownership.

Paths inside code spans and command examples are relative to the workspace root
unless a repository working directory is explicitly given. The canonical installed
entrypoint is [workspace/AGENTS.md](../workspace/AGENTS.md). Use the
[context guide](../../appchi-design/context/README.md) to select authoritative sections.

## Sub-agent delegation policy

Proactively use sub-agents when independent work or specialist review justifies
the additional cost. The lead agent decides when to delegate without asking the
user each time. Keep small tasks with the lead agent and use the fewest agents
needed. Assign clear ownership and bounded responsibilities, avoid duplicate
work, and preserve other agents' changes. Allow at most three concurrent
sub-agents, within native runtime limits. Sub-agents must not delegate recursively.
Use `eng-coordinate` for bounded assignments and integration; keep internal work
in sub-agents rather than creating user-visible chats.

## Workspace layout and instruction ownership

```text
<workspace>/
  AGENTS.md
  appchi-frontend/
  appchi-api/
  appchi-ingestion/
  appchi-processor/
  appchi-correlation/
  appchi-indexer/
  appchi-domain/
  appchi-contracts/
  appchi-migrations/
  appchi-delivery/
  appchi-design/
```

Each child is an independent Git repository. The parent is a development workspace;
do not initialize a parent Git repository, add submodules, or move repositories as
an incidental part of a feature task. Directory names alone do not prove that a
child is an independent checkout: verify its resolved Git root.

Keep the canonical entrypoint at
`appchi-delivery/workspace/AGENTS.md` and have workspace setup copy it to
`<workspace>/AGENTS.md`. Keep the canonical and installed copies consistent when
changing the entrypoint. Use `appchi-delivery/scripts/workspace.py bootstrap` to install the root copy.

Repository instructions add commands and local conventions. Preserve system
contracts across repository boundaries. Explicit user decisions and later scope
corrections govern the work; surface unresolved conflicts instead of silently
reversing a settled decision.

## Start every task with the actual workspace state

1. For every task, identify the workspace root and affected repositories. Read
   each affected repository's `AGENTS.md` and applicable scoped instructions.
2. Before editing, inspect each affected repository's Git root, branch, status,
   and relevant changes, including staged, unstaged, and untracked work.
   Preserve existing staged, unstaged, and untracked work. Inspect configured
   remotes only when needed, without exposing credentials embedded in URLs.
3. Make additional reading proportional to the task; apply every relevant category:
   - Small, localized changes: read only the source, documentation, and verification
     guidance needed for the change, including relevant README sections.
   - Behavior, architecture, or cross-repository changes: read the relevant system
     design, worker scope, affected specifications, and producer/consumer contracts
     using the index below. Identify affected producers, consumers, domain packages,
     migrations, deployment configuration, documentation, and verification. A change
     may span repositories even when the request names only one service.
   - Dependency or package changes: inspect affected locks, package versions, and
     applicable delivery workspace/release manifests. Record the actual revisions
     used; equal branch names do not prove compatibility.
   - Migration, deployment, or release-qualification work: read the relevant runbooks,
     compatibility requirements, implementation status, and readiness evidence.
4. Expand reading when evidence reveals additional dependencies, uncertainty, or
   conflicting requirements. Check the relevant scope, implementation status, and
   readiness evidence before relying on a component's existence or qualification.

Conditional reading limits startup scope; it does not make applicable requirements
optional. All applicable authoritative requirements and verification obligations
remain binding, regardless of task size.

If extraction is incomplete, locate the authoritative files in the remaining
monorepo and follow its instructions. Do not create empty replacement services,
duplicate authoritative files, or infer remote URLs from repository names. Use
verified repository configuration to obtain missing sources; otherwise report the
missing input and continue work that does not depend on it. Perform a repository
split only as part of a task that requests it.

## Repository ownership

| Repository | Owns |
|---|---|
| `appchi-frontend` | Next.js/TypeScript application, UI assets, generated API client, frontend tests and image, custom Keycloak login theme. |
| `appchi-api` | Product HTTP API, human authentication/authorization, management endpoints, synchronous operator provisioning, live hint publishing, Redis subscription, SSE and connected-client reconciliation. |
| `appchi-ingestion` | Native v1/v2 and Grafana admission, validation, trusted routing, Kafka publication, Appchi Kong policy plugin and its migrations. Public/private admission are workload variants. |
| `appchi-processor` | Source consumer, source-application orchestration, immutable replay proof, changed-event publication and Kafka input-offset transactions; Kafka protocol cutover tooling. |
| `appchi-correlation` | Changed-event consumption, rule/evidence evaluation orchestration and automatic incident creation/linking. Rule-management HTTP endpoints stay in the API. |
| `appchi-indexer` | Elasticsearch writes, PostgreSQL reconciliation, searchable watermarks, index mapping application, rebuild and index switching. Search/count HTTP endpoints stay in the API. |
| `appchi-domain` | Shared Python business rules and transactional operations: lifecycle, ownership, maintenance effects, correlation resets, CEL semantics, shared projection fields, persistence and lock helpers. No server or separate database. |
| `appchi-contracts` | Shared native/event definitions, generated HTTP contract artifacts, compatibility fixtures and published language artifacts. No service orchestration or database access. |
| `appchi-migrations` | Complete PostgreSQL migration chain, runner, schema/privilege tests and dedicated migration image. |
| `appchi-delivery` | OpenShift manifests, local Compose/setup, sanitized Keycloak realm/client configuration, workspace tooling, release manifests, system integration/qualification tooling and evidence, operational runbooks and delivery status. |
| `appchi-design` | Authoritative specifications, architecture diagrams, approved UX references, decision history/register and verification requirements. |

Use **Ingestion API** in design prose and diagrams and `appchi-ingestion` for the
repository/application. Reserve ingress terminology for network routing when that
is the intended meaning. A service-name change does not rename HTTP paths, event
fields, runtime configuration keys, or historical migration identifiers.

## Read the design first

After extraction, normative specifications live under `appchi-design/specs/`.
For behavior, architecture, or cross-repository changes, read the relevant system
design in `spec-alerts-management-system.md` and scope in `spec-worker-scope.md`
before making the change, then read the affected supplements and producer/consumer
contracts. For other tasks, use this index as needed under the reading rules above;
it is not an unconditional startup checklist:

- `spec-alert-operations.md` and `spec-alert-history-technical.md`.
- `spec-kafka-alert-pipeline.md`.
- `spec-incident-operations.md` and `spec-correlation-rules.md`.
- `spec-operator-operations.md` and `spec-reliability-operations.md`.
- `spec-identity-configuration.md` and `spec-tenant-access.md`.
- `spec-preset-operations.md`, `spec-maintenance-operations.md`, and
  `spec-dashboard-operations.md`.
- `spec-system-completion.md`.

Use `appchi-design/decisions/design-register.md` and
`appchi-design/decisions/design-decisions-2026-09-22.md` for decision provenance.
Preserve all 55 original issue identifiers and the existing decision numbering.
Acceptance requirements belong in `appchi-design/verification/verification-plan.md`.
Current evidence belongs in `appchi-delivery/status/implementation-status.md` and
`appchi-delivery/status/delivery-readiness.md`, including DEL-01 through DEL-08.

These are target locations. During extraction, consult the corresponding root
files in the monorepo or the verified repository index; do not treat an absent
target path as an absent requirement. Each contract has one authoritative home.
Later explicit corrections supersede earlier examples and deferred algorithms.

## Preserve system behavior and scope

- Backend services use Python/FastAPI; the frontend uses Next.js/TypeScript;
  deployment targets OpenShift. PostgreSQL remains transactional authority,
  Elasticsearch a rebuildable current-alert projection, Kafka the alert pipeline,
  and Redis a hint transport backed by PostgreSQL reconciliation.
- Repository separation does not introduce separate service databases, distributed
  replacements for existing SQL transactions, or a promise of independent releases.
  Preserve tenant scoping/RLS, command idempotency, expected revisions, lock order,
  atomic history/proof updates, active incident ownership and recovery fences.
- Native v1 and v2 remain accepted. V1 is unauthenticated and routes to fixed
  Global; v2 uses Kong/operator keys and immutable owning-tenant routing. Ingestion
  has no Appchi PostgreSQL, Elasticsearch, or Redis dependency. A successful 202
  requires Kafka acknowledgement under the specified admission contract.
- Source application commits PostgreSQL state and immutable replay proof before
  transactionally publishing changed events with Kafka input offsets. Preserve
  deduplication, source order, tenant correlation order and loss diagnostics;
  this is not an atomic transaction spanning PostgreSQL and Kafka.
- Company LDAP remains authoritative for users/nested teams. The Appchi team owns
  Keycloak. Explicit trusted publication and last-known trusted identity data
  remain required; automatic directory polling and its five-minute propagation
  target are deferred under REV-35. Preserve current grants, known revocations,
  session expiry and restore gates. The isolated local Keycloak user/group
  exception uses immutable UUIDs and normal OIDC/publication, without a login bypass.
- The Product API owns synchronous operator provisioning and live publishing/SSE
  under REV-34 and REV-36. No separate operator or live worker is deployed.
  Maintenance expiry release, assignment cleanup, inactivity and retention sweeps
  remain deferred. Maintenance suppression can persist until the next qualifying
  source update; elapsed time alone does not release it. Explicit publication and
  recovery/proof commands remain available within their documented scope.
- Preserve fixed Global, no tenant delete/disable, role/admin continuity, the
  365-day tenant/access audit contract, read-time history windows, existing
  retirement/replay fences, no-backfill maintenance and shared view references.
  Deferred physical cleanup does not extend access windows or resurrect content.
- One shared company Grafana uses the exact routing label
  `Appchi - {operator name}` with literal string value `true`.
- Initial release excludes product notifications, recurring maintenance, personal
  presets/dashboards, enriched historical import and full event sourcing.

Detailed behavior belongs in the specifications. Record newly discovered gaps or
contradictions in the existing design register without renumbering entries. Label
new autonomous design choices assistant-selected and report them. Keep normative
contracts, user decisions, illustrative examples and executed evidence distinct.

## Make changes across repositories coherently

Complete the affected producer and consumer work within the user's requested
scope. Do not stop at a producer change while leaving required consumer changes
unhandled. Keep changes focused; unrelated cleanup is separate work.

- Shared packages never import service routes or entrypoints. Services consume
  pinned package releases instead of importing sibling source trees or copying
  shared behavior. Extract shared helpers from route modules before moving code.
- `appchi-domain` may depend on contracts. Put only shared behavior in
  it; keep HTTP routes, service-specific consumers and process entrypoints in their
  service repositories. Shared bounded lifecycle mechanics may receive explicit
  service-owned handlers without importing them.
- Event/native definitions have one source in `appchi-contracts`. API and ingestion
  generate their HTTP schemas and publish snapshots with producer revisions.
  Regenerate derived schemas and frontend types with the actual repository tools;
  never patch generated artifacts alone or hand-maintain a second schema authority.
- Determine compatibility from the protocol/schema rules and consumer tests. Keep
  old readers/writers supported where required; do not assume a version bump makes
  an incompatible change safe.
- For local package development, use documented editable overrides or local built
  artifacts and record which source revision was exercised. Release locks must
  resolve immutable artifacts without sibling-checkout assumptions. Do not commit
  absolute machine paths, floating branches, or temporary local dependency overrides.
- Update affected dependency locks and delivery manifests together. Coordinate
  prerequisite commits/artifacts, related PRs, merge order, migration order and
  rollout compatibility when a change spans repositories. A PR in each repository
  is not an atomic release.

## Database, cutover and release discipline

Keep one ordered PostgreSQL migration chain in `appchi-migrations`. Preserve
published/applied filenames, numbering gaps, exact bytes and checksums; use new
forward migrations for changes. Check the current chain before choosing a number.
Do not create per-service migration runners against the shared schema.

Run the dedicated migration job with its migration identity before dependent
writers start. Runtime services never migrate on startup. Prefer compatible
additive changes, while preserving required offline drains, offset initialization,
writer fences and stopped-worker prerequisites. Application rollback is allowed
only for the documented compatible schema/protocol state; do not infer it from an
old image remaining available. Never mix fenced legacy and replacement consumers.

Kong plugin migrations belong to ingestion; Elasticsearch mapping/rebuild work
belongs to the indexer. Delivery owns the coordinated execution instructions.
Record the tested combination of repository revisions, image digests, package and
contract versions, migration checksums, configuration, design revision and evidence.
An updated manifest without executed checks is not a qualified release.

## Git and local execution

Run commands from their owning repository or set an explicit working directory.
For example, after verifying that the API checkout exists and has its own Git root,
these commands run from the parent workspace:

```powershell
git -C ./appchi-api rev-parse --show-toplevel
git -C ./appchi-api status --short --branch
git -C ./appchi-api diff --stat
```

Check staged and untracked changes as well as ordinary diffs before editing or
staging. Keep branches, commits and reviews per repository. Use the `codex/` branch
prefix when creating a branch unless the user specifies another convention; reuse
the relevant existing branch when appropriate. Determine base branches from the
repository instead of assuming `main`. Stage only task-related changes. Preserve
unrelated work; never use blanket resets, cleans, stashes or force pushes to make
the workspace appear clean.

Before installing, building, or testing, read the affected repository's relevant
command and runtime guidance in its README, manifests, and scripts; inspect locks
when dependency or package work or execution requirements call for them.
Use existing delivery tooling for the local stack;
avoid starting duplicate services or competing consumers against the same data.
Inspect existing processes, ports and configured isolated data before starting,
stopping or migrating anything. Use disposable synthetic test data and scoped
database/index fixtures. Serialize heavy native-CEL suites, frontend builds and
browser suites on this workstation where contention affects the compiler deadline.
Keep secrets and local state outside version control and out of logs/reports.

## Documentation and verification

Use the [repeatable verification procedure](runbooks/development.md#repeatable-verification-workflow)
for startup, readiness, test data, the user-agreed four-journey smoke baseline,
expected outcomes, evidence and cleanup. For application changes, run that baseline
plus checks selected by impact below. Exercise affected UI journeys in a real
browser even when the change is in a backend. Missing environments, skipped tests
and capability fences are explicit coverage gaps, never passing results. For
documentation-only changes, run the documentation/discovery checks instead.
Fix introduced defects, then repeat failed scenarios and affected regressions.

Keep service implementation notes with their service, system runbooks and delivery
evidence in delivery, and specifications/approved design references in design.
Executable styles/theme assets belong to frontend. Store screenshots with the
check they evidence. Update moved links, command examples and scoped instructions;
cross-repository release references should identify the applicable revision.

Run verification appropriate to the change using the actual repository tooling:

- Service/package changes: relevant static, unit and component integration checks.
- Shared domain changes: affected callers plus transactional/concurrency scenarios.
- Contract changes: generation/drift checks and affected producer/consumer tests.
- Migration changes: checksum/history integrity, supported upgrades, schema/privilege
  checks and relevant writer/read compatibility on isolated PostgreSQL.
- Pipeline/search changes: affected replay, ordering, reconciliation and recovery
  scenarios from the verification plan, using real services where required.
- Frontend changes: applicable type/build checks and affected browser workflows;
  controlled API fixtures and real integration runs are separate evidence.
- Documentation-only changes: links, code fences, ownership consistency, issue IDs
  and decision numbering. Do not claim runtime tests were run.

Use existing meaningful tests and add coverage for new behavior or regressions;
avoid tests that merely repeat implementation details. Complete required checks,
then broaden testing only for changed scope, failures or unresolved concerns.
Record commands, repository revisions, results, skipped checks and environmental
limits. Do not reuse old evidence as proof for a new revision or call unrun checks
passed. Finish with affected repositories, behavior changes, verification results,
and any remaining dependency, migration or rollout work.

## Authorization and operating-team boundaries

Within the requested task, application code, scaffolding, dependency installation,
isolated local infrastructure and tests are authorized across the initial release.
Proceed with necessary reversible work across affected repositories. Production
deployment and changes to shared company services require separate authorization;
existing explicit authorization remains valid for its stated scope.

The Appchi team owns the application and Keycloak configuration/operation.
Dedicated teams own LDAP, PostgreSQL, Elasticsearch, Kafka, Redis, OpenShift, Kong
and Grafana infrastructure, including retention, backups, HA, topology, allocation
and service recovery. Define application requirements and verify supplied service
contracts without prescribing those teams' infrastructure policy. Do not invent
versions, endpoints, credentials, allocations, named owners or recovery guarantees.
Missing integration evidence keeps affected capability gates closed. Design
completion, local execution, integration qualification and production readiness
are separate states; report only what the current evidence establishes.

For repository layout or ownership changes, read
[repository layout](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-repository-layout.md) for REV-37.
From delivery, use `python scripts/workspace.py bootstrap`, then
`python scripts/workspace.py install --editable` for coordinated local development.
Git dependency pins and locks remain the release authority; local editable code
is an explicit development override. Record per-repository status and revisions.
Python repositories use Poetry 2.3.1, committed `poetry.lock` and in-project
`.venv` environments. Validate with `poetry check --lock` before
`poetry sync --all-extras`. Normal setup must not use `--all-groups`: the optional
`lock-constraints` group preserves global version constraints without installing
dormant packages. Coordinated editable overrides use the environment's Python
with `pip install --no-deps --editable`; subsequent sync restores release pins.
