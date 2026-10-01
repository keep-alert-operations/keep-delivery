# Alert ownership redesign — 2026-09-27

Scope: user-approved single assignment control and derived alert status, REV-27. Authoritative behavior is in [alert operations](../../spec-alert-operations.md#17-assignment-and-derived-acknowledgement).

## Executed evidence

- Focused Python invocation: `python -m pytest backend/tests/unit/test_alert_ownership.py backend/tests/unit/test_cel.py backend/tests/unit/test_cel_editor.py backend/tests/unit/test_search.py backend/tests/unit/test_display_name_enrichment.py backend/tests/unit/test_native.py -q --basetemp=.local/pytest-ownership`, with `PYTHONPATH=backend` and the repository virtual environment.
- Result: **114 passed, 4 failed**, 66.89 seconds. The four failures were `CELUnavailableError: CEL compiler exceeded one-second deadline` in existing `test_checked_native_semantics` cases: `2 < 3`, `alert.node_name in [null,"x"]`, `alert.node_name in ["x","y"]`, and `alert.node_name in []`. The deadline was not relaxed. All 11 new ownership cases passed. This run preceded the final command-response eligibility masking correction.
- Scoped Ruff checks passed for changed alert/CEL/configuration/search/presentation code and updated Python cases. Scoped format completed.
- OpenAPI generated from `appchi.main.app.openapi()`; the installed `openapi-typescript` regenerated client types. Installed TypeScript `tsc --noEmit` passed after the final frontend edits. Installed Prettier formatted the affected frontend files. An initial npm wrapper invocation failed because its user-global npm target was missing; direct installed package CLIs completed generation and checking.
- Documentation check passed: 12 touched design documents, 82 local file links, balanced fences and unique sequential REV-01 through REV-27. `git diff --check` passed.
- Read-only reviewer findings about internal legacy rule inputs, linked/affected response presentation, saved legacy sort labels and command-response eligibility masking were addressed. Final review found no remaining actionable findings.
- Only the ownership-verified local API process was restarted, loading the updated code; its `/livez` responded successfully. No database migration or search rebuild was performed or required.

## Limits

Updated browser and database/search integration cases were not executed for this revision. The final eligibility-mask correction was statically reviewed but not covered by a subsequent runtime test run. A successful process liveness check does not establish end-to-end behavior. No shared-service or production deployment/qualification was performed. Earlier evidence elsewhere in the repository remains historical, not a claim that those suites were rerun here.
