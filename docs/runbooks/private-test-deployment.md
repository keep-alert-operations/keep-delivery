# Isolated OpenShift deployment using internal mirrors

This procedure matches the 2026-10-07 environment clarification: committed Git
repositories have been transferred; builds can reach only internal mirrors;
Kafka, Elasticsearch and Grafana 12 are supplied externally. Deploy Keep,
PostgreSQL, Redis, Kong and the test identity services (OpenLDAP and a mock ADAPI,
REV-73) in a dedicated OpenShift test namespace. Use synthetic data and dedicated
synthetic test users. No deployment, image build or Grafana 12 compatibility
result is claimed by this procedure.

The [full deployment guide](openshift-load-testing.md),
[bundle interface/Secret inventory](../../deploy/openshift/load-test/README.md) and
[initialization procedure](cluster-initialization.md) remain authoritative for
staged execution. This document adds source transfer and internal build steps.
All `REQUIRED_*` values must be replaced locally; credentials stay in private files.

## 1. Apply the missing deployment overlay

The transferred committed repositories do not contain several local deployment
assets. Transfer `keep-isolated-test-overlay.tar.gz`, its SHA-256 sidecar and
the standalone `deployment_overlay.py` installer supplied with the archive.
The archive adds the deployment renderer, images, test-identity/load tools,
search bootstrap, operator proof helper and mirror-aware Dockerfiles. It does not
include unrelated local frontend/executor changes, credentials or data volumes.
Current overlays also include each Python image's `pyproject.toml`, `poetry.toml`
and `poetry.lock`; rebuild the overlay after dependency-tool changes so transferred
Dockerfiles and their dependency metadata stay compatible.

On the private administration host, from the sibling workspace root:

```bash
(cd /TRANSFER && sha256sum --check keep-isolated-test-overlay.tar.gz.sha256)
python3 /TRANSFER/deployment_overlay.py check \
  --root "$PWD" --archive /TRANSFER/keep-isolated-test-overlay.tar.gz
python3 /TRANSFER/deployment_overlay.py apply \
  --root "$PWD" --archive /TRANSFER/keep-isolated-test-overlay.tar.gz
```

The installer requires the exact recorded Git base for each affected repository,
verifies every file and refuses conflicting local edits before writing anything.
Do not manually overwrite a conflict. Inspect and reconcile it. A repeated apply
accepts already-installed identical files. This remains a local test overlay, not
a committed release. Preserve the archive/manifest hashes with test results.

## 2. Obtain the private inputs

Install the cluster-compatible `oc`, Python 3.13, Poetry 2.3.1, a BuildKit-enabled
container builder and Git on the administration/build host using internal sources.
Build for the Linux architecture of the cluster nodes. The renderer itself uses
Python's standard library and does not need a dependency installation.

| Input | Placeholder to fill privately |
| --- | --- |
| Git mirror organization prefix | `https://REQUIRED_GIT_HOST/REQUIRED_ORGANIZATION/` |
| Artifactory PyPI simple index | `https://REQUIRED_ARTIFACTORY/artifactory/api/pypi/REQUIRED_PYPI/simple` |
| Artifactory npm registry | `https://REQUIRED_ARTIFACTORY/artifactory/api/npm/REQUIRED_NPM/` |
| Debian package mirrors | `https://REQUIRED_APT_HOST/REQUIRED_DEBIAN_PATH`, separate security mirror as applicable |
| Container registry prefix | `REQUIRED_REGISTRY/REQUIRED_IMAGE_REPOSITORY` |
| Kafka bootstrap and advertised addresses | `REQUIRED_KAFKA_BOOTSTRAP` and every actual reachable broker |
| Elasticsearch HTTPS endpoint | `https://REQUIRED_ELASTICSEARCH_HOST:REQUIRED_PORT` |
| Grafana HTTPS endpoint and organization | `https://REQUIRED_GRAFANA_HOST`, actual dedicated organization ID |
| Cluster/application names | Namespace, storage class, quota, allowed nodes, application/v1/v2 DNS |
| Certificates and credentials | Registry pull secret, runtime TLS/CA files, per-workload credentials |

