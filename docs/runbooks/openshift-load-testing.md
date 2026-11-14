# Keep: private OpenShift deployment and load testing

This is the handoff for an **isolated test environment**. You provide Elasticsearch,
Kafka and Grafana inside the private network. Deploy Keep, PostgreSQL, Redis,
Kong, Authelia and OpenLDAP in OpenShift. Use dedicated Authelia + OpenLDAP test
identities and your private image registry. OpenLDAP is the directory, Authelia the
OIDC provider. No connection to that cluster, deployment, or capacity
result is claimed by this document.

Start with a small diagnostic run. Measure the bottleneck before adding a Redis
cache: Redis is already required for live hints, but the current architecture
does not cache completed search/count results. PostgreSQL remains authoritative;
Elasticsearch holds the rebuildable search projection. Ingestion uses Kafka and
**has no Keep PostgreSQL, Elasticsearch or Redis dependency**.

**Complete initialization and integration checks before a system load run.**
The bundle includes an explicit search bootstrap Job and a private operator proof
helper. Follow [cluster initialization](cluster-initialization.md) to run them
against the actual supplied services. Their presence does not remove qualification
gates, establish production readiness or turn pod readiness into evidence that
search and provisioning work. Never clear capability/recovery gates manually.

| Test path | Current prerequisite / limit |
| --- | --- |
| v1 admission and source processing | Migrations, isolated Kafka, cutover, real clock evidence and runtime DB permissions; measure Kafka acknowledgement and canonical SQL state |
| Search, dashboard reads, seed search verification and full UI visibility | Successful `36-search-bootstrap.json` with fresh ES capability checks, empty reserved index/database and initialized search progress; then actual end-to-end visibility checks |
| v2 / Grafana ingestion through provisioned operators | Kong preparation, actual safe provisioning binding and deployed `32-operator-proof.json`; verify the complete operator lifecycle against every serving member |
| Correlation / maintenance | Applicable capacity and recovery evidence; dormant until current capability requirements are satisfied |

The k6 primary recipe below deliberately refuses to declare a fixture ready
before its authoritative/search checks pass. An admission-only diagnostic is not
the full-system result needed to evaluate a search/count cache.

## 1. Files to take into the network

Keep the sibling repository layout. Transfer the exact source revisions/patches
used for builds, or immutable built images plus this delivery checkout.

| File/directory | Purpose |
| --- | --- |
| [Bundle renderer and configuration](../../deploy/openshift/load-test/) | Infrastructure, application template parameters, resource allocations, Routes and network boundaries |
| [Bundle instructions](../../deploy/openshift/load-test/README.md) | Exact Secret inventory, database bootstrap and render interface |
| [Cluster initialization](cluster-initialization.md) | Search activation, internal operator proof, direct Kong member binding and retry procedures |
| [Load toolkit](../../qualification/load-test/README.md) | Executable k6 seed, ingestion and authenticated API-read tests |
| [Test identity generator](../../qualification/load-test-identity/prepare.py) | Private native-user realm and identity configuration |
| [PostgreSQL wrapper](../../deploy/images/postgres/Dockerfile) | Arbitrary-UID PostgreSQL image |
| [Redis wrapper](../../deploy/images/redis/Dockerfile) | Arbitrary-UID Redis image |
| [Existing application templates](../../deploy/openshift/) | Canonical service deployment definitions |
| [Kafka protocol procedure](kafka-first.md) | Offline initialization, clocks and loss/recovery fences |
| [Current worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md) | Which processes exist and which features remain deferred |

The commands below use **Bash on a build host or private-network administration
host**, from `keep-delivery`, unless another directory is stated. Install the
cluster-compatible `oc`, Python 3.13, a container builder and k6 (or its supplied
container) on those hosts. The cluster does not need access to GitHub, PyPI or npm
when you import fully built images.

## 2. Topology and prerequisites

```text
load generator / browsers
  |-- application HTTPS --> frontend; /api and /auth --> Product API
  |-- native v1 HTTPS --> public Ingestion API --> Kafka received
  `-- native v2 / Grafana HTTPS --> Kong --mTLS--> private Ingestion API
                                                     `--> Kafka received
Kafka received --> processor --> PostgreSQL + Kafka changed
Kafka changed  --> indexer --> Elasticsearch
Kafka changed  --> correlation --> PostgreSQL (when its capability is enabled)
Product API --> PostgreSQL, Elasticsearch, Redis, Authelia (OIDC), OpenLDAP (LDAPS)
Product API --> Kong Admin API / external Grafana for operator provisioning
Authelia --> OpenLDAP (LDAPS); Kong --> its PostgreSQL
```

Domain and contracts are libraries inside service images, not Deployments.
The migration image runs a Job. Public and private ingestion share one image but
have separate configuration and listeners. No operator worker, live worker,
directory polling worker, retention sweeper or maintenance timer is deployed.

Before applying anything, obtain:

1. A dedicated namespace, allowed worker nodes, storage class/PVC expansion policy,
   namespace CPU/memory quota and image pull credentials. Establish that the test
   cannot interfere with another Keep installation.
2. Four DNS names: application, Authelia, native v1, and Kong/native v2. Supply
   trusted certificates, browser trust and service CA bundles. The application
   origin must be the same for the UI, `/api` and `/auth`.
3. Elasticsearch HTTPS endpoint, actual version, test-only index name, API-key or
   mTLS credentials, mapping/index-management permissions for the indexer, and
   read permissions for API. Confirm allocation and retention with its provider.
