# Development and verification runbook

These commands start the implemented Appchi components from the repository root.
They do not create an authentication bypass, qualify a company integration, enable
capability gates, or authorize production deployment. Consult
[implementation status](../../implementation-status.md) for current coverage.

## Prerequisites and configuration

Install Python 3.13, `uv`, Node.js 22.17 or newer, npm, and PowerShell 7 through approved
development tooling. The scripts use the committed `backend/uv.lock` and
`frontend/package-lock.json`. They do not install or administer shared PostgreSQL,
Kafka, Elasticsearch, Redis, LDAP, Keycloak, Kong or Grafana services.

Inject configuration into the terminal environment through your approved secret
mechanism. Do not put credentials in source control, command arguments or captured
terminal transcripts. Scripts report missing variable names without echoing values.

| Variable | Input |
|---|---|
| `APPCHI_DATABASE_URL` | Supplied application PostgreSQL DSN and application role. |
| `APPCHI_MIGRATION_URL` | Supplied migration PostgreSQL DSN and dedicated migration role. Used only by the migration action. |
| `APPCHI_WRAPPING_KEY` | Secret base64 encoding of exactly 32 bytes, with the environment's managed key lifecycle. |
| `APPCHI_WRAPPING_KEY_ID` | Matching key identifier; persist the association while encrypted records use it. |
| `APPCHI_DEPLOYMENT_EPOCH` | Supplied deployment/recovery epoch; do not casually change it on restart. |
| `APPCHI_PUBLIC_ORIGIN` | Exact browser HTTPS origin. Local example: `https://localhost:3443`, without trailing slash. |
| `APPCHI_CAPACITY_PROFILE` | Absolute path to the reviewed application capacity profile. |
| `APPCHI_BACKEND_URL` | Trusted API origin for the Next.js development proxy; local example: `http://127.0.0.1:8000`. Never include credentials. |
| `APPCHI_DEV_TLS_CERTIFICATE`, `APPCHI_DEV_TLS_KEY` | Local HTTPS certificate and private-key paths for the frontend development server. The certificate must cover the selected local hostname and be trusted by the browser. |

`APPCHI_ENVIRONMENT=development` and the absolute path to
`qualification/development-capacity.json` select the explicitly **unqualified**
local development capacity example. Its numbers are neither a production budget
nor measured service capacity. A missing or gated capability remains visibly
unavailable; do not open its gate to make a page look successful.

