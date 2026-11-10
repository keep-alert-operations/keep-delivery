# Cluster initialization implementation — 2026-10-05

The user's authorized scope is the two implementation gaps identified after the
private OpenShift load-test handoff: initial search activation and deployable
operator serving-pool proof. Usage is in
[cluster initialization](../docs/runbooks/cluster-initialization.md).

## Changed behavior and repository combination

| Repository | Starting revision | Task changes |
| --- | --- | --- |
| keep-indexer | `ff88bec0e57e4fbcb3af7917e9bdbe29f5efbec3` | Explicit initial-development bootstrap, live disposable ES probe, durable retry ownership, SQL activation fences and tests |
| keep-api | `0b8a1a351378d0a7952a405b6fac4db1d03c6096` | Internal HTTPS proof service with exact member/binding validation, before/after read-only SQL checks, bounded direct gateway probes and tests |
| keep-delivery | `2d1fc70b942eae2590855538d7ca7cc6d67128ca` | Bootstrap Job, proof Deployment/Secrets/network rules, direct singleton Kong StatefulSet DNS, restricted login setup and operating instructions |
| keep-design | `7bf631cf8613cca0e10139cc5cd2b391778f9d8e` | Assistant-selected implementation boundaries and acceptance scenarios under existing REV-33/34 |
| keep-ingestion | `0d2ee23a7940c2c0866498aa0646587f101651bc` | Existing Kong probe contract consumed; no plugin source change |

These are local working-tree changes on the listed bases, not new published
release commits. Existing Dockerfile, local-runtime, workspace and executor
rotation changes were preserved. No migration, shared-package dependency or public
HTTP contract changed. Existing immutable release pins remain unchanged; build
and promote revised images together before using the new deployment assets.

The isolated environment intentionally uses development capacity and native test
identities. Initial search activation cannot reopen restore gates or adopt
populated authority. Operator provisioning remains request-owned in the API;
the helper neither creates alerts nor performs background retries. The test
Kong workload changed from Deployment to StatefulSet: an already-applied earlier
draft needs the stopped replacement procedure in the bundle README, preserving
the Kong database and skipping fresh-only initialization.

## Verification evidence

Current verification is local source execution using delivery's documented
editable development environment, not locked published service artifacts.

- Final API unit suite: 139 passed in 20.11 seconds, including the new proof and
  existing executor scenarios; four existing dependency deprecation warnings.
- Final indexer unit suite: 59 passed, including repeat-failure gate closure.
- Delivery bundle, load harness and test-identity units: 36 passed.
- Final delivery unit suite: 118 passed, 65 skipped in 13.65 seconds. The skipped
  cases require separately configured database fixtures or cover explicitly
  deferred scheduler behavior. Six dependency deprecation warnings and one
  nonfatal pytest-cache write warning were reported; skips are not passes.
- API/indexer source/tests and modified delivery tooling Ruff checks passed.
- Workspace import-boundary checks passed: 211 owned modules, five independent
  service imports. Workspace/design documentation checks passed.
- OpenAPI and internal event-schema drift checks passed.

- Operator proof final PostgreSQL integration: 8 passed in 109.70 seconds,
  including initial connection readiness, real migration/runtime-role privileges,
  write denial, restore/lease/policy fences and a SQL revision change during a
  controlled gateway response. The gateway transport in these tests is mocked;
  this is real SQL evidence, not real Kong/Grafana qualification.
- Final proof/executor targeted units after startup warmup: 91 passed.
- API wheel built using offline cached build dependencies; archive inspection
  verified the exact final `proof.py` bytes. SHA-256:
  `fe9cf717e82a6ac01dad0f6548695faa97f113fc3382b8a3b597ff01e83b7c0d`.
- Final search matrix: 22 passed in 169.03 seconds (21 bootstrap integration
  cases and the existing Elasticsearch compatibility case). This exercised the
  actual CLI, worker privileges, live disposable capability probes, retry and
  crash recovery, future-clock rejection, restore/concurrent-write fences and
  safe cleanup of owned probe resources.
- Indexer wheel built using offline cached dependencies; archive inspection
  verified both bootstrap modules match final source bytes. SHA-256:
  `fc23106f7841b14ad21710f6a64a86e2fb8d70b753272d50eb4b2e122e197f6b`.
- Delivery wheel built successfully with `uv build --offline --wheel` using
  cached build dependencies. No container image build is claimed by these
  packaging checks.
- Independent source/caller/security review, including repeat-gate and startup
  refinements, found no actionable correctness/security issue.

Fixture startup used cached PostgreSQL 18.2
and Elasticsearch 9.0.0, synthetic credentials, private TLS trust, loopback ports
and uniquely owned disposable containers/volumes. Neither the existing local
application data nor the private cluster is used. Synthetic fixture clock inputs
are controlled test data, not trusted clock evidence for deployment.
The local PostgreSQL fixture used an explicit IPv4 host address while retaining
hostname/certificate verification; no application request or SQL deadlines were
relaxed. All two owned containers, two volumes and the network were removed
after the final suites; absence of resources with the fixture owner label was
verified.

Reproduction entry points, from their owning repositories with the documented
editable delivery environment and the integration-specific private environment:

```text
keep-api:     python -m pytest tests/unit -q
keep-api:     python -m pytest tests/integration/test_operator_proof_postgres.py -q
keep-indexer: python -m pytest tests/unit -q
keep-indexer: python -m pytest tests/integration/test_search_bootstrap_integration.py tests/compatibility/test_search_elasticsearch.py -q
keep-delivery: python -m pytest tests/unit -q --basetemp=.local/pytest-cluster-delivery-final --tb=short
keep-delivery: python qualification/check_contracts.py
keep-delivery: python qualification/verify_boundaries.py
keep-delivery: python qualification/verify_documents.py
keep-design: python scripts/check_docs.py
```

The service READMEs describe required test environment variables. Integration
fixtures use fresh synthetic databases and live TLS/authenticated Elasticsearch;
do not point them at shared application data. The final search JUnit file is
retained locally at `keep-indexer/.local/search-bootstrap-final.xml`; private
credentials and fixture files remain ignored and are not release evidence.

## Remaining environment execution

The private cluster is unreachable here and `oc` is unavailable. No manifests
were applied there and no sustained load was run. Build the revised API/indexer
images, import all required images into the private registry, populate actual
service bindings/Secrets, process/dry-run the templates with the cluster's `oc`,
and execute the staged guide. Actual Kong/Grafana lifecycle, native Keycloak,
OpenShift SCC/storage/network/Routes, clock supply and combined load remain
target-environment qualification. No DEL-01–DEL-08 gate is closed by this work.
