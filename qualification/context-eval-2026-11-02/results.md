# Context layer comparison and catalog fixes — 2026-11-02

The user asked whether the context layer gets agents to the relevant context as well
as it could, asked for candidate improvements to be tested by sub-agents, then for a
live trial of the recommended one before deciding. This record covers both studies
and the four small fixes the user approved afterwards. Cases and rubrics:
[cases.json](cases.json). The design choice is recorded in the
[design register](../../../keep-design/decisions/design-register.md#context-catalog-fixes--2026-11-02).

## Snapshot

Design `1f9672e`, delivery `c986ebe`; API `4ce81f1`, contracts `ee9ae71`, correlation
`19aa5c6`, domain `1dee02d`, frontend `45a6f04`, indexer `8f7159c`, ingestion `f252e50`,
migrations `b5d9775`, processor `7565435`. Prototypes ran in `git archive` copies; no
repository changed during the studies.

## Retrieval comparison

**Metric:** for each request, the text an agent would read; a gold section counts when
at least half of its substantive lines appear verbatim; tokens are characters / 4.
Dev set: the 12 tasks of [2026-11-01](../context-eval-2026-11-01/cases.json) (29 rubric
source sections, seen by prototype builders). Held-out set: 14 new requests (21 gold
sections) written before any prototype ran. "Agent picks" means a fresh agent, who had
not seen the documents or gold, chose topics or sections without reading section text.

| Condition | Dev recall | Dev tokens | Held-out recall | Held-out tokens |
|---|---|---|---|---|
| Current, `plan --task … --content` | 21/29 | 27.5k | 9/21 (3 empty) | 16.6k |
| Current, agent picks topics | 24/29 | 32.7k | 14/21 | 33.6k |
| A: BM25 section retrieval, 12k budget | 22/29 | 11.6k | 17/21 | 11.5k |
| B: every spec section owned, agent picks topics, full content | 27/29 | 34.2k | 19/21 | 36.4k |
| B with a 12k budget, agent picks topics + task text | 21/29 | 14.5k | 17/21 | 14.4k |
| C: heading outline, agent picks ≤ 6 sections | 16/29 | 4.7k (+11k once) | 20/21 | 4.3k (+11k once) |

72 of 261 level-2 specification sections belonged to no topic, so even a perfect topic
choice reached only 15/21 held-out gold sections. Topic bundles alone delivered the
cross-cutting obligations (shared commands, generated contracts); outline and lexical
retrieval missed them.

## Live paired trial

Today's layer against B + C merged (complete topics with no default budget, plus an
outline and `read PATH#ANCHOR`). Two neutral snapshots of all eleven repositories,
randomly assigned, with the earlier trial rubrics removed; 24 read-only general-purpose
subagents (Claude Opus 5.5), three at a time, conditions interleaved; two blind scorers
each scored six requests, both reports side by side under random labels, checking cited
sources. Requests: T2, T4, T6, T9, T10, T11 from 2026-11-01 and held-out H3, H5, H6, H9,
H10, H14.

| Condition | Points | T tasks | H tasks | Tokens | Tool calls | Agent time |
|---|---|---|---|---|---|---|
| Today's layer | 50.5 / 56 | 26 / 30 | 24.5 / 26 | 1,502,082 | 313 | 52 min |
| B + C | 50.5 / 56 | 26.5 / 30 | 24 / 26 | 1,571,452 | 351 | 68 min |

| Request | Max | Today | B + C |
|---|---|---|---|
| T2 incident tags | 6 | 6 | 6 |
| T4 directory propagation | 5 | 4 | 4 |
| T6 incident note count | 5 | 4 | 4 |
| T9 Grafana tenancy | 5 | 5 | 4.5 |
| T10 restore fence | 5 | 3.5 | 4 |
| T11 operator Kong cleanup | 4 | 3.5 | 4 |
| H3 rule dry run | 4 | 4 | 4 |
| H5 DST window | 4 | 4 | 3.5 |
| H6 latency targets | 5 | 5 | 5 |
| H9 group cardinality | 4 | 3 | 3 |
| H10 audit trail | 4 | 4 | 4 |
| H14 super-admin lockout | 5 | 4.5 | 4.5 |

