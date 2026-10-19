# Appchi workspace guidance

Open the parent directory containing the eleven independent Appchi Git repositories.
This is the shared entrypoint for Codex and Claude Code. Read each affected
repository's AGENTS.md and scoped instructions explicitly, even if the client did
not load them automatically. Paths below are relative to this workspace root.

## Start with the task

1. Identify the affected repositories; verify their Git roots, branches, revisions,
   staged/unstaged diffs and untracked work before editing. Preserve others' changes.
2. Select relevant routes from `appchi-design/context/map.md`. The maintained map is
   `appchi-design/context/task-map.json`; generated route pages link exact sections.
   Use request meaning, changed paths and behavioral impact together; combine routes.
3. Read required sources and their scope corrections, then follow conditional
   dependencies that apply. For behavior, architecture or cross-repository changes,
   include relevant system design, worker scope, producer/consumer contracts and
   acceptance requirements. A small local change starts with local guidance/code.
4. Expand when a new dependency, contradiction, missing authority or uncertain scope
   appears. No matching route means inspect the specification map and ask only about
   material unresolved decisions. A search match never proves context is sufficient.
5. Begin implementation when intended behavior, constraints, affected boundaries and
   verification are understood. Reopen exploration when new evidence changes scope.

From `appchi-delivery` (Python 3.13; standard library only):

```powershell
python scripts/context.py list
python scripts/context.py plan --route incidents --route incident-mutations
python scripts/context.py plan --task "Change incident search" --format json
python scripts/context.py read --source SOURCE_ID
```

Plans provide references, reasons, conditional follow-ups and current source hashes.
Read sources before using them; a plan does not mean they were read or verified.
Read each selected document's introductory corrections before a section. Historical
algorithms can be superseded by current worker scope. Sources remain the authority;
route pages, extracted text, memories and old test reports do not replace them.

## Repository ownership

| Repository | Owns |
|---|---|
| appchi-frontend | Next.js UI, generated client, browser tests and Keycloak theme. |
| appchi-api | HTTP product API, human auth, synchronous provisioning and live/SSE. |
| appchi-ingestion | Stateless v1/v2/Grafana admission, Kafka publication and Kong plugin. |
| appchi-processor | Source application, immutable replay proof and Kafka offset transactions. |
| appchi-correlation | Changed-event evaluation and automatic incident creation/attachment. |
| appchi-indexer | Alert search projection, reconciliation, watermarks and rebuilds. |
| appchi-domain | Shared transactional business rules; no server or separate database. |
| appchi-contracts | Native/event definitions, generated HTTP artifacts and compatibility. |
| appchi-migrations | One ordered PostgreSQL migration chain and dedicated migration image. |
| appchi-delivery | Workspace tooling, deployment, runbooks and executed qualification evidence. |
| appchi-design | Authoritative specifications, decisions, architecture and acceptance requirements. |

The detailed policies in `appchi-delivery/docs/agent-workflows.md` remain binding
where applicable. Read the following sections when their trigger applies:

| Work | Required policy sections |
|---|---|
| Repository layout or ownership | Workspace layout and instruction ownership; Repository ownership |
| Behavior or scope | Read the design first; Preserve system behavior and scope |
| Cross-repository or dependency work | Make changes across repositories coherently |
| Schema, cutover, deployment or release | Database, cutover and release discipline; Authorization and operating-team boundaries |
| Git, local services, install, build or test | Git and local execution |
| Documentation or verification | Documentation and verification |
| Delegation | Sub-agent delegation policy |

## Constraints that apply across tasks

- Do not initialize a parent Git repository, move repositories or introduce submodules
  incidentally. Keep commits/reviews per repository; use `codex/` for new branches.
- PostgreSQL is transactional authority; Elasticsearch is a rebuildable current-alert
  projection, Kafka the alert pipeline and Redis a hint transport. Preserve tenant
  scoping/RLS, idempotency, revisions, lock order, atomic history/proof and recovery fences.
- Preserve native v1/v2, fixed Global, immutable operator tenant routing, Kafka-acked
  admission and the PostgreSQL-before-Kafka replay protocol. Ingestion has no Appchi
  PostgreSQL, Elasticsearch or Redis dependency. Never mix fenced consumer generations.
- Current worker scope (REV-34–36) governs deferred automation. Do not reintroduce
  directory polling, expiry/retention/assignment sweeps, operator or live workers by
  following older algorithms. Keep trusted identity, authorization and restore gates.
- Shared packages cannot import service entrypoints. Use pinned dependencies and
  documented editable overrides; regenerate contracts with producer tools. Do not
  hand-edit generated artifacts alone or commit machine-specific dependency paths.
- Preserve applied migration bytes/checksums; use new forward migrations and the
  dedicated migration job. No runtime service migrates on startup.
- Use existing local-stack tooling and isolated synthetic fixtures. Inspect processes,
  ports and data before starting/stopping services. Serialize heavy native-CEL,
  frontend build and browser suites. Keep secrets/local state out of Git and reports.
- Complete affected producer and consumer work, run required checks, and update
  inaccurate linked guidance in the same change. Report actual revisions/results and
  skipped checks; old evidence is not proof for changed code or production readiness.
- Preserve all 55 original issue IDs and REV numbering. Record new design choices as
  assistant-selected in the existing register; resolve conflicts with settled decisions.
- Reversible application work and isolated tests within the request are authorized.
  Publishing, merging, messaging, production deployment and shared-service changes
  need existing authorization. Dedicated teams retain infrastructure ownership.
- Delegate independent work when useful, at most three children; no recursive
  delegation. Use `eng-coordinate`, bounded ownership and no user-visible internal chats.
  Keep focused fixes small. Substantial tasks use the existing checkpoint mechanism;
  preserve objective, decisions, source revisions, progress and next actions on handoff.

<!-- eng-project-setup -->
## Navigation and maintenance

- Architecture: `appchi-design/architecture/README.md`.
- Source authority: `appchi-design/specs/spec-alerts-management-system.md` (Specification map).
- Setup and context commands: `appchi-delivery/docs/repository-workspace.md`.
- Requirements: `appchi-design/verification/verification-plan.md`.
- Verification procedure: `appchi-delivery/docs/runbooks/development.md`, section
  "Repeatable verification workflow". Application changes run the agreed smoke
  baseline plus impact-based checks; backend changes affecting UI also require
  browser journeys. Report blocked/skipped checks and repair/retest failures.
  Documentation-only changes use documentation/discovery checks.
- Executed evidence: `appchi-delivery/status/implementation-status.md` and `appchi-delivery/status/delivery-readiness.md`.
- Context checks: `python qualification/check_context.py` in delivery.
- Documentation checks: `python qualification/verify_documents.py` in delivery;
  `python scripts/check_docs.py` in design. These are relevant checks, not startup commands.
- Other checks: use the affected repository README/CI. Delivery's prepared `.venv`
  runs `qualification/check_contracts.py`, `qualification/verify_boundaries.py` and
  coordinated `scripts/workspace.py test`; editable installation is an explicit override.

Maintain this file in `appchi-delivery/workspace/AGENTS.md` and the thin CLAUDE.md
import beside it. Reconcile root edits before running delivery's
`python scripts/workspace.py bootstrap`; preserve any existing backup. Keep installed
and canonical copies consistent. Generic eng-* workflows stay global. The context
layer changes project navigation, not model selection, native permissions or hooks.
<!-- /eng-project-setup -->
