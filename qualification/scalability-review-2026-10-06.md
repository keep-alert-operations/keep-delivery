# Appchi scalability, architecture and code-quality review — 2026-10-06

## Conclusion and scope

**The high-level architecture is defensible, but the current implementation is not ready to be declared capable of the requested combined load.** Several concrete hot paths have avoidable amplification or growth-dependent failure. Fix those before spending effort tuning replicas or conducting full qualification. This assessment does not establish a measured maximum throughput.

User-supplied review target: **5,000 alert deliveries/minute (83.33/second), 100 active users, 50 tenants**. An even distribution is 1.67 deliveries/second per tenant, but all-v1 Global traffic and one dominant tenant must also be tested. User count alone does not define request rate: view type, refresh frequency and tabs matter. Payload size, update/new-row ratio, correlation/maintenance usage, tenant skew and retention horizon remain workload dimensions to measure.

The existing normative design requires 250 deliveries/second sustained and 500/second processing during recovery. The user's review target is recorded here without silently changing those specifications. Existing DEL-01–DEL-08 gates remain open; the prepared private-cluster harness is not executed capacity evidence.

This was a repository-wide architectural review with detailed tracing of admission, source application, correlation, maintenance, search/indexing, authentication, live updates, frontend fetching, shared contracts, migrations and deployment/test tooling. It was not a claim to inspect every line or prove every possible execution path. No application behavior, dependency pins, migration bytes or service configuration were changed. Existing working changes were preserved. This report and its offline reproductions are review evidence, not approved new product decisions.

## Prioritized findings

P1 means address before load qualification or operational reliance. P2 means a material correctness, efficiency or maintainability issue. The identifiers below are review findings, not new REV design decisions.

### SCALE-01 · P1 · Indexing waits for Elasticsearch refresh separately for each document

[Reconciliation](../../appchi-indexer/src/appchi/search/reconciliation.py), lines 110–117, invokes `publish([item])` per document. [ProjectionWriter](../../appchi-indexer/src/appchi/search/writer.py), lines 67–94, performs a visibility search, a bulk request with `refresh="wait_for"`, and another visibility search. It also rewrites unchanged equal-version documents. [Worker runtime](../../appchi-domain/src/appchi/pipeline_runtime.py), lines 52–57, performs reconciliation before each individual Kafka turn.

This serializes normal indexing around refresh latency. With a one-second refresh interval and no intervening forced refresh, the rough service-rate model is around one document per second per worker; processing a reconciliation document and then a hint can require two refresh waits. This is a conditional model, not a benchmark or a claim about the supplied cluster configuration. Elasticsearch explicitly recommends batching instead of serial `wait_for` calls in its [refresh documentation](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/refresh-parameter).

The two-second reconciliation setting is a wait between completed passes, not a two-second whole-tenant visibility guarantee. Watermarks advance after a full retained-identity pass. Retained data therefore increases scan duration even without changed documents.

**Recommendation:** bounded bulk publication, safe skipping of already-proven identical versions, coalescing repeated hints, process-owned ES clients and fair scheduling between hint processing and reconciliation. Preserve retirement/version checks and visibility proof. Adding replicas alone also duplicates work: the scan state lock is released before publication and its compare-and-set check only rejects duplicate results afterward (reconciliation lines 123–159). Introduce bounded page ownership or disjoint assignments.

### SCALE-02 · P1 · One unprojectable alert can starve search progress across tenants

[Reconciliation](../../appchi-indexer/src/appchi/search/reconciliation.py), lines 205–207, selects the oldest tenant. A failure during publication at line 144 leaves `last_page_at` and the scan cursor unchanged. The outer [runtime](../../appchi-domain/src/appchi/pipeline_runtime.py), lines 52–78, retries that work before reaching Kafka hint consumption. A persistently failing oldest tenant can therefore prevent search progress for healthy tenants handled by the worker, and replicas can select the same failing tenant.

