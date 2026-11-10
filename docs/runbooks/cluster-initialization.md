# Initial search activation and operator verification in the test cluster

Use this procedure with the [OpenShift load-test guide](openshift-load-testing.md)
and [bundle](../../deploy/openshift/load-test/README.md). It applies to a fresh,
isolated development deployment with synthetic data. It does not qualify a
production release, restore a database or replace the supplied service contracts.
Build the API and indexer images from the revised source containing these tools;
old published image/dependency pins do not contain them.

## 1. Initialize search before starting writers

Run migrations and Kafka cutover first. Keep API, ingestion, processor, indexer
and correlation stopped. The Job's `--ingestion-and-writers-stopped` flag is an
operator declaration, not a command that stops those workloads. Confirm the
actual replicas and absence of other clients with write access before applying it.

Prepare `keep-indexer-env` and `keep-indexer-bindings` as in the main guide:

- `KEEP_ENVIRONMENT=development`, `KEEP_ROLE=indexer` (set by the Job), the shared `KEEP_DEPLOYMENT_EPOCH`, a
  restricted worker login and the usual wrapping/profile settings.
- `KEEP_ELASTICSEARCH_URL=https://SUPPLIED_ES_HOST:PORT`, a valid CA file and
  the actual supported API-key or client-certificate authentication.
- `KEEP_ELASTICSEARCH_INDEX` equal to the reserved alias in the binding.
- Current host and PostgreSQL clock evidence at the paths mounted by the Job.
- `search-binding.json`, based on the [example](../../deploy/openshift/load-test/search-binding.example.json).
  Replace its evidence reference with the real allocation/exclusive-writer
  agreement. Choose the shard/replica settings with the ES owner; the example's
  one shard and zero replicas are diagnostic starting values. The two `true`
  fields assert real guarantees; they are not switches to bypass verification.

The ES identity needs cluster identity/version inspection and create/read/write/delete privileges for the reserved
physical-index and temporary probe namespace, plus mapping, refresh, PIT,
search/count and alias-management access. Limit its privileges to that allocation.
The bootstrap performs fresh capability checks on disposable indices at the
configured endpoint, including strict mappings, external versions, refresh,
pagination and atomic alias changes. It records the observed target/version and
binding evidence. A supplied reference alone cannot make those checks pass.

In private `configuration.json`, set `search_bootstrap.physical_index` to a new
name under `index_prefix`, `index_epoch` to a new UUID, and `operation_reference`
to a nonsecret change/test reference. Generate the UUID once and preserve all
three values for retries. They identify this initialization, independently of
`release_id`, which names the Kubernetes Job.

After rendering the full bundle:

```bash
oc -n "$NAMESPACE" apply --dry-run=server -f "$PRIVATE/rendered/36-search-bootstrap.json"
oc -n "$NAMESPACE" apply -f "$PRIVATE/rendered/36-search-bootstrap.json"
oc -n "$NAMESPACE" wait --for=condition=complete --timeout=600s \
  "job/keep-search-bootstrap-$RELEASE_ID"
oc -n "$NAMESPACE" logs "job/keep-search-bootstrap-$RELEASE_ID" \
  > "$PRIVATE/search-bootstrap-result.log"
```

The Job runs the indexer image's explicit command:

```bash
python -m keep.search.bootstrap \
  --binding /etc/keep/search-binding.json \
  --physical-index YOUR_RESERVED_PHYSICAL_INDEX \
  --index-epoch YOUR_PRESERVED_INDEX_EPOCH_UUID \
  --operation-reference YOUR_TEST_REFERENCE \
  --ingestion-and-writers-stopped
```

Require successful completion before starting writers. The command reserves an
exact initial binding, verifies an empty database/index, initializes tenant search
progress, audits the result and opens only the initial search capability gate.
It refuses unexpected existing indices/bindings, populated authority and restore
fences. It is not the procedure for adopting existing application data.

On failure, preserve logs and the selected index/epoch/reference. Correct the
reported cause while keeping writers stopped. A new `release_id` can name a new
Job attempt, but retain the same search identity and binding. Inspect orphaned
probe resources before cleaning them; never remove an index merely because its
name resembles a probe. Do not change SQL gates or erase the reserved intent.
For populated deployments use the existing `keep.search.rebuild_cli` protocol,
including its recovery prerequisites.

After activation, start the normal indexer and processor, admit one synthetic
alert, then verify its authoritative row, search result, exact dashboard count
and searchable progress. Initialization is not a throughput result.

## 2. Deploy the internal operator proof helper

The Product API continues to own synchronous operator provisioning. The proof
helper answers private verification requests and does not retry or provision
operators on its own. It uses the API image and a separate restricted database
login; do not give it Grafana management credentials, the wrapping key or Kong
Admin credentials.

The test bundle uses a singleton Kong StatefulSet and a headless member Service.
Keep it at one replica. Its direct proxy URL is
`https://keep-kong-0.keep-kong-members:8443`; the public `keep-kong` Service
is for normal traffic. The Kong certificate must cover this exact direct DNS name
as well as the existing internal/public names. Start it with:

```bash
oc -n "$NAMESPACE" scale statefulset/keep-kong --replicas=1
oc -n "$NAMESPACE" rollout status statefulset/keep-kong --timeout=10m
```

