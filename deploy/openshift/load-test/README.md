# OpenShift diagnostic load-test bundle

Follow the [complete runbook](../../../docs/runbooks/openshift-load-testing.md).
This directory supplies rendering and infrastructure assets, not a qualified
release. External Elasticsearch, Kafka and Grafana remain supplied services.
Use a dedicated disposable namespace/database/topic allocation with synthetic data.

## Render interface

Python 3 uses only its standard library. Run from the delivery checkout on the
private-network administration host. The generated shell script uses `oc process
--local` against the existing canonical templates in `deploy/openshift`; do not
copy or maintain a second application manifest authority.

```bash
umask 077
mkdir -p "$HOME/keep-load-test-private"
cp deploy/openshift/load-test/configuration.example.json \
  "$HOME/keep-load-test-private/configuration.json"
# Fill every REQUIRED value; initially leave global_tenant_id as null.
python3 deploy/openshift/load-test/render.py \
  --config "$HOME/keep-load-test-private/configuration.json" \
  --output "$HOME/keep-load-test-private/rendered" --bootstrap-only
bash "$HOME/keep-load-test-private/rendered/render-apps.sh"
```

The renderer never connects to the cluster and never writes real secrets. Supply
all image digests from the private registry; versions, DNS, storage class and
router namespace selector must come from your actual environment. Each image
reference is immutable, including PostgreSQL, Redis, Authelia, OpenLDAP and Kong.

`--bootstrap-only` emits the database migration but no running application
workloads. After applying migrations, read `SELECT id FROM tenants WHERE
kind='global'`, place that UUID in the private configuration and rerun both
commands without `--bootstrap-only`. Do not invent the Global UUID.

| Output | Meaning and order |
|---|---|
| `00-foundation.json` | Service account and per-service ingress NetworkPolicies. Apply after creating namespace, platform authorization and private pull secret. |
| `10-data-services.json` | Two PostgreSQL StatefulSets/PVCs, the seeded OpenLDAP StatefulSet/PVC and ephemeral Redis. Required Secrets must already exist. |
| `20-keep-migration.json` | Canonical dedicated migration Job; run and wait for success before any Keep writer. |
| `21-kong-bootstrap.json` | Fresh Kong database/plugin bootstrap only. Do not run against an existing Kong database; use its documented upgrade procedure. |
| `30-identity-kong.json` | Authelia, the explicit directory-publication pod (production LDAP worker) and Kong. All render at zero replicas. Scale after prerequisites. |
| `31-kong-routes.json` | Explicit one-off fresh-Kong route/plugin initialization. Run only after Kong is healthy and actual clock publication works; does not establish operator qualification. |
| `32-operator-proof.json` | Private API-image proof helper and Service; initially zero replicas. Configure exact direct Kong members and shared operator binding before starting. |
| `35-kafka-cutover.json` | One-off cutover Job derived from the processor pod template. Requires stopped ingestion/legacy/new workers and verified external Kafka inputs; run before activating pipeline traffic. |
| `36-search-bootstrap.json` | Explicit initial-development search activation using the indexer image, live ES probes and current clock evidence; keep writers stopped until success. |
| `40-*.json` | Canonical application workloads; all initially zero replicas. |
| `50-routes.json` | Shared application origin for `/`, `/api`, `/auth`; separate v1, v2 and Authelia hosts. Apply after TLS/DNS checks. |
| `allocation.json` | Starting requests/limits and storage; no qualification claim. |

Use `oc -n "$NAMESPACE" apply --dry-run=server -f FILE` on **each stage** before
its actual apply. There is deliberately no `oc apply -f rendered/`: parameter
files, allocation metadata and dormant stages are not an all-at-once rollout.
All `oc apply`, `get`, `wait`, `logs`, `exec` and `scale` commands need the test
namespace. Re-rendering and reapplying applications resets replicas to zero;
only do that during a coordinated stopped deployment.

The Kafka cutover Job runs `python -m keep.kafka_cutover
--ingress-and-legacy-workers-stopped`, a declaration you must actually satisfy.
The Job does not stop anything itself. Follow the main runbook's offset/topic
checks, then `oc -n "$NAMESPACE" apply -f "$PRIVATE/rendered/35-kafka-cutover.json"`
and wait for `job/keep-kafka-cutover-RELEASE_ID` to complete. Preserve logs and
results. Backoff is zero and the Job has a ten-minute deadline; investigate a
failure before an explicit retry with a new reviewed Job name.