There is a reachable payload trigger. Each note may contain 10,000 code points, but current note search tokens accumulate into a document subject to a hardcoded 1 MiB projection cap. The offline reproduction built a projection with **120 notes of 8,999 characters**, yielding **1,297,840 bytes** and `projection_document_capacity` before any ES call. The reproduction verifies the document failure; whole-worker starvation follows from the inspected scheduling control flow.

**Recommendation:** isolate failed alerts/tenants with explicit degraded status and bounded retry scheduling while continuing healthy work. Reconcile supported aggregate document size with note semantics. Do not advance the affected tenant's watermark past unproven work or silently drop searchable content.

### SCALE-03 · P1 · Ordinary browser refreshes generate ten full searches and retain unused snapshots

[AlertFeed](../../appchi-frontend/src/components/alerts.tsx), lines 128–154, requests its main feed and invokes [quick-view counts](../../appchi-frontend/src/components/alert-table.tsx), lines 85–117, for eight separate feed queries plus a suppressed-inclusive query in the normal default view. Count requests use `limit=1` and discard their continuation cursors.

[FeedReader](../../appchi-domain/src/appchi/search/reader.py), lines 165–168 and 232–236, opens a five-minute Elasticsearch point-in-time snapshot (PIT) and keeps it open when more results exist. Discarded count cursors and replaced main-feed first pages leave their PITs until expiry. The ordinary GET feed lacks the replacement/disposal mechanism available in preview flows. The [route](../../appchi-api/src/appchi/search_routes.py), around line 80, also creates a fresh ES client per request and traverses repeated authorization, watermark and retention transactions.

Illustrative demand: **100 alert tabs refreshing once every two seconds × 10 searches = 500 search requests/second**. The nonempty first-page path uses ten DB transaction contexts, implying about 5,000 contexts/second in that scenario. With enough matching rows, discarded snapshots could accumulate toward 150,000 PIT openings over five minutes before limits/failures intervene. These are source-derived demand calculations, not observed successful throughput; actual refresh cadence and result distributions determine the workload.

**Recommendation:** one batched quick-count operation with immediate PIT cleanup, explicit ownership/disposal of replaced feed snapshots, reusable clients and bounded refresh coalescing. Reuse the existing dashboard-count batching approach. Preserve exact counts and authorization checks.

### SCALE-04 · P1 · API session/live paths block the event loop on synchronous work

[Session endpoints](../../appchi-api/src/appchi/identity/session.py), including `current_session` at line 197, are async but execute synchronous PostgreSQL operations. [Authentication](../../appchi-domain/src/appchi/identity/authentication.py), line 147 onward, also performs synchronous database phases. [Accessible tenant calculation](../../appchi-domain/src/appchi/access.py), line 21 onward, adds per-tenant queries. A poll for a user with access to all 50 tenants traverses approximately 124 SQL execute calls including transaction setup/guards. A tab polls every 30 seconds; 100 such tabs imply roughly 413 SQL calls/second from session polls alone. Access to fewer tenants reduces this estimate.

Separately, [LiveRegistry](../../appchi-api/src/appchi/live.py), line 52, holds one process-wide `threading.RLock` across a PostgreSQL loader. Redis invalidation at line 81 takes that lock directly on the event loop. Slow SQL in a worker thread can therefore block HTTP dispatch, Redis handling and SSE activity on the same API process. The offline reproduction executed the actual class and observed synchronous invalidation blocked for **0.253 seconds** behind a simulated 0.250-second loader.

**Recommendation:** keep synchronous DB phases off the event loop or use async database access; compute tenant roles using set-oriented queries; never hold a global blocking lock across I/O. Use per-tenant single-flight state loading with short metadata-only locks. Preserve current-grant/revocation and restore checks. Most ordinary API routes already use synchronous handlers correctly; this finding concerns the async paths.

### SCALE-05 · P1 · Lifetime maintenance history eventually stops recurring alert processing

