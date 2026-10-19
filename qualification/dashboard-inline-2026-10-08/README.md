# Inline dashboard editing verification — 2026-10-08

The later explicit user correction replaces the separate dashboard editor with
editing on the regular board. Click the dashboard name or adjacent pen icon to
rename it. Create panel opens a dialog for one panel; panel options provide Edit,
Delete and keyboard order actions. Desktop dragging/resizing edits the local
layout. Save enables only when the complete draft differs from its saved baseline,
including name, panel CRUD and layout; reverting all changes or successful Save
leaves it disabled. Cancel leaves the existing dashboard draft unchanged. One expected-revision command saves the whole definition.

New dashboards and retained-history drafts use the same inline composer. Conflict
and uncertain-command retry semantics remain. Clean remotely deleted boards return
to the list; authorized dirty drafts stay available for Save as new. Viewers have
no creation/editing controls. Preview values are distinguished from saved count
provenance and do not silently reuse counts after a preset reference changes.

This supersedes the UI placement of the earlier
[layout implementation](../dashboard-layout-2026-10-08/README.md). Its grid,
displacement and API contracts continue. No dependency or schema change, migration,
backend rollout, merge or production deployment was needed or performed.

## Revisions and execution

| Repository | Local HEAD | Task changes |
|---|---|---|
| appchi-frontend | `5c1bfd052042b0df8538cd73f2395e2fd7f70a82` | Uncommitted inline UI, layout, tests and UI notes |
| appchi-design | `99c5def93c761386e7d5161c44f482ae4cbd7539` | Dashboard supplement, user correction and verification requirements |
| appchi-delivery | `ee37546e771e435d1f07c2b88857f548dd05fbb0` | This evidence and implementation-status entry |
| appchi-api | `e2de23d0b70bb52f8180eebb396d927567705eaf` | Existing local API; no changes for this task |

Unrelated existing changes were preserved. The frontend contract dependency remains
`eb555e8bd307c2dbc3f20a9134351696a4f5eba2`. Tests used installed Chrome and the
existing HTTPS Next.js 16.3.6 dev UI at `https://localhost:3443` with controlled
synthetic API route fixtures. They did not write shared user data. These establish
frontend interaction evidence, separately from real-service integration qualification.

Commands below ran from their owning repository. Engineering receipts record the
exact local content, including uncommitted changes; ignored dependencies and live
services are outside that fingerprint.

| Check | Actual result |
|---|---|
| `PLAYWRIGHT_CHANNEL=chrome node node_modules/@playwright/test/cli.js test --config .local/dashboard-layout.config.ts dashboard-layout.spec.ts dashboard-counts.spec.ts preset-choices.spec.ts configuration-history.spec.ts --workers=1` | 46 passed in 1.9 minutes |
| `node node_modules/typescript/bin/tsc --noEmit --incremental false` | Passed |
| Prettier `--check` on all 14 changed dashboard source/test files | Passed |
| Generated frontend contract types using the installed openapi-typescript CLI, then Prettier and `git diff --exit-code -- src/lib/generated.ts` | Passed; no contract drift |
| `node node_modules/next/dist/bin/next build` with `APPCHI_NEXT_DIST_DIR=.next-dashboard-build` | Passed; all three static pages generated; before/after frontend fingerprint matched |
| `git diff --check` in affected repositories | Passed |
| Design `python scripts/check_docs.py` | Passed; original issue/REV numbering preserved |
| Delivery `python qualification/verify_documents.py` | Passed; 121 documents, 566 source entries, REV-01 through REV-38 |
| Full repository Prettier command corresponding to `npm run format:check` | Failed on 62 files outside this task; no unrelated reformatting |

The isolated build wrapper restores original tsconfig/next-env bytes only when
Next's changes match expected generated build paths. The browser configuration
uses object spread, disables Playwright-managed servers and points to the running
HTTPS UI, avoiding duplicate dev services. Earlier browser runs found the preset
endpoint, wrapped-select test locators, focus looping, preview error/provenance,
remote-deletion preservation, and canceled-gesture snapshot issues; all relevant
corrections are covered by the passing final suite. Render review additionally
corrected crowded-header title clipping and narrow tall-panel label overflow.

## Rendered evidence

The passing synthetic browser suite captured these images; visual inspection was
performed alongside interaction/focus/save assertions.

- [Regular desktop board after dragging](desktop-board.png)
- [Saved desktop board after resizing](desktop-saved.png)
- [Single-panel edit dialog and exact threshold error](panel-dialog.png)
- [Saved board at 800 pixels](saved-800.png)
- [Saved board at 390 pixels](saved-390.png)

The authoritative interaction is in
[dashboard section 8](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-dashboard-operations.md#8-shared-editing-copying-and-conflicts).
Frontend implementation notes remain in
[UI design notes](https://github.com/appchi-alert-operations/appchi-frontend/blob/main/docs/ui-design.md).
