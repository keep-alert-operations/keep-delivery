# Context layer implementation evidence — 2026-10-12

The user requested implementation of task-based reading plans and a linked map
for the Keep workspace. This record covers local context tooling, navigation
and documentation checks. It does not qualify an application release or measure
live-agent effectiveness.

## Result and ownership

- Design owns the [catalog](../../keep-design/context/task-map.json),
  [guide](../../keep-design/context/README.md), 37 deterministic routing cases,
  [map](../../keep-design/context/map.md) and 23 generated route pages.
- Delivery owns the standard-library [reader](../scripts/context.py),
  [context gate](check_context.py), tests and workspace installation guidance.
- All eleven repository entrypoints now select sources according to task impact.
  The canonical and installed workspace entrypoints match. Existing detailed
  policies are preserved in [agent-workflows.md](../docs/agent-workflows.md).
- The root entrypoint decreased from 355 lines / 2,910 whitespace-separated words
  to 123 lines / 1,017 words. This is a file-size observation, not a measured token,
  latency or task-quality improvement.

Acceptance criteria: preserve authoritative requirements and scope corrections;
distinguish required and conditional dependencies; expose unmatched requests;
read exact sections with provenance; reject stale or broken navigation; preserve
existing local work; support ordinary Markdown navigation without new services.
These local structural and tooling criteria passed the checks below. Semantic
coverage remains an agent responsibility and requires real-task evaluation.

## Implemented behavior

`list`, `plan`, `read`, `render` and `check` operate on the current workspace files.
Explicit routes select their required closure, while other automatic matches stay
visible as suggestions. Without explicit routes, keywords and paths select
candidates. An unmatched task returns status `unresolved` and exit code 2.
Plans identify source authority, reasons, line ranges and SHA-256 values. Excerpts
include document preambles, selected sections and their subordinate headings.
Commands in catalog verification guidance are data and are never executed.

Generated pages retain normal relative links for Obsidian. The route text labels
required and conditional edges; the ordinary graph does not enforce these labels.
The renderer checks ownership and paths before writing and refuses unowned or
stale generated files. Source/route errors and generated drift fail validation.

Delivery's existing documentation gate now checks root/canonical instruction
consistency, the Claude import and the full context gate. Standalone design checks
explicitly defer context links to absent declared sibling repositories. Missing
local files and missing targets in a present sibling still fail.

## Executed verification

Windows, Python 3.13, existing delivery environment; no dependencies installed.
Commands below run from delivery unless stated otherwise.

| Check | Outcome |
|---|---|
| `python scripts/workspace.py bootstrap` | Passed; preserved existing checkouts/branches and installed matching entrypoints without overwriting prior backups. |
| `.venv/Scripts/python.exe -m pytest tests/unit/test_context.py tests/unit/test_context_integration.py tests/unit/test_workspace_instructions.py -q -p no:cacheprovider --basetemp .local/context-layer/pytest-final-2` | 45 passed, 1 skipped. |
| `.venv/Scripts/python.exe -m ruff check scripts/context.py qualification/check_context.py qualification/verify_documents.py tests/unit/test_context.py tests/unit/test_context_integration.py ../keep-design/scripts/check_docs.py` | Passed. |
| `python scripts/context.py render` then `python qualification/check_context.py` | Passed: 72 source records, 23 routes, 37 routing cases, 24 generated documents. |
| `python qualification/verify_documents.py` | Passed document targets, fences, 566 historical source-inventory destinations, REV-01 through REV-38, installed entrypoints and context gate. |
| `python scripts/check_docs.py` in design | Passed local links, fences and decision identifiers. |
| `python scripts/context.py plan --path keep-contracts/schemas/product.openapi.json --format json` | Selects behavior and HTTP contract routes; does not select native/event contracts. |
| Isolated integration smoke under ignored `.local/context-layer` | Incident mutations select behavior/incidents/shared commands/mutations with 8 source records; repeated render is byte-stable; unmatched request remains unresolved. |
| Copied standalone design fixture | Passes with 21 explicitly deferred sibling targets; broken design target and missing target in an existing sibling each fail. |
| `git diff --check` in all eleven repositories | Passed. |

The skipped test requires native Windows symlink creation privileges. The
path-containment guard is exercised separately with a mocked resolved escape;
absolute, traversal, undeclared-repository and generated-output protections pass.

Independent review found and resolved an incorrect generated read command, Windows
redirected-output encoding, and HTTP schema paths being routed to native/Kafka
context. Regression coverage executes the emitted read command from delivery,
preserves Unicode output, and checks actual Product API, merged HTTP, ingestion,
native and event schema/model paths. Remaining dependency expansion distinguishes
HTTP-generated artifacts from native/events.