Run `31-kong-routes.json` as documented in the bundle. Record its actual
`serving_member_observed` node UUID. That UUID must appear identically in the
proof configuration and `OperatorBinding.serving_members`. A DNS name or invented
display label is not a member ID. The underlying policy probes do not admit
alerts into Kafka.

Copy the API-owned configuration example:

```bash
mkdir -p "$PRIVATE/operator-proof-bindings"
cp ../keep-api/docs/operator-proof.example.json \
  "$PRIVATE/operator-proof-bindings/configuration.json"
```

Set its member to the observed UUID and direct URLs ending in
`/__keep/operator-check` and `/__keep/operator-auth-check`. Use the actual Kong
CA at the configured path. Keep the documented bounded deadline below the API
caller's timeout; slow/missing/inconsistent members must fail the operation.

Prepare the actual operator binding using the schema in
`keep-api/src/keep/operators/binding.py`. Both API and proof helper must mount
the same binding. Its `probe_url` is
`https://keep-operator-proof:8443/operator-check`. Its CA bundle must trust the
proof certificate as well as the other configured remote HTTPS endpoints.
Record actual Grafana organization/version, Kong version, complete rule inventory,
reserved namespace, controlled-writer agreement and verification evidence. Retain
the existing Grafana tree and coordinate changes with its owner.

Populate the proof binding directory with only:

- `configuration.json` and identical `operator-binding.json`.
- `postgres-ca.crt` and the referenced Kong CA/bundle.
- `probe-token`: the bearer secret also supplied to the Product API as
  `KEEP_OPERATOR_PROBE_TOKEN`.
- `gateway-probe-token`: the distinct token installed into the Kong probe plugins
  by gateway bootstrap. It is not an operator ingestion key.

Create private `operator-proof.env`:

```dotenv
KEEP_DEPLOYMENT_EPOCH=THE_SHARED_TEST_DEPLOYMENT_EPOCH
KEEP_PROOF_DATABASE_URL=postgresql://keep_proof_login:ENCODED_PASSWORD@keep-postgres:5432/keep?sslmode=verify-full&sslrootcert=/etc/keep/postgres-ca.crt&connect_timeout=1
```

The [runtime login script](../../deploy/openshift/load-test/runtime-logins.sql)
creates `keep_proof_login` with the existing worker role and read-only transaction
default. Assign its own password using the documented interactive procedure.
The helper uses read-only transactions and a bounded pool; account for its
connections separately from the API's provisioning pool. These existing worker
privileges are broader than the helper's reads; this isolated test-role setup is
not a claim of a separately qualified production least-privilege role.

Provision its server certificate with SAN `keep-operator-proof` (and the full
Service DNS name if used). Then:

```bash
oc -n "$NAMESPACE" create secret generic keep-operator-proof-env \
  --from-env-file="$PRIVATE/operator-proof.env"
oc -n "$NAMESPACE" create secret generic keep-operator-proof-bindings \
  --from-file="$PRIVATE/operator-proof-bindings/"
oc -n "$NAMESPACE" create secret tls keep-operator-proof-tls \
  --cert="$PRIVATE/operator-proof-tls/tls.crt" \
  --key="$PRIVATE/operator-proof-tls/tls.key"
oc -n "$NAMESPACE" apply --dry-run=server -f "$PRIVATE/rendered/32-operator-proof.json"
oc -n "$NAMESPACE" apply -f "$PRIVATE/rendered/32-operator-proof.json"
oc -n "$NAMESPACE" scale deploy/keep-operator-proof --replicas=1
oc -n "$NAMESPACE" rollout status deploy/keep-operator-proof --timeout=5m
```

There is no public Route. NetworkPolicy admits only the Product API to this
endpoint. The proof workload can reach the private Kong proxy and PostgreSQL;
it has no Kubernetes API token. Readiness requires loaded configuration and an
initial database connection; every operator's remote policy is verified only
during its operation-bound request.

## 3. Verify the full operator lifecycle before load

Through the normal authenticated API/UI, create a dedicated test operator and
require verified completion. Check native v2 and the real Grafana webhook reach
its immutable owning tenant. Then rotate and revoke its credentials, verifying
the active/retiring/denied states and fixed expiry. Exercise a wrong key, an
unreachable member and a mismatched member ID in controlled tests: provisioning
must return the documented incomplete/failure result, never success. Check
operation diagnostics without printing keys, tokens or complete secret files.

The helper verifies the current operation/policy before and after probes and
rejects a superseded attempt. When the API supplies the selected credential's
secret it checks real Key Auth as well as policy. Old/revoked credentials may
only have credential-ID denial evidence; do not claim their plaintext was tested.
Retain the operation results, node inventory, image digests, binding/config
digests and test timestamps with the load-test evidence.

Kong pod replacement can change its node-local UUID. The StatefulSet deliberately
uses explicit `OnDelete` updates. Stop operator mutations before replacement,
observe the new identity, repeat the required installed-version/clock/probe
verification, update both bindings and restart the API/helper with those bindings.
Do not rerun the fresh-only Kong initializer against its populated database.
Changing replicas requires direct URLs for every actual member and renewed
serving-pool qualification; do not attach an HPA or treat a load-balanced sample
as full-pool evidence.

If proof fails, preserve the durable operation and use its normal explicit retry
after repair. There is no automatic operator worker and no implied rollback of
remote changes. Keep admission/recovery fences intact.
