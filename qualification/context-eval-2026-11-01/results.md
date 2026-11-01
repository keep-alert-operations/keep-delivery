# Paired context-layer evaluation — 2026-11-01

This is Phase 2 of the [context redesign](../context-topics-2026-11-01.md): the route
layer (design `c946e86`, delivery `9926f3f`) against the topic-and-intent layer
(design `ba757d2`, delivery `3978eb2`) on 12 planning-only tasks, one run per
condition and task. It shows where the two layers differed on these tasks. It is
**not** a statistically supported claim that either layer is better.

## Result

| Condition | Rubric points | Change tasks T1–T6 | Questions T7–T12 | Tokens | Tool calls | Agent time |
|---|---|---|---|---|---|---|
| Route layer | 51 / 56 | 27 / 29 | 24 / 27 | 1,462,702 | 344 | 40.2 min |
| Topic-and-intent layer | 49.5 / 56 | 25 / 29 | 24.5 / 27 | 1,509,674 | 295 | 37.6 min |

- **Quality:** effectively level. The topic layer lost 2 points on change tasks
  (T1 −0.5, T4 −1, T6 −0.5) and gained 0.5 on questions (T7 +0.5, T11 +0.5,
  T9 −0.5). Every per-task difference is one or two rubric items, the size of
  run-to-run variation seen on 2026-10-24.
- **Plan acceptance criterion 10** ("no rubric loss on the six change tasks") is
  **not met** on this single run.
- **Cost:** the topic layer used about 3% more tokens, 14% fewer tool calls and 6%
  less agent time. The extra tokens are consistent with the always-loaded index and
  `--content` output; fewer calls with plans that carry the text.
- **Questions:** both layers reached the governing sections for all six question
  and investigation tasks. Route-layer agents chose routes by meaning from the map
  and did as well. The 2026-10-31 finding that four of eight questions went
  unresolved measured `--task` keyword matching, not agents choosing by meaning.
- **Shared misses:** neither condition stated the operator credential rules after a
  restore (T10 d, 0 both); neither gave the full same-tenant linking rules (T12 d,
  0.5 both); both proposed read-time maintenance release as an option behind a user
  decision (T3 c, 0.5 both).

## Method

- **Snapshot:** `git archive` of each repository's committed head into two scratch
  folders with neutral names (`ws1`, `ws2`), assigned to the conditions by a random
  draw. Nine repositories were identical in both; design and delivery differed only
  by the context-layer commits. Each folder's root `AGENTS.md` was copied from its
  delivery `workspace/AGENTS.md`, with a `CLAUDE.md` importing it.
- **Agents:** 24 general-purpose subagents on the same model (Claude Opus 5.5),
  read-only, three at a time, conditions interleaved. Prompts were identical apart
  from the folder path; the template is in [cases.json](cases.json).
- **Tasks and rubrics:** [cases.json](cases.json). T1–T6 and their rubrics are reused
  unchanged from [2026-10-24](../context-eval-2026-10-24/cases.json). T7–T12
  (search consistency, retention, Grafana tenancy, restore fence, operator Kong
  cleanup, cross-tenant linking) were written from the cited sources before any
  trial ran, in areas not used by the routing fixtures or the walkthrough.
- **Scoring:** met = 1, partial = 0.5, missed = 0. A separate subagent scored all 24
  reports under random labels, told only each label's task, and checked cited
  sources where a score depended on them. The mapping was applied afterwards.
  Token, tool and time figures are the harness's subagent usage reports.

## Trials

