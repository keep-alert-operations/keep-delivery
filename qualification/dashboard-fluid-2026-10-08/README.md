# Fluid dashboard layout verification - 2026-10-08

The later user correction requests fluid panel dragging/resizing like Grafana and
panels sticking to the top without unsupported vertical gaps. The active panel
follows the pointer in CSS pixels. A placeholder shows the compacted grid drop
destination; neighboring panels and released panels settle over 180 milliseconds.
Reduced-motion preference skips settling animation. Rapid pickup preserves the
visible rectangle, and clicking a settling handle without dragging resumes it.

Vertical gravity preserves x/width/height, identity and metadata, and stops below
earlier horizontally overlapping blockers. Moving, resizing, creating, deleting
and reordering all produce compacted drafts. Existing sparse saved definitions
also display compacted for editors, viewers and fullscreen. Opening remains clean
and writes nothing; the next explicit changed-definition Save persists compacted
coordinates. Grid sizes, 24-panel limit, expected revision, command identity,
permissions, conflicts, count observations and Escape/cancel semantics continue.

This refines the earlier [inline editing evidence](../dashboard-inline-2026-10-08/README.md).
No dependency, HTTP schema, migration, backend rollout or production deployment
changed. User requirements and assistant-selected presentation are recorded in
[dashboard section 4](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-dashboard-operations.md#4-layout-and-responsive-behavior).

## Revisions and execution

| Repository | Local HEAD | Scope |
|---|---|---|
| keep-frontend | `5c1bfd052042b0df8538cd73f2395e2fd7f70a82` | Uncommitted layout, count board, workspace, styles, tests and UI notes |
| keep-design | `99c5def93c761386e7d5161c44f482ae4cbd7539` | Later user correction and acceptance requirements |
| keep-delivery | `ee37546e771e435d1f07c2b88857f548dd05fbb0` | This evidence and status entry |
| keep-api | `e2de23d0b70bb52f8180eebb396d927567705eaf` | Existing running API; no task changes |

Unrelated existing changes were preserved. Contract dependency remains
`eb555e8bd307c2dbc3f20a9134351696a4f5eba2`. Browser checks use installed Chrome,
the existing HTTPS Next.js 16.3.6 dev UI at `https://localhost:3443`, and controlled
synthetic API route fixtures. They write no shared user data. This is frontend
interaction evidence, separately from real database/service qualification.

Final browser, TypeScript and build receipts share frontend content fingerprint
`7dde77ad8ff548e93a7eddbf701b902f93abc4e148c0d426da8084d4a52ff4d0`.
Tracked and nonignored untracked files are included; installed dependencies and
external runtime services are outside that fingerprint.

## Checks

| Check | Actual result |
|---|---|
| `PLAYWRIGHT_CHANNEL=chrome node node_modules/@playwright/test/cli.js test --config .local/dashboard-layout.config.ts dashboard-layout.spec.ts dashboard-counts.spec.ts preset-choices.spec.ts configuration-history.spec.ts --workers=1` | 57 passed in 2.1 minutes |
| `node node_modules/typescript/bin/tsc --noEmit --incremental false` | Passed |
| Prettier `--check` on 14 changed dashboard source/test files and UI notes | Passed |
| `node node_modules/next/dist/bin/next build` with `KEEP_NEXT_DIST_DIR=.next-dashboard-build` | Passed; original tsconfig/next-env bytes restored; before/after fingerprint matched |
| Installed openapi-typescript generation CLI, Prettier, `git diff --exit-code -- src/lib/generated.ts` | Passed; no generated contract drift |
| `git diff --check` in frontend, design and delivery | Passed |
| Full repository `node node_modules/prettier/bin/prettier.cjs --check src tests '*.ts' '*.json'` | Failed on 62 files outside this task; unchanged files were preserved |
| Local UI, API `/livez`, API `/readyz` | HTTP 200 |

The isolated build wrapper restores only expected generated build paths and uses
separate output from the live dev server. Design/delivery document checks are
the final handoff checks; their outcomes are recorded in engineering receipts.

Review findings for pickup during animation, click-only animation resume and
remote updates during a dirty drag were fixed and covered with browser tests.
The first full runs exposed a test-only access-revocation race: a background count
refresh could remove the toolbar before the test clicked Refresh. The fixture now
pauses updates before revocation; returned access loss still clears the protected
board/draft. The corrected access test passed three repeated runs.

## Rendered evidence

The passing final suite generated these screenshots; each was visually inspected.
Pointer-following motion, snapped drop geometry and cancellation are also checked
by browser geometry assertions.

- [Active fluid drag and its top-aligned placeholder](fluid-drag.png)
- [Active continuous resize](fluid-resize.png)
- [Compacted board after shrink, delete and Save](compacted-board.png)
- [Saved board at 800 pixels](saved-800.png)
- [Saved board at 390 pixels](saved-390.png)