## Fresh Kong route configuration and the remaining v2 prerequisite

The Kong database migration installs the schema. It does not create services,
Routes, plugins, consumers or a qualified Keep operator. Initialize the dedicated
test gateway after it is healthy, before sending v2 traffic:

```bash
cp deploy/openshift/load-test/kong-bootstrap.example.json \
  "$PRIVATE/kong-bootstrap.json"
# Review the actual Service DNS, CA filenames and upstream endpoint in that file.
# Privately generate $PRIVATE/kong-probe-token (32+ URL-safe random characters).
oc -n "$NAMESPACE" create configmap keep-kong-bootstrap-code \
  --from-file=bootstrap_kong.py=deploy/openshift/load-test/bootstrap_kong.py
oc -n "$NAMESPACE" create secret generic keep-kong-bootstrap-bindings \
  --from-file="configuration.json=$PRIVATE/kong-bootstrap.json" \
  --from-file="kong-admin-ca.crt=$PRIVATE/kong-tls/ca-bundle.crt" \
  --from-file="private-ingestion-ca.crt=$PRIVATE/private-ingestion/server-ca.crt" \
  --from-file="probe-token=$PRIVATE/kong-probe-token"
oc -n "$NAMESPACE" apply --dry-run=server -f "$PRIVATE/rendered/31-kong-routes.json"
oc -n "$NAMESPACE" apply -f "$PRIVATE/rendered/31-kong-routes.json"
oc -n "$NAMESPACE" wait --for=condition=complete --timeout=600s \
  "job/keep-kong-routes-$RELEASE_ID"
oc -n "$NAMESPACE" logs "job/keep-kong-routes-$RELEASE_ID"
```

