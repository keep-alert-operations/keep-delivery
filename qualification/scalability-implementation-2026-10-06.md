# Scalability implementation and verification â€” 2026-10-06

Testing stopped at the user's request on 2026-10-07. This report records the implementation of the
[system review](scalability-review-2026-10-06.md), against 5,000 alert deliveries
per minute, 100 active users and 50 tenants. It does not replace the design's
250/second sustained and 500/second recovery qualification requirements.

## Outcome at handoff

The architecture retains appropriate separation between Kafka admission,
PostgreSQL transactional authority, a rebuildable Elasticsearch projection and
Redis/SSE hints backed by database reconciliation. The review found expensive
per-alert work, repeated user queries, lock contention, blocking I/O and missing
aggregate deadlines. The application changes below address those paths while
preserving replay, tenant isolation and recovery safeguards.

**The requested capacity is not established.** The actual requested-rate run
accepted 1,250 arrivals during a 15-second, 100-user/50-tenant cohort, but source,
search and read latency failed. Later process-based target attempts could not
finish setup. The final measured 100-user run at only two arrivals/second retained
every alert but returned 195 HTTP 503s out of 382 reads. The final clock race
correction has focused passing tests but no subsequent capacity run.

**Completed verification:** 780 coordinated unit cases passed with 65 explicit
skips; 26 migration/real-search cases passed; 144 controlled browser cases passed
with seven opt-in skips; frontend production build and nine Python wheel builds
completed. Fourteen focused clock cases passed after the last production edit.
These counts describe separate, partially overlapping suites, not a single total.
The final domain wheel and full suite were not rebuilt/rerun after that narrow
clock correction. Earlier failures and their reruns are recorded below.

The final local startup completed with both required workers healthy and 17 live
gateway/authorization checks passed. The app is running at
`https://localhost:3443`. The planned final real-browser OIDC/alert/note and
identity scenarios were **not run before the user stopped testing**; the 144
browser passes used controlled API fixtures. The original frontend generated
configuration was restored after local startup. All three previously paused BI
containers are running again; Elasticsearch and SQL Server report healthy.

At testing handoff, the changes were uncommitted local development work. See the
source publication record below for subsequent Git publication. No
immutable release, production deployment or DEL gate closure is claimed. Large
late-history replay and arbitrarily large note projections retain the limits
described below. At 5,000 arrivals/minute continuously, storage must accommodate
7.2 million arrivals/day; retained-volume, soak and failure qualification remain
unperformed. No sustainable maximum rate was established on this workstation.

## Scope and compatibility

The source combination uses local editable Python packages from the eleven
independent repositories. Generated product/aggregate HTTP schemas and frontend
types come from these sources. The frontend's installed contract snapshot is an
explicit local development override. During testing, committed Git dependency
pins and delivery release locks were unchanged. The subsequent source publication
below advances those pins without claiming an integration-qualified release.

Preserved existing work includes container images, private-cluster initialization,
operator proof, local setup and prior migration-runner changes. No production
deployment or shared infrastructure change was performed. Commits and pushes were
subsequently authorized as recorded below; package-registry publication was not
performed. KeepHQ is excluded.

Apply forward migrations 0048 through 0050 with the dedicated migration identity
before updated writers. The original 44 migration SQL files and checksums are
unchanged. Revision 0050 indexes live notes by tenant and alert; it also supports
ordered streaming reads without scanning other alerts' retained notes. Apply it
in the stopped-writer migration window, allowing time to index retained notes.
Deploy compatible domain/contracts packages and API before the new
frontend, which uses the additive count and snapshot-close endpoints. Publish
immutable package artifacts and advance consumer locks and the delivery manifest
together when preparing a release. Old code must not run against new maintenance
continuation/tail history without a separately verified compatibility path.

Release order: drain and stop the old domain-writing API/processor/correlation
processes and indexer, run the dedicated migration job, start the compatible
updated backend combination, verify replay/search/maintenance gates, then serve
the updated frontend. Preserve Kafka transaction IDs, committed group offsets,
tenant ownership and restore fences. Do not restart old temporal readers after
new tail history has been written; rollback requires a verified compatible
reader or the existing protected recovery procedure. These are preparation
instructions, not an executed deployment or permission to alter shared services.

