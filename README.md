# appchi-delivery

Workspace setup, local dependencies, deployments, release inventory and system verification.

See [system design](https://github.com/appchi-alert-operations/appchi-design), [workspace setup](https://github.com/appchi-alert-operations/appchi-delivery) and [source provenance](SOURCE.md).

## Workspace

Start with [workspace setup](docs/repository-workspace.md). Open the parent directory
containing all eleven Git checkouts. Canonical system instructions are in
[workspace/AGENTS.md](workspace/AGENTS.md).

For an agent task, use the [context guide](../appchi-design/context/README.md) and
[task map](../appchi-design/context/map.md) to select the relevant sources. From
delivery, `python scripts/context.py plan --route incidents` prints a reading plan;
`python scripts/context.py read --source SOURCE_ID` reads its authoritative sections.
Run `python qualification/check_context.py` after changing the catalog or its sources.

## Development

Use Python 3.13, Poetry 2.3.1 and the committed `poetry.lock`. Configure Git authentication for the
private organization repositories before resolving internal dependencies.

```powershell
poetry check --lock
poetry sync --all-extras
poetry run ruff check src tests
poetry run pytest
poetry build --format wheel
```

Shared `appchi` modules use namespace packaging; each module has exactly one owning
distribution. Dependencies resolve immutable Git revisions in Python dependency metadata and `poetry.lock`.
For coordinated local edits use the delivery workspace setup, which installs local
packages without changing release dependency pins.