4. Kafka bootstrap **and advertised broker addresses reachable from pods**, actual
   version, TLS/SASL credentials, partition count, retention, durability and ACLs.
   Reserve `keep.development.alerts.received`,
   `keep.development.alerts.changed` and the `keep.development.*-v2` consumer
   groups exclusively for this test installation. Namespace isolation does not
   isolate Kafka names. If those names are already used, arrange an isolated
   broker/cluster endpoint before proceeding. Do not alter another installation's
   offsets or partitions.
5. External Grafana URL, CA, a dedicated test organization/scope and approved
   management credentials. Do not run workstation reset scripts against shared
   Grafana. Ordinary ingestion benchmarking can begin with v1 before operator
   provisioning is qualified; v2 needs a correctly provisioned operator/key.
6. Measured node and PostgreSQL clock evidence, refreshed continuously, plus
   access to cluster/container, PostgreSQL, Kafka and Elasticsearch telemetry.
   Grafana alone is a dashboard: its data sources/collectors must exist.

Record these in a private copy of the
[environment manifest](../../qualification/environment-manifest.example.json).
Keep unverified capabilities unqualified. The native-user test exception supplies
no evidence about LDAP integration or company-directory propagation.

## 3. Starting resource allocations

These are **proposed test starting values**, not measured support limits. Start
with one replica per active process; the renderer initially leaves application
replicas at zero so migration and protocol initialization happen first.
Change `resource_overrides` in the private bundle configuration and keep the
resulting allocation with every test report.

| Workload | CPU request / limit | Memory request / limit | Storage / notes |
| --- | --- | --- | --- |
| Keep PostgreSQL | 1 / 2 | 2Gi / 4Gi | 20Gi initial PVC, **smoke tests only until sized** |
| OpenLDAP | 250m / 1 | 256Mi / 1Gi | 5Gi PVC for the seeded directory tree |
| Kong PostgreSQL | 250m / 1 | 256Mi / 1Gi | 5Gi PVC |
| Redis | 100m / 500m | 128Mi / 512Mi | Ephemeral hints; PostgreSQL reconciliation repairs missed hints |
| Authelia | 250m / 1 | 256Mi / 768Mi | Ephemeral SQLite session store; stateless against OpenLDAP |
| Identity publication tool | 100m / 500m | 128Mi / 512Mi | Idle pod; runs the LDAP worker only on explicit command |
| Kong | 250m / 1 | 256Mi / 1Gi | Writable temporary prefix |
| Operator proof | 100m / 500m | 128Mi / 512Mi | Private API-image workload; bounded read-only SQL pool and direct gateway checks |
| Search bootstrap Job | 250m / 1 | 256Mi / 1Gi | One-off indexer-image tool, before writers start |
| Product API | 500m / 2 | 512Mi / 2Gi | Includes live/SSE and synchronous operator provisioning |
| Public ingestion | 250m / 1 | 256Mi / 1Gi | Kafka only |
| Private ingestion | 250m / 1 | 256Mi / 1Gi | Kafka only, incoming mTLS |
| Processor | 500m / 2 | 512Mi / 2Gi | PostgreSQL + Kafka |
| Indexer | 500m / 2 | 512Mi / 2Gi | PostgreSQL + Kafka + Elasticsearch |
| Correlation | 1 / 2 | 1Gi / 2Gi | Remains stopped until supported profile/evidence exists |
| Frontend | 250m / 1 | 256Mi / 1Gi | Next.js server |
| k6 generator | 1 / 2 | 1Gi / 2Gi | Proposed separate allocation; verify it can deliver the rate |

Reserve additional space for migration/bootstrap Jobs, rolling replacement pods,
metrics and real browsers. Browser load often needs a separate machine or node
pool. Total requests are only scheduler reservations; headroom below memory limits
and absence of CPU throttling must be measured. External Kafka/ES capacity is not
included in this table. Do not promise 250 alerts/s from these starting numbers.

At 250 deliveries/s, 24 hours produces **21.6 million deliveries**. With 50% new
alerts, that is about **10.8 million new rows**, even when resolutions keep the
unresolved set near 5,000. History, replay proof, indexes, WAL, Kafka and ES all
consume space. Run a 10–30 minute pilot, measure growth in each store, extrapolate
to the intended duration, include replication/retention/recovery headroom, then
resize **before** a soak. Agree storage and stop thresholds with the providers.

Start database connection pools at 2 per application process, then size from
observed concurrency. Sum every replica's pool, the API operator connection,
identity/migration/admin connections and monitoring; keep below PostgreSQL's
allocated connection budget. Scaling replicas also scales connections. More
processor replicas do not split a single Kafka partition or remove same-tenant
ordering constraints.

## 4. Build and move images

