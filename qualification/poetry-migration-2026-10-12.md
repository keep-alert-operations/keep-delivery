# Poetry migration — 2026-10-12

The user selected Poetry for every Python repository, including active scripts,
documentation, CI and container builds. This is local implementation and verification
on the existing working branches; it is not publication or release qualification.
Existing unrelated staged, unstaged and untracked work was preserved.

## Changes and invariants

- All nine Python repositories now use Poetry **2.3.1**, repository-local `poetry.toml`
  configuration and native-generated `poetry.lock` files. Their obsolete active
  `uv.lock` files were removed. The frontend retains npm and its existing lock.
- PEP 621 runtime requirements, Python 3.13 compatibility, immutable internal Git
  references and Hatchling wheel packaging remain unchanged. An optional
  `lock-constraints` group preserves coordinated exact version constraints and
  the original resolved baseline. It is excluded from ordinary sync and production
  `--only main` installs; do not use `--all-groups` for normal setup.
- Delivery adds Poetry 2.3.1 to development dependencies so its private mirror
  preparation helper and regression tests can use the matching tool APIs. This
  adds development tool dependencies only; published runtime requirements are unchanged.
- Each project explicitly declares its owned namespace packages for Poetry's root
  editable installer. Contracts schemas and migration SQL remain packaged by Hatchling.
- Workspace setup validates the lock, syncs, then installs explicit editable
  overrides with environment pip and `--no-deps`. Overrides do not change release
  pins. A later sync restores the pinned packages.
- Nine Python CI workflows and six Python service Dockerfiles use Poetry.
  Containers install a root wheel rather than depending on an editable source path.
  Existing private Git, CA, mirror, secret mounts and runtime smoke checks are preserved.
- Private build staging transfers Poetry metadata and remaps only staged registry
  sources/content hashes without resolving dependencies or executing source builds.
  It validates exact graph, artifact hash and Git-pin preservation plus native
  `poetry check --lock`. Source/release locks remain untouched.
- Active READMEs, workspace/runbook/developer guidance, scripts, the repository
  layout specification and installed/canonical agent guidance agree. Historical
  uv qualification records, monorepo snapshots and their provenance remain historical.
  Some upstream optional extra names in generated Poetry locks also contain `uv`;
  no active Keep workflow selects them.

## Verification

Python 3.13 and Poetry 2.3.1 were used on Windows. Verification installed all nine
locks in fresh disposable environments, preserving the existing development
environments and running services. Repository checks ran from their owning roots.
Local command/result logs, initial manifests and lock-equivalence reports are in
ignored `.local/poetry-migration/`.

| Check | Result |
|---|---|
| `poetry lock`, then `poetry check --lock --strict` in all nine Python repositories | Passed |
| Compare original lock records to native Poetry locks | All 392 original dependency records retain versions and Git revisions; all original artifact hashes are retained |
| `poetry sync --all-extras --no-interaction` in nine isolated environments | Passed |
| `poetry build --format wheel` in all nine Python repositories | Passed |
| Compare wheel requirements to original PEP 621 requirements | Unchanged in all nine wheels |
| Compare packaged resources to source | Six contracts schema files, all 47 migration SQL files and the checksum manifest retained exactly |
| Environment Python `-m ruff check src tests` in all nine repositories | Passed |
| API units | 139 passed |
| Contracts units | 16 passed |
| Delivery units, including Poetry mirror/workspace/overlay regressions | 188 passed, 65 skipped for unbound real-service scenarios |
| Domain units | 395 passed |
| Indexer units | 65 passed |
| Ingestion units | 5 passed |
| Migration units | 5 passed |
| Processor/correlation units | No owned unit test files in these checkouts; not claimed as passing suites |
| Coordinated local editable installation with environment pip `--no-deps` | Passed; all nine source packages installed, locks unchanged |
| `qualification/check_contracts.py` in the coordinated environment | Passed |
| `qualification/verify_boundaries.py` in the coordinated environment | Five service import boundaries passed |
| `qualification/verify_documents.py` and design `scripts/check_docs.py` | Passed |
| Root/canonical AGENTS and CLAUDE consistency | Exact bytes match |
| Current workflow YAML parsing and shell syntax | All 11 workflow files parse; 70 workflow/Docker shell blocks pass `bash -n` |
| `docker buildx build --check` for six Python service Dockerfiles | Passed with no warnings |
| Real staged API mirror remap and native lock validation | Passed; graph, pins and hashes preserved without mirror access |