## Implemented areas

| Review finding | Application change | Evidence status |
|---|---|---|
| SCALE-01 | Coalesced byte-bounded projection writes, pooled ES clients, unchanged visible-document no-op, exclusive scan leases and separate audit progress | Eleven real pipeline cases passed after scheduling correction; requested-scale latency has not passed |
| SCALE-02 | Streaming note token assembly, explicit document allocation, independent failed tenant/partition retries, honest watermarks | Real 120-note document above 1 MiB and retry/lease checks passed |
| SCALE-03 | One quick-count batch, count-only engine, purpose-bound PIT cleanup and coalesced feed refresh | Native/count tests and final 144-case controlled browser suite passed |
| SCALE-04 | Off-thread authentication SQL, set-based tenant grants, registry locks released before SQL | Focused and real authorization checks passed; 100-user latency remains failing |
| SCALE-05 | Incremental maintenance continuation and bounded union tail, off-lock exact fallback preparation | Long-history/equivalence and optimistic-fence checks passed; 19 legacy/manual and preparation regressions passed after the compatibility correction |
| SCALE-06 | Differential correlation evidence persistence | Focused/real correlation checks passed, including the final domain matrix |
| SCALE-07 | Live/targeted context locking and indexed relevant-context lookup; expired/old-epoch evidence excluded from initial decision locks | Existing domain matrix and both final real-SQL lock-scope regressions passed |
| SCALE-08 | Stable foreground activity heartbeat across session refreshes | Controlled browser assertions passed |
| SCALE-09 | Disconnected-only foreground polling and hidden-tab pause | Controlled browser assertions passed |
| SCALE-10 | Aggregate ordinary request budget, database pre-commit expiry, pooled deadline-aware ES calls; relative CEL ticks | Cursor/deadline checks and 12 real-PG guard/users/restore cases passed after ordered SQL pipelining |

## Execution environment and evidence limits

Windows workstation with 16 GiB host RAM and a Docker allocation of 12 CPUs/8 GiB.
Existing unrelated application containers and their volumes were preserved;
the three BI containers were temporarily paused only after explicit user approval
and restarted after the isolated capacity attempt. Heavy native CEL,
database/search workloads and browser/build suites are serialized. Existing Appchi
PostgreSQL, Kafka, Elasticsearch and Redis containers/volumes are reused; integration
tests create uniquely named disposable databases/indexes/topics and clean their
own fixtures. No shared or production service is exercised.

The combined diagnostic uses real HTTPS Product API requests, authorization,
PostgreSQL, Kafka admission/source transactions, indexing, SSE and Elasticsearch.
Synthetic human sessions and trusted routing fixtures are explicit. OIDC,
gateway admission, rendered browser visibility and enabled-feature capacity must
not be inferred from this diagnostic. Real OIDC/browser checks are recorded
separately. Capability gates without supplied qualification evidence remain closed.

## Completed checks and final reruns

- Coordinated repository units after the clock-I/O and local-port corrections:
  **780 passed, 65 skipped**, exit 0 in **60.182 seconds**. Contracts 16, domain
  393, migrations 5, API 139, ingestion 5, indexer 65 and delivery 157 passed. Skips require separate integration bindings and
  are not passing integration evidence.
- Expanded isolated database/integration run: **214 passed, 2 skipped, 3 failed**.
  Two failures reused Kafka test coordinates; one expected 403 where tenant
  concealment correctly returns 404. Those fixtures/expectations were corrected.
  The two skipped scheduler checks remain deferred under REV-34.
- Pipeline reruns: seven existing Kafka/source cases and all six current real
  PostgreSQL/Kafka/Elasticsearch scalability cases passed. They include 120 notes
  above 1 MiB, failed-tenant isolation, leases, delta reconciliation after lost
  hints and behind-cursor writes, and periodic repair of missing documents.
  Fixture index administration uses 30 seconds; runtime write timeouts stay at
  five seconds. A deletion fixture now uses the authoritative external version.
  The final expanded pipeline run passed **10 tests in 40.26 seconds**, including
  gate precheck races, unfinished-audit delta progress, takeover at both audit
  checkpoint boundaries and failure isolation between audit and ordinary scans.