For real human sign-in, supply the issuer, confidential-client secret, protected
immutable-directory claim/scope, exact password-authentication ACR/AMR mapping, directory source,
super-admin team and any approved CA binding described in
[identity implementation](../identity-implementation.md#required-configuration).
Use the actual configured Keycloak environment and exact callback/logout URLs.
Company LDAP verification and the qualified directory bootstrap must publish
trusted evidence before `human_auth` can become ready. The development launcher
does not seed users/sessions or set that gate. The directory adapter's `run_cycle`
and its 60-second scheduling/qualification requirements are documented there;
the launcher's `directory` worker role runs that configured verifier. Other
implemented roles are listed in `scripts/dev.ps1` and the relevant domain runbooks;
each requires its own explicit binding, capacity and readiness prerequisites.

Company bindings and actual private-network endpoints are unavailable here. The
user/service owners must supply them and execute the corresponding nonproduction
qualification; no example hostname or development version represents that evidence.

## Clean install and checks

```powershell
./scripts/dev.ps1 -Action Install
./scripts/dev.ps1 -Action Check
./scripts/dev.ps1 -Action Check -CheckConfiguration
```

`Install` runs `uv sync --project backend --locked --all-groups` and `npm ci` from
the frontend directory. `Check` verifies imports and runtime versions without
contacting services. `-CheckConfiguration` additionally validates the backend
configuration schema and DSN syntax without exposing values or claiming connectivity.
`-RequireIdentityConfiguration` checks that the real identity binding variables
are also present; presence alone is not integration qualification.

## Migrate before starting writers

With `APPCHI_MIGRATION_URL` supplied for the intended isolated development database:

```powershell
./scripts/dev.ps1 -Action Migrate
```

The real migration runner takes its dedicated advisory lock, applies ordered SQL
and checks stored migration checksums. Do not edit already-applied SQL. The scripts
do not create a database, create shared roles, copy an application DSN into the
migration variable, or run migrations implicitly during API startup. Migration
errors report their exception class without printing the connection string.

## Start the implemented workloads

Open separate configured terminals. These commands run in the foreground so Ctrl+C
stops the selected workload. Nothing is registered as a background service.

```powershell
# Terminal 1: Python API, listening only on loopback; raw access logs disabled.
./scripts/dev.ps1 -Action Start -Role Api -ApiPort 8000 -RequireIdentityConfiguration

# Terminal 2: durable PostgreSQL source-processing scan.
./scripts/dev.ps1 -Action Start -Role Worker -WorkerRole source

# Terminal 3: HTTPS UI with supplied certificate/key and API origin.
./scripts/dev.ps1 -Action Start -Role Frontend -FrontendPort 3443
```

Open the exact `APPCHI_PUBLIC_ORIGIN`. The UI uses backend OIDC and opaque Secure
cookies; HTTP development pages are not an equivalent authentication test. TLS
files may instead be passed with `-TlsCertificate` and `-TlsKey`. The API port and
`APPCHI_BACKEND_URL` must agree. Without verified identity/bootstrap, sign-in is
gated, rather than replaced with a local password or synthetic user.

When separately supplied and qualified, `APPCHI_KAFKA_BOOTSTRAP` enables launching
the relay role, and `APPCHI_ELASTICSEARCH_URL` supplies the indexer binding:

```powershell
./scripts/dev.ps1 -Action Start -Role Worker -WorkerRole relay
./scripts/dev.ps1 -Action Start -Role Worker -WorkerRole indexer
```

`-Once` requests one bounded worker turn. The current worker intentionally records
dependency failure and preserves pending work; exit code zero alone does not mean
search, publication or any capability gate is ready. Check API health, durable
progress and the corresponding verification evidence.

The provided [local Compose file](../../deploy/local/compose.yaml) is a development
service scaffold. Its complete multi-service startup, connectivity and compatibility
have **not** been verified in this environment. Do not infer a working full-release
stack from that file or these launch commands.

## Verification commands

```powershell
./scripts/verify.ps1 -Suite Unit
./scripts/verify.ps1 -Suite Build
./scripts/verify.ps1 -Suite Browser -BrowserChannel chrome
```

`Unit` runs the backend unit directory. Tests requiring explicit external fixtures
remain skipped if their variables are absent. `Build` runs frontend formatting,
TypeScript and the optimized production build. `Browser` runs all controlled
browser regression files; it temporarily excludes any real-browser opt-in.
Omit `-BrowserChannel` to use the pinned installed Playwright browser. Installing
that browser is an explicit separate command: `npx playwright install chromium`
from `frontend/`. Installed Chrome is a distinct compatibility environment, not a
claim that the pinned browser download succeeded.

For an explicitly isolated PostgreSQL test database, supply
`APPCHI_TEST_DATABASE_URL` and run:

```powershell
./scripts/verify.ps1 -Suite Database
```

This applies migrations and writes synthetic records to that supplied test database.
Never point it at production or shared company data. The ordinary integration suite
does not automatically drop that caller-owned database.

For the real HTTPS browser/API/PostgreSQL smoke, supply
`APPCHI_REAL_BROWSER_DATABASE_ADMIN_URL` for a **local isolated cluster** whose role
can create/drop disposable databases:

```powershell
./scripts/verify.ps1 -Suite BrowserReal -BrowserChannel chrome
```

That fixture creates and later drops only its randomized `appchi_browser_test_*`
database, starts/stops its own unique-port API/UI process trees and uses synthetic
database session evidence exclusively inside the test. It never adds a test login
route or enables authentication on the ordinary application database. See the
[frontend evidence](../frontend.md#real-browserapipostgresql-smoke) for exact assertions
and the distinction from company Keycloak/LDAP qualification.

`-Suite All` runs Unit, Build and Browser, then includes Database and BrowserReal
only when their explicit environment variables are present. Every omitted suite is
printed as `SKIPPED`; an otherwise successful command does not qualify those gates.
Tests stop on the first failing suite. Scripts restore their temporary PYTHONPATH
and browser-channel changes and do not print secret configuration.

## Frontend image and OpenShift artifact

The [frontend Dockerfile](../../frontend/Dockerfile) builds standalone Next.js output.
Supply `APPCHI_BACKEND_URL` as a **build argument** containing the trusted internal
API origin. Next.js resolves its rewrites during the build; changing a runtime
environment variable does not rewrite an already-built image's API routing.

```powershell
docker build --file frontend/Dockerfile --build-arg APPCHI_BACKEND_URL='<supplied trusted internal API origin>' --tag '<local image name>' .
```

This is a local build command, not a publish or deployment instruction. The Node
image tag is a development selection. Release CI must verify the complete image,
record its immutable digest/SBOM/scan results, and bind it to the intended API origin.
If the approved gateway routes `/api` and `/auth` directly, review that routing
contract explicitly; do not silently rely on an unconfigured Next.js proxy.

The [frontend OpenShift template](../../deploy/openshift/frontend.template.yaml)
contains only a Deployment and ClusterIP Service. Every image, replica and resource
input is required; no company allocation is invented. `FRONTEND_IMAGE_REPOSITORY`
plus `FRONTEND_IMAGE_DIGEST` must resolve to an approved `repository@sha256:<64 hex>`
image. `API_INTERNAL_URL` must exactly match its `APPCHI_BACKEND_URL` build argument;
the template uses that exact variable name and records the binding annotation.

Supply qualified replica count, CPU/memory/ephemeral-storage requests and limits,
and bounded temporary/cache volume sizes through a reviewed parameter file. Local
rendering, when the owning platform supplies `oc`, is:

```powershell
oc process --local --filename deploy/openshift/frontend.template.yaml --param-file '<supplied frontend parameter file>' --output yaml
```

Rendering is not deployment authorization. The template does not create public
routes, TLS certificates, network policy, registry credentials, infrastructure
retention or service-owner allocations. The non-sensitive shell probe checks only
the frontend process; it does not qualify backend capabilities or user sign-in.
Containers drop capabilities, use runtime-default seccomp, run as non-root and
mount bounded writable temporary/cache volumes under an otherwise read-only root.

## Executed evidence and limits

PowerShell parsing, `dev.ps1 -Action Check`, synthetic configuration validation,
locked dependency installation, generated-contract checks, TypeScript and the
optimized production build have passed locally. The frontend template contains
two objects and 13 required parameters; local structure checks are not OpenShift
server validation.

Actual PostgreSQL/Elasticsearch backend tests, controlled browser workflows and
real HTTPS browser/API/PostgreSQL smoke checks have passed locally. Current totals,
migration coverage and outstanding reruns are maintained in
[implementation status](../../implementation-status.md). Packaging checks do not
establish correctness of subsequent changes.

No OpenShift deployment, container build, shared-service change, company identity
login, sustained capacity or coordinated infrastructure restore has been
qualified. The private-environment runbook records the corresponding gates.
