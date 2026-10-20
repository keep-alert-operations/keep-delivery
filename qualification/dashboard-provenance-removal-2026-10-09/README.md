# Dashboard Count provenance removal - 2026-10-09

The user requested removing Count provenance. The button, details renderer, local
toggle state and unused styling were removed, including the reserved action row.
This applies to ordinary, narrow and fullscreen boards. Internal batch metadata
and revision/context checks still guard refreshed counts; visible errors and
stale/unavailable states remain. No backend contract, dependency or migration changed.

Executed verification from frontend:

- `PLAYWRIGHT_CHANNEL=chrome node node_modules/@playwright/test/cli.js test --config .local/dashboard-layout.config.ts dashboard-layout.spec.ts dashboard-counts.spec.ts --workers=1`: 47 passed in 1.9 minutes. Checks include absent provenance controls in desktop/narrow/fullscreen, stable panel positions during refresh, changed-context retry, editing, discard, dragging/resizing and focus behavior.
- `node node_modules/typescript/bin/tsc --noEmit --incremental false`: passed.
- Prettier `--check` on the five changed frontend files and `git diff --check`: passed.
- Isolated production build using `.local/build-dashboard.py`: passed; expected generated configuration changes were restored.
- Design `python scripts/check_docs.py` and `git diff --check`: passed.
- Local UI and API readiness checks returned HTTP 200.

Visually inspected renders: [dirty desktop draft](toolbar-dirty.png),
[800px saved board](saved-800.png) and [390px saved board](saved-390.png).
These synthetic browser fixtures write no shared data and remain separate from
real-service qualification. Prior unrelated local changes were preserved.

Local HEADs: frontend `5c1bfd052042b0df8538cd73f2395e2fd7f70a82`, design
`99c5def93c761386e7d5161c44f482ae4cbd7539`, delivery
`ee37546e771e435d1f07c2b88857f548dd05fbb0`. Task changes are uncommitted.