- Maintenance/correlation: latest pure suite **36 passed**; off-lock preparation
  suite **3 passed**; earlier real affected suite **28 passed, 2 skipped**.
  The final native/domain matrix passed 108 tests and failed eight legacy manual
  transition cases with `maintenance_preparation_changed`. Their fixture selects
  pre-cutover temporal behavior, which the incremental continuation rejects.
  A separately suspected same-transaction proof reuse scenario was not reproduced;
  its speculative change is removed. The confirmed compatibility correction uses
  exact locked reduction for a legacy timeline only when it fits the existing
  bounded page allocation; current pipeline behavior keeps its incremental path.
  All **19 inline-maintenance and preparation tests passed** on rerun.
- Migrations: the final full chain suite passed **23 tests in 86.69 seconds**,
  including the expression index and separate audit cursor fields. After adding
  audit-specific retry/error fields, a combined package/fresh-install/upgrade
  and real Elasticsearch search/count run passed **9 tests in 20.80 seconds**.
  The final complete migration/package and real Elasticsearch rerun, including
  revision 0050, passed **26 tests in 50.62 seconds**. It covers fresh/repeated
  installation, legacy upgrades through 0049, history/checksum integrity, schema
  and privileges, concurrent migration serialization, preserved scan state, and
  the actual ordered live-note lookup plan. The refreshed compatibility XML is
  `qualification/pytest-local-runtime-es.xml`.
- User paths: six real-PG deadline/access tests passed, plus focused count/unit
  suites. **16 targeted Edge browser cases passed with a clean exit 0**. These use
  controlled API/stream fixtures. Five deterministic CEL single-flight tests
  passed, followed by the passing native CEL/count cases in the 108-test matrix.
  Ordered guard/setup pipelining passed **13 focused unit tests** and **12 real
  PostgreSQL tests**, including acquisition-snapshot races, uncertain lock
  completion, restore draining, tenant authorization and deadline rollback.
- All nine Python repositories built wheels using `uv build --wheel
  --offline`, with cached build dependencies. These are local build checks, not
  published or immutable release qualification. The final narrow clock-time
  correction followed the last domain wheel build. The per-repository results are
  `status/evidence/scalability-final-wheel-builds.json`.
- Frontend `node node_modules/next/dist/bin/next build`, using the isolated ignored
  `.next-scalability` output, passed with TypeScript and route generation in
  **145.43 seconds**. Next's temporary generated type-path changes were removed
  afterward, restoring the original `tsconfig.json` and `next-env.d.ts` exactly.
  The later final build after the preset fix also passed (compile 72 seconds,
  TypeScript 14.5 seconds), with those original bytes restored and verified.
- Generated contracts match both HTTP producers and event models. Independent
  imports passed for all five services (218 owned modules). Documentation checks
  passed across 112 documents/566 source entries and REV-01 through REV-38.

## Workload diagnostics, not capacity qualification

The smallest real API-subprocess smoke accepted and applied all ten measured
arrivals, retained twenty total seeded/new alerts in PostgreSQL and search, and
reported no API or worker errors. Source p99 was 230 ms, sampled search p99 2.10 s,
and API read p99 775 ms. Its concurrency and duration do not establish capacity.

A subsequent 10 alerts/second, ten-user, five-tenant run accepted all 100 new
alerts and drained to exact PostgreSQL/search/API counts with zero watermark lag.
It **failed the latency objective**: source p99 was 21.85 seconds and sampled
search p99 29.44 seconds. One Elasticsearch timeout was retried. SQL instrumentation
found 71 explicit statements per alert; PostgreSQL application p50 was 197 ms,
while Kafka commit p50 was 5.8 ms. The next optimization targets database setup
round trips while preserving the recovery lock and separate statement snapshots.
Evidence: `status/evidence/scalability-smoke-subprocess.json` and
`status/evidence/scalability-smoke-phase-costs.json`.

