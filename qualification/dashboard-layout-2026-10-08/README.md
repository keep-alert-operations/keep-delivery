# Dashboard panel layout verification — 2026-10-08

The frontend dashboard editor now supports pointer dragging and corner resizing,
grid snapping, deterministic downward collision displacement, cancellation and
keyboard alternatives. Changes stay in the tab's draft until explicit Save.
Title, preset, color and removal edits preserve manually placed panels. New
panels fill the first available gap; explicit order controls preserve the requested
order even when panels have different widths.

The existing dashboard API definition, command identity, expected revision,
permissions and layout validation contracts are unchanged. This work requires
no dependency, database migration or coordinated backend rollout.

## Revisions and environment

| Repository | Local HEAD | Task scope |
|---|---|---|
| appchi-frontend | `5c1bfd052042b0df8538cd73f2395e2fd7f70a82` | Uncommitted layout implementation, tests and UI notes |
| appchi-api | `e2de23d0b70bb52f8180eebb396d927567705eaf` | Existing local API; no task changes |
| appchi-delivery | `ee37546e771e435d1f07c2b88857f548dd05fbb0` | This evidence and implementation-status entry |

Existing unrelated edits in these checkouts were preserved. The workspace
manifest and dependency pins were inspected; the frontend contract dependency
remains `eb555e8bd307c2dbc3f20a9134351696a4f5eba2`. Node 22.17.1, Next.js 16.3.6
and the installed Chrome browser were used. The existing HTTPS dev UI remained
available at `https://localhost:3443` throughout the checks.

## Executed verification

Commands ran from `appchi-frontend`, with engineering receipts recorded for the
final browser, type, changed-file formatting and isolated build checks.

| Check | Result |
|---|---|
| `PLAYWRIGHT_CHANNEL=chrome node node_modules/@playwright/test/cli.js test --config .local/dashboard-layout.config.ts tests/dashboard-layout.spec.ts tests/dashboard-counts.spec.ts tests/preset-choices.spec.ts tests/configuration-history.spec.ts --workers=1` | 33 passed in 1.2 minutes |
| `node node_modules/typescript/bin/tsc --noEmit` | Passed |
| Prettier `--check` on the six changed TypeScript/TSX/CSS/test files | Passed |
| `node node_modules/next/dist/bin/next build`, with `APPCHI_NEXT_DIST_DIR=.next-dashboard-build` | Passed; compiled and generated all three static pages |
| `git diff --check` | Passed |
| `npm run format:check` | Failed on 66 unchanged baseline files; no unrelated formatting applied |

The ignored local browser configuration uses the tracked Playwright configuration
with an empty `webServer` list and `https://localhost:3443` as its base URL.
API responses are synthetic fixtures in isolated browser contexts. These checks
exercise Save, the complete submitted rectangles, stable panel IDs, observed
revision/command/CSRF fields, navigation and reload, and conflict preservation;
they are not evidence of a new real-database round trip. No real dashboard or
preset was changed. The API and UI health/connectivity checks passed before the
feature work.

Interaction coverage includes real mouse dragging/resizing, a later panel moving
ahead of earlier panels without losing pointer capture, keyboard focus and
announcements, pointer/Escape cancellation, editing during a gesture, secondary
pointer isolation, collision chains and grid overflow, mixed-width ordering,
unchanged positions after metadata/add/remove edits, viewer denial, saved-layout
reload, 800/390-pixel responsive display, count refresh/fullscreen and history-copy
regressions. Synthetic secondary-pointer events do not qualify physical touch
hardware. The responsive checks do not qualify every browser zoom combination.

The bundled Playwright browser was absent; using the installed Chrome resolved
that launch limitation. Earlier temporary config attempts inherited the test
server and refused to start a second Next instance; no existing server was stopped.
The first interaction run exposed test assumptions about error text and initial
focus, which were corrected. Review findings about ordering and gesture snapshots
were fixed and covered by regressions.

The production build used a separate output directory. Next's generated
`tsconfig.json` includes and `next-env.d.ts` build paths were inspected and restored
with guards that retained the original bytes only when changes matched the expected
generated additions. A final isolated build with this cleanup passed with matching
before/after frontend fingerprints. Existing `tsconfig.json` edits were preserved.
No new production qualification, merge or deployment is claimed.

## Rendered evidence

Screenshots came from the passing synthetic browser suite and were visually
inspected. They supplement interaction assertions.

- [Desktop editor after rearrangement](desktop-editor.png)
- [Saved desktop layout after resize](desktop-saved.png)
- [Saved layout at 800 pixels](saved-800.png)
- [Saved layout at 390 pixels](saved-390.png)

Frontend behavior is documented in [UI design notes](https://github.com/appchi-alert-operations/appchi-frontend/blob/main/docs/ui-design.md).
