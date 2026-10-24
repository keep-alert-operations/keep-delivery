# Incident inactivity/retention label removal

The user asked to remove incident screen statements that promise automatic
inactivity resolution or retention removal. [Worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md#2-assignment-and-data-lifetime-automation)
(REV-34) defers both sweeps; the incident detail requirement to show these
deadlines is superseded by that scope correction.

## Revisions

| Repository | Revision | Use |
|---|---|---|
| appchi-frontend | `42e91ce` on `codex/remove-stale-incident-deadlines` (base `9ed49b0`) | UI change and browser tests |
| appchi-delivery | branch `codex/remove-stale-incident-deadlines` (base `fc26f85`) | Client guide and this record |
| appchi-design | `60debd7` | Scope source, read only |
| appchi-api / appchi-domain / appchi-contracts | `08bf1ec` / `d4fb748` / `5420c8f` | Response inspection, read only |

## Change and decisions

- Incident creation help now says incidents stay active until someone resolves
  them and nothing resolves or removes an incident automatically.
- Active detail no longer shows "Resolves automatically after 30 days without
  activity" or its "Deadline …" tooltip; Last activity remains.
- Resolved detail no longer shows "Retained until …".
- Kept: Activity, Cycles and the resolved-cause text for a recorded `inactivity`
  cause, and the alert history `incident-inactivity`/`source-inactivity` entries.
  They describe past events, and incident specification section 4 retains the
  `inactivity` cycle cause. **Assistant-selected.**
- The Product API still returns the stored `retention_at` and `last_activity`
  incident columns as undocumented pass-through fields; the OpenAPI contract
  does not describe them or advertise deferred behavior. Worker scope keeps
  these fields for compatibility, so API, contracts and generated client are
  unchanged.
- Client guide chapters 4 and 7 no longer say some screens still show
  deadlines; the no-sweep statements remain.

## Checks

From `appchi-frontend` (Node 22.17.1, Chrome 155.0.8059.86):

- `npm run typecheck`: passed.
- `npm run format:check`: reported 90 files, because this Windows checkout has
  `core.autocrlf=true` and Prettier defaults to LF. Both changed files pass
  Prettier when checked as LF (repository) content.
  `npx prettier --check --end-of-line auto src tests *.ts *.json` reports only
  the unchanged `tsconfig.json`, which has mixed endings in this checkout.
- `PLAYWRIGHT_CHANNEL=chrome npx playwright test tests/worker-scope.spec.ts tests/incident-notes.spec.ts tests/incident-filters.spec.ts tests/workspace.spec.ts`:
  45 passed, including three new worker-scope incident tests. The bundled
  Playwright browser was missing, so the documented installed-Chrome channel was used.

Browser review used the frontend dev server routed through `APPCHI_BACKEND_URL`
to a temporary loopback-only synthetic API fixture in the in-app browser. The
incident list, creation page, active detail and resolved detail (with a stored
`retention_at`) showed none of the removed labels. Desktop (1366 px) and phone
(375 px) renders were inspected. At about 800 px the detail control row is
crowded; the removed text was wider, so this was not introduced here.

From `appchi-delivery`: `index.html` was regenerated with
`node build-guide.mjs <bundled marked 17.0.5>` from an LF-normalized copy of the
guide sources, so only the two edited paragraphs changed.
`python qualification/verify_documents.py` checked 172 documents, 566 source
entries and REV-01..REV-38 with no link, fence or numbering error. It exited 1
on one existing, unrelated entrypoint error: the installed workspace `CLAUDE.md`
(LF) differs byte-wise from this checkout's `workspace/CLAUDE.md` (CRLF under
`core.autocrlf`); both contain only `@AGENTS.md`. It was not changed here.
`git diff --check` passed.

## Not executed

The S1–S4 smoke baseline and a real-stack incident journey did not run:
PowerShell 7, required by `scripts/local-runtime.ps1`, is not installed and
Docker Desktop was not running. Mocked browser checks do not establish real
authentication, persistence or API integration.