After ordered guard/setup pipelining and reuse of the source's gate precheck,
the same small profile drained correctly again with zero final watermark lag.
Source p99 improved to **10.96 seconds**, sampled search p99 to **13.96 seconds**;
read p99 was **2.56 seconds** and admission p99 **1.81 seconds**. A transient index
handler timeout was recovered. This still fails the selected latency objectives.
The ready barrier verified all ten users were connected throughout measurement.
Evidence: `status/evidence/scalability-smoke-guard-pipeline.json`.

The first larger attempt timed out creating its isolated Elasticsearch index
before workers or traffic. A second attempt failed during preload because
Windows denied replacement of the synthetic clock file while it was open.
Neither attempt reached a measured cohort or supplies capacity evidence.
Only test fixture administration and clock-file sharing retries were adjusted;
runtime deadlines and acceptance thresholds remain unchanged.

No successful requested-scale run is recorded yet. Final command results,
workload measurements, browser screenshots, source snapshot and remaining delivery
limits will be recorded here after integration completes.

The subsequent 15-second simultaneous cohort did execute 83.33 deliveries/second
with 100 connected users across 50 tenants. It **failed**: all 1,250 offered
arrivals were admitted and ultimately joined 500 seeded alerts in PostgreSQL and
search, with all 50 API tenant counts exact, but only 81 measured alerts applied
by the end of the 15.1-second arrival window. Source p99 was **84.35 seconds**,
sampled search p99 **135.68 seconds**, and read p99 **15.87 seconds** with 136
request-timeout responses. Generator p99 lateness was 1.48 seconds. One durable
source failure, recovered partition progress, pool timeouts and clock errors were
recorded. This is failed evidence, not sustainable requested-scale throughput.
See `status/evidence/scalability-harness-100-users.json`.

The run exposed an ordinary reconciliation scheduling defect: completed empty
passes retained an old scheduling timestamp and could repeatedly outrank pending
tenants. Completion now updates that timestamp. The fix and existing real
pipeline cases passed **11 tests in 21.04 seconds**. Further diagnostics will
separate worker processes, matching the production process model more closely:
the original harness ran all sixteen source clients and four index clients in
one Python interpreter. No performance improvement is asserted before that run.

The first separate-process smoke ran four source and two indexer processes,
eight partitions, ten users and five tenants, with 100 seeded alerts and 100 new
arrivals at ten/second. All admitted alerts reached PostgreSQL and Elasticsearch,
all tenant counts were exact, all ten SSE connections remained present, and no
controlled worker/API errors or final watermark lag were recorded. Source p99
was **4.07 seconds**, read p99 **1.80 seconds**, admission p99 **62 ms** and sampled
search p99 **7.39 seconds**. It still failed search latency. Source PostgreSQL
application p50 was 252 ms and indexer active-turn p50 was 1.496 seconds. The six
workers used approximately 382 MiB sampled resident memory. This comparison also
uses the production worker lifecycle, so it is not a pure thread-versus-process
experiment. A redundant source precheck transaction and zero-note cursor setup
were removed before the next target run. The maintenance-enabled path still
prepares outside writer locks; the disabled path retains coordinator/duplicate
and live-gate rechecks in its reused transaction. The gate-change rollback and
large/live/deleted-note regressions passed. Across the latest focused runs,
19 pipeline/Kafka cases and both new real-SQL correlation lock cases passed.
Two fixture setup failures were corrected or rerun; no failed attempt is counted
as passing evidence.

Full controlled-browser execution against the production build completed with
**142 passed, 7 explicitly gated real-stack skips, 1 failed** in 3.3 minutes.
The failure reproduced unchanged: saving an alert filter as a preset could read
the previous URL and initialize the editor with `true`. The router search-param
fix and a draft-preservation regression are implemented; final build and browser
execution are superseded by the final result below. The interrupted development-server run and its timing
failures are retained separately. All ten of those failures passed unchanged
against the production build.

Final controlled-browser verification passed **144 tests, with seven explicit
real-stack opt-in skips**, exit 0 in 2.5 minutes. The preceding run had 143 passes,
seven skips and one incident-pagination failure. Its trace identified a fixture
that closed the SSE connection immediately and triggered a reconnect refresh
during pagination. Three unchanged case reruns passed; holding that filter-only
fixture's stream open then passed all four incident cases three times, followed
by the complete green suite. Assertions and timeouts were not relaxed. The
preset screenshot was inspected: the edited CEL draft remains intact after URL
query changes and the editor layout is readable. TypeScript and targeted
formatting passed. Owned browser/server processes were stopped afterward.

