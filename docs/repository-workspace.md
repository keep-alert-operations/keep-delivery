# Develop from the system workspace

Clone `appchi-delivery` into an empty parent directory, then run from that clone:

```powershell
python scripts/workspace.py bootstrap
python scripts/workspace.py install --editable
.venv/Scripts/python.exe scripts/workspace.py test
.venv/Scripts/python.exe qualification/check_contracts.py
.venv/Scripts/python.exe qualification/verify_boundaries.py
python scripts/workspace.py inventory --output .local/workspace-inventory.json
```

On Linux/macOS use `.venv/bin/python`. Install Python 3.13, uv 0.10.8, Node.js
22.17 or newer, npm and Git first. Authenticate Git for the private organization
repositories using your approved credential helper. Open the **parent directory**
in your agent/editor; `AGENTS.md` there covers the complete system.

Bootstrap clones the ten sibling repositories at the manifest's exact commits.
Existing checkouts and branches are preserved; inspect inventory mismatches before
releasing. The parent is not a Git repository. Commit, review and push changes in
each affected child separately. Use `codex/` for new work branches.

`install` resolves the committed delivery lock. `--editable` then installs local
Python sources from every sibling into delivery's environment without changing
release pins. Use this environment for coordinated development and system tests.
Each Python repository also supports `uv sync --locked --all-extras` and its own
unit suite. Without `--editable`, delivery uses the pinned Git packages even when
sibling source files have been edited. Frontend always uses its committed npm lock.

After changing an HTTP producer, regenerate contracts with
`qualification/generate_contracts.py`. Commit contracts, advance affected Python
Git dependency references and locks, then advance frontend's `@appchi/contracts`
Git dependency and run `npm run generate`. Update delivery's manifest and lock
after consumer checks. Generated documents are reviewed compatibility changes.

## Run and verify locally

The existing local setup commands now run from delivery. `scripts/dev.ps1` starts
the selected API, worker or sibling frontend; `scripts/local-runtime.ps1` coordinates
the isolated local dependency stack. These scripts retain the existing local ports
and Compose project identity. Stop the monorepo-managed local stack before starting
this workspace's stack, so both sets of processes do not consume or manage the same
development resources. Bootstrap does not start services or copy local credentials.
Generate a fresh development environment with the documented local identity setup.

Run `scripts/verify.ps1 -Suite Unit` for all owned Python unit suites. The database
suite requires `APPCHI_TEST_DATABASE_URL` pointing to an isolated disposable test
database. Kafka, Elasticsearch, Kong, real browser and private environment checks
retain their explicit service bindings; skipped checks are not qualification.

## Build and CI

Every application repository and migrations owns its Dockerfile. Build from that
repository's root. Private Git dependencies use a BuildKit secret:

```sh
docker build --secret id=github_token,env=APPCHI_REPOSITORIES_TOKEN -t appchi-api:local .
```

The token needs read access to the selected private repositories; it is not written
into image layers or dependency URLs. Do not commit it. Frontend builds also require
the trusted `APPCHI_BACKEND_URL` build argument. Record immutable resulting image
digests, SBOM and scan evidence before a release; development base-image tags are
not a company runtime qualification.

GitHub Actions workflows requiring sibling code use the repository/organization
secret `APPCHI_REPOSITORIES_TOKEN` with read-only contents access to the Appchi
repositories. Supply this through the organization's approved credentials process.
Default repository-scoped `GITHUB_TOKEN` cannot read sibling private repositories.
Bootstrap publishes source and workflows; it does not establish CI credentials or
claim that remote checks, image builds or production qualification succeeded.

`qualification/release_artifacts.py` and `legacy/` preserve historical monorepo
evidence tooling. They validate that historical format, not a multi-repository
release. Use `qualification/workspace_release.py` for the new source/image inventory.
