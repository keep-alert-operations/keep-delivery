# Appchi

For the running local core stack, open **https://localhost:3443** and use the
[local runtime guide](docs/local-runtime.md) for credentials, startup commands,
service addresses and unavailable features.

For local sign-in without company LDAP, use the
[local Keycloak setup](docs/local-keycloak.md). It provides custom users/groups,
real username/password login and a separate local Appchi database.

Appchi is a Python/FastAPI and Next.js implementation of the design documents in this repository. PostgreSQL owns application state; Elasticsearch is the current-alert search projection, Kafka carries durable pipeline notifications, and live views reconcile PostgreSQL progress.

**This checkout is an implementation checkpoint, not a completed initial release.** Alert/incident workflows, maintenance processing, automatic correlation, shared configuration, protected deletion and global administration have connected local implementations and tests. Operator provisioning has an executable, explicitly configured adapter; protected alert/incident cleanup and search rebuilding are implemented with local checks. Protected permission/credential recovery and rollback reconstruction, rendered visibility observations, and remaining UX/release work are still under implementation. Private-service qualification and production readiness are separate and have not been established. See [implementation status](implementation-status.md) for every original design issue and remaining release gates.

## Run and test

Use [the development runbook](docs/runbooks/development.md) for installation, configuration, migration and startup commands. Python 3.13 and Node 22 are required by the checked locks. PostgreSQL is required; ordinary interactive sign-in additionally needs configured Keycloak and directory verification. There is no application authentication bypass.

From PowerShell at the repository root:

```powershell
uv sync --project backend --locked
npm --prefix frontend ci
$env:APPCHI_MIGRATION_URL = '<isolated PostgreSQL migration connection>'
backend/.venv/Scripts/python.exe -m appchi.migrate
$env:APPCHI_TEST_DATABASE_URL = '<isolated PostgreSQL test connection>'
$env:APPCHI_IDENTITY_TEST_ADMIN_URL = '<isolated PostgreSQL test cluster administrator connection>'
backend/.venv/Scripts/python.exe -m pytest backend/tests -q --basetemp=.local/pytest --junitxml=qualification/pytest-results.xml
backend/.venv/Scripts/python.exe qualification/check_contracts.py
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend test
```

Test DSNs must target disposable development data. Identity and real-browser tests create and drop uniquely named test databases; the application itself never receives those administrator credentials. The opt-in [real browser smoke](docs/frontend.md) exercises Next.js → FastAPI → PostgreSQL with a test-only verified session fixture, acknowledgement and note persistence. Ordinary browser regression tests use controlled API responses and do not prove full service integration.

Docker is available: separate local Keycloak and Kafka/Redis Compose projects
are running alongside native PostgreSQL 18.2 and Elasticsearch 9.0.0. Real
browser login, alert ingestion/search, live events, acknowledgement and notes
passed against this stack. The full infrastructure Compose stack, application
container builds and OpenShift execution remain unverified.

## Repository

- `backend/appchi`: application modules, explicit transactional SQL, strict models and service adapters.
- `migrations`: ordered checksummed migrations; runtime processes do not migrate schemas.
- `frontend`: application screens, scoped API client and generated OpenAPI types.
- `contracts`: generated HTTP contract.
- `qualification`: development-only capacity profile, service-manifest template and executed test evidence.
- `deploy`: development containers and parameterized application deployment templates.
- `docs`: implementation boundaries, private integration inputs and runbooks.

[Private-environment qualification](docs/runbooks/private-qualification.md) explains how to collect evidence on the network available to you. The manifest contains configuration/evidence references, never credentials. Do not enable a capability just to remove an unavailable response: its implementation and required integration checks must pass first.