Two attempted correlation-module runs stopped before assertions because native
CEL compilation was unavailable. Both ran with the required escalated local
execution permissions. A fixed-literal probe then identified one cold-start
timeout at 1,121 ms; subsequent main/worker-thread compilations completed in
181â€“351 ms. The one-second compiler deadline is unchanged. Fixed reason-code
logging now distinguishes deadline, child failure and child unavailability
without logging expressions, native error text or identity data. Eight focused
sanitization/single-flight tests passed. The two new row-lock regressions now
seed valid SQL fixtures directly, avoiding an unrelated compiler dependency;
their real PostgreSQL NOWAIT assertions passed.

## Remaining capacity and data-volume limits

### Additional local diagnostic conditions

The 8-source/2-indexer process target with the unrelated BI containers still
running did not reach measurement: all 5,000 seed records were acknowledged,
but only 3,887 applied before the 600-second drain bound. That attempt records
zero measured arrivals and cannot establish the requested rate. Its evidence is
`status/evidence/scalability-target-core-processes-bounded-setup.json`.
The harness now bounds the whole preload phase, including offering and both
stores' convergence, to one deadline. Runtime timeouts were not enlarged.

With explicit user approval, the three `alerts-bi-*` containers were temporarily
stopped for isolated testing; their IDs and prior running state are retained in
the ignored `.local/scalability-paused-containers.json` for restoration. Two stop
calls reported Docker exit-event/zombie errors before inspection confirmed all
three exited. Their volumes and configuration were preserved. Restoration is
recorded below and was confirmed again at handoff.

