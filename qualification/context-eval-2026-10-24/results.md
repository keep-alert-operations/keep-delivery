# Paired context-layer evaluation — 2026-10-24

This is the first paired trial of the context layer that the
[context guide](../../../keep-design/context/README.md#maintain-and-evaluate-the-layer)
asks for before claiming a benefit. It measures planning only: each trial agent
read the workspace and wrote a plan; nothing was implemented. With one run per
condition and task, it shows where the conditions differed on these six tasks. It
is **not** a statistically supported claim that the layer helps or costs more.

## Result

| Condition | Rubric points | Tokens | Tool calls | Agent time |
|---|---|---|---|---|
| With the context layer | 28.5 / 29 | 927,755 | 212 | 30.1 min |
| Without it | 27 / 29 | 876,957 | 205 | 29.9 min |

- **Scope corrections:** both conditions surfaced every recorded decision in all
  three scope traps (REV-34 inactivity, REV-34 maintenance release, REV-35 directory
  propagation) and the recorded live merge window. Spec preambles name those
  corrections whether or not routing is used, so routing added little there.
- **Cross-cutting obligations:** the 2-point gap came from two tasks. Without the
  layer, the incident-tags plan never mentioned live invalidation (with the layer,
  a route source led to system completion section 18). The directory plan also
  omitted the bootstrap and restore-integrity gates.
- **Cost:** with the layer the agents used about 6% more tokens, and a similar
  number of tool calls and time. No efficiency gain is shown.
- **Not one-sided:** without the layer, the note-count plan found the REV-12/13
  exact-match screen requirement, which the with-layer plan missed. The rubric did
  not include it.

## Method

- **Snapshot:** `git archive` of each repository's `origin/main` into two scratch
  copies. The without copy had `keep-design/context/`, `scripts/context.py` and
  `qualification/check_context.py` removed, and its trial prompt said the routing
  layer was unavailable. Repository AGENTS.md files still mention the map in
  both copies.
- **Agents:** 12 general-purpose subagents on the same model (Claude Opus 5.5),
  each given one task and one copy, read-only. They ran three at a time, with
  conditions interleaved. Every prompt was identical apart from the workspace path
  and the one-paragraph description of the available tooling.
- **Tasks and rubrics:** [cases.json](cases.json). Rubrics were written from the
  cited sources before any trial ran. One case (removing the deadline labels) was
  replaced before running because main already contained that change.
- **Scoring:** met = 1, partial = 0.5, missed = 0, scored by the coordinating
  agent from each final report. Scoring was **not blind**: the scorer knew each
  trial's condition. Token, tool and time figures are the harness's subagent
  usage reports.

| Repository | Revision |
|---|---|
| keep-api | `08bf1ec` |
| keep-contracts | `5420c8f` |
| keep-correlation | `5cad5e3` |
| keep-delivery | `b66649f` |
| keep-design | `60debd7` |
| keep-domain | `d4fb748` |
| keep-frontend | `f1e7938` |
| keep-indexer | `7639693` |
| keep-ingestion | `c019991` |
| keep-migrations | `e34fdea` |
| keep-processor | `476320f` |

The design snapshot predates the Current scope notes, so this measures the layer
as it was before those notes existed.

## Trials

| Task | Condition | Rubric | Tokens | Tools | Seconds | Notes |
|---|---|---|---|---|---|---|
| T1 incident inactivity | with | 3.5/4 (c partial) | 130,836 | 26 | 320 | Found REV-34 and asked keep-or-reverse; said stored fields are kept but not that incidents resolve by explicit command. |
| T1 incident inactivity | without | 3.5/4 (c partial) | 123,937 | 25 | 342 | Same findings. Also found that the retained `inactivity.py` checks the fenced legacy `ingest_items` table. |
| T2 incident tags | with | 6/6 | 166,520 | 43 | 535 | Treated tags as new scope needing a REV entry; named the outbox and live generation (system completion 18). |
| T2 incident tags | without | 5/6 (e missed) | 147,245 | 33 | 269 | Same scope finding; no live invalidation, only the post-mutation refetch from incident operations 15. |
| T3 maintenance release | with | 4/4 | 128,253 | 25 | 211 | Ruled out read-time release; reported skipping two required sources the plan listed. |
| T3 maintenance release | without | 4/4 | 157,912 | 34 | 249 | Proposed raising read-time release, which the rule it quoted forbids. |
| T4 directory propagation | with | 5/5 | 114,701 | 22 | 178 | Found the stale register row A3. Reported that `read --source verify-identity` returned a 2-line preamble and missed the plan's REV-35 paragraph. |
| T4 directory propagation | without | 4.5/5 (d partial) | 131,960 | 27 | 263 | Same REV-35 finding and A3; no bootstrap or restore-integrity gates. |
| T5 live batching | with | 5/5 | 180,310 | 43 | 247 | Found the recorded 500 ms / 1 s / 250 ms window and that the frontend already implements it. |
| T5 live batching | without | 5/5 | 155,807 | 43 | 327 | Same, plus analysis of server-side hint spacing. |
| T6 incident note count | with | 5/5 | 207,135 | 53 | 315 | Additive OpenAPI change; named the REV-36 invalidation; found the list's "Linked alerts" column likely empty. |
| T6 incident note count | without | 5/5 | 160,096 | 43 | 343 | Same; also found the REV-12/13 exact-match screen constraint. |

## Findings to act on

- **Verification-plan structure:** its REV-34/REV-35 scope paragraphs sit inside
  the first section, not the preamble, so a section read through the resolver
  misses them (T4 with). The per-section Current scope notes added to design on
  2026-10-24 cover the affected acceptance sections. Moving the paragraphs into
  the preamble is a separate structural change.
- **Retained inactivity engine:** `keep-domain/src/keep/inactivity.py` checks
  the legacy `ingest_items` table that migration 0045 fences, so its pending-source
  check would need redesign if inactivity sweeps are ever reinstated (T1 without).
- **Incident list:** the table's "Linked alerts" column reads totals that the list
  response does not return (T6, both conditions). Not verified at runtime.
- **Register row A3** still states the five-minute enforcement without the REV-35
  qualification (T4, both conditions).

## Limits

One run per cell, a non-blind scorer and six tasks cannot separate a layer effect
from run-to-run variance. Trial agents shared a session-level instruction file
that mentions the context map, and could see that the without copy lacked it.
Planning reports are not implemented changes: no defects, human corrections or
task acceptance were measured. To claim an effect, repeat each cell at least three
times, score blind (conditions hidden from the scorer), and include implementation
tasks.
