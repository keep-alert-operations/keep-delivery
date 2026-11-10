# Private OpenShift load-test handoff evidence

Prepared 2026-10-05 for the user's isolated private OpenShift network. The user
supplies Elasticsearch, Kafka, Grafana and an internal registry; the deployment
bundle supplies the other application/infrastructure definitions. The user chose
dedicated Keycloak test users. No private-cluster endpoint was accessed, no image
was pushed and no cluster workload or load test was started.

Start at [the complete guide](../docs/runbooks/openshift-load-testing.md). Its
resource allocations are assistant-selected diagnostic starting values, not
measured capacity guarantees. Correlation/maintenance and recovery qualification
gates remain intact. Native test identity publication uses the existing loopback
adapter inside the Keycloak pod; no authentication/business package was changed.

## Source and scope

The working sources started at these revisions. Container changes and this
handoff are uncommitted; preserve their patches/hashes with any image built from
them. Existing local API, frontend and delivery work was preserved.

| Repository | Starting revision |
| --- | --- |
| keep-delivery | `2d1fc70b942eae2590855538d7ca7cc6d67128ca` |
| keep-api | `0b8a1a351378d0a7952a405b6fac4db1d03c6096` |
| keep-ingestion | `0d2ee23a7940c2c0866498aa0646587f101651bc` |
| keep-processor | `4f7a3fc81483d193033c3eec9f54f6aaeb6a5392` |
| keep-correlation | `ad68f9f4838848f5d318888be4b0e067d5b51e5b` |
| keep-indexer | `ff88bec0e57e4fbcb3af7917e9bdbe29f5efbec3` |
| keep-migrations | `1ebe4cf72fd3878dbc44d4f7c8f5dc8c3d2a5236` |
| keep-frontend | `468ba80eeac6ca2ad4b95752edb7b8005aa354fb` |
| keep-domain (unchanged) | `830d4f65556d93692cca60695fafb522769fe122` |
| keep-contracts (unchanged) | `42107e3814a07518878291929740f5fa120fbff1` |
| keep-design (unchanged) | `7bf631cf8613cca0e10139cc5cd2b391778f9d8e` |

Dependency pins, locks, HTTP/event contracts and migration bytes were not changed
by this task. Production images continue to resolve the committed immutable
dependency pins; editable workspace test dependencies are a development override.
No release manifest is represented as qualified by these changes.

## Executed local checks

- Bundle unit tests: 17 passed after correcting the API parameter set and using
  a workspace-local pytest temporary directory. Checks cover complete canonical
  template parameters, placeholder/digest refusal, Global bootstrap ordering,
  dormant applications, explicit publication, TLS/network boundaries, cutover and
  the fresh-only gateway initializer. PostgreSQL entrypoint shell syntax passed.
- Load toolkit tests: 8 passed after a rerun with a larger subprocess timeout
  during workstation contention. The earlier run had four Node subprocess timeouts,
  not assertion failures. Tests exercise actual native schemas/identity fields,
  bounded lifecycle/key ownership, rate accounting, admission and dashboard
  response contracts, exact raw/compact payload byte bounds and JavaScript syntax.
  This is not traffic against Keep.
- Test identity generator tests: 4 passed. OIDC/PKCE, distinct UUIDs, separated
  clients, read-only directory permissions and HTTPS origin validation checked.
- Combined targeted suite: **29 passed** in 29.16 seconds using the prepared
  delivery environment. Pytest reported one cache-directory permission warning;
  it did not skip or fail tests. Bundle, toolkit and identity Ruff checks passed.
  These checks do not validate server-side OpenShift APIs.
- Kong plugin image built from the locally available Kong 3.9.1 base, and its
  module smoke passed under arbitrary UID `1001230000:0`, read-only root,
  dropped capabilities, no network and writable `/tmp`.
- Keycloak's optimized Java build completed, but final image assembly was cancelled
  when the workstation became unresponsive. Recursive permission rewriting was
  removed from the Dockerfile. The final revised Keycloak image is not yet built.
- The exact locked Linux x86_64 native CEL wheel was inspected after SHA-256
  verification (`aa80370ed20cee201a7671634f9c58dc17fd93f15addf680d7e0393969bc51b3`).
  Its eight native modules require `libstdc++.so.6`; the four CEL-using Python
  service Dockerfiles now install `libstdc++6`. Build-time native compilation
  checks were added, but these are not evidence that every service image was built.