Build on a host with source/package access. Private Python Git dependencies and
the frontend's private dependencies use the existing Dockerfile BuildKit secret;
never use build arguments for credentials. Each application build context is its
own repository root. Use pinned base-image digests, record build logs and scan the
resulting images. See [workspace build instructions](../repository-workspace.md#build-and-ci).

| Image | Build context | Dockerfile |
| --- | --- | --- |
| API | `../keep-api` | `Dockerfile` |
| Ingestion, both listeners | `../keep-ingestion` | `Dockerfile` |
| Processor | `../keep-processor` | `Dockerfile` |
| Correlation | `../keep-correlation` | `Dockerfile` |
| Indexer | `../keep-indexer` | `Dockerfile` |
| Migrations | `../keep-migrations` | `Dockerfile` |
| Frontend | `../keep-frontend` | `Dockerfile` |
| Kong + Keep plugin | `../keep-ingestion` | `kong/Dockerfile` |
| PostgreSQL wrapper, each chosen vendor base | `deploy/images/postgres` | `Dockerfile` |
| Redis wrapper | `deploy/images/redis` | `Dockerfile` |
| k6 toolkit | `qualification/load-test` | `Dockerfile` |

Example application build; obtain `KEEP_REPOSITORIES_TOKEN` through your normal
credential manager without printing it. `$BUILD_REGISTRY`, `$RELEASE` and every
base variable below must be real values you selected.

```bash
docker build --secret id=github_token,env=KEEP_REPOSITORIES_TOKEN \
  --build-arg PYTHON_BASE_IMAGE="$PYTHON_BASE_IMAGE" \
  -t "$BUILD_REGISTRY/keep-api:$RELEASE" ../keep-api
docker build --build-arg KONG_BASE_IMAGE="$KONG_BASE_IMAGE" \
  -f ../keep-ingestion/kong/Dockerfile \
  -t "$BUILD_REGISTRY/keep-kong:$RELEASE" ../keep-ingestion
```

Authelia and OpenLDAP need no Keep-built image: mirror `authelia/authelia` and an
`entryUUID`-capable OpenLDAP (for example `bitnami/openldap`) by digest and reference
them directly in the `authelia` and `openldap` configuration image keys. There is no
Keycloak theme image and no separate identity-provider database.

Repeat the application build for every application/migration image and use the
specific base arguments documented beside the infrastructure Dockerfiles. Build
frontend with `NODE_BASE_IMAGE`; direct Routes send `/api` and `/auth` to API, so
do not bake a workstation URL into the frontend. Test Linux architecture must
match cluster nodes, including native CEL and frontend native dependencies.
Preserve the checked base lineages when mirroring: Python 3.13 on Debian Bookworm
and Node 22 on Debian Bookworm. These Dockerfiles use their package manager and
interpreter layout; an arbitrary vendor base is not a drop-in replacement.
Source builds also require the Dockerfile build frontend, OS package repositories,
Python/npm packages and locked private Git dependencies. Mirroring only base images
does not make source builds offline; mirror every input or import complete images.

If both registries are reachable on a transfer host, use an approved registry
copy tool to copy the images. Otherwise use `docker save` (or OCI archives),
transfer the archives through your approved path, and load/tag/push on a host in
the private network:

```bash
# Build host, repeat/include the complete image list:
docker save -o keep-images.tar "$BUILD_REGISTRY/keep-api:$RELEASE"
sha256sum keep-images.tar > keep-images.tar.sha256
# Private host, after transferring archive and checksum:
sha256sum --check keep-images.tar.sha256
docker load -i keep-images.tar
docker tag "$BUILD_REGISTRY/keep-api:$RELEASE" "$PRIVATE_REGISTRY/keep-api:$RELEASE"
docker push "$PRIVATE_REGISTRY/keep-api:$RELEASE"
```

Take the **destination registry digest** for each image and put its
`registry/path@sha256:...` reference in the bundle config. A tag alone is not a
deployment identity. Include the wrappers, Kong plugin, k6 image and the mirrored
upstream Authelia and OpenLDAP images in the transfer. This is a local test bundle if it contains uncommitted
patches; preserve the patch hashes rather than claiming they are committed
release revisions. [OpenShift image guidance](https://docs.redhat.com/en/documentation/openshift_container_platform/4.19/html/images/managing-images)
describes registry integration; use the documentation for your installed release.

## 5. Prepare private configuration and test identities

Create a private working directory outside source control. Restrict file access;
the generated LDIF seed and Authelia configuration contain passwords/client secrets.
Do not put rendered Secrets, session cookies or operator keys in reports.

```bash
umask 077
mkdir -p "$HOME/keep-load-private"
PRIVATE="$HOME/keep-load-private"
cp deploy/openshift/load-test/configuration.example.json "$PRIVATE/config.json"
python qualification/load-test-identity/prepare.py \
  --app-origin "https://$APP_HOST" --authelia-origin "https://$AUTHELIA_HOST" \
  --users 100 --output "$PRIVATE/identity"
```

Fill the bundle config with namespace, release ID, storage class, registry secret,
DNS hosts and all image digests. Initially leave `global_tenant_id` null and use
bootstrap rendering. Global is created by migrations; read its actual UUID later.
Never generate a replacement UUID or create a second Global tenant.

The identity generator creates one `load-admin` and 100 separate load users with
random passwords and immutable UUIDs. Only the admin belongs to the global admin
group. Global's normal baseline still applies; grant users the appropriate role
in the specific test tenant through Keep administration. The confidential web
client uses code flow + PKCE, signed OIDC tokens and normal secure sessions. No
password grant or preinserted session is used.

Mount the generated `seed.ldif` into OpenLDAP once (as the `keep-openldap-seed`
Secret). OpenLDAP loads it into its data volume on first start and generates an
immutable `entryUUID` per entry; it does not re-seed over an existing tree. Preserve
these generated credentials with the volume; generating them again is a new identity
set, not a password reset. The generator refuses an existing output directory.

OpenLDAP serves LDAPS; Authelia authenticates against it and emits the raw `entryUUID`
verbatim in the `keep-directory` scope. OpenLDAP's server certificate must cover its
in-cluster DNS name (`keep-openldap`). After the seed loads, resolve the admin group's
`entryUUID` and replace the `RESOLVE_AT_DEPLOY_...` placeholder for
`KEEP_SUPERADMIN_GROUP` in `api-identity.env`. The API uses the real external Authelia
issuer, the `ldap-local` source, the raw `entryuuid` claim as the immutable ID, and the
normal authorization guards; `KEEP_OIDC_REQUIRED_ACR` is omitted (optional for Authelia)
while `amr=pwd` stays required. The `identity-publisher` pod runs Keep's production LDAP
directory worker over the same OpenLDAP; there is no development-only Keycloak adapter.
[Authelia's documentation](https://www.authelia.com/configuration/) describes its LDAP
backend, OIDC provider and claims policies.

Create the versioned Secrets listed in the bundle README. Merge the generated
`api-identity.env` into the API environment and `publisher-identity.env` into the
publisher environment; do not replace their database/epoch/wrapping settings.
Mount `directory.json` and the OpenLDAP CA in the identity bindings Secret. Back-channel
logout is no longer required; RP-initiated `end_session` logout is retained.

## 6. Application configuration by workload

Use one deployment epoch and one wrapping-key version for this isolated database.
Generate a random 32-byte base64 wrapping key using a secret manager. Retain it
with the database for the lifetime of the test. Supply exactly one of
`KEEP_WRAPPING_KEY` and a mounted `KEEP_WRAPPING_KEYRING`. Do not give those
keys, DB credentials or OIDC secrets to ingestion.

For the first diagnostic run, set `KEEP_ENVIRONMENT=development` explicitly.
Use TLS, certificate validation and network isolation throughout. This permits
the honest unqualified development capacity profile; it does **not** qualify the
deployment for staging/production. An unqualified profile is rejected by API
outside development. Do not set `qualified=true` just to start a pod.
Correlation and maintenance budgets are optional (REV-39): an unset budget is
unlimited, and those features no longer need `qualified` to run.

| Workload | Environment and mounted files |
| --- | --- |
| All Keep processes | `KEEP_ENVIRONMENT`, `KEEP_DEPLOYMENT_EPOCH`; template role/listener/clock settings; relevant CA files |
| API | Runtime `KEEP_DATABASE_URL`, wrapping key/keyring, `KEEP_POOL_SIZE=2`, `KEEP_CAPACITY_PROFILE=/etc/keep/capacity.json`; all generated OIDC values; Kafka, ES and Redis below |
| Public ingestion | Kafka settings below, `KEEP_KAFKA_ADMISSION_ENABLED=false` initially; template supplies `KEEP_GLOBAL_TENANT_ID` and role `ingestion-api` |
| Private ingestion | Same Kafka settings; role `private-ingestion`; all four `KEEP_GATEWAY_*` TLS/peer settings from the bundle/readiness instructions |
| Processor | Worker `KEEP_DATABASE_URL`, wrapping key/keyring, pool 2, capacity profile, Kafka settings; template supplies Global UUID |
| Indexer | Worker DB, wrapping key/keyring, pool 2, capacity profile, Kafka and ES write bindings |
| Search bootstrap Job | Indexer environment/bindings plus `search-binding.json`, explicit physical index/epoch/reference and current clock evidence; initial development activation only |
| Operator proof | Dedicated `KEEP_PROOF_DATABASE_URL`, shared deployment epoch, fixed member config, identical operator binding, distinct API/gateway probe tokens and server TLS; see cluster initialization |
| Correlation | Worker DB, wrapping key/keyring, pool 2, Kafka; profile supporting correlation with current evidence and existing capability/recovery fences |
| Migration Job | Dedicated migration DSN only, as required by the migration template; never API or worker login |
| Identity tool | Dedicated identity login granted normal worker publication privileges, wrapping key/keyring, epoch, development environment, generated publisher secret and directory JSON |
| Frontend | Template port/host; no DB, Kafka, Redis or secret OIDC client credential |

For example, prepare private `processor.env` with these fields, replacing every
placeholder. The password must be URI encoded, while the mounted CA path is a
literal container path. Copy only applicable fields to each other role:

```dotenv
KEEP_ENVIRONMENT=development
KEEP_DEPLOYMENT_EPOCH=THE_SHARED_TEST_DEPLOYMENT_EPOCH
KEEP_DATABASE_URL=postgresql://keep_processor_login:ENCODED_PASSWORD@keep-postgres:5432/keep?sslmode=verify-full&sslrootcert=/etc/keep/postgres-ca.crt&connect_timeout=5
KEEP_WRAPPING_KEY=BASE64_OF_THE_SHARED_RANDOM_32_BYTE_KEY
KEEP_POOL_SIZE=2
KEEP_CAPACITY_PROFILE=/etc/keep/capacity.json
KEEP_KAFKA_BOOTSTRAP=SUPPLIED_BROKER_HOSTS_AND_PORTS
KEEP_KAFKA_SECURITY_PROTOCOL=SASL_SSL
KEEP_KAFKA_CA_FILE=/etc/keep/kafka-ca.crt
KEEP_KAFKA_SASL_MECHANISM=SCRAM-SHA-512
KEEP_KAFKA_SASL_USERNAME=SUPPLIED_WORKLOAD_USERNAME
KEEP_KAFKA_SASL_PASSWORD=SUPPLIED_WORKLOAD_PASSWORD
```

The SASL mechanism above is an example, not a broker requirement. For mTLS instead,
use `SSL`, omit all SASL fields and supply `KEEP_KAFKA_CLIENT_CERT` and
`KEEP_KAFKA_CLIENT_KEY`. Mount corresponding CA/client files. API, indexer and
correlation use their own login/password and permitted Kafka identity. API adds
the generated identity fragment and its ES/Redis/operator settings. Indexer adds
ES. Identity publication does not need Kafka, ES or Redis. Its `identity.env`
contains development environment, shared epoch/wrapping key, its dedicated DB URL
and the generated publisher secret.

Public ingestion's environment contains only environment, deployment epoch,
Kafka fields and `KEEP_KAFKA_ADMISSION_ENABLED=false` initially. Global UUID and
role come from template parameters. Private ingestion adds the actual paths and
approved Kong leaf certificate fingerprint:

```dotenv
KEEP_GATEWAY_CERT_FILE=/etc/keep/gateway-server.crt
KEEP_GATEWAY_KEY_FILE=/etc/keep/gateway-server.key
KEEP_GATEWAY_CA_FILE=/etc/keep/kong-client-ca.crt
KEEP_GATEWAY_PEER_SHA256=APPROVED_KONG_CLIENT_CERTIFICATE_SHA256_HEX
```

Its own server certificate must match the internal name Kong uses. Mount only
these private-ingestion TLS files and its Kafka trust/auth files. Migration's
`migration.env` uses `KEEP_MIGRATION_URL=postgresql://keep_migrator:ENCODED_PASSWORD@keep-postgres:5432/keep?sslmode=verify-full&sslrootcert=/etc/keep/postgres-ca.crt`.
The canonical migration template mounts its binding Secret; ordinary application
images never run migrations on startup.

For the initial unqualified API/processor/indexer profile, copy
`qualification/development-capacity.json` into their bindings as `capacity.json`.
It is a bounded diagnostic allocation, not proof for 250/s or a correlation/
maintenance profile. Preserve gates and record unavailable features in results.

Kafka environment: `KEEP_KAFKA_BOOTSTRAP`,
`KEEP_KAFKA_SECURITY_PROTOCOL=SSL` or `SASL_SSL`,
`KEEP_KAFKA_CA_FILE=/etc/keep/kafka-ca.crt`, and the supplied client cert/key
pair or SASL mechanism/username/password. Do not disable TLS verification.
Processor ACLs must allow transactional publication and offset commits; changed
consumers use `read_committed`. The provider must permit the exact transaction
IDs and groups used by the checked source. Do not casually increase partition
counts after initialization: partition stability is part of ordering.

ES environment: `KEEP_ELASTICSEARCH_URL`, a unique
`KEEP_ELASTICSEARCH_INDEX`, `KEEP_ELASTICSEARCH_CA_FILE`, plus
`KEEP_ELASTICSEARCH_API_KEY` or the certificate/key pair. Use the same index
binding in API and indexer. Follow [cluster initialization](cluster-initialization.md)
to prepare the explicit `RebuildBinding` and run `36-search-bootstrap.json` with
writers stopped. It performs live capability checks and applies the checked
mapping before initial activation. Reserve the alias/index/probe namespace with
the ES owner and retain its actual allocation/exclusive-writer evidence.
The existing `rebuild_cli bind` only records an existing mapping/alias; it does
not replace this activation procedure or reopen restore gates.

Redis: API and participating publishers use a TLS URL with ACL credentials,
including the mounted CA option, for example
`rediss://USER:PERCENT_ENCODED_PASSWORD@keep-redis:6379/0?ssl_ca_certs=/etc/keep/redis-ca.crt`.
Use the bundle's exact port/service name. Select no persistent business-state
storage here: Redis is hint transport. Verify restart/reconciliation behavior.

Operator provisioning on API additionally needs
`KEEP_OPERATOR_DATABASE_URL`, `KEEP_OPERATOR_BINDING`,
`KEEP_OPERATOR_GRAFANA_TOKEN`, `KEEP_OPERATOR_KONG_TOKEN` and
`KEEP_OPERATOR_PROBE_TOKEN`. The binding must describe the actual management
interfaces, TLS and safe-writer/probe contract, not a workstation file. Read the
[private integration checklist](private-qualification.md) together with current
worker scope: the **API performs synchronous provisioning**, not an operator
worker. One shared Grafana routes on label `Keep - {operator name}` with literal
string `true`. Never route by a user-supplied tenant header.

Clock templates mount `health.json` and `pg-health.json` from
`keep-clock-health` at `/run/keep-clock`. Evidence includes actual synchronized
status, measured offset and `measured_at`; the runtime rejects stale evidence
over 60 seconds and offsets over one second. Arrange a real provider that measures
every eligible workload node and PostgreSQL. A single node's reading cannot
represent other nodes. Account for ConfigMap propagation delay, or adapt mounts
to node-local continuously refreshed evidence. **Do not periodically write the
current timestamp with a made-up zero offset.** This infrastructure binding must
be resolved before pipeline readiness. See the Kafka runbook's clock contract.

## 7. Deployment and startup sequence

Follow the exact render/apply filenames in the bundle README. The sequence is:

1. Check `oc whoami`, context and namespace. Install pull credentials and private
   Secrets. Render bootstrap-only assets and inspect every object. Perform
   `oc apply --dry-run=server` against the installed OpenShift API. Restricted SCC,
   arbitrary UID, writable mounts, storage class and network rules must admit.
2. Apply foundation and the three PostgreSQL instances plus Redis. Wait for ready
   state and prove TLS/auth from an allowed pod. Separate PG instances avoid
   accidentally coupling Keep and vendor database versions/credentials.
3. Run the dedicated Keep migration Job and require successful completion.
   Provision the API and worker/publication login roles using the bundle SQL
   procedure. They must not be superusers, table owners, or `BYPASSRLS` identities.
   Do not start application writers before migrations succeed.
4. Read the actual Global UUID, put it in private configuration and rerender full
   assets. Verify it is the fixed Global created in this database. Preserve the
   migration checksum/revision evidence.
5. Run Kong's plugin/database migration Job **before** starting Kong. Start
   OpenLDAP (seeded) and Authelia against it, and Kong with its configured PostgreSQL/TLS/plugin.
   Follow the bundle's `31-kong-routes.json` initialization procedure to create
   only the fresh gateway's routes, verified upstream and plugins. This does not
   create an operator or satisfy the separate provisioning proof contract.
   The Kong Admin API stays private. Private ingestion must admit only its approved
   Kong client certificate fingerprint. Never expose private ingestion directly.
6. Run one explicit trusted identity publication:

   ```bash
   oc -n "$NS" scale deploy/keep-directory-publication --replicas=1
   oc -n "$NS" rollout status deploy/keep-directory-publication --timeout=5m
   oc -n "$NS" exec deploy/keep-directory-publication -c identity-publisher -- \
     python -m keep.identity.worker \
     --config /etc/keep/directory.json --once --bootstrap
   ```

   This runs Keep's production LDAP directory worker against OpenLDAP (the same
   directory Authelia authenticates against), not a Keycloak adapter.
   Require `complete`, `accepted` and bootstrap readiness in its nonsecret result.
   The initial bootstrap flag cannot lift restore/reconciliation gates. After
   changing users/groups, rerun with `--once` and without `--bootstrap`. There is
   no polling daemon. Check disabled and recreated users do not inherit access.
7. With **all ingestion and consumers still stopped**, apply the rendered
   `35-kafka-cutover.json` one-off Job. It executes the processor image's
   `python -m keep.kafka_cutover --ingress-and-legacy-workers-stopped`
   with the processor DB/Kafka/epoch bindings. Record all three
   initialized group boundaries. Retry a partial failure only while still fenced.
   Never reset consumer offsets after admitting test traffic.
8. Supply genuine clock evidence. Run `36-search-bootstrap.json` while all writers
   remain stopped and require success. Configure and start `32-operator-proof.json`
   with the observed direct Kong member binding before provisioning. Start API, frontend,
   processor and indexer for their supported paths.
   Verify normal OIDC login and an authorized UI action. Examine application
   capability diagnostics, not only HTTP liveness.
9. Enable `KEEP_KAFKA_ADMISSION_ENABLED=true` in both ingestion Secrets, then
   start public/private ingestion. Provision a test operator via the normal API/UI
   before using its v2 key. Verify v1 reaches Global and v2 reaches the owning
   test tenant. Validate the Kafka acknowledgement, canonical state and ES row
   for a single synthetic key before seeding a dataset.
10. Start correlation with the other workers. Correlation and maintenance are
    always on (REV-39); there is no gate or activation command, and an unset
    budget is unlimited. Configure measured budgets in the profile when a run
    needs limits. Without measured capacity evidence the run cannot claim
    correlation or maintenance capacity; do not fabricate a profile. Baselines
    recorded before REV-39 ran with correlation and maintenance off and do not
    describe the always-on default.

The bundle creates application Deployments stopped; use its documented deployment
names with `oc scale --replicas=1` in that order. Wait for each rollout and inspect
its logs before proceeding. Use one-off Jobs for protocol helpers rather than
starting a competing worker in an already-running Deployment.

For example, after prerequisites and corresponding manifests are applied:

```bash
NS=YOUR_TEST_NAMESPACE
oc -n "$NS" scale deploy/keep-authelia --replicas=1
oc -n "$NS" rollout status deploy/keep-authelia --timeout=10m
# Start the direct member before Kong route initialization.
oc -n "$NS" scale statefulset/keep-kong --replicas=1
oc -n "$NS" rollout status statefulset/keep-kong --timeout=10m
# Publish identities; complete Kong initialization, Kafka cutover and search bootstrap.
# Apply/configure the proof helper and record the actual member UUID first.
oc -n "$NS" scale deploy/keep-operator-proof --replicas=1
oc -n "$NS" scale deploy/keep-product-api deploy/keep-frontend \
  deploy/keep-processor deploy/keep-indexer --replicas=1
oc -n "$NS" rollout status deploy/keep-product-api --timeout=5m
oc -n "$NS" rollout status deploy/keep-processor --timeout=5m
oc -n "$NS" rollout status deploy/keep-indexer --timeout=5m
# After admission configuration and gateway prerequisites:
oc -n "$NS" scale deploy/keep-ingestion-api \
  deploy/keep-private-ingestion --replicas=1
oc -n "$NS" get pods,jobs,pvc
```

## 8. Establish observability before applying load

Capture timestamped measurements at a stable interval throughout warmup, run and
drain. Store run ID, config, image digests, requested and actual replicas/resources,
source revisions/patches, schema version, profile hash and external versions.

| Layer | What to measure / implication |
| --- | --- |
| Generator | Attempted/sent/accepted/rejected/uncertain members, batches/s, p95/p99, active VUs, dropped iterations, generator CPU/network; dropped work invalidates the claimed offered rate |
| Router/Kong/ingestion | Response codes, connection/TLS costs, per-route latency, Kafka publish latency, resource throttling; 202 means Kafka acknowledgement only |
| Kafka | Received/changed log ends, group lag by partition, oldest unprocessed age, errors/rebalances/transaction aborts, retention headroom |
| Processor | Applied deliveries/s, replay/duplicate outcomes, durable errors, PG commit time and locks; sustained lag growth means processing below offered load |
| PostgreSQL | CPU, RSS, connections/pool waits, lock waits/deadlocks, slow queries, IOPS/WAL/storage growth, transaction latency; use provider-enabled query statistics where available |
| Indexer / ES | Projection backlog/age, bulk errors/retries, searchable watermark, query p99, refresh/merge/heap/CPU/IO/disk |
| API / Redis | Query/command p99, pool wait, SSE connections/reconnects, hint backlog, Redis memory/connections/latency and reconciliation outcomes |
| Correlation | Canonical-commit-to-evaluation latency, changed lag, failures, incident linking correctness, qualified rule count |
| Real UI | Receipt-to-correct-render latency, query identity/revision, tenant, foreground state, missing/error/late acknowledgements |

`oc adm top pods` and pod events are useful but not a complete time series. Keep
raw generator output and durable stage evidence. Redact tokens, cookies, keys and
personal information. Use synthetic alerts only.

Proposed diagnostic stop conditions: any data/tenant correctness failure,
unexplained accepted-member loss, OOM/restart loops, disk below the agreed safety
margin, or lag growing without a bounded drain path. Also stop if the generator
cannot sustain its schedule. Intentional overload has separate expected errors;
do not omit them from the report.

## 9. Run the tests

Use the [toolkit README](../../qualification/load-test/README.md) for executable
commands, config variables, normal-session capture and container/Job mounting.
The toolkit keeps load-generator image/config/secrets separate from application
images. Run it inside the private network, preferably on different resources from
the application; do not involve this laptop across a network it cannot reach.

Create a fresh `RUN_ID` for each independent run. Seed 5,000 unresolved synthetic
keys using `seed.js`. Wait until they are committed and searchable before setting
the explicit fixture-ready flag and running `ingestion.js` with the **same** run
ID. The primary bounded lifecycle alternates new rows and valid resolutions of
older owned keys; update-heavy profiles include firing updates too. Inspect the
actual new/update/resolution counters. Bounded unresolved count does not bound
history/storage growth. No automatic retries turn uncertain outcomes into success.

| Phase | Input | Duration | Purpose |
| --- | --- | --- | --- |
| Smoke | One v1 member, one v2 member; then 10/s | 5 min | Routing, schema, identity, pipeline correctness |
| Staircase | 25, 50, 100, 250 members/s in separate profiles | 5–10 min each | Locate onset of queueing/throttling; wait for drain between independent runs |
| Normal-rate reference | About 66.7/s (e.g. 667 per 10 seconds) | 15–30 min | Compare expected operating load with capacity margin |
| Mixed primary | 250/s, 5,000 unresolved, 50/50 new/update lifecycle; 50 authenticated users | 30–60 min pilot | Establish storage slope and representative combined pressure |
| Sustained | Same primary workload | 24 h | Stability and latency under continued retained history growth; only after pilot sizing |
| Update heavy | 20/80 new/update | At least 4 h for qualification | Repeated-key writes/history and projection churn |
| New heavy / larger set | 80/20, 10,000 unresolved stress starting point | At least 4 h for qualification | Growth pressure; this profile cannot maintain a fixed set with too few resolutions |
| Overload | 1,000/s | 60 sec | Explicit rejections/uncertainty, no false 202, bounded recovery |
| Recovery | Continue 250/s while draining a controlled backlog | Until fully drained | Demonstrate processing at least 500/s total, not 500 new inputs/s |

Use an arrival-rate generator for ingestion so a slow server does not silently
reduce offered load; track dropped iterations. Virtual users alone are not an
alerts/s measurement. With batch size 10, 250 members/s is 25 HTTP requests/s.
The toolkit uses exact arrival windows for fractional request rates, including
250 members/s in 100-member batches and the 66.7/s reference profile. Inspect the
achieved counters. [k6's load-model documentation](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/)
explains why this matters.

Repeat the primary profile for v1 concentrated in Global and v2 concentrated in
one tenant, then distributed tenants. Exercise batch sizes 1/10/50/100, 1/4/16KiB
members, the largest valid member up to 64KiB and the 4MiB envelope bound. Measure
the actual encoded member/envelope size: a message-character count is not total
JSON size. Validate mixed 207 outcomes individually. Malformed-member tests are
separate correctness cases; don't label their expected 4xx as normal capacity.

The primary toolkit profile uses a 3,500-character message and small JSON
whitespace padding for an exact 4KiB raw member. Its byte metrics distinguish
compact decoded content from wire bytes. The supplied 16/64KiB padding profiles
measure transport size; they do not simulate equally large stored content or
maximum-complexity Unicode fields. The native schema's 8,192-character message
limit remains enforced. Use the matching named profiles and inspect both byte
metrics when interpreting database/ES pressure; 100 members of 64KiB would exceed
the 4MiB envelope limit and are refused before sending.

Run `reads.js` concurrently with distinct users' sessions obtained through normal
OIDC sign-in. Begin at 5 users, then 50 and 100. Use saved alert views, an actual
dashboard with 10 then 24 panels, including shared/repeated presets. The script
uses current definition revisions and CSRF/session handling. A raw API read test
does not measure browser rendering, SSE delivery or interactive commands. The
toolkit limits session chunks to their valid lifetime; obtain fresh normal logins
for subsequent chunks rather than changing session expiry or reusing expired
cookies over a 24-hour soak.

For full product qualification, add actual browser users with one then three
tabs, alert search/detail, assignment/unassignment and resolve/reopen commands, view changes,
dashboard invalidation and foreground/background transitions. Use the checked
frontend browser suite as correctness coverage, and instrument this workload for
continuous rendered-version acknowledgements. Keep all eligible observations;
timeouts and missing acknowledgements are failures. Sampling a few browsers is
useful diagnosis, **not** 100% visibility evidence. This handoff's k6 scripts do
not implement the complete browser SLI collector.

Correlation adds 50 then 100 representative rules and maintenance adds 20 windows
only after those capabilities are properly qualified. Do not count time-based
maintenance release or recurring cleanup as implemented features; elapsed time
alone does not release suppression in the current scope.

## 10. Judge results and run recovery checks

Track end-to-end stage timestamps and correctness, not just HTTP averages:

| Metric | Target to evaluate |
| --- | --- |
| Admission p99 | <=1 second, successful members Kafka-acknowledged |
| Ordinary authoritative command/detail p99 | <=1 second |
| Search/count p99 | <=2 seconds |
| Canonical commit to correlation pass p99 | <=2 seconds, for enabled/qualified correlation |
| Incoming receipt to correct foreground render p99 | <=5 seconds, with complete eligible observations |
| Sustained input | 250 deliveries/s, including one-tenant concentration, without unbounded lag |
| Backlog recovery | >=500 processed/s while 250 new/s continues, subject to qualified allocation |

These are evaluation criteria from the
[reliability specification](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-reliability-operations.md)
and [verification plan](https://github.com/keep-alert-operations/keep-design/blob/main/verification/verification-plan.md).
A short run does not establish 99.9% availability over a rolling 30 days. Report
startup/warmup separately; never erase later degraded periods. Report percentile
sample counts, all errors and the actual offered/accepted/processed rates.

For recovery, first use a small, timed interruption of one test worker, leaving
ingestion and retained Kafka input intact. Restore it and compare offsets,
canonical alert/history/proof state and search convergence. Expand only after the
small case passes and storage/retention permit it. A two-hour backlog at 250/s is
1.8 million deliveries; at 500 processed/s with 250/s continuing, net drain is
250/s and takes about two hours. Increasing new input to 500/s is not this test.

Separately restart Redis and confirm foreground state converges through PostgreSQL
reconciliation; interrupt indexer and verify reprocessing/reconciliation; exercise
approved duplicate/replay and per-key order cases; verify revoked users cannot
retain access and test data cannot cross tenants. Never reset offsets or manually
delete proof to clear a failure. Record any retention gap as an explicit loss
interval and follow the documented recovery gates.

Compare a baseline with exactly one proposed optimization at a time, with the
same images except that change, data distribution, resources and test duration.
If PG repeated reads dominate, examine query/index/pool behavior. If ES dominates,
examine query cost, projection refresh and allocation. If writes or Kafka lag
dominate, a Redis read cache will not fix that path. A cache proposal must then
specify tenant/access isolation, invalidation, query/definition/index revision
identity and stale-read limits; it is a separate design change.

## 11. Troubleshooting and handoff evidence

| Symptom | Check first |
| --- | --- |
| ImagePullBackOff | Destination digest, registry trust and namespace pull Secret; public Internet is not required |
| Pending / permission denied | Quota, PVC/class, restricted SCC, assigned UID/fsGroup and writable mount permissions; do not solve by granting privileged SCC |
| API startup profile error | Environment and actual profile; preserve `qualified=false` for diagnostic development |
| Login succeeds at Authelia but API rejects it | External issuer/redirects/CA, immutable `sub`/`sid`/`entryuuid` claim, `keep-directory` scope, `amr=pwd`, explicit accepted directory publication and admin group `entryUUID` |
| Worker alive but not ready | Real fresh node/PG clock evidence, Kafka advertised addresses/ACLs, DB permissions, gates, cutover/group boundaries |
| 202 but row not visible | Received lag -> PG commit/proof -> changed lag -> indexer/watermark -> query/render; 202 alone is not completion |
| v2 fails / 403 or 503 | Operator/key state, Kong plugin loaded/migrated, upstream mTLS CA/fingerprint, actual immutable tenant routing |
| k6 drops iterations | Generator resources/VUs/network and server response time; lower rate for diagnosis or increase generator capacity without hiding drops |
| Growing p99 but low app CPU | PG locks/pool wait, ES IO/queueing, Kafka partition ordering, network/TLS or shared-service contention |

Save a sanitized result directory per run: config/profile, revision and digest
inventory, start/end times, clock evidence, workload/fixture totals, raw k6 output,
resource/lag/query time series, browser evidence where collected, correctness and
drain results, and a conclusion listing measured limits and untested features.
Export before deleting Jobs/pods. Stop generators first, let accepted input drain,
capture final counts/offsets, then scale down test applications. Decommission only
the dedicated namespace/storage and explicitly reserved external test resources
after preserving evidence; never use tenant deletion as cleanup (unsupported).

This package is preparation for execution in your network. Its local validation
record is [OpenShift load-test handoff evidence](../../qualification/openshift-load-test-handoff.md).
Server-side admission, private TLS/DNS, actual external-service compatibility,
native-user bootstrap and sustained load remain checks to execute there.
