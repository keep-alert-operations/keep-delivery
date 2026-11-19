# Keep workspace guidance

Open the parent directory containing the eleven independent Keep Git repositories.
This is the shared entrypoint for coding agents; Claude Code loads it through
`CLAUDE.md`. Read each affected repository's AGENTS.md and scoped instructions
explicitly, even if the client did not load them automatically. Paths below are
relative to this workspace root.

## Start with any request

Questions, changes, investigations, reviews and operations all start here.

1. Name the intent (what the request asks you to do) and pick the topics it concerns
   from the index below by meaning. Combine topics when a request crosses areas.
2. From `keep-delivery`, run `python scripts/context.py plan --intent INTENT
   --topic TOPIC --content`. The plan says what to do and when you are finished,
   includes the authoritative sections with their scope corrections, shows where the
   code lives and lists related topics with the condition for reading each.
3. Before editing, identify the affected repositories; verify their Git roots,
   branches, revisions, staged/unstaged diffs and untracked work. Preserve others' changes.
4. Follow each related topic whose condition holds. For behavior, architecture or
   cross-repository changes, include system design, worker scope, producer/consumer
   contracts and acceptance requirements. A small local change starts with local code.
5. Expand when a new dependency, contradiction, missing authority or uncertain scope
   appears. When no topic fits, `find` ranks matching headings across specifications,
   decisions and runbooks; ask only about material unresolved decisions. A match
   never proves context is sufficient.
6. Act when intended behavior, constraints, affected boundaries and verification are
   understood. Reopen reading when new evidence changes scope.

<!-- BEGIN generated context index: edit keep-design/context/task-map.json, then run context.py render. -->
**Intents:** `question` (understand or answer) · `change` (add or modify) · `review` (check a diff) · `investigate` (unexpected behavior) · `operate` (run, release, recover). Unclear requests start as `question`.

| Area | Topic | Covers |
|---|---|---|
| System | `behavior` | Product model, services and data authority, cross-system invariants, repository boundaries and current worker scope. |
| System | `requirements-and-decisions` | Where requirements, the 55 issue IDs, REV decisions and acceptance scenarios live, and how to trace them. |
| Alert flow | `alert-pipeline` | How an alert travels: v1/v2/Grafana admission, Kafka publication, source processing with replay proof, then changed events. |
| Alert flow | `alerts` | Alert fields and identity, lifecycle, assignment, notes, history, dismissal and bulk manual operations. |
| Alert flow | `event-contract` | Native v1/v2 input fields and Kafka event schemas: definitions, serialization, generation and compatibility. |
| Alert flow | `search` | Alert CEL filters, search and counts, the Elasticsearch projection, indexing progress, reconciliation and rebuilds. |
| Alert flow | `live-updates` | Live publishing from the API, SSE streams, reconnect and polling, scoped invalidation and Redis hints. |
| Alert flow | `recovery` | Restore and replay fences, protected proof, retirement, retention, deletion and access to retained history. |
| Incidents | `incidents` | Incident content, origin, lifecycle, assignment, membership, detail, list and period reports. |
| Incidents | `incident-mutations` | Incident notes, content edits, assignment, resolve and reopen transitions, and command results. |
| Incidents | `incident-membership` | Linking, unlinking and moving alerts between incidents, active ownership and resolve/reopen ownership effects. |
| Incidents | `incident-search` | Incident list filters, ordering, cursors, counts and PostgreSQL-backed incident search. |
| Incidents | `correlation` | Automatic incident creation and attachment from changed events: rule conditions, grouping, evidence and ordering. |
| Product areas | `maintenance` | Maintenance windows: matching, overlap, suppression, timezones, release when a window ends, and management. |
| Product areas | `shared-views` | Saved presets and dashboards: definitions, panels, count batches, shared editing and references. |
| Product areas | `operators` | Operator lifecycle and API keys, Kong gateway policy, synchronous provisioning and shared Grafana routing. |
| Product areas | `identity-access` | Sign-in and sessions, tenants and roles, grants, directory publication, tenant visibility and assignee eligibility. |
| Platform | `shared-commands` | Shared mutation rules: request validation, expected revisions, idempotent retries, conflicts and error results. |
| Platform | `database` | PostgreSQL tables, columns, indexes, RLS and privileges, transactions and lock order, and the migration chain. |
| Platform | `http-contract` | Product and Ingestion API endpoints, request/response envelopes, errors, OpenAPI generation and the generated client. |
| Platform | `reliability` | Timeouts and retries, backpressure and admission control, dependency outages, recovery guarantees, telemetry and alerting. |
| UI | `ui-appearance` | Visual spacing, typography, color, layout and styling that preserve interaction, contracts and saved data. |
| UI | `ui-behavior` | Navigation, forms, loading and error states, accessibility, keyboard use and browser state. |
| Engineering | `local-development` | Running the local stack, prerequisites, isolated fixtures, the smoke baseline, test commands and the workspace. |
| Engineering | `dependencies` | Python and Node packages, lock files, internal revision pins and coordinated editable environments. |
| Engineering | `delivery` | Deployment, OpenShift workloads, release artifacts, qualification, readiness and coordinated rollout. |
| Engineering | `documentation` | Agent instructions, this context catalog, navigation, document links and prose that do not change requirements. |
<!-- END generated context index -->

From `keep-delivery` (Python 3.13; standard library only):