- PostgreSQL wrapper built from cached PostgreSQL 18.2 and passed an isolated
  runtime check: arbitrary UID, read-only root, dropped capabilities, temporary
  data, no published ports/network, TLS `verify-full` query success and rejected
  plaintext TCP. The temporary container/storage were removed afterward.
- Redis wrapper built from cached Redis 7.4.2 and passed an isolated arbitrary-UID,
  read-only-root, dropped-capability, no-network smoke: CA-verified TLS PING and
  publish with ACL credentials, unauthenticated access rejected, unrelated
  channel access denied. The temporary container/storage were removed afterward.
- k6 image built from official k6 1.3.0. Its four entrypoints initialized under
  actual k6 with arbitrary UID and a read-only filesystem, without network access.
  One unsupported top-level `maxDuration` option was corrected to a scenario
  option. The latest-source inspection matrix passed the seed case, then stalled
  during Docker startup and was cancelled. The final source needs an image rebuild
  and complete runtime exercise on the private runner. No Keep requests were sent.
- The system document checker passed with 111 documents, 566 source entries and
  REV-01 through REV-38 intact. Local link targets in the four new primary
  guide/evidence documents were also checked and exist.

Reproduce the targeted source checks from `keep-delivery` (Windows paths shown):

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_openshift_load_test_bundle.py tests/unit/test_load_test_harness.py tests/unit/test_load_identity_prepare.py -q --basetemp=.local/pytest-load-handoff
.venv/Scripts/ruff.exe check deploy/openshift/load-test qualification/load-test-identity tests/unit/test_openshift_load_test_bundle.py tests/unit/test_load_test_harness.py tests/unit/test_load_identity_prepare.py
python qualification/verify_documents.py
```

Locally built wrapper manifest digests (not pushed to the user's registry):

| Image | Manifest digest |
| --- | --- |
| PostgreSQL wrapper | `sha256:acae98ae18bd211aed09c83c5050be96bce84c044ab9fba8e804e99b652ef560` |
| Redis wrapper | `sha256:a1b9ecc814b206389ad41f976e8b37c0eb39bfcd2753b576c7cfeeaf601e8b76` |

The final PostgreSQL Dockerfile was restored to the exact successfully tested
version after cancelling an assertion-only rebuild during renewed host pressure.
Its SHA-256 is `977ea842791bfe1f812be576f845031f2dcd1c03bf95a547256be70649bceefe`;
the entrypoint is `ba5c1152358085bb8e8e224092d21258b6596c5193049366e91334fee923a9f2`.
Redis Dockerfile SHA-256 is
`c586e9eebf182abfcc6b1bacf2b19ff4d1d8e7a05cf8f282cd3795c3d644484b`;
its config is `cc0cbb793044cd7004df41f19c561199a84ba71a5f87cb8cf3c66708a5649c6a`.

## Limits to validate in the private environment

The cluster's `oc` is unavailable here. Execute local template processing and
server-side dry runs on the administration host, then validate SCC/arbitrary UID,
PVC permissions, Routes, CNI, TLS, registry pulls, external service contracts,
clock evidence, migration/bootstrap, real Keycloak import/login/publication,
operator provisioning and every image's runtime smoke. Final Keycloak image and
all application image rebuilds remain unverified
unless explicitly supplemented with a later result.

The k6 workload is an initial executable diagnostic toolkit. It does not implement
an unattended 24-hour OIDC login flow or complete browser-render SLI capture.
No sustained throughput, five-second visibility, HA, LDAP, restore qualification
or production-readiness claim follows from this handoff. Preserve real outcomes,
including failures and missing observations, when executing the guide.

The original cold-start review found two implementation gaps beyond image
packaging: loopback-only initial search activation and the missing deployable
operator proof wrapper. The subsequent
[cluster initialization implementation](cluster-initialization-2026-10-05.md)
supplies those tools and deployment integration. Their actual private-environment
execution is still required; generic `rebuild_cli bind` or raw Kong plugin probes
alone are insufficient. Correlation/maintenance evidence remains separately required.