## Source combination and preservation

All repositories remained on `codex/scalability-hardening`. Checks exercised the
working trees, including pre-existing authorized modifications and this change;
these HEAD values alone do not reproduce those working trees. Initial and final
status inventories and original entrypoint copies were kept in ignored local
task evidence. Existing staged, unstaged and untracked work was preserved.

| Repository | HEAD at verification |
|---|---|
| keep-api | `b865b54135bd07b6e8e97c9ff43308600a3aac26` |
| keep-contracts | `a8bebad730846362aa23a08ebc17a5e6605a72ba` |
| keep-correlation | `7abeacd388d3300ba3d7f3fd14878835ff45ea41` |
| keep-delivery | `8f0ae7a16c4fdbec27abb27dbbf54824aadbfd20` |
| keep-design | `48f8431ec3a3828e8e4b5fbd3627dbe2f8df8643` |
| keep-domain | `da0b94ade43b9bf9a999e6653c8fecce725b6a09` |
| keep-frontend | `5c1bfd052042b0df8538cd73f2395e2fd7f70a82` |
| keep-indexer | `fc63b612e086a45603c1080d004cf94a98c3e136` |
| keep-ingestion | `269da924f5c33686df60c19a1341753f36a85a0c` |
| keep-migrations | `7e6ffd4dba92559ddc3ad2eeebf6ef1106229e0b` |
| keep-processor | `f23b3275afd48e2a8371932fbf1951513d05963b` |

## Delivery prerequisites and limits

At initial local verification, no commits, pushes or deployment had been
performed. The publication prerequisite was to commit the companion design
change and advance `workspace/manifest.json` to its available immutable revision.
The initial pin was the old design HEAD above and did not contain the catalog.
The authorized push preparation below resolves this prerequisite. Fresh pinned CI must
then pass on the published combination; local dirty-tree results do not establish
that outcome. No nonexistent commit or floating dependency pin was introduced.

No product behavior, database schema, service package or deployment configuration
was changed by this task. Service, database, browser and load suites were not run
for the navigation/tooling change. Obsidian itself was not launched; ordinary
links and generated graph structure were checked. Paired live-agent trials for
requirement coverage, unnecessary reading, tokens, time and human corrections
remain future evaluation work, not claims supported by these fixtures.

## Authorized push preparation — 2026-10-12

The user subsequently requested pushing the changes. Only context-layer files and
related bootstrap prerequisites were staged. Unrelated product code, design
corrections, deployment work and guides remain local; mixed README/register files
were staged as reviewed blobs without replacing their working copies.

The recovery route's reference to an unpublished cluster-initialization acceptance
heading was removed. It retains the committed Reliability and Worker scope
correction sections, along with normal exploration obligations. The new Checkout
and dependencies heading is included with context setup documentation. No product
requirements were changed to make the catalog pass.

All eleven exact staged trees were exported to an isolated workspace. On those
exports, the focused suite passed 45 tests with one native Windows symlink skip;
Ruff passed, design checks passed, and delivery's full document/context gate passed
134 Markdown files, 566 inventory entries, 72 sources, 23 routes, 37 routing cases
and 24 generated documents. This verifies the publishable content without relying
on unrelated dirty files.

Design and the nine guidance-only commits were pushed before delivery. The
workspace manifest now pins those immutable commits. Application/package payloads
did not change in these sibling commits, so existing package dependency locks
remain valid; only workspace documentation/tooling source pins advanced. Delivery
is identified by its own commit, with `self` retained in the manifest.

| Repository | Pushed prerequisite commit |
|---|---|
| keep-api | `e8ca51dbbe083cdfc358ff3f3200766297d8db66` |
| keep-contracts | `870e075432ef9eaba4cfdb89a8e6e4c7b5e3c49a` |
| keep-correlation | `50518169792148fc705f9ecb2561cab5480767a5` |
| keep-design | `69e86646827eab2047f68dd0c5bc212b3af5e6ac` |
| keep-domain | `04c09764a6d73f01dc4685bd6fdf270a019567de` |
| keep-frontend | `2ac606e65bb5b29f945820a46a32915817e8c187` |
| keep-indexer | `4781b9860f1c8650d23665aeac0789546534eaa2` |
| keep-ingestion | `ea168498f65727ba96a44e10da63c66a367f6255` |
| keep-migrations | `7ebd18ff25ee10fb894a86fac52910ec27f96b3b` |
| keep-processor | `d808b92fa63c87c3ebd8991b769f725782927de3` |

Remote CI completion and production qualification are separate from the successful
local checks and branch pushes; neither is asserted by this record.