| Task | Routes | Topics | Notes from the scorer |
|---|---|---|---|
| T1 incident inactivity | 4/4 (105,057 tok, 23 tools, 153 s) | 3.5/4 (111,162, 19, 153) | Topics: verification-plan note named only as something to edit, not as making expiry future qualification. |
| T2 incident tags | 6/6 (157,871, 34, 269) | 6/6 (135,526, 32, 237) | Both complete. |
| T3 maintenance release | 3.5/4 (119,914, 30, 211) | 3.5/4 (114,103, 15, 154) | Both recommend read-time release behind a REV-56 decision. |
| T4 directory propagation | 4.5/5 (94,707, 18, 122) | 3.5/5 (106,324, 18, 134) | Topics: no bootstrap/restore-integrity gates or revocation rules; REV-35 verification paragraph only implied. |
| T5 live batching | 4.5/5 (118,846, 29, 239) | 4.5/5 (118,554, 23, 196) | Both: REV-36 "no live worker" implied, not stated. |
| T6 incident note count | 4.5/5 (136,558, 37, 257) | 4/5 (161,081, 36, 263) | Topics: did not consider whether note commands publish the existing invalidation. |
| T7 search consistency | 4.5/5 (126,010, 25, 182) | 5/5 (124,693, 23, 172) | Routes: rebuild mentioned without the alias switch. |
| T8 retention | 4/4 (117,552, 30, 197) | 4/4 (128,739, 26, 177) | Both found REV-34 deferral. |
| T9 Grafana tenancy | 5/5 (120,511, 29, 189) | 4.5/5 (120,619, 24, 162) | Topics: catch-all branch named without saying every alert still reaches it. |
| T10 restore fence | 3.5/5 (112,808, 21, 147) | 3.5/5 (119,724, 24, 187) | Both missed the credential rules. |
| T11 operator Kong cleanup | 3.5/4 (142,108, 35, 239) | 4/4 (145,827, 30, 243) | Routes: facts stated but not named as a spec conflict. |
| T12 cross-tenant link | 3.5/4 (110,760, 33, 208) | 3.5/4 (123,322, 25, 177) | Both missed no-stealing, the 100-alert limit and 409. |

## Findings to act on

- **Fixed in this change:** the `search` topic pointed at
  `appchi-indexer/src/appchi/*.py`; the indexer code is under `src/appchi/search/`
  (T7 topics). Running the T4 request through `plan --task` afterwards showed "LDAP
  group" selecting `correlation` through its generic alias "grouping"; the alias is
  removed and a routing case guards it.
- **A possible cause of the change-task dip, untested:** the topic layer ranked
  sources that only a required topic brings in as `background`, after the requested
  topics' sources. Worker scope arrives that way through `behavior`, and T4's missed
  item sits in worker scope section 4. One run cannot show this. **Changed after the
  trial (user-approved):** `scope` sources from required topics now rank right
  after the requested topics' own sources and are never labelled background. This
  ranking has not been trialled.
- **Documentation drift found by trial agents (not changed here):** the indexer's
  search-rebuild runbook gives a `max_document_bytes` range of 1024–1048576 while
  `rebuild.py` allows 104857600, and `docs/search-implementation.md` still refers to
  `backend/appchi/search` (T7); delivery's `private-qualification.md` step 4 still
  says to run the directory worker, contradicting REV-35 (T4); the incident list's
  "Linked alerts" column still reads fields the list endpoint does not return (T6,
  both conditions, as on 2026-10-24).
- **Design questions raised again:** operator operations 21 says Kong cleanup "may
  finish later" although worker scope 3 leaves nothing to finish it (T11, both).

## Limits

One run per cell and twelve tasks cannot separate a layer effect from run-to-run
variance; repeat each cell at least three times before claiming an effect.

- **Possible unblinding:** the scorer did not see condition labels, but reports name
  the tools they used ("routes", "topics", `--intent`), which can reveal the setup.
- **Old rubrics in the snapshots:** the 2026-10-24 rubrics for T1–T6 were present in
  both snapshots. One agent noticed them and said it did not use them.
- **Startup instructions:** every subagent also received this session's
  startup instruction file, which describes the route layer. The prompt told agents
  to follow the snapshot's `AGENTS.md`; the topic-condition agents therefore saw
  conflicting guidance.
- **Tuning:** the topic layer's aliases were tuned on routing fixtures that include
  questions close to T1 and T2, not on T7–T12.
- **Planning only:** no defects, human corrections or acceptance of implemented
  changes were measured.
