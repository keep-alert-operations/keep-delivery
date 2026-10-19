# Dashboard toolbar correction - 2026-10-09

The user requested replacing Pause updates with Discard changes and removing
Preview draft counts. The main toolbar now has one editor-only Discard changes
button, disabled while clean, a panel dialog is open, or commands are locked.
Existing confirmation/reload semantics remain; cancelling preserves the draft,
and completing discard restores the saved definition and focus to its name.
The duplicate menu/body discard controls and draft-count preview requests/state
were removed. New/history drafts have no empty More actions menu.

Ordinary and fullscreen dashboards have no pause/resume control. Automatic count
updates, hidden-tab suspension, manual Refresh counts and saved provenance remain.
New or changed preset references ask users to Save before displaying a count.
Backend preview/validation API contracts, dependencies and migrations are unchanged.

## Verification

- The four affected Chrome suites passed 57 tests in 2.2 minutes using
  `PLAYWRIGHT_CHANNEL=chrome node node_modules/@playwright/test/cli.js test --config .local/dashboard-layout.config.ts dashboard-layout.spec.ts dashboard-counts.spec.ts preset-choices.spec.ts configuration-history.spec.ts --workers=1`.
- After the final conditional menu guard, three targeted new/history dashboard
  checks passed. The removed preview fixture now verifies the selected preset
  reference through an explicit dashboard Save.
- `node node_modules/typescript/bin/tsc --noEmit --incremental false` passed.
- Prettier `--check` passed on the seven changed frontend source/test/doc files.
- The isolated production build passed with final source, including TypeScript;
  `.local/build-dashboard.py` uses `.next-dashboard-build` and restores only the
  expected generated tsconfig/next-env changes, preserving the running UI.
- Frontend/design `git diff --check` and design `python scripts/check_docs.py`
  passed. The final delivery documentation check is part of handoff.
- Local UI and API readiness returned HTTP 200.

The browser uses synthetic route fixtures against the running HTTPS dev UI;
it writes no shared user data and is separate from real-service qualification.
Desktop, 800-pixel and 390-pixel screenshots were visually inspected:
[dirty toolbar](toolbar-dirty.png), [800 pixels](saved-800.png),
[390 pixels](saved-390.png). No production deployment was performed.

Local HEADs remain frontend `5c1bfd052042b0df8538cd73f2395e2fd7f70a82`,
design `99c5def93c761386e7d5161c44f482ae4cbd7539` and delivery
`ee37546e771e435d1f07c2b88857f548dd05fbb0`; task changes are uncommitted and
unrelated changes were preserved. Existing repository-wide formatting issues
outside this task were not changed or requalified by these scoped checks.