During a subsequent small fixture's database creation, Appchi PostgreSQL logged
an untracked child exiting with code 2 and restarted its server processes. It
completed recovery at 2026-10-06 21:36:41 UTC. PostgreSQL was PID 1; the exact
origin of the orphan is unproven. Its [postmaster source](https://raw.githubusercontent.com/postgres/postgres/REL_18_STABLE/src/backend/postmaster/postmaster.c)
confirms that this exit status takes the child-crash path. Local Compose now
enables [`init`](https://docs.docker.com/reference/compose-file/services/#init)
for both PostgreSQL containers, delegating orphan reaping to an init process.
Only the Appchi PostgreSQL container was recreated at this point, retaining its
named volume; configuration validation, `HostConfig.Init=true`, container health
and `pg_isready` all passed. This is local runtime hardening, not a change to the
company database service. The uncertain fixture database was confirmed absent.

The adjusted run used 24 source processes, two indexers, 32 partitions and an
API pool of 32 with the BI containers paused. API and all workers started, but
unpaced seed setup offered 2,020 records and acknowledged 2,011 in 19.14 seconds;
nine admissions returned 503 after Kafka request timeouts. Measurement never
began and the 100-user cohort was not started. Confirmed source work reached
199 turns; PostgreSQL application p50 was 647 ms and whole-turn p50 1.19 seconds.
Two worker Kafka exceptions were recorded. These are failed setup/recovery
diagnostics at roughly 105.5 offered records/second, not a measured 83.33/second
steady-state result. All scoped fixtures/processes were cleaned up. See
`status/evidence/scalability-target-core-init-bi-paused.json`. Additional replicas
alone did not improve this workstation's observed processing costs. The earlier
15-second simultaneous cohort remains failed requested-rate evidence; a passing
60-second combined cohort and longer qualification still require execution.

After that attempt, all three exact previously running BI containers were
restarted. Their current state and health observations are recorded in
`status/evidence/scalability-container-restoration.json`; no BI configuration or
volume was changed. Both configured health checks (Elasticsearch and SQL Server)
passed; Kibana is running without a configured health check. Appchi's unused
Kafka/Elasticsearch containers were then temporarily stopped, preserving volumes,
to give the restored services and controlled-browser checks memory headroom.
Both were restarted before the subsequent real-service checks and paced baseline.
The user explicitly selected continued workstation testing and reporting its
measured limits, rather than moving this task to a target cluster.

Unchanged worker-health states now attempt atomic heartbeat publication at most
once per second, with immediate ready/draining transitions. Successful writes
alone update persisted heartbeat freshness; failed writes cannot hide expiry.
Eight deterministic heartbeat tests passed. The combined target must be rerun
before attributing any throughput improvement to this change.

### Unqualified growth cases

The paced baseline with BI services running reached a complete 30-second cohort:
100 connected users, 50 tenants, 50 seeded alerts, and two new alerts/second.
All 60 measured arrivals were admitted and all 110 total alerts were applied and
searchable; counts were exact in all tenants with zero faults/watermark lag.
Admission p99 was 157 ms, source p99 364 ms and sampled search visibility p99
4.47 seconds. Interactive reads still failed: **197 of 400 completed requests
returned `503 request_timeout`**, with individual-request p99 **19.07 seconds**.
The arrival window was 29.53 seconds; outstanding reads extended total measured
work to 46.22 seconds. Available host RAM was 548 MiB at startup and 350 MiB at
measurement. This is context, not proof of the cause. The record is
`status/evidence/scalability-baseline-100-users-paced.json`; it is not a passing
100-user capacity result or a measured maximum sustainable rate.

Review confirmed that AnyIO route queueing is inside the middleware deadline.
A scaled in-memory probe of the actual app sent a 503 before the blocked handler
finished; thread shielding alone therefore does not explain the measured excess.
Clock-evidence file reads were still synchronous on the event loop. They now run
in awaited thread work, retaining the admission timestamp, context budget and
fail-closed checks. Five actual-app regressions cover blocked-file concurrency,
admission/ordinary timeouts and real clock evidence; these and existing clock
tests passed **12 cases**. This fixes a concrete blocking risk without asserting
that it caused the observed nine-second excess. A final diagnostic separates
ASGI entry-to-response-start from complete client feed/count request time.

That final instrumented repeat admitted all 60 arrivals and retained all 110
alerts in PostgreSQL and search, with exact tenant counts, 100 SSE connections,
zero pipeline faults and zero remaining watermark lag. Source p99 was 397 ms
and sampled search visibility p99 4.895 seconds. However, **195 of 382 reads
returned 503**: 167 request timeouts and 28 clock rejections. Feed response p99
was 19.523 seconds at the client, while ASGI entry to response-start p99 was
10.234 seconds; count values were 10.415 and 10.036 seconds. These are separate
distributions, not paired samples to subtract. They show the server response-start
budget near ten seconds and additional client-observed delay outside that phase;
they do not isolate the cause of that additional delay. See
`status/evidence/scalability-baseline-100-users-clock-offload.json`.

The clock errors exposed a confirmed queueing race: evaluating a newly published
clock sample against the request's earlier arrival time could incorrectly call it
future evidence. The final correction uses execution-time UTC for clock checks,
while retaining the original arrival timestamp for commands. **14 focused tests
passed**, including deterministic queued-new-publication acceptance and rejection
of genuinely future evidence; independent review found no actionable issue.
No combined load rerun followed this correction, so the preceding performance
results are explicitly from the immediately prior revision. The user selected
continued workstation testing and reporting of measured limits. No passing
100-user rate or maximum sustainable capacity was established.

The enabled-feature diagnostic finally reached traffic after correcting the
synthetic maintenance timestamps to the existing supported second precision.
The successful run needed no compiler retry. It passed correctness with 40/40
alerts in PostgreSQL and search, 40 changed source events consumed by correlation,
8/8 expected suppressions, two automatic incidents, 32 active incident owners,
exact counts in both tenants, two active SSE connections, and zero controlled
errors or remaining watermark lag. Latency failed: source p99 2.95 seconds,
read p99 2.86 seconds, sampled search visibility p99 10.75 seconds.
`status/evidence/scalability-features-small-final.json` contains the actual result.
This is two-user/two-tenant feature correctness evidence, not requested-scale
feature qualification. Earlier compiler/setup failures remain separate failed
records; the bounded diagnostic retry does not alter the production compiler
deadline and counts as an error if used.

Normal maintenance processing advances an incremental checkpoint. A late source
input, changed catalog or legacy history can still require exact replay of that
alert's retained timeline. Reads are paged and preparation occurs outside writer
locks, but the exact reducer still retains linear history in memory and can do
quadratic union/provenance work. This is not a constant-memory guarantee for an
arbitrarily large late-history replay. It needs a separately verified staged
reducer before such a guarantee can be made.

Note aggregation preserves all searchable tokens and supports documents above
1 MiB within the explicit integration allocation. Arbitrarily large aggregates
are not guaranteed to fit one Elasticsearch document/request. Qualification
must account for bulk metadata and worker memory as well as document bytes.
Larger retained aggregates would require a separately designed chunked or
separate-note projection with correct alert-level counts and snapshot semantics.
No token truncation or new user-visible note limit was introduced.

The short combined diagnostics also do not qualify months of retained data,
hot-tenant skew, infrastructure failover, or the design's stronger sustained and
recovery rates. Physical retention and timer-driven cleanup remain deferred by
the approved worker scope; read-time visibility limits remain enforced. Local
fixtures and successful unit tests do not close those deployment gates.


## Final verification commands

The persistent isolated `appchi_local` database was migrated through 0050 with
the dedicated migration identity while application writers were stopped.
Initial full local startup then encountered Windows' reserved TCP range
58447â€“58546, covering the existing Kong Admin and Grafana bindings. The
assistant-selected local solution is a validated optional ignored
`.local/operators/ports.json`; documented defaults, loopback binding and TLS
remain unchanged. This workstation selects free ports 58647 and 58648 after
IPv4/IPv6 reservation and exclusive loopback-bind checks. Both launchers, Python
configuration and Compose consume the same validated values. **29 local setup
unit cases**, Ruff, PowerShell syntax and Compose configuration checks passed.
This does not prescribe company infrastructure ports or alter Windows networking.

Commands below run from `appchi-delivery` unless a repository is named. Private
loopback credentials are loaded by the ignored helper, never placed in command
arguments or evidence. Each real database fixture has a unique disposable name.

```powershell
.venv/Scripts/python.exe scripts/workspace.py test
.venv/Scripts/python.exe .local/run_scale_checks.py appchi-delivery ../appchi-migrations/tests/unit ../appchi-migrations/tests/integration/test_alembic.py ../appchi-indexer/tests/compatibility/test_search_elasticsearch.py ../appchi-domain/tests/compatibility/test_count_elasticsearch.py -q --tb=short --junitxml=qualification/pytest-local-runtime-es.xml
```

A final coordinated unit attempt stopped at 383 domain passes and one failure
(after 16 contract passes): a compile-cache follower's timed wait returned just
before its monotonic deadline and exposed a raw `TimeoutError`. The corrected
loop reuses the original remaining budget, preserves owner failures/cancellation,
and allows shared compilation to finish for other callers. Deterministic early
wake/success/expiry and owner-failure cases are included in the **12 passing**
focused single-flight/logging tests. No deadline or assertion was extended.
The later coordinated rerun passed all 780 unit cases with 65 explicit integration
skips. Existing dependency deprecations and unwritable optional pytest-cache
warnings did not prevent execution. Generated JUnit/report updates change the
delivery evidence fingerprint; final source revisions and dirty-tree hashes are
recorded separately from ignored dependencies and service state.

## Source publication — 2026-10-07

The user subsequently authorized committing and pushing this task's code.
Coordinated `codex/scalability-hardening` branches contain the implementation,
contracts, migrations and evidence. Python dependency locks, the frontend
contracts lock and `workspace/manifest.json` now reference the published immutable
commits. The [publication inventory](../status/evidence/scalability-publication.json)
records the exact sibling revisions; delivery identifies itself as `self`.

Unrelated pre-existing deployment, bootstrap, operator-proof, startup-repair and
agent-setup work remains local. Ignored credentials, runtime state and build
outputs are excluded. Earlier execution used the documented dirty editable
combination and is not proof of a clean checkout of these publication branches.
No local tests were rerun after the user stopped testing. Dependency resolution
changed only Appchi references, not third-party package records. Publication
does not merge the branches, deploy services or close capacity/readiness gates.
