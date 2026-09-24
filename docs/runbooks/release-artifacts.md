# Release inventory and bundle checks

The application CI workflow now supplies real PostgreSQL 18.2 and Elasticsearch
9.0.0 using the existing isolated development selections. Backend integration
tests receive both endpoints. CI also checks OpenAPI/event generation, regenerates
frontend types and fails on a diff, then runs frontend formatting, type checking,
build and browser checks. JUnit results, the source inventory and browser reports
are saved as CI artifacts. These workflow changes have not been run on GitHub;
they do not qualify company service versions or a release.

Generate an inventory from the exact committed source used to build images:

```sh
python qualification/release_artifacts.py inventory --root . \
  --source-revision '<full actual source commit>' \
  --output qualification/release-inventory.json
python qualification/release_artifacts.py verify-inventory \
  qualification/release-inventory.json --root .
```

The inventory hashes every finalized SQL migration, API/event contract, generated
frontend type file, dependency manifest/lock, selected runtime profile source,
deployment template and runbook. Versions come from those actual files: application,
OpenAPI/events, CEL compiler/profile/cost model, Unicode and tzdata. It rejects
missing inputs, duplicate migration-order prefixes and a tzdata lock/profile
mismatch. Migration gaps are allowed; an absent number is not invented. Drafts
under `.local` are excluded. Regenerate after any included file changes.

Copy `qualification/release-manifest.example.json` into the private release
bundle. Its null values deliberately fail validation. Fill them only from real
build and qualification outputs. Keep paths relative to the bundle root and add
the actual SHA-256 digest for each referenced artifact. Include the source tree
inputs named by the inventory, with their original bytes. Do not put secrets in
the manifest, configuration diff or attached reports.

Required bundle contents are:

- The verified inventory and matching full source revision.
- Backend/frontend image references pinned by digest, with SBOM, security scan
  and build-provenance artifacts for each image.
- Service manifest, qualified capacity profile, configuration diff, correctness
  and qualification results, security review, rollout/rollback rehearsal and
  support-handoff evidence.
- An explicit supported schema range, matching event versions, supported CEL
  profile, compatible rollback image pair and compatibility evidence.

```sh
python qualification/release_artifacts.py verify-manifest \
  '<private release manifest path>' --root '<release bundle root>'
```

The checker rejects missing, empty or altered artifacts; incomplete evidence
categories; mutable image tags; inconsistent source/profile/schema references;
duplicate JSON properties; and paths or symlinks outside the bundle. It neither
fetches external evidence nor builds, installs, signs, publishes or deploys.
Successful validation proves bundle completeness and byte integrity only. A
well-formed failed scan or fabricated report still requires rejection during
release review: this checker does not interpret scan findings, authenticate
attestations, prove supported company versions or confer production approval.

Build images once and promote the same digests after the required reviews.
Apply migrations through the dedicated migration job, qualify bounded backfills
and drain incompatible writers before enabling new behavior. Rollback uses the
declared compatible image/schema/event/profile combination; never rewind the
database as an application rollback shortcut.

Remaining gates include actual CI execution, real-service compatibility outside
the local PostgreSQL/ES selections, authenticated end-to-end browser coverage,
accessibility, dependency/container scans and SBOM production, immutable image
build/provenance verification, sustained capacity/recovery qualification, staffed
support and private OpenShift rollout/rollback rehearsals. This checker does not
close those delivery records.

Local checker evidence: 25 offline cases passed. One symlink-escape case was
skipped because this Windows host did not permit unprivileged symlink creation;
the case remains enabled for Linux CI. Tests use explicitly synthetic bundles and
cover missing/changed inputs, migration conflicts, unpinned images, compatibility
mismatches, duplicate JSON keys and incomplete evidence. No image or service
qualification results were manufactured.
