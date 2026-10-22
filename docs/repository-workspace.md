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

On Linux/macOS use `.venv/bin/python`. Install Python 3.13, Poetry 2.3.1, Node.js
22.17 or newer, npm and Git first. Authenticate Git for the private organization
repositories using your approved credential helper. Open the **parent directory**
in your agent/editor; `AGENTS.md` there covers the complete system.

## Codex and Claude Code guidance

Open the parent workspace in either agent. Both use the shared root `AGENTS.md`;
`CLAUDE.md` imports it with `@AGENTS.md`. The shared guidance routes agents to each
affected repository's scoped `AGENTS.md`, README, CI and existing specifications.
Global skills and personal agent settings remain outside these repositories.

Maintain the canonical [AGENTS.md](../workspace/AGENTS.md) and
[CLAUDE.md](../workspace/CLAUDE.md) in delivery's `workspace/` directory, then run
`python scripts/workspace.py bootstrap` from delivery to install both at the root.
References inside the canonical guidance are relative to that installed root.
Bootstrap saves a differing root file as `<name>.before-bootstrap`; if that backup
already exists, it stops before replacing either instruction file. Reconcile root
edits into the canonical files before rerunning, and retain any backup still needed.

When commands, ownership or requirements change, update their existing authority
and the corresponding guidance references. The concise entrypoint links the
[detailed engineering policies](agent-workflows.md), which preserve the original
workspace requirements and remain binding when applicable. Preserve manual guidance
when maintaining the bounded `eng-project-setup` section. Validate with
`python qualification/verify_documents.py` from delivery and
`python scripts/check_docs.py` from design; check that both installed files match
their canonical copies and that the Claude import resolves to the root AGENTS.md.
The context catalog is project navigation; it adds no model configuration, plugin,
personal runtime or second requirements authority.