The retrieval gains did not become better answers. On H6, H9 and H14, whose governing
sections no current topic covers, today's agents scored the same: agents choosing by
meaning searched the specifications for what the plan lacked. Both arms missed the
same details (H9's enabled-rule and CEL budgets, `capacity_profile_invalid` and the
100-event/100 ms yield; T6's untouched event contracts; T10's "evidence is not
proof"), which points at extracting details from long sections rather than finding
them. The 2026-11-01 routes-versus-topics trial also ended level.

## Fixes applied

User-approved; navigation and tooling only. Branch `codex/context-catalog-fixes` in
design and delivery.

1. The `delivery` alias "production" is removed; it stems to "product", so every
   "Product API" request selected delivery and about 15k tokens of its policy.
2. `policy-database`, `policy-delivery`, `policy-dependencies`, `migration-guide` and
   `release-artifacts` list `intents: ["change", "operate"]`; other intents' plans keep
   them as pointers without text. `plan --content` emits each document's preamble and
   shared sections once and names the lines shown above.
3. `check` reports specification sections reached by topics (198/261, counting cited
   subsections and ancestors); `check --unowned` lists the rest without failing.
4. Intent inference ignores failure cues inside suppositions, lets a sentence that
   opens with an instruction decide (catalog `openers`: "plan", "design", "let", "show";
   "show me" is a question), then treats a question sentence as a question, then the
   earliest cue. New cues: "whose"; investigate "users report", "customers report".

Not adopted: BM25 retrieval, the outline and `read PATH#ANCHOR`, complete coverage and
a content budget, because the trial showed no answer-quality gain for them.

## Verification

Windows 11, Python 3.13, both branches with uncommitted changes at the time of each run.

| Check | Result |
|---|---|
| `python scripts/context.py check` | Passed: 78 sources, 27 topics, 5 intents, 71 routing cases (8 new), 29 generated documents unchanged, 198/261 spec sections reached |
| `python qualification/check_context.py` | Passed; design heading parser matches context.py |
| Design `python scripts/check_docs.py` | Passed: 265 design-owned catalog sections |
| `pytest tests/unit/test_context.py test_context_integration.py test_check_context.py test_workspace_instructions.py` | 96 passed, 1 skipped (existing Windows symlink case) |
| Full `pytest tests/unit` (delivery `.venv`) | 297 passed, 65 skipped, 0 failed |
| `python qualification/verify_documents.py` | Passed: 186 documents, 562 entries, REV-01..REV-56 |
| Ruff on `scripts/context.py`, `tests/unit/test_context.py` | Passed |
| Retrieval harness, same cases as above | Recall unchanged (21/29, 9/21; agent-picked 24/29, 14/21); mean tokens 27.5k → 22.9k, 16.6k → 16.2k, 32.7k → 29.6k, 33.6k → 31.5k |
| Intent inference on the 26 study requests | All 26 as expected (about half were wrong before), e.g. "Plan the fix" after a user report → investigate, "Make suppression end …" → change, "If the indexer fails …, how …?" → question |

## Limits

One run per cell; ±1 point is within run-to-run variation, and near-ceiling scores
leave little room to show a difference. Agent time is unreliable (runs shared one
machine). Trial agents also received the session's startup instructions describing
today's layer, and the harness kept no transcripts, so how often B + C agents used the
outline is unknown. Retrieval recall measures delivered sections, not answers. The
held-out gold favors single primary sections; the dev rubrics include cross-cutting
sources. The fixes were not trialled with agents. They change topic selection only
through the removed alias, change the inferred intent (and so the intent's added
sources) only when `--intent` is omitted, and kept recall unchanged on the harness.
