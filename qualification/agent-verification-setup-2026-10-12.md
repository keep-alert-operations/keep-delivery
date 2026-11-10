# Agent verification setup — 2026-10-12

Documentation/setup change only. The user selected four critical smoke journeys:
identity/access/logout; v1 admission and browser alert work; v2 admission and
tenant isolation; manual incident lifecycle. Dashboards/presets are added when
affected. The user accepted clearly identified synthetic records retained locally
with a run ledger. Expected product behavior remains defined in design.

## Inspected snapshot and scope

All eleven repositories were independent Git roots on `codex/scalability-hardening`.
Delivery HEAD was `c8ad8ba1bd2c0a07b5cc13c0be077d3892efe070`, frontend
`2ac606e65bb5b29f945820a46a32915817e8c187`, design
`69e86646827eab2047f68dd0c5bc212b3af5e6ac`. Existing dirty source, tests,
runbooks, specifications, status files and untracked guides were preserved.
This describes working-tree inspection, not a clean release combination.

Read the root/canonical instructions and Claude imports, delivery/design/frontend
instructions, documentation context route, engineering policies, workspace and
local runtime/identity/v2 runbooks, verification requirements, local test scripts,
Playwright configs/tests and owned CI workflows. No dependencies were installed.

Updated existing delivery guidance and synchronized the installed root AGENTS.md
with its canonical copy. Existing CLAUDE.md files remain unchanged: the root
`@AGENTS.md` import resolves to shared workspace guidance, which explicitly routes
both clients to affected repository instructions. Frontend's own CLAUDE.md also
imports its AGENTS.md. No new hooks, model settings, registries or frameworks.
The existing `AGENTS.md.before-bootstrap` backup was preserved; bootstrap was not
needed because the exact known entrypoint changes were applied to both copies.

## Executed checks

| Working directory | Command/check | Actual result |
|---|---|---|
| `keep-delivery` | `C:/Python313/python.exe qualification/verify_documents.py` | Exit 0: 169 Markdown documents, 566 source entries, REV-01..REV-38, no errors. Includes canonical/installed entrypoint and Claude import checks. |
| `keep-delivery` | `qualification/check_context.py`, invoked by the document check | Passed: 72 sources, 23 routes, 37 evaluation cases, 24 generated documents. These are navigation checks, not agent-compliance tests. |
| `keep-design` | `C:/Python313/python.exe scripts/check_docs.py` | Exit 0: document targets, fences and review identifiers passed. |
| `keep-delivery` | `.venv/Scripts/python.exe -m pytest tests/unit/test_workspace_instructions.py tests/unit/test_context_integration.py -q --basetemp=.local/pytest-agent-verification-20261012` | Exit 0: 9 passed in 0.66s; one non-failing warning that `.pytest_cache/v/cache` could not be created (Windows access denied). No application fixtures or service tests ran. |
| `keep-delivery` | `git diff --check -- AGENTS.md workspace/AGENTS.md docs/agent-workflows.md docs/repository-workspace.md docs/runbooks/development.md` | Exit 0: no whitespace errors in changed tracked guidance. |
| Workspace root | SHA-256 comparison of root/canonical AGENTS.md and CLAUDE.md | Both pairs match exactly; root and frontend Claude imports resolve to existing AGENTS.md files. |

Commands above were executed locally against the dirty workspace described here,
not on remote CI. No application startup, browser smoke, database integration,
gateway checks, build, deployment or fresh agent session ran. Existing application
evidence was inspected only and was not promoted to a current pass.

## Documented procedures and remaining coverage

The [development runbook](../docs/runbooks/development.md#repeatable-verification-workflow)
contains startup/readiness, test access/data, scenario assertions, regression
selection, evidence, cleanup and repair/retest instructions. These are procedures,
not newly executed application evidence.

- S1/S2 have separate opt-in real-stack browser suites; S3 has gateway/API
  automation plus a manual browser check. S4 has a concrete manual procedure and
  partial mocked/PG automated coverage, but no dedicated real-stack E2E automation.
- Frontend CI currently checks generated client drift, formatting, types and
  build, but does not run Playwright. The default browser suite uses controlled
  routes and cannot establish real sign-in or persistence.
- BrowserReal's source still imports the absent
  `keep.ingestion.service.Admission`, uses the retired receipt flow and old note
  selectors. Static inspection establishes fixture drift, not an executed test
  failure. Repair and rerun it before relying on it; synthetic session seeding
  would still not verify Keycloak sign-in.
- Local identity provider logout needs the enrolled console factor. Synthetic
  records persist intentionally; cleanup restores mutations and records retained
  IDs. Production, company integrations, capability recovery and load qualification
  remain separate.

Native file/import/reference checks establish that both clients can discover the
guidance from the parent workspace. No fresh Codex/Claude model session was launched
to test instruction-following behavior; discoverability is not compliance proof.

Future update request: "Refresh the agreed verification workflow against current
scripts and CI; preserve product decisions, local changes and the distinction
between documented coverage and checks executed."