Both agents must follow the [repeatable verification workflow](runbooks/development.md#repeatable-verification-workflow).
It records the user-agreed critical smoke journeys, startup/readiness, isolated
fixtures, assertions, coverage gaps and cleanup. File/import validation establishes
discoverability, not proof that a new Codex or Claude session followed the guidance.
Keep actual command results in dated delivery evidence; the setup validation is
recorded in [the 2026-10-12 setup report](../qualification/agent-verification-setup-2026-10-12.md).

For future updates, ask: "Use eng-project-setup to refresh this workspace's agent
guidance from current requirements and CI; preserve existing instructions and keep
the delivery canonical copies and installed root files consistent."

## Context routing and Obsidian

Design owns [task-map.json](../../appchi-design/context/task-map.json), the
[context guide](../../appchi-design/context/README.md) and representative routing
fixtures. Delivery owns the standard-library reader and validator. From delivery:

```powershell
python scripts/context.py list
python scripts/context.py plan --task "Add incident assignment" --format json
python scripts/context.py plan --route incidents --route incident-mutations
python scripts/context.py read --source SOURCE_ID
python scripts/context.py render
python qualification/check_context.py
```

Use a source ID returned by the plan. Explicit routes select their required
dependency closure; otherwise task keywords and changed paths suggest candidate
routes. Conditional edges describe when to expand. Neither matching nor a passing
fixture establishes that the plan is complete. Inspect affected callers and scoped
instructions, and resolve missing authority or conflicting requirements before acting.
An unmatched task returns a nonzero status and requires further exploration.

The reader emits exact sections with their introductory scope corrections, line
ranges and current SHA-256 hashes. A saved plan is a snapshot: regenerate it after
source changes or a handoff. Reading plans and source hashes do not prove that an
agent read, understood or complied with a requirement. Keep decisions, source
revisions, progress and unresolved questions in the existing task checkpoint.

`render` generates [map.md](../../appchi-design/context/map.md) and linked route
pages from the catalog. To browse them in Obsidian, open the **parent workspace**
as a vault and use the local graph on an incident route. Ordinary Markdown links
connect routes, prerequisite routes, conditional routes and authoritative sources.
The page text labels mandatory versus conditional edges; Obsidian's ordinary graph
does not enforce those semantics. No Obsidian plugin is required by the agent tools.
Keep any personal vault settings local and do not move the repositories.

After changing routes, source headings or filenames, render again and run the full
context check. It checks source targets, exact headings, required dependencies,
fixtures and generated-file drift. `verify_documents.py` also checks installed
entrypoint consistency and invokes this gate. Standalone design checks defer links
to absent sibling checkouts; the delivery workspace check requires them all.
The fixtures test deterministic selection, not end-to-end agent effectiveness or
measured context savings. Use paired real-task trials before making those claims.

Context changes across design and delivery must be reviewed together. Once the
design changes are committed and available, update delivery's workspace manifest
to that immutable revision before publishing the delivery change. Fresh CI uses
those pinned sources; a passing check against dirty local siblings is not release
qualification. Update other affected repository instructions in the same coordinated
handoff, without committing local editable overrides.

## Checkout and dependencies

Bootstrap clones the ten sibling repositories at the manifest's exact commits.
Existing checkouts and branches are preserved; inspect inventory mismatches before
releasing. The parent is not a Git repository. Commit, review and push changes in
each affected child separately. Use `codex/` for new work branches.

`install` resolves the committed delivery lock. `--editable` then installs local
Python sources from every sibling into delivery's environment without changing
release pins. Use this environment for coordinated development and system tests.
Each Python repository also supports `poetry check --lock` followed by
`poetry sync --all-extras` and its own
unit suite. Without `--editable`, delivery uses the pinned Git packages even when
sibling source files have been edited. Frontend always uses its committed npm lock.

Committed `poetry.toml` files keep environments in `.venv` and use the system Git
client, so existing Git credential helpers and scoped mirror rewrites still apply.
Install Poetry 2.3.1 as a separate tool; the application environments keep pip
available for the explicit `--no-deps --editable` development overrides. The helper
first validates the lock with `poetry check --lock`, then runs `poetry sync --all-extras`.
Sync restores the release pins and removes editable overrides; rerun
`python scripts/workspace.py install --editable` when restoring local development.
The overrides use `--no-deps`. A runtime requirement that a sibling added after
delivery's last repin is therefore installed separately, at the exact version and
hashes in that sibling's committed `poetry.lock`; install stops if no sibling lock
pins it. The helper finds the environment with `poetry env info --path`, so
`POETRY_VIRTUALENVS_IN_PROJECT=false` with a short `POETRY_VIRTUALENVS_PATH` also
works for deep worktree paths. An environment created before the Poetry migration
has neither pip nor the development group's Poetry: the helper's sync adds Poetry
and `ensurepip` restores pip before the overrides.
Use delivery's `.venv` Python directly after overrides for coordinated checks and
service launchers. `poetry run` selects an environment without synchronizing it,
but a later `poetry sync` or `poetry install` can replace the local sources.

Delivery's development group also includes pinned Poetry 2.3.1 for the native
lock metadata API used by the private build helper and its regression tests.
This does not replace the separate Poetry bootstrap tool, and runtime images
install only their main dependencies.

Dependency maintenance uses the committed Poetry lock. A separate optional
`lock-constraints` Poetry group preserves the previous exact version constraints,
including transitive packages, without adding them to published PEP 621 runtime
requirements. Poetry resolves this group together with the application graph;
ordinary sync installs the main and development groups, leaving dormant constraint
packages uninstalled. Do not use `--all-groups` for normal setup or runtime images.
Change constraints deliberately, run `poetry lock`, review the version/source/hash
diff and run `poetry check --lock` before syncing. Hatchling remains the build
backend, and Poetry is the dependency and command tool.

On Windows, keep checkout and temporary environment paths short: the upstream
native CEL wheel includes deep Bazel directories. The migration's first deep
test environments exceeded Windows MAX_PATH; shallow disposable environments
installed the same locked wheel successfully. See the
[Poetry migration evidence](../qualification/poetry-migration-2026-10-12.md).

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

The secret must be available to `appchi-api`, `appchi-ingestion`,
`appchi-processor`, `appchi-correlation`, `appchi-indexer`, `appchi-domain`,
`appchi-frontend` and `appchi-delivery`. Delivery bootstraps the pinned workspace,
so the credential must cover all eleven private workspace repositories. Use a
dedicated credential with read-only Contents permission. Configure its value
directly in Actions secrets; do not paste it into chat, commit it, or substitute
a developer's general-purpose GitHub login token. After configuring access,
rerun the failed jobs and inspect their results: the credential preflight failure
means the later checks have not executed on GitHub.

The organization was verified on GitHub Free on 2026-10-01. On that plan,
organization secrets are unavailable to private repositories, so configure this
as a **repository secret in each of the eight consumers**. An organization secret
alone will not unblock these jobs. See
[GitHub's secret availability rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets#creating-secrets-for-an-organization).

The dedicated `Appchi CI repository reader` credential was configured in all eight
repositories on 2026-10-01 with read-only Contents/Metadata access to the eleven
selected workspace repositories. It expires on **2026-12-30**. Rotate it before
expiry by updating `APPCHI_REPOSITORIES_TOKEN` in all eight repositories and
verifying their workflows; secret values must remain outside source control.

Frontend checkout uses `persist-credentials: false`. Otherwise the checkout
action's repository-scoped GitHub authorization header overrides the credential
helper when npm fetches the private contracts repository from the checkout.

The retained `appchi` monorepo's `application-checks` workflow was disabled on
2026-10-01 at the user's request. Its backend/frontend jobs are retired following
the repository split. Keep the independent repositories' owned checks active;
historical failed monorepo runs remain in GitHub. See the
[repository CI audit](../qualification/ci-repair-2026-10-01.md).

`qualification/release_artifacts.py` and `legacy/` preserve historical monorepo
evidence tooling. They validate that historical format, not a multi-repository
release. Use `qualification/workspace_release.py` for the new source/image inventory.
