# Context topics and intents — 2026-11-01

The user asked that any request — a question, a change, an investigation, a review
or anything else — reach the relevant Keep context easily and know what to do
with it. On 2026-10-31 eight natural questions run through the route layer gave
four unresolved plans and two partial ones. The user approved a redesign plan as
written: topics instead of change routes, intents that say what to do, a generated
index in the workspace AGENTS.md, easier matching with a `find` fallback, new
fixtures, then a paired trial (Phase 2) and derived traceability (Phase 3, separate
approval). This record covers Phase 1 only. No product behavior, schema, contract
or deployment changed. The design decision is recorded in the
[design register](../../keep-design/decisions/design-register.md#context-topics-and-intents--2026-11-01).

## Revisions

| Repository | Branch | Base | State |
|---|---|---|---|
| keep-design | `codex/context-topics`: `ba757d2`, then `f72c299` (trial fixes) | `c946e86` (REV-55 head; in `main` through merge `e6e581e`) | design#21 merged as `d0ac727` (same tree as `f72c299`), at the user's request |
| keep-delivery | `codex/context-topics`: the commits carrying this record | `6255670` (`main` after REV-55 merged) | delivery#29, merged at the user's request |

**Assistant-selected:** the plan said to branch from `main` after REV-55 merged.
REV-55 was still open and touches the register, `check_docs.py`,
`verify_documents.py`, the status record and the manifest, so the branches were
first stacked on it. On 2026-11-02 the user asked for REV-55 to be merged; after it
merged, delivery was rebased onto `main`, and design's base is already in `main`.
`workspace/manifest.json` pins design#21's merge commit `d0ac727`.

## Changes

- **Catalog v2 (design):** `context/task-map.json` holds 78 sources, 27 topics in
  seven areas and five intents. The 23 route IDs are kept as subject topics with a
  `covers` line, two to four `ask_like` examples, `aliases`, `orientation`,
  `evidence` and `related` entries; `requirements-and-decisions`, `alert-pipeline`,
  `reliability` and `local-development` are new. New sources: architecture overview,
  specification map, "Read the design first", reliability failure sections,
  Kafka-first runbook and development runbook. `scope_sections` adds
  the verification plan's first section (which holds its REV-34/35 paragraphs) and
  "Worker scope correction" to every partial read of that document, closing the
  open question recorded on 2026-10-24.
- **Intents:** `question`, `change`, `review`, `investigate` and `operate`, each
  with the topic fields it shows, extra sources (`investigate` adds worker scope;
  `operate` adds the authorization and release policies), steps, a finish line and
  eng-* skill pointers. Unclear requests start as `question`.
- **Resolver (delivery):** `scripts/context.py` reads only schema 2. `plan` takes
  `--intent`, `--topic` (with `--route` as an alias) and `--content`; it infers the
  intent from cue words when none is given and reports why. Matching stems whole
  words conservatively, never by prefix, and auto-selects at most three topics;
  path matches always select. Sources are ranked: orientation, the requested
  topics' own sources, `scope` sources from required topics, intent additions,
  then `background` from topics that are only required. Unresolved plans list
  ranked `find` candidates. `find`
  ranks headings across design specifications, verification, decisions,
  architecture and delivery docs and status, labelling cited headings with their
  source's authority and topics. `render` writes the map, 27 topic pages and the
  index block between markers in `workspace/AGENTS.md`; `check` fails on drift, a
  missing marker pair or an index longer than 40 lines.
- **Entry:** the workspace AGENTS.md "Start with any request" section and a
  31-line generated index (intents plus one row per topic). The root copy was
  installed by copying the canonical file: the root matched the committed canonical
  copy, and the existing `AGENTS.md.before-bootstrap` at the workspace root would
  make `bootstrap` refuse, so it was left untouched.
- **Guides:** design context guide rewritten; design README, delivery README,
  `docs/repository-workspace.md` and `docs/agent-workflows.md` updated; the
  2026-10-12 register entry is marked superseded in part. Design `check_docs.py`
  also checks `scope_sections` headings. `context/routes/` (23 generated pages) is
  removed in favor of `context/topics/`. Service repositories' AGENTS.md pointers
  ("Choose task-relevant sources from the context map using intent, paths and
  behavioral impact") remain accurate and are unchanged.

Deviations from the plan, all assistant-selected: 27 topics rather than 28 (the
plan's table listed four new topics, not five); code locations stay in the existing
`repositories` and `path_globs` fields rather than a new `code` field; the review
cue `diff` became `this diff`/`the diff` because "differ" stems to "diff"; and the
root AGENTS.md was installed for local verification before merge (restore it with
`git -C keep-delivery show main:workspace/AGENTS.md`).

## Verification

Windows 11, Python 3.13; delivery's existing `.venv`; both repositories on
`codex/context-topics` with uncommitted changes at the time of each run.

| Check | Result |
|---|---|
| `python scripts/context.py check` | Passed: 78 sources, 27 topics, 5 intents, 63 cases (62 at the trial snapshot), 29 generated documents |
| `python qualification/check_context.py` | Passed; design heading parser matches context.py |
| `python qualification/verify_documents.py` | Passed: 184 documents, 562 entries, REV-01..REV-55, no entrypoint errors |
| Design `python scripts/check_docs.py` | Passed: 265 design-owned catalog and scope sections |
| `pytest tests/unit/test_context.py test_check_context.py test_context_integration.py test_workspace_instructions.py` | 84 passed, 1 skipped (existing Windows symlink case) |
| Full `pytest tests/unit` | 284 passed, 65 skipped, 1 failed: `test_local_v2_setup.py::test_send_verifies_kafka_admission_search_and_tenant_isolation`, which fails identically on the unmodified REV-55 base `9926f3f` (checked in a temporary worktree); this change does not touch it |
| Ruff on `scripts/context.py`, `tests/unit/test_context.py`, `qualification/check_context.py` | Passed |
| `git diff --check` (design, delivery) | Passed |

Routing cases: the 37 converted cases pass with unchanged include/exclude
expectations. The 25 new cases cover the eight 2026-10-31 questions (each now
selects its expected topics without explicit topics), architecture, decision
lookup and local testing questions, four investigations, a review, three
operations, three changes with over-selection exclusions, an unclear request, an
unresolved request whose `find` candidates include reliability section 15, and an
explicit-topic closure. A `--content` plan for "What does the processor do on
replay?" is 1,106 lines; nothing is truncated.

## Fresh-agent walkthrough

One general-purpose subagent (Claude Opus 5.5, read-only) handled four requests —
a Kafka-outage question, an "incidents never close" report, an incident-tags
feature and a processor-retry review — using only the workspace instructions. It
used 23 tool calls in total: three shared orientation calls, then 3–6 per request.

- It chose `question`, `investigate`, `change` and `review` with fitting topics.
  The investigate step "check current worker scope first" answered the second
  request almost at once: inactivity resolution is deferred by REV-34, not broken.
- **Limitation:** a subagent receives the instructions loaded when this session
  started, so it saw the old route wording and reported AGENTS.md as stale. The
  canonical and installed files already held the new steps. It still found
  `--intent` and `--topic` from `map.md` and `--help` within its three orientation
  calls. A new top-level session loads the new file; that was not exercised.
- `--task` for the tags feature picked `incidents` and `incident-search` only,
  as designed; the agent added the remaining topics by meaning.

Fixed after the walkthrough: pipeline section 8 appeared twice when
`alert-pipeline` and `reliability` were both selected (the separate diagnostics
source was removed); plans listed prerequisite sources before the ones that answer
the request (now ranked, with a `background` role); and review plans labelled
their checks "Change checks" (now "Verification checks").

Not changed here: AGENTS.md names "the existing checkpoint mechanism" without
saying it is the engctl task record, and agent-workflows "Read the design first"
still mentions the monorepo extraction. The agent also surfaced three design
questions, recorded for the user rather than resolved:

- Incident operations 4 (REV-46) lists source-inactivity expiry as a resolving
  trigger, but that expiry is deferred, so the clause cannot currently fire.
- System completion 29 still describes "ten consecutive failures block the
  tenant" and a retry endpoint, with only a preamble note that the REV-33 design
  fences them; which rule governs processor retries is unclear from the section.
- Pipeline 3 says malformed records and unknown proof fence the partition; the
  processor fences only `source_coordinate_conflict` (`kafka_source.py:544`) and
  retries other proof codes. Whether malformed records are caught earlier was not
  checked.

## Not verified

- Phase 2 ran on 2026-11-01 ([results](context-eval-2026-11-01/results.md)): route
  layer 51/56, topic layer 49.5/56 (change tasks 27 vs 25 of 29; questions 24 vs
  24.5 of 27), with 3% more tokens, 14% fewer tool calls and 6% less time for the
  topic layer. One run per cell; the plan's "no loss on change tasks" criterion is
  not met on that run, and no efficiency or quality improvement is claimed. The
  trial found the stale `search` path glob, fixed afterwards. Also afterwards, with
  the user's approval, worker-scope (`scope`) sources from required topics were
  ranked ahead of other background sources; that ranking has not been trialled.
  Checking it on the T4 request showed "LDAP group" selecting `correlation` through
  the generic alias "grouping"; the alias was removed and a regression case added.
- CI ran on design#21 (passed); the delivery pull request's CI is in that pull request.
- Matching and intent inference are deterministic heuristics tested by fixtures;
  they do not establish that an agent chooses well. The walkthrough is one run.
