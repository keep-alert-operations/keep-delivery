# Alerts page renamed Feed - 2026-10-11

The user requested the Alerts page be named Feed. The sidebar's visible and
accessible label, page title and breadcrumbs now use Feed. Alert details return
through one Feed breadcrumb; preset-origin breadcrumbs remain contextual.
Existing `/alerts` URLs, API contracts and alert record terminology remain.
Frontend, design and delivery documentation were updated; no dependency,
migration or deployment changed. Prior local work was preserved.

Executed from frontend:

- `PLAYWRIGHT_CHANNEL=chrome node node_modules/@playwright/test/cli.js test --config .local/dashboard-layout.config.ts redesign.spec.ts navigation-session.spec.ts theme.spec.ts workspace.spec.ts --grep 'reference palette|alert detail uses|page, administration|account theme switch|dark narrow layout' --workers=1`: five checks passed in 24.1 seconds. Coverage includes home/history navigation, detail return, desktop/narrow rendering, themes and keyboard access.
- `node node_modules/typescript/bin/tsc --noEmit --incremental false`: passed.
- Prettier `--check` on the eight changed frontend files and `git diff --check`: passed.
- Isolated production build through `.local/build-dashboard.py`: passed; expected generated configuration edits were restored.

Inspected [desktop](feed-desktop.png) and [narrow](feed-narrow.png) renders.
Browser fixtures use synthetic API data and write no shared data. Real-service
integration was not rerun for this naming change.

Local HEADs: frontend `5c1bfd052042b0df8538cd73f2395e2fd7f70a82`, design
`99c5def93c761386e7d5161c44f482ae4cbd7539`, delivery
`ee37546e771e435d1f07c2b88857f548dd05fbb0`. Changes are uncommitted.