[Maintenance processing](../../appchi-domain/src/appchi/maintenance/processing.py), line 250, records source receipts including repeats. Lines 295–325 load the alert's entire retained receipt history on each reconciliation and reject histories exceeding `max_inputs`. [Runtime](../../appchi-domain/src/appchi/maintenance/runtime.py), lines 150–164, uses that path for source arrivals when maintenance is enabled, even without a matching current window. Work grows with inputs and historical segments under tenant coordination.

The actual function rejected **101 retained receipts with `max_inputs=100` and zero segments** in the offline reproduction. The value 100 is a test limit, not an assumed production allocation. Any finite limit has the same eventual failure for a sufficiently long-lived repeating alert. Its inputs are removed during retired-alert cleanup, not normal active-alert processing. At the cap, the accepted Kafka record cannot be applied and partition progress is blocked. Raising the cap delays the failure while increasing work.

There is a related catalog problem: processing lines 105–110 load all historical maintenance segments against one cap, including ended history. Enough past windows can block new alerts too. [Maintenance specification](../../appchi-design/specs/spec-maintenance-operations.md), line 116, says ended non-deleted windows do not consume active evaluation quota. The implementation needs to distinguish active evaluation capacity from retained historical proof.

**Recommendation:** verified incremental temporal checkpoints and bounded suffix replay, with separate current catalogs and historical proof. Preserve delayed-arrival/control semantics and no-backfill rules. Test years of repeated input/control history, not only fresh alerts. This does not require silently reinstating the deferred maintenance or retention workers.

### SCALE-06 · P1 · Correlation rewrites all evidence per changed input

[`_persist`](../../appchi-domain/src/appchi/correlation/runtime.py), lines 510–542, deletes prepared rules' evidence and reinserts all surviving memberships individually. Each membership resolves its context through [a locking lookup](../../appchi-domain/src/appchi/correlation/repository.py), line 426. The work occurs under shared tenant coordination, and [the consumer](../../appchi-correlation/src/appchi/correlation/consumer.py), lines 69–86, may repeat arbitration within that transaction.

Executing the actual `_persist` function with SQL/context spies and 1,000 distinct membership objects produced **1,001 direct SQL executions plus 1,000 context lookups**. At 83.33 changed inputs/second concentrated in one such tenant, that shape implies roughly 167,000 SQL executions/second before the rest of processing. This is an operation-count model, not a database benchmark or a statement that every source repeat qualifies for correlation.

**Recommendation:** differential evidence persistence, batched keyed upserts/deletes and one context resolution per distinct context. Preserve full atomic attachment, source-time evidence, reset and ownership semantics.

### SCALE-07 · P2 · Alert operations lock the complete historical correlation context set

[Correlation lock preparation](../../appchi-domain/src/appchi/correlation/runtime.py), lines 37–45, selects and locks all tenant rules and contexts. [Maintenance catalog locking](../../appchi-domain/src/appchi/maintenance/runtime.py), line 102, invokes this even without a maintenance catalog. Source application and ordinary alert commands reach it. Historical contexts are cleaned during deleted-rule cleanup rather than routine input handling, so work grows with lifetime group cardinality.

The tenant ordering boundary is intentional, but the work inside it is too broad. A single tenant receiving 83.33 inputs/second has approximately 12 ms per arrival available for serialized source work before accounting for correlation and user commands. That necessary bound is not a measured latency.

**Recommendation:** identify relevant active contexts and lock them in canonical order, while retaining the required coordinator and proving ownership/reset races. Additional Kafka partitions or replicas do not remove a shared tenant bottleneck.

### SCALE-08 · P2 · Session polling prevents the activity heartbeat from firing

[Workspace](../../appchi-frontend/src/components/workspace.tsx), lines 92–111, installs a fresh session object on each successful 30-second poll. Its activity effect at line 164 starts a 60-second interval and depends on the entire session object. Each poll therefore cleans up and restarts the interval before the activity request fires.

