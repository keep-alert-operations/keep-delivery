# Alembic conversion evidence — 2026-10-01

This is local correctness and packaging evidence for REV-38. It does not qualify
production, perform the Kafka cutover, or migrate the existing application stack.

## Source and environment

- Migrations: `1ebe4cf72fd3878dbc44d4f7c8f5dc8c3d2a5236`.
- Design: `62e348b96b1b6dbe3024a89613db49edab6b914c`.
- Delivery: changes based on `6f00d8028eb6559fd6e194c4f527c7a4e6a946b6`;
  this evidence and the coordinated pins are committed together in delivery.
- Other repository revisions and working states are in the
  [workspace inventory](alembic-workspace-inventory.json). Delivery is recorded
  dirty because the inventory was captured before committing this evidence.
- Windows, Python 3.13.5, uv 0.10.8, PostgreSQL 18.2, Alembic 1.20.0,
  SQLAlchemy 2.1.1 and Psycopg 3.3.6. A newly initialized loopback-only PostgreSQL
  cluster under ignored local state supplied unique disposable test databases.
  No application database or existing PostgreSQL service was migrated. The
  disposable server was stopped after verification.
- Application compatibility tests used the documented editable workspace sources.
  Wheel execution and a separate locked delivery installation were checked too.

## Executed checks

| Check | Result |
|---|---|
| Historical SQL SHA-256 values compared with the original migrations commit's manifest | All 44 unchanged; gaps 0010, 0042 and 0043 preserved |
| `uv sync --locked --all-extras` in migrations | Passed |
| `uv run --locked ruff check src tests` in migrations | Passed |
| `uv run --locked pytest tests -q -p no:cacheprovider` in migrations | 21 passed, no skips; [JUnit](alembic-migrations-tests.xml) |
| `uv build --wheel` in migrations | Passed |
| `uv run --locked alembic heads` / `history` | One head, 0047, and 44 ordered revisions |
| Wheel installed with locked dependencies in a separate environment; Python and console entrypoints executed outside the checkout | Fresh install and repeat passed; [result](alembic-wheel-smoke.json) |
| Existing identity, maintenance and search-bootstrap SQL tests in delivery | 27 passed, no skips; [JUnit](alembic-consumer-tests.xml) |
| Ruff on changed delivery Python files | Passed |
| Delivery `uv lock --offline`, followed by `uv sync --locked --offline` in a separate environment | Passed; resolved the exact new migration Git revision and verified 44 revisions/head 0047 |
| `python qualification/verify_documents.py` | 102 documents, 566 source entries, REV-01 through REV-38; zero errors |
| Original decision register/history compared with the prior design commit | Existing content preserved verbatim; REV-38 appended without renumbering |

The migration tests cover fresh and repeated runs, legacy adoption through
0001/0039/0046/0047, unchanged data and ledger timestamps, unknown/checksum/gap
rejection, missing/modified SQL, empty/unknown/mismatched Alembic state, multiple
revision heads, pending-file validation, rollback after a later SQL failure and
successful retry, read-only current inspection, rejected stamp/downgrade/offline
commands, two-process lock serialization, ledger privileges and tenant RLS.

Consumer command (from delivery, with the existing isolated test-admin binding):

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_identity_sessions.py tests/unit/test_maintenance_repository.py tests/integration/test_local_search_bootstrap_sql.py -q -p no:cacheprovider
```

The first consumer run had 26 passes and one `cel_compilation_unavailable` response
on the first maintenance case while other checks were active. The complete suite
then passed 27/27 when run separately, without changing the compiler deadline or
application code. This is consistent with the existing workstation-sensitive
native-CEL limitation; the initial failure remains part of this evidence.
The migration failure-path test was also corrected during development to expect
Psycopg's actual `UndefinedTable` exception; the final full run is the 21-pass report.

The wheel used for the isolated smoke check has SHA-256
`3957def5a10209e48612813a31f511500b76b79607e23908e67183f46cab0298`.

## Publication and rollout limits

The new prerequisite commits were resolved from the local Git cache for the lock
and installation checks. They must be published before a fresh remote clone/CI
can resolve the delivery pins. No source commits were pushed during this task.

Publish migrations and design before delivery. Build/promote the dedicated
migrations image by digest, execute existing pending-migration prerequisites, and
run the migration job before dependent writers as described in the
[runbook](../docs/alembic-migrations.md). Do not resume the old runner after adoption.

Remote CI, container build/runtime, staging/company-service integration,
Elasticsearch-backed search, full system/browser/capacity/security/recovery
qualification and production rollout were not executed. All DEL-01 through
DEL-08 production gates remain governed by the existing readiness record.
