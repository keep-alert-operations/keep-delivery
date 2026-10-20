# Private test deployment preparation — 2026-10-07

User scope: committed repositories already transferred to a private network;
internal-only build mirrors; supplied Kafka, Elasticsearch and Grafana 12;
deploy PostgreSQL, Redis, Kong, Keycloak and Appchi to isolated OpenShift.
Every environment URL remains a placeholder. No credentials were requested.

See the [private test procedure](../docs/runbooks/private-test-deployment.md).

## Implementation and acceptance

- Seven application/migration Dockerfiles accept optional BuildKit-only apt,
  public CA and dependency environment secrets. Frontend also accepts npmrc.
  Git askpass accepts internal mirror username/token-file settings.
- Separate build contexts regenerate uv locks through a named internal index.
  Compare package versions, Git pins, dependency edges/markers and original
  artifact hashes. Refuse public artifact hosts and restore failed staged relocks.
  Source release locks remain unchanged.
- Explicit source overlay transfers missing deployment/search/proof/image assets
  onto the recorded committed bases. Verify member paths and hashes, matching
  independent Git roots/bases and existing-file hashes before any write.
  Reject conflicting local changes; identical repeat application is safe.
- Existing Grafana remains external. No Grafana image/version is selected.
  Actual Grafana 12 legacy provisioning and controlled-writer compatibility,
  direct Kong member proof and real lifecycle execution remain target checks.
- Clock reports remain genuine measured infrastructure inputs. No fabricated
  reports, synchronization daemon, capability override or new worker is supplied.

## Source bases

Delivery `ee37546e771e435d1f07c2b88857f548dd05fbb0`;
API `e2de23d0b70bb52f8180eebb396d927567705eaf`;
ingestion `3515ac96bd1958483b5583d59afc13d9782e6111`;
processor `c4bb2225505453b7fd6bfea20283355da79958cf`;
correlation `7a408f990021c2f4531c1cd90c52641c80e1bc90`;
indexer `a56e94c8f07c6d98342c809e05ebc8b5ac780538`;
migrations `016d0f554de570a96e4da1cf054c9b1c70b5c768`;
frontend `5c1bfd052042b0df8538cd73f2395e2fd7f70a82`.
Shared package pins remain in the existing release locks. The archive manifest
records exact base revisions and source/base hashes for every included file.
Existing unrelated edits were preserved and excluded from the bounded overlay.

## Verification

From delivery, the final targeted command was:

```text
.venv/Scripts/python.exe -m pytest tests/unit/test_prepare_internal_build.py tests/unit/test_deployment_overlay.py tests/unit/test_openshift_load_test_bundle.py tests/unit/test_load_identity_prepare.py tests/unit/test_load_test_harness.py -q --basetemp=.local/pytest-private-deployment-final --tb=short
.venv/Scripts/ruff.exe check scripts/prepare_internal_build.py scripts/deployment_overlay.py tests/unit/test_prepare_internal_build.py tests/unit/test_deployment_overlay.py
python qualification/verify_documents.py
```

Results: **58 passed** in 4.41 seconds; Ruff passed; documentation passed with
120 documents, 566 source entries and REV-01 through REV-38 intact. Units include
altered versions/hashes/Git sources/dependency edges, escaped or unassigned tar
members, altered payloads, conflicting target files, relock rollback and idempotent
overlay application. These use controlled fixtures, not actual private services.

The independent security review identified metadata source execution during
relocking before the hash comparison. The final helper uses `uv lock --no-build`
and tests assert that flag; wheels/static metadata are required. No remaining
actionable P1/P2 findings were reported in the bounded final review.

Dockerfile shell syntax, transfer rehearsal and archive checks are recorded in the
transfer sidecar report. No container build, mirror connection, private OpenShift
access, deployment, remote provisioning or load result is claimed. Local
source/static checks cannot establish target image/runtime compatibility,
authenticated metadata resolution, actual image-layer secret absence or clock
supply. Runtime Node/client CA trust remains separately configured and tested.