Mirror the pinned commits of the private Git dependencies with their history.
The mirror prefix must expose each repository as `PREFIX/keep-NAME.git`.
Recreating repositories with new root commits will not satisfy the existing pins.
Python wheels/sdists must be the original bytes, including the Linux Python 3.13
native CEL wheel; do not replace a package under the same version with a rebuild.
Mirror build requirements such as Poetry/hatchling as well as runtime packages.

Mirror these base-image lineages by immutable digest: Python 3.13 slim Bookworm,
Node 22 Bookworm slim, Keep-plugin-compatible Kong, an OpenLDAP image that meets
the bundle's non-root contract, and the chosen compatible PostgreSQL/Redis bases.
The mock ADAPI runs on the mirrored Python 3.13 slim Bookworm image. The PostgreSQL wrapper requires
the official Debian entrypoint and NSS wrapper. Existing local test selections
are evidence about those local versions, not proof for different vendor images.
Also mirror the `docker/dockerfile` BuildKit frontend: the Dockerfile `syntax`
directive otherwise needs Docker Hub even when FROM images are internal.

## 3. Prepare build secrets

Commands below use Bash, from `keep-delivery`, with private files outside Git:

```bash
umask 077
PRIVATE="$HOME/keep-test-private"
mkdir -p "$PRIVATE"
```

Create `build.env` with trusted POSIX assignments. It is sourced as executable
shell input by the build; populate it yourself, never from an untrusted download:

```dotenv
PIP_INDEX_URL='https://REQUIRED_ARTIFACTORY/artifactory/api/pypi/REQUIRED_PYPI/simple'
KEEP_GIT_BASE_URL='https://REQUIRED_GIT_HOST/REQUIRED_ORGANIZATION/'
KEEP_GIT_USERNAME='REQUIRED_GIT_USERNAME'
NPM_CONFIG_REGISTRY='https://REQUIRED_ARTIFACTORY/artifactory/api/npm/REQUIRED_NPM/'
POETRY_HTTP_BASIC_INTERNAL_USERNAME='REQUIRED_PYPI_USERNAME_IF_NEEDED'
POETRY_HTTP_BASIC_INTERNAL_PASSWORD='REQUIRED_PYPI_TOKEN_IF_NEEDED'
```

Remove unused authentication settings. Supply pip authentication through the
approved pip/keyring mechanism or private `PIP_INDEX_URL` credentials in this
BuildKit-only secret; never put credentials in arguments, Dockerfile ARG/ENV,
source lockfiles or reports. The staged Poetry index URL must contain no credentials.
The metadata-only host remap does not require package or Git credentials. Supply
these credentials only to the actual build through the secrets below. Host Python/Poetry/Git
must trust the corporate CA too.

Create `git-token` containing only the Git read token. The BuildKit secret ID
remains `github_token` for compatibility; the token is used against the configured
internal Git mirror. For npm create `npmrc` with your actual registry and
host/path-scoped `_authToken` configuration. Its scope must match the private
registry path; do not use a global token or disable strict SSL.

Create `build-ca.pem` with the public corporate CA certificate(s), no private
keys. It is installed before apt and used for Python/npm/Git package access.
Create `debian.sources` with the actual signed Bookworm repositories, for example:

```text
Types: deb
URIs: https://REQUIRED_APT_HOST/REQUIRED_DEBIAN_PATH
Suites: bookworm bookworm-updates
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: https://REQUIRED_APT_HOST/REQUIRED_DEBIAN_SECURITY_PATH
Suites: bookworm-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
```

Use the infrastructure-supplied signing key if the mirror resigns packages.
No `trusted=yes`, insecure registry, TLS bypass or credential-bearing apt URL.

## 4. Stage mirror locks and build the application images

Poetry locks identify registry artifacts by filename and SHA-256. The supplied
helper creates separate build copies and adds a named primary internal source to
both the project and lock. It preserves all package versions, pinned Git sources,
dependency edges, groups, markers, extras and artifact hashes. It computes only the
staged project metadata hash using the pinned Poetry API, then executes
`poetry check --lock`. It never resolves dependencies, contacts the mirror or runs
a source build backend during this step. Source/release locks are untouched.

