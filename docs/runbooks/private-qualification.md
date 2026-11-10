# Private nonproduction qualification

The user will deploy nonproduction within a private network that this agent cannot access. No credentials or private endpoints need to be sent into this conversation. Keep actual configuration and test evidence in that environment and return sanitized failures or evidence references as useful.

## Prepare bindings

Copy `qualification/environment-manifest.example.json` into your environment configuration repository. Supply actual service versions, permitted namespaces, trust/credential references, allocation evidence and owner-provided durability/recovery guarantees. No example service tag in `deploy/local/compose.yaml` asserts a company-installed version. Do not place secret values in this manifest or commit environment files.

Run `python qualification/check_environment_manifest.py <manifest.json>` to check completeness. This performs no network requests and proves only that required references are present. It does not enable database capability gates or qualify infrastructure.

Use distinct migration/runtime/worker identities. Runtime identities must not be PostgreSQL owners or superusers. Apply migrations with the migration account. Check row-level and direct-partition denial with the restricted runtime role, then review the remaining privilege gaps in implementation status before any release.

## Executable checks presently available

1. From `keep-delivery`, run `scripts/verify.ps1 -Suite Unit` and, with `KEEP_TEST_DATABASE_URL` set to a disposable test database, `scripts/verify.ps1 -Suite Database` (see [run and verify locally](../repository-workspace.md#run-and-verify-locally)). Keep JUnit output, exact lockfiles, migration checksums and the actual PostgreSQL version.
2. Set `KEEP_TEST_ELASTICSEARCH_URL` to a disposable test Elasticsearch endpoint and, from `keep-delivery`, run `.venv/Scripts/python.exe -m pytest ../keep-indexer/tests/compatibility ../keep-domain/tests/compatibility -q`. The tests create uniquely named test indexes and remove them. Run them with a restricted test identity and review the tests before pointing them at a shared cluster. This is native CEL versus real Elasticsearch evidence; mocked search tests are not equivalent.
3. From `keep-delivery`, run `scripts/verify.ps1 -Suite Build` and `scripts/verify.ps1 -Suite Browser`. Separately run the opt-in real browser test from the [frontend guide](https://github.com/keep-alert-operations/keep-frontend/blob/main/docs/frontend.md#real-browserapipostgresql-smoke) (`scripts/verify.ps1 -Suite BrowserReal`); it creates/drops an isolated PostgreSQL database and starts temporary HTTPS services. It validates real application writes but intentionally does not prove real SSO.
4. Configure LDAP using the [identity implementation](https://github.com/keep-alert-operations/keep-api/blob/main/docs/identity-implementation.md) with the exact immutable-ID/eligibility/membership schema, then publish once with `python -m keep.worker directory --once`. There is no directory worker to run: REV-35 ([worker scope 4](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md#4-directory-synchronization-is-nice-to-have)) allows only explicit one-off publication. Verify complete paging/nested groups, known negative evidence, outages and active-session enforcement against actual company data with approved synthetic accounts, publishing explicitly again after each source change. Five-minute upstream propagation is deferred with the worker and is not a current qualification target. The application must not infer that a failed lookup is a trusted deletion.
5. Configure Keycloak issuer/client/trust/protected directory-ID mapper and accepted password-authentication claims. Exercise login/logout/refresh uncertainty and revocation through the browser. Record actual installed versions and read-only evidence of mapper/client settings.
6. From `keep-delivery`, run the isolated real mTLS tests with `.venv/Scripts/python.exe -m pytest tests/integration/test_gateway_tls.py -q`. For Kong deployment, additionally verify the actual stripping/key-auth/private TLS behavior described below; local TLS tests do not prove installed Kong behavior.

## Private native-v2 listener

`python -m keep.private_ingestion` starts a dedicated Uvicorn HTTP/1.1 TLS listener. Required environment references are `KEEP_GATEWAY_CERT_FILE`, `KEEP_GATEWAY_KEY_FILE`, `KEEP_GATEWAY_CA_FILE` and comma-separated lowercase leaf certificate SHA-256 fingerprints in `KEEP_GATEWAY_PEER_SHA256`, plus normal backend settings. Configure `KEEP_ROLE=private-ingestion`. Optional bind host/port default to loopback:8443; provide the intended pod bind address explicitly.

It requires a trusted client certificate and an allowlisted peer fingerprint. The protocol obtains the peer certificate from the TLS transport, not forwarded headers. The application accepts exactly one canonical `X-Consumer-ID` and `X-Credential-Identifier` only on that verified connection and rejects forwarded Authorization/X-API-Key secrets. Kong must strip spoofed identity headers, authenticate native `X-API-Key`, strip the secret and supply the recorded credential ID. Certificate rotation requires overlapping approved fingerprints followed by removal of the retired fingerprint.

The ordinary Uvicorn listener accepts public v1 into fixed Global but cannot authenticate v2 by headers. PostgreSQL remains authoritative for v2 ownership/current/retiring/expiry/revocation and receipt authorization. Keep `v2_ingress` false until installed Kong and operator lifecycle bindings are qualified. Grafana webhooks use the same pinned private listener and durable v2 admission, retaining exact member bytes, adapter version and native mapping. SQL operator lifecycle, provisioning jobs and the profile-driven Kong/Grafana executor are connected. Controlled HTTP and SQL fixtures do not qualify installed-service provisioning.

## Remaining qualification gates

Correlation and maintenance are always on (REV-39), with no activation step; their qualification still depends on complete durable temporal processing, measured bounds and connected workers. Do not qualify a pure algorithm test as a connected pipeline. Operator remote sagas, search rebuilding and resource cleanup have executable local checks. Rollback reconstruction, protected permission/credential recovery producers, rendered observations and remaining release workflows require implementation before their complete acceptance exercises can pass.

Use the normative `verification-plan.md` as the acceptance checklist. Record failures, skipped tests and unimplemented scenarios separately. Required next environment exercises include real Kafka ordering/replay/failure, Redis loss/reconnect, Elasticsearch failover/index rebuild, actual Kong/Grafana CAS or controlled-writer safety, credential cutovers, and independent recovery-evidence rollback survival. Never toggle a capability based solely on an HTTP health response.

Sustained-load/profile qualification, crash/rollback recovery, accessibility/performance audit, container/dependency/security scans, immutable release digests, OpenShift rollout and the rolling production SLO are unperformed. Production deployment is not authorized by the implementation request. Infrastructure retention, backup, HA, allocation and recovery policy remain with the dedicated operating teams.

The minimal recovery-evidence resource kinds include `audit`. Audit retirement uses the immutable audit event UUID; platform events use fixed Global as the external evidence scope without changing their platform scope in Keep. The supplied evidence service must explicitly support this application binding before audit cleanup can run.

## Authenticated service bindings and worker roles

Set `KEEP_ELASTICSEARCH_API_KEY` as a workload-scoped secret and
`KEEP_ELASTICSEARCH_CA_FILE` to the mounted trust bundle. Optional
`KEEP_ELASTICSEARCH_CLIENT_CERT` and `KEEP_ELASTICSEARCH_CLIENT_KEY` must be
provided together. Credentials and nondevelopment search bindings require HTTPS;
certificate verification cannot be disabled through application settings.
These settings use the [official Elasticsearch client authentication/TLS options](https://www.elastic.co/docs/reference/elasticsearch/clients/python/connecting).

Kafka accepts `KEEP_KAFKA_SECURITY_PROTOCOL=SSL` or `SASL_SSL` for private
deployments. Configure `KEEP_KAFKA_CA_FILE` and, if required, the paired
`KEEP_KAFKA_CLIENT_CERT`/`KEEP_KAFKA_CLIENT_KEY` plus secret
`KEEP_KAFKA_KEY_PASSWORD`. SASL requires `KEEP_KAFKA_SASL_MECHANISM` (`PLAIN`,
`SCRAM-SHA-256` or `SCRAM-SHA-512`), `KEEP_KAFKA_SASL_USERNAME` and secret
`KEEP_KAFKA_SASL_PASSWORD`; it is accepted only with TLS. Other mechanisms need
a reviewed adapter extension. Plaintext is limited to isolated development.
Application idempotence, acknowledgement and offset settings remain fixed.
See the [official Kafka Python client configuration](https://docs.confluent.io/kafka-clients/python/2.15/overview.html).

`KEEP_RECOVERY_BINDING` names the mounted JSON `EvidenceBinding` and
`KEEP_RECOVERY_CREDENTIAL` contains the adapter bearer credential. Both must be
supplied. Run `protected-deletions` to resume configuration deletion jobs. The
`retention`, `incident-retention` and `audit-retention` sweeps are deferred (REV-34;
[worker scope 2](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md#2-assignment-and-data-lifetime-automation))
and `keep.worker` rejects them, so they are not part of this qualification;
reinstated incident cleanup would also need the independently protected `command`
evidence kind. No worker automatically turns on recovery readiness.

The [OpenShift handoff](openshift.md) describes separate migration, API, mTLS
ingress, worker and network templates and their required owner-supplied inputs.
They have been structurally checked locally, not admitted or run by a cluster.


Search reconstruction has an executable [rebuild CLI](https://github.com/keep-alert-operations/keep-indexer/blob/main/docs/runbooks/search-rebuild.md). Its
owner-supplied alias/allocation binding is separate from connection credentials.
Document parity and uncertain cutover have been exercised on isolated Elasticsearch
9.0.0. This does not qualify the private service or complete rollback restore.

Operator provisioning runs inside the Product API request (REV-34;
[worker scope 3](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md#3-request-owned-operator-provisioning));
there is no operators worker. The API accepts the explicit executable profile
described in [operator integration](https://github.com/keep-alert-operations/keep-api/blob/main/docs/operators-implementation.md). Mount its binding,
the separate `KEEP_OPERATOR_DATABASE_URL` and the Grafana, Kong and
readiness-probe credentials into the Product API only. A missing or unsupported
capability fails the provisioning request with 503 and an explicit retry path; an
HTTP fixture is not installed Grafana/Kong compatibility evidence.

The [restore fencing procedure](restore-fencing.md) now provides controlled begin/status/collect commands and a separately privileged database role. This is a partial recovery implementation: complete checkpoint enumeration cannot enable human access, credentials or ingestion. Validate the supplied [checkpoint wire contract](recovery-checkpoints.md) and retain the unresolved family-specific recovery gates. [Wrapping-key rotation](../wrapping-key-rotation.md) provides bounded authenticated rewrap without automatic key retirement.
