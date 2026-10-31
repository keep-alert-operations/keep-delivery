# Implementation status

## 2026-10-30 Incident reports by period (REV-53)

The user approved the [incident reports mock](https://claude.ai/artifact/NxBWGUChKyvq1CnRJmxoky)
(option A, incidents only; noise reports left for later) and the
[implementation plan](../docs/incident-reports-implementation-plan.md). Register REV-53;
[incident operations 20](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-incident-operations.md#20-incident-reports-i5-n3-rev-53).
Committed on `codex/incident-reports` branches and opened as pull requests, not merged:
design `63c4033` (stacked on the REV-52 branch `0680546`, open PR #17, which must merge
first), migrations `8cfa16c`, API `4132381`, contracts `8e473d3`, frontend `01fb357`
(pins `@appchi/contracts` `8e473d3`) and `7a945df`, from `origin/main` (`2d0e8c7`,
`5bb8de8`, `9f4736f`, `1a3dfc5`, `ccd6e33`). This delivery change pins those branch
heads in the manifest, `pyproject.toml` and the lock; merge every pull request with a
merge commit so the pinned SHAs stay on main. Domain, ingestion, processor, correlation
and indexer are unchanged.

- Migrations: 0058 indexes `incident_cycles(tenant_id,opened_at)`, `(tenant_id,resolved_at)`
  where resolved and `incident_activity(incident_id,recorded_at)` for `assignment` and
  `eligibility_lost`; README, checksums, head and chain-length tests, and an upgrade test
  that the report's reads use the leaf indexes on all 32 partitions.
- API: `GET /api/tenants/{tenant_id}/reports/incidents` (`reports/incidents.py`, pure
  `reports/figures.py`). One read-only repeatable-read transaction after `authorize`
  (view): cycles opened or resolved in the period and the previous period (1.2 s budget,
  503 `query_deadline`, 100,000-cycle limit 422 `report_too_large`), then the period's
  assignment history in a savepoint (0.7 s budget; on timeout first response and the
  assignee breakdown are reported unavailable). Day and week bins are computed with the
  pinned tzdata, so DST days have 23 or 25 hours. The history read is set-based: per-cycle
  lookups took about 2 s on 20,000 cycles, and planning a literal 20,000-element id array
  took 0.55 s, so the array goes through a scalar subquery.
- Contracts: regenerated with `generate_http_contracts.py`; additive only (one path and
  18 `Report*`/`IncidentReport*` schemas, no existing path or schema changed).
- Frontend: Reports rail item, `reports-pages.tsx`, `styles/reports.css`, `chart-1` and
  `chart-2` tokens (validated with the dataviz palette checker for both themes), the
  incident list reading its filters from the URL once, `generated.ts` regenerated from
  the local contracts, `docs/ui-design.md` and `DESIGN.md`. `.impeccable/design.json`
  was not regenerated.
- Design: REV-53, incident operations 20 and preamble, system completion 3 and 10,
  specification map, verification plan "Incident reports (REV-53)", `check_docs.py`
  REV-01..53, context `task-map.json` and rendered routes. Delivery: this entry, the
  plan, `verify_documents.py` REV-01..53 and the readiness rollout note.
- Executed on 2026-10-30 on the uncommitted worktrees, with delivery's `.venv` holding
  editable siblings and an isolated PostgreSQL 18.2 container with synthetic data only:
  migrations unit and Alembic integration 37 passed; `test_incident_reports.py` 5
  passed (lifecycle through the API, exact figures on seeded history with a deleted rule,
  a retired incident and another tenant, the partial result on a forced history timeout,
  access and validation, and a 90-day report over 20,000 cycles and 200,000 activity
  rows in 0.69 s through the API); API report unit tests 14 passed. Coordinated unit
  suites passed: contracts 33, domain 553, migrations 5, API 224, ingestion 103,
  processor 49, correlation 2, indexer 88 and delivery 256 (65 skipped for unset
  services). Five API unit tests that start the native CEL compiler first failed on its
  one-second deadline while the browser suite ran alongside; they passed on an idle rerun.
  Design `check_docs.py`; delivery `verify_documents.py`, `check_context.py`,
  `check_contracts.py` (OpenAPI matches the application) and `verify_boundaries.py`
  passed. Frontend: `tsc --noEmit` and `next build` passed (frontend has no lint script;
  `format:check` flags only CRLF working copies of untouched files on this Windows
  checkout, and every changed file passes Prettier). Playwright in installed Chrome (the
  pinned headless shell 1243 is not installed): the full suite 234 passed and 8 skipped;
  `incident-report.spec.ts` 6 passed (default period request, figures and deltas, the
  breakdown and its link into the filtered Incidents list, 90 days and custom past
  retention, 24 hours, Compare off, first load, empty, partial with Retry, failure with
  Retry, refresh keeping figures, keyboard definitions, table view, dark and 390 px); the
  real HTTPS browser/API/PostgreSQL case in `integration-real.spec.ts` passed (empty
  period, held first load, figures from real incident commands, breakdown with "(you)",
  light and dark at 1280 and 390 px with no page overflow, a failed report recovering on
  Retry, and the Critical row opening the filtered Incidents list); its screenshots were
  reviewed.
- Executed on the shared local stack, 2026-10-30, at the user's request: the worktree
  API ran on 127.0.0.1:58100 and the worktree UI replaced the main UI on
  https://localhost:3443; the main API, ingestion and workers were unchanged, and Kafka
  was started from the main Compose configuration. Migration 0058 was not applied to
  `appchi_local`, so main's migration history stays valid; the report is correct
  without it. S1 identity 5 passed and S2 native v1 1 passed (run from the main
  frontend checkout as black-box suites). S4 with real Keycloak sign-in as
  `local-editor` passed in installed Chrome: explicit High severity, link to the run's
  alert, a note kept over reload, assignment, Investigating, Resolved with the alert
  still firing, and a reopen starting a second cycle with the link kept. The Reports
  page then showed this activity (24 hours in Global: 4 opened, 3 new and 1 reopened,
  1 resolved and reopened, first response 378 s); light and dark at desktop and
  390 px, first load, a failed report recovering on Retry, and Local Demo's empty
  24 hours were captured. Two earlier attempts left synthetic incidents: their notes
  returned 409 because the test typed before the live refresh updated the notes
  revision after linking, which the page reports as "The resource changed". Ledger and
  screenshots are under ignored `.local/verification/6966c7d1-3625-4146-8cd7-8044589efb1d`.
- Not run: S3 (Kong and the local v2 services were stopped; ingestion is unchanged),
  Kafka- and Elasticsearch-bound suites, CI and production qualification.

## 2026-10-29 Plainer alert actions and a steadier dismiss dialog (REV-51)

After trying REV-49 on the local stack the user asked to drop the "…" from Dismiss,
Link to incident and Assign in the row menu and on the alert page, to give every
alert-page action an icon, to stop the dismiss dialog flagging an empty custom number
as an error, and to keep the dialog one size. Frontend only; no API, contract or
migration change. Merged to main through pull requests on 2026-10-29: frontend #12
(`1a3dfc5`) and design #16 (`c973b4d`), whose CI `verify` passed; this delivery change
pins both heads in the manifest.

- Frontend: `icons.tsx` gains view, resolve, reopen, assign, link, clock, undo and
  external icons; `alerts.tsx` uses them on View alert, Resolve/Reopen alert, Dismiss,
  Assign, Link to incident, Open runbook and the band's Change and Undismiss;
  `alert-table.tsx` drops the ellipses from its row menu. `dismiss-dialog.tsx` treats an
  empty custom field as waiting (neutral message, no red border, Dismiss disabled) and
  stacks both modes, the longest summary and command errors in fixed grid cells.
  `docs/ui-design.md` describes it. Bulk actions and incident pages keep "Assign…".
- Tests: `dismissal.spec.ts` gains an empty-custom and constant-size case; its
  refresh case compares runs with and without a dismissal ending, with the live
  stream held so reconnect reloads cannot fall in the window. Label expectations
  updated in ui-cleanup, link-incident, redesign, workspace, local-runtime and
  integration-real specs.
- Design: REV-51; alert operations 29 and incident operations 5 wording;
  `check_docs.py` expects REV-01 through REV-51. Delivery: `verify_documents.py`
  expects REV-51; client guide step "Select **Assign**" (the HTML edition was edited to
  match because no `marked` module is installed to rerun `build-guide.mjs`).
- Executed against the running local HTTPS dev server (mocked API routes, installed
  Chrome): `tsc --noEmit` passed; the dismissal spec passed 20 of 20 repeats; 75 tests
  across dismissal, ui-cleanup, link-incident, redesign, workspace, grouping,
  quick-views, alert-drag-select, loading-states, theme and alert-evidence passed. In
  the signed-in local app the dialog measured 546 px in every state at 1024 px and
  668 px at phone width; a real click on the empty custom field showed the neutral
  message. Design `check_docs.py`, delivery `verify_documents.py` and
  `check_context.py` passed.

## 2026-10-29 Free panel width and height, sized on the grid only (REV-50)

The user asked to remove Width and Height from the panel create/edit dialog, remove the
panel's maximum height and let width grow one column at a time to the full page width.
See the design register REV-50 and dashboard operations 4. It was built as REV-49 with
migration 0056; the timed alert dismissal merged REV-49 and 0056 first, so it was
renumbered REV-50 with migration 0057 and rebased onto those mains. Merged to main
through pull requests on 2026-10-29 with merge commits, in dependency order: domain #13
(`d34e038`), contracts #4 (`9f4736f`), migrations #5 (`2d0e8c7`), API #8 (`5bb8de8`),
ingestion #7 (`b20440a`), processor #6 (`fcd38b4`), correlation #5 (`2535bea`) and
indexer #9 (`f605f5b`) (each pins domain `d34e038`), frontend #10 (`4b3a266`, pins
contracts `9f4736f`) and design #14 (`cf76bb7`). Every PR's CI `verify` passed on its
final head before its merge. This delivery commit pins those heads in the manifest and
lock. Domain keeps pinning contracts `ae5ce85`: only the HTTP schema files changed, which
the contract check reads from the workspace checkout. Old layouts stay valid.

- Domain: `Panel.w` is an integer 3–12 and `Panel.h` an integer from 2, still inside
  x+w <= 12 and y+h <= 100; new `tests/unit/test_dashboard_panel_size.py`.
- Migrations: 0057 replaces the `dashboard_panels` checks `w IN (3,4,6,12)` and
  `h IN (2,3,4)` with `w BETWEEN 3 AND 12` and `h >= 2`; the grid check is unchanged.
  No stopped-writer window; run it before the updated API. The signed-in journey found
  it: without it PostgreSQL rejected a 7 by 8 panel, which the API reports as 503
  `database_unavailable`.
- Contracts: panel `w`/`h` enums become integer ranges in `openapi.json` and
  `product.openapi.json`, on top of `set_dismissal`.
- Frontend: the panel dialog drops the Width and Height selects; `dashboard-layout.ts`
  validates whole sizes from 3 by 2; pointer resize snaps to the nearest whole column
  and unit up to the row edge and the grid bound; arrow keys step by one. Below 1024
  pixels the handles stay hidden, so size can no longer be changed there.
  `generated.ts` matches `npm run generate` from contracts `9f4736f`.
- Tests: `dashboard-layout.spec.ts` adds a keyboard case (width 3 to 12 one column at a
  time, height 2 to 9, no size fields in the dialog, saved `w: 12, h: 9`) and
  `placePanel` checks; the responsive cases edit the title instead of the removed
  Height select.
- Executed on the rebased code: domain unit suite 553 passed; migrations unit and
  Alembic integration on a disposable PostgreSQL 18.2 container 35 passed (chain to
  0057, upgrade through 0056); frontend `tsc --noEmit`; design `check_docs.py`; delivery
  `verify_documents.py`, `check_context.py`, `check_contracts.py` and
  `verify_boundaries.py`; Poetry 2.3.1 `check --lock` in delivery and all five services.
  Before the rebase (frontend on `79412f6`, isolated copy, installed Chrome):
  `dashboard-layout` 37 of 38 passed; the failure, "picking up a settling panel
  preserves its visible position", fails intermittently on base `79412f6` too.
  `preset-choices`, `dashboard-counts` and `redesign` passed. API unit failures seen
  then were native-CEL tests that pass on rerun alone.
- Signed-in local journey (2026-10-29, before the rebase, without the dismissal): the
  local Product API was restarted on the new domain with the launcher's environment
  and process record, and the migration then numbered 0056 was applied with
  `scripts/local_identity.py database`. As Local Administrator on Global at 1440 by 900,
  a new `smoke-rev49 panel size` dashboard got a `testing` panel; the Create and Edit
  panel dialogs show no Width or Height. Arrow keys gave 5 by 6 and a corner drag
  snapped to 7 by 8. Save before the migration returned 503; after it the create
  returned 201 and reload read `w: 7, h: 8`. Widening stopped at 12; the update returned
  200 and reload read `w: 12, h: 10`. Deleting the smoke dashboard returned 503
  `recovery_not_ready` (local recovery gate closed), so dashboard `b1bfe88c-…` remains.
- Local stack: its ledger recorded that pre-renumbering `0056_free_panel_size.sql`; the
  row was removed and `alembic_version` set back to 0055 so the merged 0056 and 0057
  apply through the migration step.
- Not run: the journey on the merged code, `next build`, the full Playwright suite and
  delivery integration tests against PostgreSQL.

## 2026-10-29 Dismiss an alert for a time window (REV-49)

The user asked to dismiss an alert for minutes, hours or until a date, approved the
[dismiss mock](https://claude.ai/artifact/Nvp1SJzsc29Y1HYSnfcK99) (dismissed alerts are
suppressed) and chose that the first source update after a dismissal ends may restart
correlation, as an ended maintenance window does. See the design register REV-49 and
alert operations 29. Merged to main through pull requests on 2026-10-29 with merge
commits, in dependency order: contracts #3 (`ae5ce85`), migrations #4 (`5086d7e`),
domain #14 (`3b1bf0e`, pins contracts `ae5ce85`), API #9 (`07f50bf`), indexer #10,
processor #7, correlation #6 and ingestion #8 (each pins domain `3b1bf0e`), frontend #11
(`672a0c6`, pins contracts `ae5ce85`) and design #15 (`9b3c07c`). Every PR's CI
`verify` passed before its merge; the API run first failed on the OpenAPI display-field
test and the frontend run on Prettier, both fixed before merging. This delivery commit
pins those heads in the manifest and lock.

- Migrations: `0056_alert_dismissal` adds `alerts.dismissed_until`, `dismissed_at`,
  `dismissed_by`, a completeness check and a partial index. Additive; the stored
  `suppressed` flag keeps meaning source and maintenance causes.
- Contracts: `DismissalValue` and the `set_dismissal` alert command kind (not bulk);
  HTTP schemas regenerated with `qualification/generate_http_contracts.py`.
- Domain: `alerts/dismissal.py`; `apply_command` validates firing-only, server-clock
  durations and the 30-day bound; a new occurrence ends a dismissal; the first source
  input or command at or after the end clears it. Reads, CEL `suppressed`, feed and count
  filters and incident counts treat a running dismissal as suppression at their clock.
  Correlation treats a recorded dismissal as suppression until a write clears it;
  dismissing calls `release_alert`; a maintenance release during a dismissal creates no
  qualifier. Search documents carry `dismissed_until` only when present; feed, group
  and count responses return `next_change_at` from a global aggregation in the same
  request.
- API: `set_dismissal` behind `APPCHI_ALERT_DISMISSAL_ENABLED` (default off), the reason
  written as a note in the same transaction, command and detail responses with
  `dismissal`.
- Frontend: `dismiss-dialog.tsx`; Dismiss…, Change dismissal… and Undismiss in the row
  menu and on the alert page; "Suppressed · until" indicator; detail band and history
  labels; Feed and alert page refresh at the end. Client regenerated with `@appchi/contracts`
  pinned to `ae5ce85`.
- Rollout: run 0056; deploy the indexer and writers; rebuild the search index so it has
  the `dismissed_until` mapping (strict mapping; local bootstrap compares it exactly);
  then enable the flag; then the UI. Rollback is the flag; older code ignores the
  columns, so a dismissed alert becomes visible rather than hidden.
- Executed (this checkout, editable worktree sources, disposable PostgreSQL 18 on
  127.0.0.1:55491, local Elasticsearch 9.0 test indices, no Kafka binding):
  unit suites: domain 551 and API 210 with only CEL one-second compile-deadline timeouts
  failing (different tests each run; they pass on rerun), contracts 33, migrations
  unit and Alembic integration 34, indexer 88, processor 49, correlation 2; new
  `test_dismissal.py` (domain, 10), contracts `test_alert_dismissal_command.py` and
  delivery `test_alert_dismissal.py` (4, with `test_product` 9) passed. The delivery
  integration suite run file by file had failures in 20 files; rerunning those files on
  this branch and on `main` (`appchi-workspace` checkout, same bindings) gave 17 failing
  tests here and 69 on `main`. The only branch-only failures were `test_cel_routes`
  compile-deadline timeouts, which then passed 2 of 3 reruns, matching `main`'s latency.
  Kafka-bound cases were skipped without `APPCHI_TEST_KAFKA_BOOTSTRAP`. Design
  `check_docs.py`, delivery `verify_documents.py`, `check_context.py` (after
  `context.py render`), `check_contracts.py` and `verify_boundaries.py` passed.
  Frontend with installed Chrome: `tsc --noEmit` and Prettier on changed files passed;
  new `dismissal.spec.ts` (5 cases, run twice) and the related grouping, quick-views,
  preset-columns, redesign, workspace, alert-drag-select, preset-settings-tabs,
  loading-states, alert-evidence, live-workspace-recovery, theme and ui-cleanup specs
  (87) passed; the real HTTPS browser/API/PostgreSQL journey (`integration-real.spec.ts`,
  new dismissal case: dialog, error state, dismiss with reason, band, reload, change to
  one minute, return by time alone, undismiss, history labels, phone width) passed. The
  existing real smoke passed once and later timed out in its 15-second fixture call
  on the loaded host.
- Not run: S1–S4 on the shared local stack (its database and search index were left
  untouched; it needs 0056 and a rebuild first), Kafka-bound integration, the search
  rebuild against a mapping change, and delivery CI on the pinned heads.

## 2026-10-28 Link an alert to an incident from the alert (REV-48)

The user asked to link an alert to an incident from the alert page and from the Feed
row's ⋯ menu. See the [design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md#rev-48--link-an-alert-to-an-incident-from-the-alert-2026-10-28).
Frontend only: it reuses the existing link and move commands; no API, contract or
migration change. Merged to main through pull requests on 2026-10-28 by rebase:
frontend [#9](https://github.com/appchi-alert-operations/appchi-frontend/pull/9) as
`4fb364c` (on `79412f6`, which merged drag-to-select as #8) and design
[#13](https://github.com/appchi-alert-operations/appchi-design/pull/13) as `d2f1f1f`.
The workspace manifest names those heads. Tests and the local journey ran on
drag-to-select commit `4c5fb8f` plus this change; `4c5fb8f` and `79412f6` have
identical trees, and the rebased commit passes `tsc`. CI: frontend and design
`verify` passed on the pull-request heads before the merges.

- Frontend: new `link-incident.tsx` (`LinkIncidentDialog`: search active incidents,
  choose one, Link at its revision, or Move with both revisions when another active
  incident owns the alert); `alerts.tsx` opens it from the alert page (beside Assign…)
  and the Feed rows; `presets-pages.tsx` from preset result rows; `alert-table.tsx`
  and `alert-groups.tsx` carry `onLinkIncident` to the row menu; `incidents.css`
  styles it; `docs/ui-design.md` describes it.
- Tests: new `link-incident.spec.ts` (4 cases: Feed menu link body and path; alert page
  move body with both revisions and the owner marked Linked here; Escape returns
  focus without a command; viewers see no action); `ui-cleanup.spec.ts` expects the
  third menu item.
- Design: REV-48; incident operations 5; `scripts/check_docs.py` expects REV-01
  through REV-48. Delivery: `verify_documents.py` expects REV-01 through REV-48.
- Executed with installed Edge against the already-running HTTPS dev server (mocked
  API routes, as the specs define): `tsc --noEmit` passed; Prettier passed on every
  changed file; `link-incident`, `alert-drag-select`, `ui-cleanup`, `grouping`,
  `quick-views`, `preset-columns`, `preset-settings-tabs`, `alert-evidence`,
  `redesign`, `incident-notes` and `incident-filters` passed (`ui-cleanup` after its
  menu expectation was updated). Design `check_docs.py`, delivery
  `verify_documents.py` and `check_context.py` passed.
- Signed-in local journey (run `fdbfd6a6-dd0f-49b4-8ffd-18f34272b9c4`, ledger under
  `.local/verification/`): the user signed in as Local Administrator on Global; the
  running dev server served this working tree against the unchanged local API. From
  the Feed row menu a synthetic firing alert with no incident was linked to a new
  `smoke-fdbfd6a6` incident (link 200; the row showed it); from the alert page the
  dialog named that owner, marked it Linked here and offered Move to incident, which
  moved the alert to a second smoke incident (move-alerts 200). Both incidents' and
  the alert's activity recorded link and move. Cleanup unlinked the alert and resolved
  both smoke incidents. The only console error was one `view-state` 500 shortly after
  sign-in, before any link command.
- Not run: the full Playwright suite and `next build`.

## 2026-10-28 Preset editor settings in tabs (REV-47)

The user found the preset create/update screen overloaded and chose option D of the
[preset editor options canvas](https://claude.ai/artifact/CkERUg32kLwT2AuWdzSJML),
keeping Validate CEL. See the [design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md#rev-47--preset-editor-settings-in-tabs-beside-the-preview-2026-10-28).

Merged to main through pull requests on 2026-10-28: frontend
[#7](https://github.com/appchi-alert-operations/appchi-frontend/pull/7) as `e578791`
and design [#12](https://github.com/appchi-alert-operations/appchi-design/pull/12) as
`715eb5a`, both rebased onto unchanged mains (`9270f25`, `b63ac17`). The workspace
manifest names those heads. Frontend only: no API, contract or migration change.
CI: design `verify` passed; frontend `verify` (format, typecheck, `next build`) passed
on the pushed commit before the merge, and the pull-request run of the same commit
passed just after it.

- Frontend: `definition-editor.tsx` splits the editor into the preview panel (draft
  strip, quick views, table) and a Preset settings panel (Name, then Filter, Columns,
  Sort and Group by tabs); `cel-editor.tsx` replaces the preset-only `fieldChips` row
  with a `referencePopover` option and reports validity through `onValidity`;
  `presets.css` and `alerts.css` restyle them. `docs/ui-design.md` and `DESIGN.md`
  describe the screen.
- Tests: new `preset-settings-tabs.spec.ts` (6 cases: keyboard tabs and kept state,
  hidden-tab problem flags, empty name, 422 save opens Filter, Add columns and the
  saved body, popover and Escape); `preset-columns`, `grouping` and `cel-editor`
  updated for the tabs, the column list name, the Group by radios and the popover.
- Design: REV-47; `scripts/check_docs.py` expects REV-01 through REV-47. Delivery:
  `verify_documents.py` expects REV-01 through REV-47.
- Executed on the final code with installed Chrome against the already-running HTTPS
  dev server (mocked API routes, as the specs define): `tsc --noEmit` passed;
  Prettier passed on every changed file; the four affected specs plus quick-views and
  ui-cleanup ran 35 passed; an earlier run of the related specs (quick-views,
  ui-cleanup, configuration-history, workspace, redesign, loading-states, theme,
  preset-choices, navigation-session filters) ran 216 passed, 7 skipped, before three
  CSS-only fixes found by screenshots (popover clipped by the settings panel, tab bar
  cut off at 375 pixels, radio labels stacked by the global label rule). Screenshots
  at 1440, 1024, 900 and 375 pixels in light and dark were reviewed. Design
  `check_docs.py` passed; the Impeccable detector reported only three pre-existing
  radius advisories.
- Not run: `next build` (it would share `.next` with the running dev server), the
  full Playwright suite, a signed-in journey on the local stack (Docker and the API
  were down), and CI. `prettier --check src tests` still reports 54 untouched files
  whose working copies have CRLF endings.

## 2026-10-27 REV-39–46 merged to main

The user asked to merge the grouping work together with the then-uncommitted REV-39–41
and REV-46 code and the client-guide refresh. Each is its own commit; every repository
fast-forwarded main to its branch (`codex/alert-grouping`, or
`codex/always-on-correlation-maintenance` for correlation, processor, indexer and
ingestion). Python services pin appchi-domain `98986b6`, frontend pins appchi-contracts
`749f670`, and delivery's manifest and lock name the merged heads.

Executed on the final code before the repins, with editable siblings (Windows, Python
3.13, a disposable PostgreSQL 18.2 container):

- Unit: domain 541, API 210, indexer 88, ingestion 103, processor 49, migrations 5 and
  correlation 2 passed. The processor's two receive-lead tests first failed because
  REV-41 moved their log values into the message; the tests now read the message.
- Integration: migrations Alembic 28 passed (chain 0052→0055); processor 3 passed;
  delivery 419 passed, 36 skipped, 12 failed while the browser suite ran in parallel.
  All 12 (cutover literal check, inline maintenance, maintenance alert test, idle
  reconciliation, scalability page statements) passed when rerun with their modules,
  24 passed.
- Frontend: `tsc --noEmit` passed; with installed Chrome against the running HTTPS dev
  server, 208 passed, 7 skipped and 2 failed waiting on a first page compile (grouping
  Save as preset, theme persistence); both specs passed on rerun, 8 passed.
- Documents and boundaries: design `check_docs.py`, `verify_documents.py`,
  `check_context.py`, `check_contracts.py` and `verify_boundaries.py` passed.
- REV-46 signed-in local journey: see the REV-46 entry below.

Not run: Elasticsearch- and Kafka-bound integration cases (no isolated bindings were
supplied, so they skipped), the S1–S4 real-stack suites, and CI on the pushed commits.
The local stack's database was already at 0055.

## 2026-10-27 Resolve an incident when all its linked alerts resolve (REV-46)

The user asked that an incident resolve when all of its linked alerts are resolved. See
the [design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md#rev-46--resolve-an-incident-when-all-its-linked-alerts-resolve-2026-10-27).

Committed on `codex/alert-grouping` in domain, API, frontend, design and delivery,
above the REV-39–41 code commits, and merged to main with REV-39–46 on 2026-10-27. No
migration or contract change: `cause` is free text.

- Domain: `incidents/state.py` adds `became_resolved` and
  `resolve_when_alerts_resolved` (same effects as manual resolution, cause
  `alerts_resolved`, activity `alerts-resolved`, alert history
  `incident-alerts-resolved`); the source processor and the deferred
  `expire_alert` call it after publishing the transition.
- API: `alerts/routes.py` calls it after single commands and once after a bulk loop.
- Frontend: incident detail, Cycles and Activity name the new cause; alert history names
  the release; the New incident facts no longer say nothing resolves automatically.
  Test: `worker-scope.spec.ts` adds one case and updates the facts text.
- Design: REV-46; incident operations preamble and 4/12; correlation rules 15;
  verification plan Incidents; `scripts/check_docs.py` expects REV-01 through REV-46.
- Delivery: new `tests/integration/test_incident_auto_resolution.py` (4 cases);
  `test_product.py` bulk case now expects the incident resolved; client guide 03/04;
  `verify_documents.py` expects REV-01 through REV-46.
- Checks on the final backend code, in a throwaway PostgreSQL 18.2 container:
  the new file plus `test_product.py` ran 11 passed, 2 failed; the two failures were
  CEL cold-compile deadlines in preset tests and both passed alone. Correlation,
  Kafka correlation batching, source-driven and inline maintenance, inactivity pages,
  incident listing, alert cleanup, incident retirement, correlation cleanup and note
  revision suites ran 81 passed, 1 skipped. API `test_alert_ownership.py` and domain
  lock/preparation unit tests passed; Ruff passed.
- Frontend: TypeScript and Prettier passed; with installed Chrome against the running
  HTTPS dev server, `worker-scope`, `incident-filters` and `incident-notes` ran 18
  passed, and `worker-scope` 6 passed after the copy change.
- Documents: design `check_docs.py` passed; delivery `verify_documents.py` had no errors.
- Signed-in local stack, 27 October, as `local-editor` in Global with the stack restarted
  on this code (migrations at 0055), using the two synthetic `smoke-40060096-…` v1 alerts:
  a new High incident linked both; resolving alert a left it Open; resolving alert b
  resolved it, with "when all linked alerts resolved" in the detail, Cycles ("Cycle 1
  resolved when all linked alerts resolved") and Activity, and alert b's history read
  "Released from its incident after all of the incident's linked alerts resolved". A new
  firing v1 admission for alert a (202) opened occurrence 2 with no incident, and the
  incident stayed resolved.

## 2026-10-27 Assigned to me, quick-view toggle and a shorter Reset (REV-45)

The user asked for a quick filter for alerts assigned to them, for pressing a selected
quick view again to return to All, and for the grouping toolbar's Reset to saved button
to read Reset. See the
[design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md#rev-45--assigned-to-me-quick-view-toggle-and-a-shorter-reset-2026-10-27).

Committed locally on the `codex/alert-grouping` branches, not pushed: design
`9d18998` (from `1326e47`), frontend `7d72ad9` (from `04e6827`) and this delivery
commit (from `781e861`). The frontend's existing uncommitted `administration.tsx`
edit is not part of this change and stays uncommitted. No API, domain, contract,
migration or indexer change: `assignee_id` was already in the Feed CEL catalog and the
quick-count batch allows 24 queries.

- Design: REV-45; alert operations, Product API and screens; preset section 4 (Reset);
  verification plan; `scripts/check_docs.py` expects REV-01 through REV-45.
- Frontend: `alert-table.tsx` adds Assigned to me (`assignee_id` equal to the signed-in
  principal's ID, escaped as a CEL string) after a divider without a dot, and pressing
  the selected view again selects All; `composeCel` takes that ID on the Feed, preset
  results and editor preview; `presets-pages.tsx` labels the grouping button Reset with
  a "Back to the saved grouping" tooltip; `DESIGN.md` and `docs/ui-design.md` follow.
  Tests: new `tests/quick-views.spec.ts` (3), and `ui-cleanup.spec.ts` and
  `grouping.spec.ts` updated.
- Delivery: `verify_documents.py` expects REV-01 through REV-45.
- Checks on the final code:
  - Documents: design `check_docs.py` passed; delivery `verify_documents.py` had no
    errors and `check_context.py` exited 0.
  - Frontend: TypeScript passed; Prettier passed on every changed file. Installed
    Chrome against the running HTTPS dev server: the full suite before the toggle ran
    205 passed, 7 skipped, 3 failed (two `dashboard-counts.spec.ts:247` live-refresh
    modes and `preset-columns.spec.ts:268`, whose table did not render within 5
    seconds); all three passed alone, and the dashboard test passed 6 of 6 repeated.
    After the toggle, the quick-view, cleanup, grouping, redesign, history, workspace,
    correlation-tester and preset-columns specs ran 72 passed, 0 failed.
- Not verified: the signed-in local stack (the user chose to skip the live browser
  check and rely on the tests); the full suite was not rerun after the toggle.
- Pending: pushing and the REV-44 pins listed below.

## 2026-10-27 Group alert results by one field (REV-44)

The user asked to group Feed and saved-preset alerts by a field, each group a mini
table, approved the [alert grouping mock](https://claude.ai/artifact/FhJsfdk4VB7kAXkjBfx9Dm)
and the plan, and chose most-alerts-first ordering with a 1,000-value cap. See the
[design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md#rev-44--group-alert-results-by-one-field-2026-10-26).

Committed locally on new `codex/alert-grouping` branches, one per repository, not
pushed: design `1326e47` (from `dbe6c9c`), migrations `322e72e` (from `e34fdea`),
domain `279c6be` (from `d4fb748`), API `ccb0733` (from `08bf1ec`), contracts `749f670`
(from `5420c8f`), frontend `04e6827` (from `55ed846`) and this delivery commit (from
`b58a2f6`). The user's own uncommitted always-on correlation work (migrations
0053/0054 and the domain, API, indexer and frontend edits, including a staged domain
deletion) stays out of those commits and uncommitted in the same checkouts. The
migration chain-count expectations in `test_migration_package.py` and
`test_alembic.py` count 0053 and 0054 as well, so they stay uncommitted with that
work.

- Design (`codex/ui-cleanup` @ `dbe6c9c` + edits): REV-44; preset sections 3, 4, 8,
  14 and 16; alert operations 24; technical baseline 17; product model; verification
  plan; `scripts/check_docs.py` expects REV-01 through REV-44.
- Migrations (@ `e34fdea` + the user's 0053/0054): `0055_preset_group_by` adds
  nullable `presets.group_by`, chained after 0054, with its checksum, README note and
  chain tests.
- Domain (@ `d4fb748`): `GROUPABLE` and the optional `PresetDefinition.group_by`;
  `SearchReader.groups` (one composite aggregation for exact counts on the rows'
  PIT, compiled `display_status` filters, 1,000-value cap, one multi-search for the
  shown groups' first rows) and `page(group=...)` for group continuations; group
  labels and response models.
- API (@ `08bf1ec`): preset saves persist `group_by`, and a null value is left out
  of command fingerprints; `POST /alerts/groups`, `POST /presets/preview/groups`, and
  group rows on `GET /alerts` and `POST /presets/preview`.
- Contracts (@ `5420c8f`): `product.openapi.json` and `openapi.json` regenerated with
  `qualification/generate_contracts.py`, additions only.
- Frontend (`codex/ui-cleanup` @ `55ed846`): Group by control, grouped results
  (`alert-groups.tsx`), Feed and preset wiring with Reset to saved, editor field,
  history comparison, styles, `DESIGN.md` and `docs/ui-design.md`; `generated.ts`
  regenerated from the local contracts snapshot (the `@appchi/contracts` pin still
  needs advancing after contracts are committed).
- Indexer: no change; every groupable field already has an exact keyword mapping.
- Delivery: `tests/integration/test_preset_grouping.py`; `check_docs.py` and
  `verify_documents.py` expect REV-01 through REV-44.
- Checks on the final code, against the working trees above (including the user's
  uncommitted work):
  - Documents: design `check_docs.py` passed; delivery `verify_documents.py` had no
    errors and `check_context.py` exited 0; `check_contracts.py` reported the OpenAPI
    contract matches the application.
  - Migrations: unit 5 passed; Alembic integration 28 passed (fresh install, upgrades
    from every listed revision through 0054, CLI, privileges) on a disposable
    PostgreSQL 18 cluster.
  - Domain: unit 541 passed; on a real two-shard Elasticsearch 9.0 index, the new
    grouping compatibility tests 4 passed (exact ordered groups, continuations
    without gaps, cursors bound to their group, the empty group last, group-list
    continuation, status groups equal to the Status quick views, the 1,000-value
    cap); the indexer's existing reader compatibility test 1 passed.
  - API: unit 210 passed; with real PostgreSQL, identity and Elasticsearch bindings,
    the configuration, count, read, deletion, retention, command-retention,
    display-name and new grouping suites 66 passed, and the search pipeline, restore
    fencing, product and scalability suites 37 passed; ruff check passed.
  - Frontend: TypeScript passed; Prettier passed on every changed file. The controlled
    Chrome suite (installed Chrome 155, isolated `APPCHI_NEXT_DIST_DIR` server) ran 206
    passed, 8 skipped, 0 failed, including the new `tests/grouping.spec.ts` (6). Two
    earlier runs against the shared HTTPS dev server each had one failure of
    `scalability-users.spec.ts:150`, which passed 6 of 6 alone and in the isolated run.
  - Real browser (temporary harness, not committed): a separate API process on the
    final code, a disposable database, a two-shard index of 12 synthetic alerts and
    the running HTTPS dev frontend in Chrome. Severity groups had exact counts in
    severity order with the Severity column hidden, Show 2 more and Show fewer, and
    Collapse and Expand all. Status groups matched the quick-view counts. Assignee groups
    showed the directory name with "(you)" and Unassigned last. Application groups put
    the most alerts first. Save as preset carried the grouping, the preset persisted
    it across reload, the temporary change offered Reset to saved, and an editor change
    to Node put No value last. The run also showed a switch of grouping field briefly
    rendering the previous groups under the new labels; that was fixed and a
    regression test added.
- Local runtime stack, 2026-10-27, at the user's request: after the user hit
  `request_failed` (the API still ran pre-grouping code and returned 405), the
  launcher stopped the API, ingestion, frontend and workers. It restarted the stopped
  Kafka, Kong and Grafana containers and migrated `appchi_local` from 0053 through
  0054 and 0055 with the workers stopped. `python -m appchi.kafka_cutover
  --record-topic-identity` then verified the broker history and recorded both topic
  IDs, and `local-runtime.ps1 -Action Start` restarted everything on the current
  code (3 workers healthy, local v2 17 gateway checks passed). Two earlier launcher
  attempts stopped with an unhelpful error and a third, captured run completed;
  every step also passed when run alone.
- Signed in through local Keycloak in the desktop browser pane (Local Demo
  tenant, 27 alerts). Feed by Severity gave Critical 5, High 4 and Warning 18, with
  status summaries summing to the quick views (17 Firing, 8 Acknowledged, 2
  Resolved), the Severity column hidden, and Show 5 more growing Warning to
  10 of 18. Feed by Assignee put "Local Administrator (you)" (9) before Unassigned
  (18). The "shilat" preset (`true`, saved without grouping) opened normally; a
  toolbar change to Status gave Firing 17, Acknowledged 8 and Resolved 2 with that
  preset's own columns minus Status, and Reset to saved restored one table without
  changing the saved preset.
- Not verified: the harness run answered the live event stream with one idle event,
  and the signed-in check did not wait for a live invalidation, so live refresh of a
  grouped view is unobserved. Saving a grouping on an existing local preset was not
  done, to leave the user's presets unchanged (covered by the harness run and API
  tests). Dark mode and the production index shard layout were not checked, and the
  p99 search targets are unmeasured.
- Pending: the migrations branch needs the user's 0053 and 0054 committed beneath it
  (0055's `down_revision` is `0054`); pushing; advancing the API's domain pin, the
  frontend's `@appchi/contracts` pin and their locks to published commits; and
  delivery manifest pins. The checks above ran on the combined working trees before
  the commits split them.

## 2026-10-26 Quieter alert, preset and dashboard screens (REV-43)

The user asked to quieten the Feed, saved presets and dashboards and approved a mock
before implementation. See the [design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md#rev-43--quieter-alert-preset-and-dashboard-screens-2026-10-26)
and the frontend [UI mapping](https://github.com/appchi-alert-operations/appchi-frontend/blob/main/docs/ui-design.md).

- Frontend commit `55ed846` on local branch `codex/ui-cleanup` (from `4b9ed52`), also
  fast-forwarded into local `main`; not pushed: row ⋯ menus with Assign to me or
  Unassign and Resolve or Reopen; no standing Live pill (a quiet notice only when
  delivery is degraded, unavailable or search lags); no revision numbers on those
  screens; lighter quick views with dots and no Active view; plain severity and status
  text, no source line or New chip in rows; times relative to now with the exact time
  in the tooltip. `DESIGN.md`, `.impeccable/design.json` and the docs follow.
- Checks on the final code: TypeScript and Prettier passed; the controlled Chrome
  suite (installed Chrome 155, isolated `APPCHI_NEXT_DIST_DIR`) ran 200
  passed, 7 skipped, 0 failed; the preset specs reran 23 passed after a late presets-list
  grid fix. Prettier flags only the checkout's CRLF copies.
  New `tests/ui-cleanup.spec.ts` covers the quick views, the row menu (keyboard,
  Escape, no navigation), relative times, the alert detail and the presets list.
- Real local stack (Chrome, signed in through local Keycloak, run ledger
  `e819557e-a363-42a1-b209-528b2dc3edc1`): Feed header empty while live delivery was
  connected; quick views without Active; a synthetic S2 alert went through Assign to
  me (persisted after reload), Unassign, Resolve and Reopen from its row menu and ended
  firing and unassigned as before, with a second occurrence recorded by the reopen;
  alert detail without revision or source line; presets and dashboards lists without
  revisions; 390px and dark renders.
- Not verified: S2 itself stopped at admission, because local ingestion `/readyz`
  returned 500 and admission 503 (an existing environment fault, left untouched); the
  Global tenant had no saved presets or dashboards, so those screens were checked only
  with controlled fixtures; S1, S3 and S4 were not rerun. No API, contract, migration or
  deployment changed.

## 2026-10-26 Ocean palette on the existing UI (REV-42)

After a redesign round with five palette prototypes, the user kept the existing
frontend design and replaced only its colors with the Ocean palette, in light and
dark. See the [design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md#rev-42--ocean-palette-on-the-existing-design-2026-10-26)
and the frontend [UI mapping](https://github.com/appchi-alert-operations/appchi-frontend/blob/main/docs/ui-design.md).

- Frontend local branch `design/6-ocean-palette`: `329cf83` recolors the UI and the
  Appchi Keycloak theme; `4b9ed52` adds `DESIGN.md` and `.impeccable/design.json`.
  Not pushed or merged. Every color is a semantic role in `src/app/theme.css`, defined
  once per theme; layout, type, components, copy and behavior are unchanged.
- On that branch: TypeScript passed; Prettier passed apart from the checkout's existing
  CRLF conversion; the isolated `next build` passed; the controlled Chrome suite ran
  194 passed, 7 skipped and 1 failed. The failure, `scalability-users.spec.ts:150`, is a
  request-count race that passed 10 of 10 isolated runs on the branch and with the
  previous CSS.
- Synthetic-fixture renders at 1440 and 390, light and dark, covered the Feed, alert
  detail, incidents, presets, rules, maintenance, dashboards, administration, sign-in
  states and the Keycloak template. The impeccable detector and finish review ran; the
  review's two fixes were applied.
- Not verified: authenticated browsing with real data, the theme inside a running
  Keycloak, and browsers other than Chrome. No dependency, migration, API or deployment
  changed.

## 2026-10-26 Kafka topic incarnation detection (REV-41)

Follow-up to the REV-40 reset: the source processor itself now refuses a recreated
received topic. See the [design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md#rev-41--kafka-topic-incarnation-detection-in-the-source-2026-10-26)
and [evidence](../qualification/kafka-topic-incarnation-2026-10-26.md).

- Migration 0054 adds `received_topic_id` and `changed_topic_id`; cutover records both.
  The source compares the received topic's ID on every assignment and refuses a
  different (`source_topic_identity_changed`) or unrecorded
  (`kafka_topic_identity_unrecorded`) one before any producer, offset or input.
- Another delivery at a recorded coordinate is `source_coordinate_conflict` and fences
  the partition with no timed retry. Retained failures log the exception class and
  AppError code or SQLSTATE in the message.
- `python -m appchi.kafka_cutover --record-topic-identity` records the IDs for an
  earlier cutover or after a reviewed reset, only while broker history matches.
- IDs compare by the low 32 bits of each half: confluent-kafka 2.15.1 on Windows
  reports no more.
- Open: the local application database still needs the reviewed rollout (0054, then
  the recording command); the correlation and search groups do not check the changed
  topic's identity.

## 2026-10-24 Always-on correlation and maintenance (REV-39)

User decision: correlation rules and maintenance windows are always on, with no
capability gate; per-rule and per-window Enable/Disable stays. An unset capacity
budget is unlimited, and restore no longer turns these features off. See the
[design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md#rev-39--always-on-correlation-and-maintenance-2026-10-24).

- Migration 0053 deletes the `correlation` and `maintenance` gate rows and restarts
  rules saved blocked with `consumer_not_integrated` at a new evaluation epoch.
  Stop every writer before applying it.
- Domain `appchi.shared.capacity` treats a missing or null budget as unlimited and
  rejects an invalid one with `capacity_profile_invalid`; correlation and
  maintenance budgets no longer need `qualified`.
- Removed: the maintenance activation command and runbook, the REV-30
  `scripts/local_correlation.py` preview and its profile flags, and the API's
  `processing_ready` field. New rules are created `healthy`.
- Maintenance always prepares its catalog. Alerts without a current checkpoint still
  fail reads with `maintenance_proof_unavailable`; the existing stack runs the
  maintenance warmup once per tenant
  ([upgrade steps](../docs/local-runtime.md#always-on-correlation-and-maintenance-rev-39)).
- Window deletion still needs the `recovery` and `human_auth` gates and the recovery
  evidence store.
- The local launcher always starts the correlation worker. Earlier load-test
  baselines ran with correlation and maintenance off.
- The CEL compile child is a plain subprocess importing only the compiler; the
  spawned child had re-imported each worker's entrypoint and exceeded the
  one-second deadline in the processor and correlation workers.

Executed 2026-10-25 on uncommitted `codex/always-on-correlation-maintenance`
working trees with editable siblings (Windows, Python 3.13.5, PostgreSQL 18.2 in a
disposable container for tests):

- Unit/integration: migrations 30 passed (including the 0053 upgrade test); domain
  unit 538 passed; API unit 167 passed (two modules not importable without
  `httptools`); correlation and processor unit passed; delivery 712 passed, 35
  skipped, 9 failed only on missing `httptools`/`poetry` in the delivery `.venv`
  (four further modules skipped for the same import); correlation/maintenance
  real-CEL subset rerun after the compiler change: 44 passed, 2 REV-34 skips.
  Frontend `tsc --noEmit` passed.
- Local stack: `appchi_local` backed up to
  `.local/runtime/pre-always-on-20261025.dump`, Start applied 0051–0053, warmup
  completed 22/22 Global and 12/12 Local Demo alerts. Browser (real sign-in): feed
  and alert detail load; a maintenance window was created; a native v1 alert
  matching it arrived firing and suppressed with one maintenance cause; a new rule
  was created enabled and healthy and a matching v1 alert produced a correlation
  incident; Platform health lists no correlation/maintenance gates. Test rule and
  window were disabled and the incident resolved (run ledger
  `.local/verification/561756da-3cac-4b63-b5a4-2e695d197a2c`).
- Not verified: native v2 (S3), because the private ingestion listener needs
  `httptools`, missing from the delivery `.venv` before this change. No load,
  staging or production evidence is claimed.

## 2026-10-24 Context layer follow-ups

Navigation, guidance and test-documentation changes only; no product behavior
changed. Design CI now fails on a renamed or duplicated catalog section (design#10,
merged and pinned), and the context gate checks that design's heading parser
matches the resolver. Entrypoint checks ignore checkout line endings. Specs gain
Current scope notes under 59 superseded sections (design branch, not yet merged).
Agents are told to choose routes explicitly, and the 22 incident integration tests
cite their requirement IDs and sections.

A paired planning-only evaluation scored 28.5/29 with the layer and 27/29 without,
using about 6% more tokens with it; one run per cell does not establish an effect.
Documentation, context and focused unit checks passed. Six existing unit tests
failed locally on missing `httptools`/`poetry` modules, and the tagged integration
suites were collected but not run. See [executed evidence](../qualification/context-layer-followups-2026-10-24.md).

## 2026-10-24 Incident inactivity/retention labels removed

Incident creation help and detail no longer promise automatic inactivity
resolution or retention removal, which REV-34 defers. Last activity remains;
recorded inactivity resolutions in history still describe those past events.
The client guide no longer says some screens show those deadlines. API,
contracts and generated client are unchanged.

TypeScript, changed-file formatting and 45 affected synthetic Chrome checks passed
on frontend 42e91ce; desktop and phone renders were inspected against a synthetic
API fixture. S1–S4 were not run: PowerShell 7 is not installed and Docker was not
running. See [executed evidence](../qualification/incident-deadline-labels-2026-10-24.md).

## 2026-10-22 Integration test hygiene

Test-only and tooling changes; no product code changed. Search tests bind
Elasticsearch before the API starts; the ingest-route, REV-27/REV-34 and
Kafka-first-outbox tests match current behaviour; broker tests restore
`kafka_pipeline_control`; the correlation-cleanup tests no longer need a draft
fixture; the BrowserReal seed uses the Kafka-first source step;
`workspace.py install` restores pip and installs requirements siblings added since
the repin. The [runbook](../docs/runbooks/development.md#verification-commands)
lists every test binding.

Executed on delivery 82c6c72 and frontend bba55ef (pinned siblings, fresh private
PostgreSQL 18.2, Elasticsearch 9.0.0 and Kafka 4.0.0): delivery integration 436
passed, 13 skipped (Kong opt-in and REV-34 deferred-scheduler prototypes), 2 failed
on the native-CEL compile deadline and passed when rerun alone; delivery unit 293
passed, 3 skipped; BrowserReal 1 passed. Not executed: S1–S4, the Kong cases.

## 2026-10-19 Pipeline round-trip reduction

A throughput/latency review led to these changes in domain 6d4cfa6, processor
f483832 and migrations c5f3e9c, pinned by api, correlation, indexer and ingestion:

- Tenant-scoped and opted-in READ COMMITTED units take the restore guard inside
  their own transaction, so they no longer open two extra transactions per unit.
- Each Kafka source alert takes the tenant coordinator once; its
  `tenant_work_state` statements drop from 9 to 3; new alerts are inserted once and
  `publish()` is pipelined.
- Maintenance/correlation preparation reads gates (removed by REV-39), catalog and checkpoints in one
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
(REV-39 later made correlation and maintenance always on.)

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

**Superseded by REV-39 (2026-10-24):** the preview, its activation and profile were removed; correlation and maintenance are always on. The record below is history.

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

No capability is enabled merely to remove an unavailable response. Maintenance/correlation are always on (REV-39); their qualification still needs complete temporal proof and measured capacity; operator jobs retain pending/needs-attention without verified remote execution. Test fixtures opening gates are not deployment approval.

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

## Performance improvements — 2026-10-23

At the user's request, every remaining item from the 2026-10-20 performance review
of `main` was implemented and merged. Each item became one PR per affected
repository and went through fresh-context review rounds until nothing remained to
fix. Consumers were repinned to producer merge commits, and CI passed on every
final `main`.

The changes cover four areas:

- **Worker paths:** fewer transactions and statements in the worker, changed-event,
  correlation and `read_committed` paths.
- **Search hints and reconciliation:** shared search-hint bulks, index-ordered
  reconciliation pages, watermark-only view-generation bumps and due-first indexer
  scheduling.
- **API reads and live updates:** a 4-transaction feed page with migration 0052,
  cached idle SSE wake-ups, and bounded live-hint deletes.
- **Ingestion and frontend:** single-parse ingestion, one Kafka ack poll thread,
  httptools/uvloop serving with a request-smuggling fix found in review, and
  coalesced frontend live invalidations.

The [implementation evidence](../qualification/performance-improvements-2026-10-23.md)
records each item, its PRs, the measured local effects, the defects found in
review and the merged revisions.

On the merged revisions:

- **Delivery integration suite:** 449 cases passed, 2 known-flaky cases failed,
  and 13 were explicitly skipped.
- **Frontend:** generation, formatting, typecheck and build passed. The controlled
  browser suite passed 190 cases, with 2 timing failures that also occur on
  earlier code.

Not run:

- the S1–S4 local-stack smoke baseline and the real-backend browser journeys,
  because PowerShell 7 was unavailable;
- the before/after load comparison, which the user stopped.

The figures are local diagnostics. They establish no capacity, release or
production readiness.
