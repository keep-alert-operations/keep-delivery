# Alembic migration execution

REV-38 changes the `appchi-migrations` runner to Alembic. The deployment command
remains `python -m appchi.migrate`, with `APPCHI_MIGRATION_URL` supplied through
the dedicated migration secret. The OpenShift migration Job remains the owner;
runtime services do not migrate at startup. SQL files and their original
checksums are unchanged. The package owns `migration_sql/checksums.json` and
the Alembic revisions/environment.

## Release and execution

Build the migrations image from the exact repository revision in the delivery
workspace manifest and Python lock. Publish prerequisite migration/design commits
before publishing the delivery commit that pins them. Record the migrations image
digest, repository/design revisions and SQL checksums in release evidence.

Before execution, check which SQL files are pending and complete their existing
operational prerequisites. The Kafka protocol cutover still requires the documented
drain/fence/offset procedure; migration 0046 still requires stopping obsolete
schedulers. Adopting Alembic does not perform these steps or qualify a rollout.

Run the dedicated migration job before dependent writers. The first execution on
a legacy database verifies that `schema_migrations` is a checksummed, gap-free
prefix of the known chain, adopts that exact revision, and executes only pending
SQL. The original ledger and timestamps remain. Alembic's `alembic_version` must
agree with that ledger after every successful upgrade. Both are protected from
the API/worker roles.

All changes share one PostgreSQL transaction and the existing advisory lock;
concurrent jobs serialize and any failure rolls back adoption and pending changes.
Inspect an integrity error and restore the correct release artifact or investigate
the ledger discrepancy before retrying. Do not edit applied SQL/checksums, manually
stamp a database, drop either ledger, or use the old runner after adoption.
An empty/inconsistent existing Alembic ledger is an error, not an adoption signal.

Application rollback still requires a compatible schema/protocol combination.
There is no automatic SQL downgrade, database rewind or offline SQL bypass.
Use a reviewed forward migration when a schema correction is necessary.

## Local development and verification

The existing editable workspace setup installs the migration package; reinstall
after dependency changes. From `appchi-migrations`, use the locked Alembic
`history`, `heads`, `current` and `upgrade head` commands documented in its README.
`current` is read-only and identifies a verified legacy database without adopting
it. History/heads do not require a database. Supply the dedicated migration
identity through the environment, never a checked-in connection string.

Migration tests use `APPCHI_MIGRATION_TEST_ADMIN_URL` for a disposable isolated
PostgreSQL cluster and create/drop unique test databases. API/maintenance/search
compatibility tests use the existing isolated test-admin bindings. Release evidence
must distinguish editable development code, wheel execution and qualified images.

See [conversion evidence](../qualification/alembic-migrations.md) and the
[normative contract](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-repository-layout.md#alembic-migration-execution-rev-38).
