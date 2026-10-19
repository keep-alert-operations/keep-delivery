# Dashboard text cleanup - 2026-10-09

The user requested removing the visible draft reminder, preset-opening hint,
drag/resize instructions and tenant/live/capture/revision status line. Those texts
are removed from the ordinary, narrow and fullscreen dashboard presentation.
Accessible handle descriptions and visually hidden count status remain; exact
capture/revision details are still available on demand through Count provenance.
Its action row retains a stable height so refresh does not move panels. Save,
Discard, panel interactions, automatic refresh and visible errors/stale markers
continue. No backend, dependency or migration change was required.

Verification from frontend:

- `PLAYWRIGHT_CHANNEL=chrome node node_modules/@playwright/test/cli.js test --config .local/dashboard-layout.config.ts dashboard-layout.spec.ts dashboard-counts.spec.ts --workers=1`: 47 passed in 1.8 minutes.
- `node node_modules/typescript/bin/tsc --noEmit --incremental false`: passed.
- Prettier `--check` on seven changed frontend files and `git diff --check`: passed.
- Isolated production build using `.local/build-dashboard.py`: passed, with expected generated config paths restored.
- Design `python scripts/check_docs.py` and `git diff --check`: passed.

The synthetic API browser fixtures write no shared data and are separate from
real-service qualification. Visually inspected screenshots show
[a dirty desktop draft](toolbar-dirty.png) and saved boards at
[800 pixels](saved-800.png) and [390 pixels](saved-390.png).
Prior unrelated local changes were preserved. Local HEADs remain frontend
`5c1bfd052042b0df8538cd73f2395e2fd7f70a82`, design
`99c5def93c761386e7d5161c44f482ae4cbd7539`, delivery
`ee37546e771e435d1f07c2b88857f548dd05fbb0`; task changes are uncommitted.