[Authentication](../../appchi-domain/src/appchi/identity/authentication.py), line 65, applies a 30-minute idle limit. The source-derived timer model produced **zero activity callbacks over 30 minutes**, explaining how an actively used foreground session can expire. This is not yet an executed browser expiry test.

**Recommendation:** key the interval to stable session identity and read current CSRF data through a ref or equivalent mechanism. Add a browser fake-clock regression spanning multiple polls and the idle deadline.

### SCALE-09 · P2 · Healthy SSE still incurs duplicate browser polling

[Workspace](../../appchi-frontend/src/components/workspace.tsx), line 577, polls `/view-state` every two seconds even while SSE is healthy. [SSE](../../appchi-api/src/appchi/live.py), line 163 onward, independently reauthorizes/reconciles every two seconds. Each HTTP poll performs another authorization/state transaction.

For 100 visible tabs, that is approximately **100 authorization transaction contexts/second even in an otherwise idle system**, before feeds, counts, commands and session polling. Three simultaneously visible tabs per user triples that demand. Tenant vector caching does not coalesce session authorization or the polling transaction. The design's disconnected-browser fallback does not require this permanent duplicate polling.

**Recommendation:** use foreground HTTP polling as a disconnected-stream fallback, share tenant generation reads and measure the session-check budget without weakening revocation guarantees.

### SCALE-10 · P2 · Ordinary Product API requests have no aggregate handler deadline

[HTTP middleware](../../appchi-domain/src/appchi/http.py), line 128, sets `asyncio.timeout(None)` outside ingestion. The [reliability contract](../../appchi-design/specs/spec-reliability-operations.md), section 8, specifies a ten-second ordinary API/search handler deadline. Individual SQL/ES timeouts do not bound total time across repeated queries, pool waits and external calls.

Under contention, work can outlive the intended request budget and hold scarce resources after a caller or gateway gives up. Some specialized count/preview routes have explicit deadlines, so coverage is inconsistent.

**Recommendation:** propagate a remaining deadline through ordinary request work, with the separate authorized operator and SSE budgets. Ensure cancellation and thread execution do not claim rollback of committed commands.

Additional correctness observation: ad-hoc first-page alert feeds refresh on generation change, but lack the relative-time CEL tick required by the technical design. In a quiet tenant, a rolling time-window query can remain stale as alerts age out. Add relative-time refresh when fixing query coalescing, not as another unbounded source of searches.

## Architecture and code-quality assessment

**Keep the main service/store split.** Kafka admission is decoupled from PostgreSQL availability; PostgreSQL owns state and atomic history/proof; search is rebuildable; Redis is a hint path with durable reconciliation. Independent source, correlation and search roles are appropriate. Stable replay identities, transactional Kafka offsets, RLS, explicit lock order, tombstones and conservative search watermarks show careful correctness design.

Strict shared schemas, generated HTTP contracts and immutable Git dependency pins are good choices. Fresh checks found no HTTP/event drift or startup import-boundary failures. The migration runner protects original SQL bytes and legacy history; all 44 SQL checksums match. CEL has static/cost bounds, a compiler deadline and a bounded cache; unconditional recompilation was not found.

**The weakest area is composition of efficient operations.** A count badge invokes a complete paginated feed pipeline; unchanged evidence is rewritten wholesale; a bounded scan is composed from serial refresh waits; synchronous database work enters async handlers. Correctness checks are individually reasonable but their aggregate work is not budgeted well.

Transaction behavior also has hidden coupling: `Database.transaction()` flushes inline maintenance effects on exit, and maintenance locking reaches correlation state. Several large domain/route/frontend modules combine persistence, policy, orchestration and presentation. Refactor those boundaries while fixing the concrete paths, with explicit inputs, transaction ownership and reusable query services. A wholesale rewrite or more service splitting is not warranted by these findings.

Service isolation checks cover imports reachable at startup, not every lazy runtime path. Important source/correlation integration tests remain in delivery, and those small service repositories lack their own unit suites. Coordinated CI therefore remains essential. Existing tests are valuable for correctness but small fixtures and mocked browser APIs do not exercise sustained cardinality, refresh amplification or multi-replica scheduling.

