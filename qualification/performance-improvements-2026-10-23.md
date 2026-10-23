# Performance improvements: implementation and verification (2026-10-23)

This records the implementation of the remaining performance items from the
2026-10-20 code review of `main`. That review followed the
[scalability work](scalability-implementation-2026-10-06.md). The figures below
are local diagnostics from one Windows workstation. They are not capacity,
release or production qualification, and **no DEL gate is closed** by this work.

## Outcome

- **Process:** each review item was implemented separately as one PR per affected
  repository. A fresh-context reviewer reviewed each PR until a round found
  nothing more to fix.
- **Merged:** every PR is merged into `main` with merge commits, and consumers are
  repinned to their producers' merge commits. CI passed on every final `main`.
- **Integration suite:** the delivery integration suite on the merged revisions
  passed 449 cases, failed 2 known-flaky cases and skipped 13.
- **Not run:** the full local-stack smoke baseline (S1–S4), the real-backend
  browser journeys and the before/after load comparison (see
  [Not verified](#not-verified)).

The design-level decisions and follow-ups that remain are listed under
[Still open](#still-open).

## Results by item

Item numbers are the review's. All PRs are in `appchi-alert-operations`, so
`domain#7` means `appchi-alert-operations/appchi-domain#7`.

| Item | What changed | Measured effect | PRs |
|---|---|---|---|
| #6 | The worker loop reuses clock-health evidence while the file is unchanged, for at most 1 s; age is checked on every call. The processor keys changed events by the applied tenant. | — | domain#7, processor#3, design#4 |
| #7 | Changed-event offsets are committed every 100 inputs / 100 ms, only after durable writes. The correlation gate is read in the main transaction. | 1 transaction per event, down from 3 | domain#8, correlation#3, delivery#8, design#5 |
| #8 | `worker_turn` reads rule health in its existing revision query. | No per-rule reloads | domain#3 |
| #9 | Evidence-checkpoint and incident-link reads are batched. | 2 statements, down from 6 (test case) | domain#4 |
| Context lock | The repeated context lock is skipped when inline correlation is on. | — | domain#5 |
| #10 | CEL cache evictions are logged, and reuse and sizing are documented. The cold-compile process decision is recorded as open. | — | domain#6, design#3 |
| #11 | All busy partitions' search hints share one bulk of up to 500 documents. A failed shared call isolates its partitions until their failed inputs are handled. | 393–486 hints/s, up from 1.8–12.4 (4 partitions) | domain#12, indexer#7, delivery#11, design#8 |
| #12 | `view_generation` is bumped only when the watermark advances, or when an audit repairs documents. | Idle browsers stop reloading | indexer#4, delivery#4 |
| #13 | Reconciliation cursor pages use index order, and delta pages use the 0049 index. | Idle delta page: 3 buffers, down from 50,300 (skewed tenant) | indexer#3, delivery#3 |
| #14 | A page's notes stream through one named cursor. Caller-ordered pages walk notes per alert through the index. | One cursor per page | indexer#5, delivery#7 |
| #15 | The indexer checks for due tenants before taking the writer lease. During a Kafka outage it waits until the next due time. | Idle: 3.3 statements/s, down from 82.8. Outage: 3.6, down from 231 | indexer#6, domain#10, delivery#9, design#7 |
| #16 | A feed page uses 4 transactions and 2 authorizations, down from 10 and 7. Migration 0052 adds a partial index for the retirement probe. | Probe: 0.07 ms, down from about 390 ms | api#5, migrations#3, delivery#6 |
| #17 | Idle SSE wake-ups are served from cache with no transaction. Re-authorization runs every 15 s and before every change, within the 30 s bound of section 20. | 5 transactions per 10 s for 1 or 3 tabs, down from 15 for 3 | api#3 |
| #18 | Live-hint pacing applies only when idle. Hint deletes are batched and bounded to 200 ms. | 200 hints in 0.42 s, down from at least 10 s | api#4, delivery#5 |
| #19 | The ingest body is parsed once and reused. HTTP clock checks share one evidence cache per app. | Single body: about 113 µs, down from 270. Batch of 100: about 4.6 ms, down from 9.9 | domain#9, ingestion#5, design#9 |
| #20 | Kafka delivery acks resolve from one poll thread. | Single-alert median: about 5 ms, down from 31 | ingestion#3 |
| #21 | Ingestion and the API serve HTTP with httptools/uvloop. A bounded protocol keeps h11's request checks and closes a request-smuggling path found in review. | — | ingestion#4, api#6 |
| #22 | Live invalidations merge in a trailing window with a cap, and the user's own changes refresh at once. | — | frontend#2, design#6 |
| `read_committed` | Hot unscoped units take the restore guard inside their transaction, and the test wrappers forward keywords. | 1 transaction per unit, down from 3 | domain#11, processor#4, delivery#10 |

The measurements came from private test containers on a loaded workstation, and
some use in-memory feeds. Treat them as indicative only. Design choices made for
these items are recorded in the
[design register](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md)
through the design PRs listed above.

## Defects found in review

- **#21 (high):** request smuggling through an unsupported `Upgrade` on the
  verified Kong gateway connection, which could have forged consumer, operator
  and tenant identity. It was fixed and covered by deterministic tests. The same
  security rounds also fixed:
  - unbounded trailers;
  - 400 responses written over pending responses;
  - Transfer-Encoding checks that were looser than h11's.
- **#11 (high):** on the old domain pin, the indexer would have run with hints
  disabled while reporting ready. It now refuses to start.
- **#17:** a lock-order inversion that could exhaust the API pool.
- **#12:** audit repairs were never announced to browsers.
- **#13:** a delta-page regression on skewed tenants.
- **#14:** a full sort on the hint path.
- **#18:** a deadlock with the rebuild and bootstrap writers.
- **#15:** an empty scan counted as due on every turn.
- **#16, #20:** tests that passed on Windows but failed on Linux CI.

## Merged revisions and rollout

Stacked PRs were retargeted to `main` after their base merged. delivery#8 was
folded into the delivery repin PR, delivery#14. The `main` revisions after the
performance merges are:

| Repository | `main` |
|---|---|
| appchi-domain | `d4fb748` |
| appchi-design | `60debd7` |
| appchi-migrations | `e34fdea` |
| appchi-api | `08bf1ec` |
| appchi-indexer | `7639693` |
| appchi-ingestion | `c019991` |
| appchi-processor | `476320f` |
| appchi-correlation | `5cad5e3` |
| appchi-frontend | `311ed32` |
| appchi-delivery | `a3c49b4` |

Unrelated work merged afterwards: appchi-contracts#2 (`5420c8f`),
appchi-frontend#4 (`9ed49b0`), and appchi-delivery#15, which pins the workspace
manifest to both.

**Rollout constraint:** migration 0052 must run in the stopped-writer window. It
blocks `projection_work` inserts while its index builds.

## Post-merge verification

These checks ran on the merged revisions above. No load testing was run, at the
user's request.

- **Install: passed.** `scripts/workspace.py install` reported
  `poetry check --lock` clean and ran `npm ci` for a frontend worktree at
  `311ed32`. Installed service revisions matched `workspace/manifest.json`,
  except contracts: the lock pins `eb555e8`, while the manifest then named
  `870e075`. That manifest entry was later updated by appchi-delivery#15.
- **Delivery integration suite: 449 passed, 2 failed, 13 skipped (580.7 s).**
  - **Environment:** private PostgreSQL 18.2, Elasticsearch 9.0.0 and Kafka
    4.0.0 containers.
  - **Database:** a fresh database migrated through `appchi.migrate.main`,
    including 0052.
  - **Kafka readiness:** confirmed before tests with an idempotent produce, a
    transactional init/produce/commit and a consumer-group offset commit.
  - **Failure 1:** `test_alert_detail_consistency.py::test_detail_uses_one_snapshot_during_concurrent_disable`
    missed the native CEL compile deadline (1059 ms against 1 s). This is the
    known busy-host flake, and the test passes alone.
  - **Failure 2:** `test_scalability_pipeline.py::test_page_statements_keep_rows_and_use_the_bounding_index`
    got a Bitmap Index Scan plus Sort instead of
    `alert_identity_projection_changes`. It failed in the full run, then passed
    alone four times in a row. It appears to depend on shared-database planner
    statistics. The cause was not determined, and the test was not compared
    against pre-merge code.
  - **Skips:** 2 Kong tests (no local Kong) and 11 explicit REV-34
    deferred-component skips.
- **Frontend (appchi-frontend#4 merge head):**
  - generation, formatting, typecheck and production build passed;
  - the controlled Playwright suite on Chrome passed 190 cases, failed 2 and
    skipped 7;
  - `workspace.spec.ts:321` timed out under load and passed three reruns;
  - `scalability-users.spec.ts:150` fails intermittently on `311ed32` as well,
    so it predates that merge.

## Not verified

- **The S1–S4 smoke baseline on the full local stack, and the real-backend
  browser journeys.** The local-stack scripts require PowerShell 7, which was not
  installed on the workstation.
- **#22:** the real-backend journey and the 5 s p99 visibility qualification.
- **The before/after load comparison** (the
  [pipeline load comparison](../docs/runbooks/development.md#pipeline-load-comparison-beforeafter)).
  It was stopped at the user's request. The
  [baseline on the pre-merge packages](pipeline-load-baseline-2026-10-22.md) is
  the "before" side, and no "after" run exists.
  `qualification/scalability_load.py` still needs a `consume()` phase for a
  repeatable comparison.
- **Native-CEL suites** pass reliably only when run alone. The 1 s compile
  deadline misses under host load (see #10).
- **#21:** the llhttp edge cases were taken from llhttp 9.x behaviour, not from
  the vendored parser source. Feed tests cover them.

## Still open

- **#10 decision:** keep spawning a CEL compiler process per cache miss, or move
  to a persistent worker or a Linux `forkserver`. The recommendation in the
  design register is `forkserver`, measured on the Linux image first.
- **Design-level limits, measured on the pre-merge baseline:**
  - correlation serializes per tenant at about 1 event/s for a single tenant;
  - partition confirmation costs 4–10%, because its batch window never forms.
    Tuning the window matters more than the per-row flag.
- **Grafana webhooks** still parse twice. A single parse needs a change to
  `map_webhook` in appchi-contracts.
- **`read_committed` follow-up:** the indexer `reconciliation.py` units were left
  out and need a `fence`/`check_writer` review first.
- **Flaky tests:**
  - `test_alert_ownership[False-firing-False-firing]`;
  - the bounding-index plan assertion and the CEL deadline case above;
  - frontend `scalability-users.spec.ts:150`.
- **Test hygiene:**
  - stale integration tests;
  - shared-database isolation, including `legacy_pipeline_fenced`;
  - the missing `0032` fixture;
  - Poetry and `httptools` absent from the local delivery virtual environment;
  - a PowerShell 7 guard;
  - the test-binding documentation;
  - six search tests that set `elasticsearch_url` after app startup and fail
    with `search_not_ready`.
- **Older committed qualification evidence** contains a workstation user-profile
  path.