Final owned unit results total **813 passed, 65 skipped**. Service-backed database,
Kafka, Elasticsearch, Kong and browser qualification, complete image builds and
container runtime smoke execution were not performed for this tooling migration.
Static Docker checks do not establish image or deployment qualification.

Initial deep verification environments exceeded Windows MAX_PATH while extracting
the upstream CEL wheel's bundled Bazel files. Shallow disposable environments
resolved the installation failure without changing the artifact or operating system.
The first unit harness omitted a parent temporary directory, causing fixture errors
in delivery, domain and indexer; it also did not propagate aggregate test failures.
Recorded individual check exits, rather than that wrapper's successful exit, identify
those initial failures. The harness was corrected and affected suites rerun. API's
initial first CEL compilation exceeded the existing one-second deadline; its complete
serialized rerun passed without changing the deadline or production code. Earlier
logs remain available; the table reports final results.

## Tested source combination and delivery status

These HEADs identify the starting repositories; the verified working trees include
this migration and pre-existing local work, so HEAD alone does not reproduce them.

| Repository | HEAD |
|---|---|
| keep-api | `e2de23d0b70bb52f8180eebb396d927567705eaf` |
| keep-contracts | `eb555e8bd307c2dbc3f20a9134351696a4f5eba2` |
| keep-correlation | `7a408f990021c2f4531c1cd90c52641c80e1bc90` |
| keep-delivery | `ee37546e771e435d1f07c2b88857f548dd05fbb0` |
| keep-domain | `b8461620318c5dff6197094cfc9ea967c7073b01` |
| keep-indexer | `a56e94c8f07c6d98342c809e05ebc8b5ac780538` |
| keep-ingestion | `3515ac96bd1958483b5583d59afc13d9782e6111` |
| keep-migrations | `016d0f554de570a96e4da1cf054c9b1c70b5c768` |
| keep-processor | `c4bb2225505453b7fd6bfea20283355da79958cf` |
| keep-design | `99c5def93c761386e7d5161c44f482ae4cbd7539` |

No dependency revisions or release manifest pins were advanced: runtime package
requirements and schema bytes did not change, and no new commits were published.
Publication must capture the actual resulting repository revisions together;
existing release pins remain the immutable authority until a coordinated update.
No database migration, merge, push or deployment was executed.

For development, follow [workspace setup](../docs/repository-workspace.md).
Reverting the task's packaging, workflow, script and documentation changes restores
the old toolchain; this migration changes no application data or SQL checksums.

## Authorized source publication

The user subsequently requested committing and pushing the Poetry migration to
the existing `codex/scalability-hardening` branches. The workspace manifest records
the resulting nine sibling revisions; delivery identifies itself as `self`.
Runtime dependency Git pins remain unchanged because the package code, public
runtime requirements and schema bytes did not change. The new Poetry locks are
the dependency authority at each published source revision.

The published change excludes pre-existing application changes, deployment overlays,
draft guides, and agent setup changes. Those working files retain the Poetry
adaptations locally. The self-contained Poetry mirror helper and its regression
tests are included. Published containers retain their original infrastructure
configuration with the Poetry installation/build changes applied; existing local
CA, mirror, and runtime-library adjustments remain local.

Additional checks ran on clean snapshots of the exact staged trees: all nine native
`poetry check --lock --strict` checks passed; six `docker buildx build --check`
checks passed; all nine runtime requirement sets matched their original metadata;
design documentation and delivery documentation passed (106 documents, 566 source
entries, REV-01..REV-38); and the published mirror-helper suite passed 19 tests.
The earlier 813-pass matrix describes the full local working trees, including
unrelated work, and does not serve as an exact-commit test result. Full image
builds, hosted CI, live integration, merges and deployment remain unexecuted.

Publication revisions are recorded in [the workspace manifest](../workspace/manifest.json).