## Long-run capacity and operational risks

- At 5,000 deliveries/minute continuously, the system receives **7.2 million receipts/day**. A 4 KiB workload alone represents about 29.5 GB/day of input bytes before repeated raw/parsed/proof/history snapshots, indexes and WAL. Actual bytes per receipt must be measured. Updates still create retained receipt/proof work.
- Automatic physical cleanup is explicitly deferred under REV-34. That is an accepted scope decision, not a missing worker to restore without authorization. It requires a supported operating horizon, storage/WAL/backup budget and evidence that old data does not make hot paths progressively slower. Hash partitioning does not by itself provide age-based data disposal.
- Tenant-keyed changed ordering and tenant SQL coordination constrain a dominant tenant. Fifty uniformly busy tenants and one busy Global tenant are materially different load cases.
- A source consumer handles each record synchronously across SQL application, Kafka transaction and proof confirmation. Parallel replicas help only within partition allocation and downstream lock constraints. Current error recovery can restart an entire consumer on one failed partition; selective partition pause/fencing would improve isolation.
- A successful 202 proves Kafka acknowledgment, not timely display or guaranteed survival past Kafka retention. No downstream-lag admission gate is intentional. Measure accepted, applied, correlated, searchable and rendered rates separately, with lag/retention-risk visibility.
- The k6 tooling explicitly omits real browser/SSE/render behavior. Its active-session heartbeat also differs from the browser implementation, so passing protocol-read tests alone would miss SCALE-08. The checked-in default read profile uses five users and must be changed for this target; multi-tenant distribution also needs explicit fixtures.

## Recommended implementation and qualification order

1. Repair batched indexing and isolate failed projections; repair maintenance lifetime bounds and correlation evidence writes. Preserve proof/order semantics with regression scenarios.
2. Fix browser count/PIT amplification, event-loop blocking and session timer behavior. Remove redundant live polling and implement request budgets.
3. Reduce historical context locking, reuse external clients, and make replica/partition ownership explicit. Add operation-count and retained-state regression coverage around the hot paths.
4. Qualify the exact release/allocation against the requested **83.33 deliveries/s + 100 users + 50 tenants simultaneously**. Include uniform tenants, 80% traffic in one tenant, all-v1 Global, one/three tabs, alert feeds and dashboard panels, realistic rule/evidence cardinality, and representative payload/new-row mixes. These skew cases are proposed test scenarios, not new product limits.
5. Run a sustained growth/soak profile and backlog recovery with arrivals continuing. Measure admission p99, authoritative application delay, search/count p99, actual browser visibility, DB pool/lock waits, SQL statements and WAL per input, ES bulk size/PIT count/refresh time, event-loop lag, partition lag, memory and CPU. Test one poisoned projection, Redis loss and one blocked partition while healthy tenants continue.

Use the existing one-second admission, two-second search/count and five-second render objectives unless the user explicitly changes them. The existing normative 250/s sustained and 500/s recovery qualification remains additional release work. Choose numeric budgets and replicas from combined measurements; do not extrapolate independent maxima or infer capacity from functional tests.

## Reviewed repository snapshot

All eleven checkout roots were verified independently. HEADs match the delivery workspace manifest (delivery is `self`). Review includes existing working files, so commit IDs alone do not reproduce all reviewed content. No staged changes were present at initial inspection.

