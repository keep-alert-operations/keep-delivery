# Context layer follow-ups — 2026-10-24

After a review of the context layer, the user asked to merge the design CI catalog
check and then implement the other suggestions: mark superseded spec sections,
steer agents to explicit route selection, pilot requirement-ID traceability in
tests, and run a paired evaluation. The user chose inline status notes, a
convention plus incident pilot, and planning-only trials. No product behavior,
schema, contract or deployment changed.

## Revisions

| Repository | Branch / commit | State |
|---|---|---|
| appchi-design | `9390d25`, merge of design#10 (`6474d81`) | Merged after both CI checks passed |
| appchi-design | `codex/current-scope-notes`: `697baec`, `ec40ad4` on `9390d25` | Local, not pushed |
| appchi-delivery | `codex/context-layer-followups`: `707f3e6`, `99ef85c` and this record, on `b66649f` | Local, not pushed |

`workspace/manifest.json` pins appchi-design to `9390d25`. The scope notes branch
is not pinned until it is merged.

## Changes

- **Design CI catalog check (design#10, merged):** `scripts/check_docs.py` fails
  when a catalog section in a design-owned source is renamed, duplicated or
  missing, using a copy of the resolver's heading parser.
- **Parser parity:** `qualification/check_context.py` compiles design's mirrored
  `heading_titles` without running the design script and fails when it reads any
  design document's headings differently from `context.py`.
- **Line endings:** bootstrap and `verify_documents.py` compare entrypoints with
  CRLF normalized. A Windows checkout with `core.autocrlf=true` had reported the
  root `CLAUDE.md` as drifted when only its line endings differed.
- **Current scope notes (design branch):** 59 one-sentence notes under affected
  headings in 15 specification and verification documents. Each names the REV
  decision, links the governing section and says what remains normative.
  Headings, anchors, issue IDs and text are unchanged. The preset and dashboard
  preambles gain the missing REV-34 correction. The design register records the
  presentation as assistant-selected.
- **Route selection guidance:** workspace AGENTS.md and the design context guide
  say to choose routes by their conditions and pass them with `plan --route`.
  `--task` and `--path` matching only suggests candidates.
- **Requirement-ID convention:** a test of specified behavior names its IDs and
  design sections in its docstring, such as
  `Requirements: I5; incident operations 14.` **Assistant-selected:** the
  user-chosen pilot named appchi-api and appchi-domain, but neither has incident
  tests. Incident behavior is tested in this repository, so the pilot covers the 22
  incident tests in the listing, note-revision, retirement and inactivity
  integration suites.
- **Evaluation:** [paired planning-only trials](context-eval-2026-10-24/results.md).

## Notes review

Three read-only mapping agents proposed about 60 notes. The coordinating agent
checked each against worker scope, rewrote several and applied 59. Changes from
the proposals:

- One note pointed REV-36 at operator provisioning; it now links worker scope 5.
- The reliability alerting note no longer drops the operator persistent-failure
  signals, which still apply to failed request-owned operations.
- Two marginal proposals were not applied: reliability 20 ("scheduler lag") and
  the system design "Human reads and writes" section.

The applying script checked that each heading occurs exactly once and that every
link resolves to an existing heading anchor.

## Verification

Windows 11, Python 3.13; delivery's existing `.venv`; workspace checkouts on the
branches above.

| Check | Result |
|---|---|
| design#10 CI | 2 checks passed before merge |
| Design `python scripts/check_docs.py` (scope notes branch) | Passed: 254 design-owned catalog sections |
| Standalone design copy: rename, duplicate, fence or delete a referenced heading or source | Each fails with source and heading; the unchanged copy passes with 21 deferred sibling links; the previous script passed the renamed copy |
| `python qualification/check_context.py` | Passed: 72 sources, 23 routes, 37 cases, 24 generated pages; design heading parser matches |
| `python qualification/verify_documents.py` | Passed: 174 documents, 566 inventory entries, REV-01..REV-38, no entrypoint errors |
| Focused pytest: `test_check_context.py`, `test_context.py`, `test_context_integration.py`, `test_workspace_instructions.py` | 55 passed, 1 skipped (existing Windows symlink case) |
| Full `pytest tests/unit` | 241 passed, 66 skipped, 6 failed |
| Ruff on changed Python and `tests` | Passed |
| Tagged integration suites, `pytest --collect-only` | 36 tests collected |
| `git diff --check` (design, delivery) | Passed |

The 6 unit failures are `ModuleNotFoundError` for `httptools` and `poetry`, which
the local `.venv` lacks. They are in `test_kafka_first_admission.py` and
`test_prepare_internal_build.py`, which this change does not touch. CI installs
both with `poetry sync --all-extras`.

## Not verified

- The tagged incident integration tests were collected, not executed: they need
  the isolated PostgreSQL stack, and only docstrings changed.
- No smoke baseline, browser journey or service suite ran; none of the changed
  files is application code.
- CI has not run on either local branch.
- The evaluation's limits are listed in its record. It does not establish that
  the layer improves or worsens agent outcomes.

## Open design questions found

These came from the mapping and evaluation agents. They were not resolved here.

- Operator operations 21 says remaining Kong cleanup after deletion "may finish
  later", but worker scope 3 leaves no background orchestration to finish it.
- Reliability 8 gives operator mutations a 100-second handler budget. Worker scope
  3 selects 90 seconds of request-owned work within a 100-second gateway allowance.
- Maintenance 18's "Updating suppression" label assumes fan-out; without fan-out
  it may only mean search lag.
- The verification plan's REV-34/REV-35 scope paragraphs sit inside its first
  section, so resolver reads of other sections omit them.
- Register row A3 states five-minute enforcement without the REV-35 qualification.
- The retained `inactivity.py` checks the fenced legacy `ingest_items` table.
- The incident list's "Linked alerts" column reads totals the list response does
  not return.
