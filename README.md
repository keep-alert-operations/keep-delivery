# keep-delivery

Workspace setup, local dependencies, deployments, release inventory and system verification.

See [system design](https://github.com/keep-alert-operations/keep-design), [workspace setup](https://github.com/keep-alert-operations/keep-delivery) and [source provenance](SOURCE.md).

## Workspace

For daily product use, read the [illustrated Keep client guide](docs/client-guide/README.md).
It covers alerts, incidents, shared views, maintenance and correlation, with
step-by-step workflows and an offline reading edition.

Start with [workspace setup](docs/repository-workspace.md). Open the parent directory
containing all eleven Git checkouts. Canonical system instructions are in
[workspace/AGENTS.md](workspace/AGENTS.md).

For any agent request, use the [context guide](../keep-design/context/README.md) and
[context map](../keep-design/context/map.md) to pick an intent and topics. From
delivery, `python scripts/context.py plan --intent question --topic incidents --content`
prints what to do and the authoritative sections; `python scripts/context.py find "words"`
ranks matching headings when no topic fits.
Run `python qualification/check_context.py` after changing the catalog or its sources.

For an isolated private-cluster deployment with supplied Elasticsearch, Kafka and
Grafana, start with the [OpenShift deployment and load-testing guide](docs/runbooks/openshift-load-testing.md).
It includes image transfer, service configuration/resources, dedicated Authelia +
OpenLDAP test identities, staged startup and executable load-generator assets.

## Development

Start onboarding with the [interactive developer guide](docs/developer-guide/index.html).
It covers architecture, source processing and replay, domain behavior, local setup,
tests, security and operations, with explorable scenarios and knowledge checks.

Use Python 3.13, Poetry 2.3.1 and the committed `poetry.lock`. Configure Git authentication for the
private organization repositories before resolving internal dependencies.

```powershell
poetry check --lock
poetry sync --all-extras
poetry run ruff check src tests
poetry run pytest
poetry build --format wheel
```

Shared `keep` modules use namespace packaging; each module has exactly one owning
distribution. Dependencies resolve immutable Git revisions in Python dependency metadata and `poetry.lock`.
For coordinated local edits use the delivery workspace setup, which installs local
packages without changing release dependency pins.