| Repository | HEAD | Review coverage / initial working state |
|---|---|---|
| appchi-api | `0b8a1a351378d0a7952a405b6fac4db1d03c6096` | Auth, live, search, command composition; existing operator-proof/executor, README, Dockerfile and test work. |
| appchi-contracts | `42107e3814a07518878291929740f5fa120fbff1` | Native/event/schema ownership and generation; clean. |
| appchi-correlation | `ad68f9f4838848f5d318888be4b0e067d5b51e5b` | Ordered consumer and shared evidence path; existing Dockerfile work. |
| appchi-delivery | `2d1fc70b942eae2590855538d7ca7cc6d67128ca` | Release/deployment resources, harness, CI and qualification; existing initialization/load-test/local-runtime work. |
| appchi-design | `7bf631cf8613cca0e10139cc5cd2b391778f9d8e` | Core design, scope, domain/reliability supplements and acceptance; existing pipeline/register/verification additions. |
| appchi-domain | `830d4f65556d93692cca60695fafb522769fe122` | SQL, maintenance, correlation, search, identity, CEL, transaction boundaries; clean. |
| appchi-frontend | `468ba80eeac6ca2ad4b95752edb7b8005aa354fb` | Session/live, feed/count fetching and state flow; existing Dockerfile, Keycloak image and browser-test work. |
| appchi-indexer | `ff88bec0e57e4fbcb3af7917e9bdbe29f5efbec3` | Projection, reconciliation, bootstrap/rebuild boundaries; existing bootstrap/docs/image/tests work. |
| appchi-ingestion | `0d2ee23a7940c2c0866498aa0646587f101651bc` | Validation, gateway context and concurrent Kafka admission; existing image work. |
| appchi-migrations | `1ebe4cf72fd3878dbc44d4f7c8f5dc8c3d2a5236` | Schema/index/partition review and checksum integrity; existing Dockerfile work. |
| appchi-processor | `4f7a3fc81483d193033c3eec9f54f6aaeb6a5392` | Application/proof/offset ordering, concurrency and failures; existing Dockerfile work. |

## Fresh verification and limits

Commands below ran on this workspace on 2026-10-06 using the prepared delivery Python 3.13.5 environment with editable sibling source imports. No services were started, migrated, stopped or load-tested.

| Check | Actual outcome |
|---|---|
| Delivery `.venv/Scripts/python.exe qualification/check_contracts.py` | Passed: OpenAPI and internal event schema models match committed artifacts. |
| Delivery `.venv/Scripts/python.exe qualification/verify_boundaries.py` | Passed: 211 owned modules; five independent service entrypoints import successfully under the boundary guard. |
| Design `C:/Python313/python.exe scripts/check_docs.py` | Passed, including a fresh run after the review-register addition. |
| Delivery `C:/Python313/python.exe qualification/verify_documents.py` | Passed after review additions: 112 documents, 566 source entries, REV-01 through REV-38 intact, no errors. |
| Frontend `node node_modules/typescript/bin/tsc --noEmit --incremental false` | Passed. No frontend build or browser suite was run. |
| Targeted delivery pytest command below | **39 passed, 25 skipped, 6 deprecation warnings**, 7.06 seconds. Real-PostgreSQL-dependent cases were skipped; no integration pass is claimed. |
| [Offline reproductions](scalability-review-2026-10-06.py) | Passed: maintenance cap, oversized projection, evidence write counts, live lock blocking, source-derived activity timer model, all 44 migration checksums. |
| Delivery `.venv/Scripts/python.exe -m ruff check --no-cache qualification/scalability-review-2026-10-06.py` | Passed. |

```powershell
# From appchi-delivery
.venv/Scripts/python.exe -m pytest tests/unit/test_maintenance_processing.py tests/unit/test_maintenance_temporal.py tests/unit/test_kafka_first_admission.py tests/unit/test_identity_sessions.py tests/unit/test_api_live_publisher.py tests/unit/test_load_test_harness.py -q -p no:cacheprovider --basetemp=.local/pytest-scalability-review-20261006
.venv/Scripts/python.exe -B qualification/scalability-review-2026-10-06.py
```

The reproductions use actual selected production functions/classes with synthetic data or SQL spies. They do not establish database latency, whole-cluster starvation empirically, browser rendering or end-to-end throughput. The timer check is a source-derived model, not React/browser execution. No production bindings, live load, EXPLAIN plans, soak/recovery benchmark, UI render review or complete security certification was performed. Current workload capacity and production readiness therefore remain unverified.