```powershell
python scripts/context.py list
python scripts/context.py plan --intent question --topic alert-pipeline --content
python scripts/context.py plan --task "Why does a dashboard count differ from the alert list?"
python scripts/context.py find "scheduler cadence"
python scripts/context.py read --source SOURCE_ID
```

Without `--topic`, `--task` matches request words and `--path` matches changed files;
they suggest at most three topics and infer the intent, which the plan reports. Plans
provide references, reasons, related topics and current source hashes. Read sources
before using them; a plan does not mean they were read or verified. Historical
algorithms can be superseded by current worker scope. Sources remain the authority;
topic pages, extracted text, memories and old test reports do not replace them.

## Repository ownership

| Repository | Owns |
|---|---|
| keep-frontend | Next.js UI, generated client, browser tests and the sign-in page. |
| keep-api | HTTP product API, human auth, synchronous provisioning and live/SSE. |
| keep-ingestion | Stateless v1/v2/Grafana admission, Kafka publication and Kong plugin. |
| keep-processor | Source application, immutable replay proof and Kafka offset transactions. |
| keep-correlation | Changed-event evaluation and automatic incident creation/attachment. |
| keep-indexer | Alert search projection, reconciliation, watermarks and rebuilds. |
| keep-domain | Shared transactional business rules; no server or separate database. |
| keep-contracts | Native/event definitions, generated HTTP artifacts and compatibility. |
| keep-migrations | One ordered PostgreSQL migration chain and dedicated migration image. |
| keep-delivery | Workspace tooling, deployment, runbooks and executed qualification evidence. |
| keep-design | Authoritative specifications, decisions, architecture and acceptance requirements. |

The detailed policies in `keep-delivery/docs/agent-workflows.md` remain binding
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
  incidentally. Keep commits/reviews per repository. Name new branches by the kind of
  work: `feat/`, `fix/`, `docs/`, `refactor/`, `test/` or `chore/` plus a short
  kebab-case description, such as `fix/incident-count-cursor`.
- PostgreSQL is transactional authority; Elasticsearch is a rebuildable current-alert
  projection, Kafka the alert pipeline and Redis a hint transport. Preserve tenant
  scoping/RLS, idempotency, revisions, lock order, atomic history/proof and recovery fences.
- Preserve native v1/v2, fixed Global, immutable operator tenant routing, Kafka-acked
  admission and the PostgreSQL-before-Kafka replay protocol. Ingestion has no Keep
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
- A test of specified behavior names its governing requirement IDs and sections in its
  docstring (`Requirements: I5; incident operations 14.`) so readers can trace it.
- Preserve all 55 original issue IDs and REV numbering. Record new design choices as
  assistant-selected in the existing register; resolve conflicts with settled decisions.
- Reversible application work and isolated tests within the request are authorized.
  Publishing, merging, messaging, production deployment and shared-service changes
  need existing authorization. Dedicated teams retain infrastructure ownership.
- Delegate independent work when useful, at most three children; no recursive
  delegation. Use `parallel-build` with bounded ownership for parallel packages, and
  keep internal work in subagents rather than separate sessions. Keep focused fixes
  small. Substantial tasks keep their plan file in `.plans/` at the workspace root and
  record the objective, decisions, source revisions, progress and next actions there.

## Navigation and maintenance

- Architecture: `keep-design/architecture/README.md`.
- Source authority: `keep-design/specs/spec-alerts-management-system.md` (Specification map).
- Setup and context commands: `keep-delivery/docs/repository-workspace.md`.
- Requirements: `keep-design/verification/verification-plan.md`.
- Verification procedure: `keep-delivery/docs/runbooks/development.md`, section
  "Repeatable verification workflow". Application changes run the agreed smoke
  baseline plus impact-based checks; backend changes affecting UI also require
  browser journeys. Report blocked/skipped checks and repair/retest failures.
  Documentation-only changes use documentation/discovery checks.
- Finish rule: after an app change (anything but docs, CI and agent files), run the
  `smoke-runner` subagent with the command the smoke gate names before finishing;
  the gate blocks finishing without a covering run. Handle its result: fix and rerun
  a failure the change caused, report a stack problem or a block with its reason, and
  state `stack_includes_change` in the final report. See the runbook's "One command
  and the finish gate".
- Executed evidence: `keep-delivery/status/implementation-status.md` and `keep-delivery/status/delivery-readiness.md`.
- Context checks: `python qualification/check_context.py` in delivery.
- Documentation checks: `python qualification/verify_documents.py` in delivery;
  `python scripts/check_docs.py` in design. These are relevant checks, not startup commands.
- Other checks: use the affected repository README/CI. Delivery's prepared `.venv`
  runs `qualification/check_contracts.py`, `qualification/verify_boundaries.py` and
  coordinated `scripts/workspace.py test`; editable installation is an explicit override.

Maintain this file in `keep-delivery/workspace/AGENTS.md` and the thin CLAUDE.md
import beside it. Reconcile root edits before running delivery's
`python scripts/workspace.py bootstrap`; preserve any existing backup. Keep installed
and canonical copies consistent. The context index above is generated from the
catalog by `context.py render`; edit the catalog, not the block. Generic workflow
skills (planning, verification, review) stay in the global agent setup. Bootstrap
also installs the `smoke-runner` subagent, the smoke gate hooks and one allow rule
from `workspace/.claude/`. The context layer itself changes project navigation, not
model selection, native permissions or hooks.