The actual private-ingestion **server** CA must be supplied above (it may differ
from the CA that issued Kong's **client** certificate). `KONG_CLIENT_SSL` supplies
that client certificate. The Job has access to internal Kong Admin under the
`keep-bootstrap` label; its configured admin hostname must match the certificate.
If your selected Kong distribution requires an Admin token, add it as a private
file and set `admin_token_file` to its mounted path; the script never prints it.
Do not expose Kong Admin through a Route for this operation.

The initializer verifies Kong is fresh and then installs:

| Route path | Configuration |
|---|---|
| `/api/ingest/v2/` | Native `X-API-Key` only; query/body keys disabled and trusted identity headers scrubbed. |
| `/api/ingest/v2/grafana` | Exact single Bearer credential normalized for Key Auth; rejects simultaneous X-API-Key. |
| `/__keep/operator-check` | Token-protected raw policy proof, without alert admission. |
| `/__keep/operator-auth-check` | Token-protected real Key Auth admission/denial proof, without alert admission. |

All routes use the installed Keep policy plugin. Its clock path is the real
`/run/keep-clock/health.json` ConfigMap mount and `unqualified_local_clock` is
**false**. The plugin cannot admit active credentials with missing/stale clock
evidence. The bootstrap script refuses any preexisting gateway configuration;
on partial failure inspect exact resources and repair deliberately. It does not
automatically delete resources, retry uncertain writes, create policies/keys,
change Grafana's shared routing tree, fabricate capabilities, or open SQL gates.

The API-image `keep.operators.proof` helper supplies the private
`OperatorBinding.probe_url` endpoint. It checks current operation/policy fences
before and after direct per-member checks and returns complete nonce-bound proof
without admitting an alert. Deploy `32-operator-proof.json` using
[cluster initialization](../../../docs/runbooks/cluster-initialization.md).
Do not point `probe_url` at a raw Kong endpoint or copy workstation evidence IDs.

Kong now renders as a singleton StatefulSet with a headless member Service. Use
`statefulset/keep-kong` for scale/rollout and direct
`keep-kong-0.keep-kong-members` DNS for member probes; include that name in
its TLS certificate. Node replacement requires re-observing its UUID and updating
the qualified bindings. Keep replicas at one until the whole pool is requalified.
This changes the earlier draft bundle's Kong workload kind: on an already-applied
draft, stop and remove only the old Kong Deployment before applying this
StatefulSet. Preserve the separate Kong database and do not rerun fresh bootstrap.

Before provisioning, prepare an actual
`grafana-provisioning-v1-kong-keep-policy-v1` binding with the supplied Grafana
organization/version, actual Kong version/member inventory, HTTPS management,
probe and webhook URLs, CA file, controlled-writer agreement, reserved namespace,
complete rule inventory and wrapper/serving-pool verification evidence. The schema
and full required capability set are authoritative in
`keep-api/src/keep/operators/binding.py` and `remote.py`; the installed image can
validate your private JSON with `OperatorBinding.model_validate_json(...)`.
Mount it in Product API bindings and supply the separate management/probe tokens.
Use normal operator APIs to provision credentials only after those prerequisites;
never create synthetic policy rows/keys as an alternative load-test shortcut.
The existing company Grafana tree must be coordinated with its owner; this test
bootstrap does not assume it is empty or permit replacing its routing tree.

The generated script records absolute source paths for the private-network host;
regenerate it after transferring/moving the checkout. Application templates inherit
the pull secret from the generated ServiceAccount. The migration Job receives
`app=keep-migration` during rendering so the PostgreSQL ingress policy admits it.

After successful Keep migration, initialize the separate runtime logins once:

```bash
oc -n "$NAMESPACE" exec -i keep-postgres-0 -- \
  psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -U keep_migrator -d keep \
  < deploy/openshift/load-test/runtime-logins.sql
for role in keep_api_login keep_processor_login keep_indexer_login \
            keep_correlation_login keep_identity_login keep_operator_login \
            keep_proof_login keep_recovery_login; do
  oc -n "$NAMESPACE" exec -it keep-postgres-0 -- \
    psql -X -h /var/run/postgresql -U keep_migrator -d keep -c "\\password $role"
done
oc -n "$NAMESPACE" exec keep-postgres-0 -- \
  psql -X -At -h /var/run/postgresql -U keep_migrator -d keep \
  -c "SELECT id FROM tenants WHERE kind='global'"
```

The SQL deliberately fails if a login already exists; inspect an interrupted
bootstrap instead of dropping/recreating roles. Password prompts do not echo.
Use distinct generated passwords stored privately. Roles inherit the published
`keep_api`, `keep_worker` or `keep_recovery` group; they never get superuser,
DDL ownership, role creation or BYPASSRLS. The current worker group remains broad
cross-tenant authority by design. The API uses its API login for normal SQL and
`keep_operator_login` only through `KEEP_OPERATOR_DATABASE_URL`. Mount the
identity login only in the identity-tooling sidecar. Keep recovery credentials
out of ordinary application pods. There is no ingestion database login.

## Secret and trust inventory

Create with `oc create secret generic NAME --from-file=...` or `--from-env-file=...`
using private files, then manage changes through your approved secret procedure.
Never put passwords in shell arguments/history, rendered files, Git or reports.
Generated mounts use mode 0440; restricted SCC must supply the permitted filesystem
group so the arbitrary UID can read them and write PVCs/emptyDir volumes.

The following commands use the private-file layout; provide the actual files,
not placeholder certificates or examples. `PRIVATE` should be outside Git. Use
`--dry-run=client -o yaml | oc apply -f -` only if updating a previously reviewed
Secret intentionally; initial `create` commands fail safely on existing names.

```bash
PRIVATE="$HOME/keep-load-test-private"
for db in postgres kong-db; do
  oc -n "$NAMESPACE" create secret generic "keep-$db-credentials" \
    --from-file="password=$PRIVATE/$db/password"
  oc -n "$NAMESPACE" create secret generic "keep-$db-tls" \
    --from-file="tls.crt=$PRIVATE/$db/tls.crt" \
    --from-file="tls.key=$PRIVATE/$db/tls.key"
done
oc -n "$NAMESPACE" create secret generic keep-redis-config \
  --from-file=redis.conf=deploy/openshift/load-test/redis.conf \
  --from-file="users.acl=$PRIVATE/redis/users.acl"
oc -n "$NAMESPACE" create secret generic keep-redis-tls \
  --from-file="tls.crt=$PRIVATE/redis/tls.crt" \
  --from-file="tls.key=$PRIVATE/redis/tls.key" \
  --from-file="ca.crt=$PRIVATE/redis/ca.crt"
# OpenLDAP: seeded directory tree, admin credentials and its LDAPS certificate.
oc -n "$NAMESPACE" create secret generic keep-openldap-env \
  --from-env-file="$PRIVATE/openldap.env"
oc -n "$NAMESPACE" create secret generic keep-openldap-seed \
  --from-file="seed.ldif=$PRIVATE/identity/seed.ldif"
oc -n "$NAMESPACE" create secret generic keep-openldap-tls \
  --from-file="tls.crt=$PRIVATE/openldap/tls.crt" \
  --from-file="tls.key=$PRIVATE/openldap/tls.key" \
  --from-file="ca.crt=$PRIVATE/openldap/ca.crt"
# Authelia: config, the files it loads by reference, and its HTTPS certificate.
oc -n "$NAMESPACE" create secret generic keep-authelia-config \
  --from-file="configuration.yml=$PRIVATE/identity/configuration.yml"
oc -n "$NAMESPACE" create secret generic keep-authelia-env \
  --from-env-file="$PRIVATE/authelia.env"
oc -n "$NAMESPACE" create secret generic keep-authelia-secrets \
  --from-file="$PRIVATE/authelia-secrets/"
oc -n "$NAMESPACE" create secret generic keep-authelia-tls \
  --from-file="public.crt=$PRIVATE/authelia/tls.crt" \
  --from-file="private.key=$PRIVATE/authelia/tls.key"
oc -n "$NAMESPACE" create secret generic keep-identity-env \
  --from-env-file="$PRIVATE/identity.env"
oc -n "$NAMESPACE" create secret generic keep-identity-bindings \
  --from-file="directory.json=$PRIVATE/identity/directory.json" \
  --from-file="openldap-ca.crt=$PRIVATE/openldap/ca.crt" \
  --from-file="postgres-ca.crt=$PRIVATE/postgres/ca.crt"
oc -n "$NAMESPACE" create secret generic keep-kong-env \
  --from-env-file="$PRIVATE/kong.env"
oc -n "$NAMESPACE" create secret generic keep-kong-tls \
  --from-file="$PRIVATE/kong-tls/"
for role in api ingestion private-ingestion processor indexer correlation migration; do
  oc -n "$NAMESPACE" create secret generic "keep-$role-env" \
    --from-env-file="$PRIVATE/$role.env"
  oc -n "$NAMESPACE" create secret generic "keep-$role-bindings" \
    --from-file="$PRIVATE/$role-bindings/"
done
# Correlation is always on (REV-39); its capacity profile is optional.
```

Each `*-bindings` directory contains only the files intended for that workload;
do not point `--from-file` at a shared directory containing other roles' secrets.
The private registry pull Secret referenced in configuration is supplied through
the cluster's approved credential procedure; never use a password CLI flag.

| Secret | Required keys/content |
|---|---|
| `keep-postgres-credentials` | `password` for the dedicated `keep_migrator` bootstrap/migration login. |
| `keep-kong-db-credentials` | `password` for the isolated `kong` DB login. |
| `keep-postgres-tls`, `keep-kong-db-tls` | Each has `tls.crt`, `tls.key`; server SANs must include its Service DNS names used by clients. |
| `keep-redis-config` | `redis.conf` from this directory and private `users.acl`; example below. |
| `keep-redis-tls` | `tls.crt`, `tls.key`, `ca.crt`; server SAN includes `keep-redis` and full service DNS when used. |
| `keep-openldap-env` | `LDAP_ADMIN_USERNAME`/`LDAP_ADMIN_PASSWORD` for the seeded directory's bind account; must match the publisher's `KEEP_LOCAL_DIRECTORY_SECRET`. |
| `keep-openldap-seed` | `seed.ldif` from the dedicated test-identity preparation; loaded once into the OpenLDAP volume. |
| `keep-openldap-tls` | `tls.crt`, `tls.key`, `ca.crt` for the LDAPS listener; server SAN includes `keep-openldap`. |
| `keep-authelia-config` | `configuration.yml` from the identity preparation: LDAP backend, OIDC `keep-web` client and the `entryuuid` claims policy/scope. |
| `keep-authelia-env` | The `AUTHELIA_*_FILE` variables pointing at the mounted secret files (LDAP bind password, session/storage/HMAC/JWT secrets). |
| `keep-authelia-secrets` | The individual secret files referenced by `keep-authelia-env` (for example `ldap_password`, `session_secret`, `oidc_hmac_secret`, `jwt_secret`). |
| `keep-authelia-tls` | `public.crt`, `private.key`; SAN includes the public Authelia route hostname. |
| `keep-identity-env` | Dedicated identity publisher `KEEP_DATABASE_URL`, wrapping/epoch settings and `KEEP_LOCAL_DIRECTORY_SECRET` (the LDAP bind password); see main runbook. |
| `keep-identity-bindings` | `directory.json` (the sanitized LDAP schema), the OpenLDAP CA and PostgreSQL CA at referenced `/etc/keep/` paths. |
| `keep-kong-env` | `KONG_PG_PASSWORD` matching its isolated DB. Additional approved Kong settings as needed. |
| `keep-kong-tls` | `server.crt`, `server.key` for proxy/admin HTTPS, with SANs for the public v2 host, `keep-kong` and direct `keep-kong-0.keep-kong-members`; `kong.crt`, `kong.key` for private-ingestion client mTLS; `ca-bundle.crt` trusts the Kong DB and private-ingestion server. |
| `keep-api-env`, `keep-api-bindings` | Product API settings and referenced files; include CA files, development capacity profile, operator/recovery bindings when qualified. |
| `keep-ingestion-env`, `keep-ingestion-bindings` | Public ingestion settings and Kafka CA/client files only; never a Keep database credential. |
| `keep-private-ingestion-env`, `keep-private-ingestion-bindings` | Private ingestion settings, Kafka files, gateway server/client CA and probe credentials; never a Keep database credential. |
| `keep-processor-env`, `keep-processor-bindings` | Source worker settings and referenced TLS/profile files. |
| `keep-indexer-env`, `keep-indexer-bindings` | Projection worker settings and referenced TLS/profile files. |
| `keep-operator-proof-env` | `KEEP_PROOF_DATABASE_URL` using its dedicated read-only-default login and actual `KEEP_DEPLOYMENT_EPOCH`. |
| `keep-operator-proof-bindings` | `configuration.json`, identical `operator-binding.json`, `probe-token`, `gateway-probe-token`, PostgreSQL and Kong CA files; see cluster initialization. |
| `keep-operator-proof-tls` | `tls.crt`, `tls.key`; SAN includes `keep-operator-proof` and any full Service DNS name used. |
| `keep-correlation-env`, `keep-correlation-bindings` | Correlation worker settings and referenced TLS files. A capacity profile is optional: correlation and maintenance are always on (REV-39) and an unset budget is unlimited. |
| `keep-migration-env`, `keep-migration-bindings` | `KEEP_MIGRATION_URL` for dedicated login and its CA. No application starts its own schema migration. |

For Redis, generate a strong distinct password privately, hash its raw bytes using
SHA-256 without a trailing newline, and put the hex digest after `#` in users.acl:

```text
user default off
user keep on #REPLACE_WITH_PASSWORD_SHA256 &keep.development.tenant.* +ping +publish +subscribe +psubscribe +unsubscribe +punsubscribe +client|setinfo
```

Mount `redis-ca.crt` in API bindings. Use a URL-encoded password in
`KEEP_REDIS_URL=rediss://keep:PASSWORD@keep-redis:6379/0?ssl_cert_reqs=required&ssl_check_hostname=true&ssl_ca_certs=/etc/keep/redis-ca.crt`.
The listener probe is only TCP; separately verify authenticated, TLS-verified
publish/subscribe and PostgreSQL reconciliation before attributing live latency.

`keep-clock-health` is a **ConfigMap with actual continually refreshed**
`health.json` and `pg-health.json` measurements from the platform's clock evidence
publisher. This bundle does not generate healthy reports or a polling replacement.
All workloads using a common report must actually share the verified clock bound;
adapt to per-node evidence if the platform cannot establish that. Missing/stale
measurements keep affected operations unready. See the runbook for the schema and
measurement/refresh contract.

## Images, restrictions and storage

Build PostgreSQL from `deploy/images/postgres/Dockerfile`, choosing an approved
**Debian** official PostgreSQL base via `POSTGRES_BASE=...@sha256:...`.
The build verifies NSS wrapper exists. The wrapper uses the official initdb
entrypoint, a PVC child directory, read-only TLS input copied into a UID-owned
0600 private-key file, TLS-only network authentication and an `emptyDir` mounted at
the official image's `/var/run/postgresql` socket directory. It never
requests root, fixed runtime UID, privileged SCC or `anyuid`. The two PostgreSQL
instances can select different approved majors for Keep and Kong.
Choose versions supported by their actual clients and verify on the target cluster.
Do not change the PostgreSQL major against an existing data PVC without its upgrade
procedure. These standalone test databases are not an HA or backup design.

Build Redis from `deploy/images/redis/Dockerfile` with an approved
`REDIS_BASE=...@sha256:...`; it directly executes redis-server under the SCC UID.
It has TLS + ACL, no persistent data and no result cache. Kubernetes kills its
ephemeral data safely because PostgreSQL generation reconciliation repairs hints.

The PostgreSQL bootstrap account created by the upstream image is a superuser;
it is used only for the migration/bootstrap operations. After migration, create
separate restricted runtime logins as described in the runbook. Never mount its
password in the API/workers/ingestion. Kong owns its separate disposable test
database; OpenLDAP keeps its seeded tree in its own PVC and Authelia keeps only an
ephemeral SQLite session store. Tighten their bootstrap/runtime roles for a
production deployment using their supported procedures.

The defaults are assistant-selected starting requests/limits for diagnostics.
Override by service in `resource_overrides`, e.g.
`"api": ["1", "2", "1Gi", "2Gi"]` means CPU request/limit and memory request/limit.
All app replicas start at 0; initial test scale is usually 1. Increasing replicas
requires rechecking DB pool totals, Kafka partition ownership, serving-pool proof
and actual clock/profile applicability. The `keep-directory-publication` pod runs the
API image at 100m/128Mi requested and 500m/512Mi limit; it sleeps until an explicit
exec runs the LDAP directory worker.

20Gi Keep PostgreSQL and 5Gi supporting PVCs are **smoke-test starting values**.
The formal 250 deliveries/s for 24 hours produces 21.6 million accepted deliveries
and immutable proof/history growth; measure bytes per delivery and index/topic
growth in a shorter run, then have the platform owners allocate capacity and
retention. Deferred cleanup does not cap disk growth. PVCs remain on StatefulSet
deletion; never delete a namespace/PVC as routine rollback.

## Network boundaries and validation limits

Application/API/auth Routes share an origin; the API Routes go directly to Python
so Next.js is not an extra proxy for SSE or long operator requests. Edge routes
rely on the cluster router's trusted wildcard/domain certificates. Kong/Authelia
use passthrough TLS and their supplied matching certificates. Only v2 enters Kong;
private ingestion has no Route and only accepts Kong mTLS. Kong Admin has no Route
and its policy permits Product API plus an explicitly labelled bootstrap pod.

Ingress NetworkPolicies use namespaced pod selectors and the actual router
namespace selector. Anyone allowed to create pods in this test namespace can copy
labels, so namespace RBAC is part of the boundary. Pod probes require the cluster's
normal node-to-pod probe allowance. The bundle does **not** create egress policy:
have the platform authorize DNS, route/OIDC reachability and supplied ES/Kafka/
Grafana endpoints including all advertised Kafka brokers. Do not add an invented
CIDR or a permanent broad allow rule to work around an existing deny policy.

Read-only filesystems, arbitrary UID, SCC volume permissions, pull access, PVC
provisioning, Routes, CNI policy, TLS, migrations, clients and capacity require
target-cluster verification. Local renderer/unit checks cannot establish them.
Useful primary references: [PostgreSQL official image](https://hub.docker.com/_/postgres),
[Authelia configuration](https://www.authelia.com/configuration/),
[Kong PostgreSQL TLS](https://developer.konghq.com/gateway/postgresql-tls-reference/).