Run `relock` with delivery's `.venv/bin/python` after its locked development setup;
that environment includes Poetry 2.3.1 and its tool libraries. Alternatively use
the interpreter that owns the standalone Poetry tool. The helper rejects other
Poetry versions before making changes. `stage` and the deployment renderer need
only the standard library.
The mirror must serve the original artifact bytes and keep all metadata/downloads
inside the private network; verify this through the actual build egress. Poetry
validates downloaded artifacts against the retained hashes. Remapping alone
cannot prove mirror availability, native wheel compatibility or network routing.

Provide host credentials privately, then run:

```bash
set -a
. "$PRIVATE/build.env"
set +a
export KEEP_GIT_TOKEN_FILE="$PRIVATE/git-token"
export SSL_CERT_FILE="$PRIVATE/build-ca.pem"
export GIT_SSL_CAINFO="$PRIVATE/build-ca.pem"
PYPI_INDEX='https://REQUIRED_ARTIFACTORY/artifactory/api/pypi/REQUIRED_PYPI/simple'

for name in api ingestion processor correlation indexer migrations; do
  context="$PRIVATE/build/keep-$name"
  python3 scripts/prepare_internal_build.py stage \
    --repository "keep-$name" --output "$context"
  chmod 700 "$context/scripts/git-askpass.sh"
  .venv/bin/python scripts/prepare_internal_build.py relock \
    --context "$context" --index "$PYPI_INDEX" \
    --git-base-url "$KEEP_GIT_BASE_URL"
done
python3 scripts/prepare_internal_build.py stage \
  --repository keep-frontend --output "$PRIVATE/build/keep-frontend"
```

Use a credential-free index URL in `--index` even if your private pip URL has
authentication. For Poetry authentication, `POETRY_HTTP_BASIC_INTERNAL_USERNAME/PASSWORD`
apply to the staged named index. A remap/check failure restores the build copy's
original project and lock files; correct the mirror rather than accepting drift.

Frontend retains its original `package-lock.json` and integrity values.
`npm ci` uses the configured npm registry and Git URL rewrite. npm treats its
default `registry.npmjs.org` lock URLs as the configured registry; inspect build
egress and mirror metadata to ensure no other public downloads are attempted.
See [npm lockfile semantics](https://docs.npmjs.com/files/package-lock.json/) and
[Poetry package sources](https://python-poetry.org/docs/repositories/#package-sources).

Fill these credential-free values locally, then build and push:

```bash
REGISTRY='REQUIRED_REGISTRY/REQUIRED_IMAGE_REPOSITORY'
TAG='REQUIRED_TEST_BUILD_ID'
PYTHON_BASE='REQUIRED_REGISTRY/python@sha256:REQUIRED_DIGEST'
NODE_BASE='REQUIRED_REGISTRY/node@sha256:REQUIRED_DIGEST'
SYNTAX='REQUIRED_REGISTRY/dockerfile@sha256:REQUIRED_DIGEST'

for name in api ingestion processor correlation indexer migrations; do
  docker build \
    --build-arg "BUILDKIT_SYNTAX=$SYNTAX" \
    --build-arg "PYTHON_BASE_IMAGE=$PYTHON_BASE" \
    --secret "id=apt_sources,src=$PRIVATE/debian.sources" \
    --secret "id=build_ca,src=$PRIVATE/build-ca.pem" \
    --secret "id=build_environment,src=$PRIVATE/build.env" \
    --secret "id=github_token,src=$PRIVATE/git-token" \
    -t "$REGISTRY/keep-$name:$TAG" "$PRIVATE/build/keep-$name"
  docker push "$REGISTRY/keep-$name:$TAG"
done

docker build \
  --build-arg "BUILDKIT_SYNTAX=$SYNTAX" \
  --build-arg "NODE_BASE_IMAGE=$NODE_BASE" \
  --build-arg KEEP_BACKEND_URL=http://keep-product-api:8000 \
  --secret "id=apt_sources,src=$PRIVATE/debian.sources" \
  --secret "id=build_ca,src=$PRIVATE/build-ca.pem" \
  --secret "id=build_environment,src=$PRIVATE/build.env" \
  --secret "id=github_token,src=$PRIVATE/git-token" \
  --secret "id=npmrc,src=$PRIVATE/npmrc" \
  -t "$REGISTRY/keep-frontend:$TAG" "$PRIVATE/build/keep-frontend"
docker push "$REGISTRY/keep-frontend:$TAG"
```

Authenticate the builder to Artifactory through its normal credential store.
Use no password CLI flags. Record the destination digest of each push.
Preserve `.keep-internal-build.json` receipts and staged lock hashes with the
test inventory. These are explicit local build overrides, not new release pins.

Build the remaining wrappers with mirrored digests, also supplying
`--build-arg "BUILDKIT_SYNTAX=$SYNTAX"` wherever a syntax directive exists:

| Result image | Dockerfile / context | Required base argument |
| --- | --- | --- |
| Keep Kong | `../keep-ingestion/kong/Dockerfile` / `../keep-ingestion` | `KONG_BASE_IMAGE` |
| Keep PostgreSQL | `deploy/images/postgres/Dockerfile` / `deploy/images/postgres` | `POSTGRES_BASE` |
| Kong PostgreSQL | Same wrapper, compatible chosen base | `POSTGRES_BASE` |
| Redis | `deploy/images/redis/Dockerfile` / `deploy/images/redis` | `REDIS_BASE` |
| Optional k6 | `qualification/load-test/Dockerfile` / `qualification/load-test` | `K6_IMAGE` |

Do not build Kafka, Elasticsearch or Grafana images for this environment.
The API image also runs explicit identity publication and operator proof tools;
the indexer image also runs the search bootstrap Job. Domain/contracts are
libraries resolved into images, not additional Deployments.

## 5. Clock measurement prerequisite

NTP/chrony synchronizes node clocks. Keep also needs recent evidence of their
actual synchronization and measured UTC offset, plus the PostgreSQL clock.
For example, the platform can provide chrony/NTP monitoring for eligible nodes
and a measured PostgreSQL `clock_timestamp()` comparison against trusted UTC,
accounting for measurement uncertainty and network delay. Do not assume the
database shares the application's host clock because both run in OpenShift.

The mounted ConfigMap `keep-clock-health` contains two files:

| Key | Meaning |
| --- | --- |
| `health.json` | Trusted report covering the actual workload host/node |
| `pg-health.json` | Trusted report covering the authoritative PostgreSQL clock |

Each JSON report contains `synchronized` (boolean), `offset_seconds` (numeric),
and `measured_at` (UTC RFC3339 timestamp). The runtime requires true synchronization,
absolute offset at most one second and measurement age at most 60 seconds
(and no more than one second into the future). Refresh faster than that deadline,
including ConfigMap mount propagation time; ordinary ConfigMap updates may reach
pods too slowly, so validate the chosen delivery method before pipeline startup.
Reports must cover every node on which the bound workloads can run. A shared
report is acceptable only with measured coverage of all eligible nodes; otherwise
use per-node publication/mounts and scheduling bindings. Ingestion needs node
evidence only, while API/workers also need PostgreSQL evidence. Kong uses the node
report for credential expiry.

The bundle does not implement a clock collector. Ask the OpenShift platform team
which trusted clock measurements already exist and how their collector can supply
these reports. The collector needs no access to application credentials; constrain
any ConfigMap writer to this namespace/resource. If no collector exists, it is a
remaining deployment prerequisite. Do not write timestamps with a fabricated zero
offset, switch on Kong's local clock bypass, or remove clock gates to start pods.

## 6. Render and start the isolated test environment

Copy `deploy/openshift/load-test/configuration.example.json` into `$PRIVATE`.
Replace every placeholder with namespace, release ID, storage class, pull Secret,
route hostnames and all 13 immutable image references. PostgreSQL bases may share
one image digest only when that base is compatible with both applications;
database data/credentials remain separate. Leave `global_tenant_id` null initially.
Create per-workload TLS/credential Secrets using the bundle's exact inventory.
Keep Grafana credentials out of ingestion. Use the generated test users, Keep's
password sign-in against the test OpenLDAP and an explicit directory refresh from
the mock ADAPI.

```bash
python3 qualification/load-test-identity/prepare.py \
  --app-origin https://REQUIRED_APPLICATION_HOST \
  --users 10 --output "$PRIVATE/identity"
python3 deploy/openshift/load-test/render.py \
  --config "$PRIVATE/configuration.json" \
  --output "$PRIVATE/rendered" --bootstrap-only
bash "$PRIVATE/rendered/render-apps.sh"
```

Process/dry-run and apply each stage using `oc -n REQUIRED_NAMESPACE`.
Never apply the entire rendered directory at once. Start foundation and the
two PostgreSQL instances/Redis, complete the Keep migration Job and runtime
logins, then read the database-created Global UUID. Update configuration and
rerender without `--bootstrap-only`. Complete Kong migrations, OpenLDAP, mock
ADAPI and identity-tooling startup, fresh-only Kong routing and the explicit
bootstrap directory refresh. Initialize Kafka while
all ingestion/consumers remain stopped. Supply real clock evidence and complete
search bootstrap with writers stopped. Start supported API/frontend/processor/
indexer paths; enable admission and start ingestion only after these steps pass.
Use the exact commands in the full guide, including Job completion waits.

Start with one replica per active process. The renderer initially leaves application
replicas at zero. Reapplying those manifests stops them again. Correlation remains
stopped until its profile/evidence/recovery prerequisites are satisfied; deferred
maintenance, retention, directory, operator and live workers are not deployed.
API owns live publishing and synchronous operator provisioning.

## 7. Connect existing Grafana 12

Use a dedicated test organization and org-bound service account. Capture the
exact installed version from `/api/health` and organization ID from `/api/org`;
the binding checks exact values, not just major version 12. The deployed adapter
uses `/api/v1/provisioning/*` and its legacy controlled-writer profile. Verify
actual installed contact-point, policy and complete alert-rule inventory/read/write
behavior before activating operator provisioning. Keep's local Grafana 11.6
tests do not establish compatibility with this installation.

Reserve the test policy/rule namespace, preserve the existing routing tree and
establish the controlled writer required by the binding. Grafana must reach the
Kong v2 HTTPS route and trust its CA. The API must reach Grafana management and
trust its CA. Prepare direct serving-member Kong proof, the deployed proof helper,
and an actual validated `OperatorBinding`; do not fill capability fields with
invented evidence. Verify create, rotate, revoke and delivery to the owning tenant
through normal APIs. Exact routing label: `Keep - {operator name}` with literal
string value `true`. See [Grafana provisioning API](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/alerting_provisioning/).

V1 admission/source/search can be tested before operator provisioning passes.
V2/Grafana tests require verified provisioning. Existing Grafana is also only a
dashboard: actual metric collectors/data sources are needed for load observation.

## 8. Acceptance and remaining evidence

First prove normal login/authorization, one v1 synthetic alert in Global, Kafka
acknowledgement and source proof, Elasticsearch search and exact dashboard counts,
then live updates. Prove the v2/Grafana lifecycle separately. Validate arbitrary
UID, TLS, PVC permissions, actual Routes/CNI, secret reads and registry pulls on
the target cluster. Apply workload-specific egress policies for DNS and the exact
supplied endpoints; the bundle's foundation includes ingress boundaries, not a
complete external egress allocation. Keep unqualified capabilities closed.

Record actual source bases + overlay hash, staged lock hashes, image digests,
migration checksums, configuration/resource settings and test outcomes. First
measure short diagnostic load and storage growth. No capacity result follows from
the proposed starting allocations. This isolated deployment does not establish
production qualification or close DEL-01 through DEL-08.
