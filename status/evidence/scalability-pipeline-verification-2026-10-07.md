# Pipeline scalability verification, 2026-10-07

The pipeline changes have focused correctness evidence. The requested complete
60-second workload of 5,000 alerts/minute, 100 active users and 50 tenants is **not
qualified**. The final large run failed during preload, before the user cohort.
The small enabled-maintenance/correlation workload passed correctness and failed
latency. No production deployment, release pin update, or capacity approval follows
from these local results.

A subsequent paced baseline reached all 100 active users with only two alerts/s.
Its alert pipeline met the sampled latency criteria, but 197 of 400 user reads
returned HTTP 503 `request_timeout`. The full user workload therefore failed even
at that low arrival rate on this workstation; this does not establish a production
capacity ceiling or isolate an application-only cause.

The final instrumented repeat after moving clock-file I/O off the API event loop
also kept up with the two-alert/s pipeline but failed user reads. Its separate
server/client timing evidence is below. No further workload retry was performed.

## Execution scope

These runs used the existing coordinated editable Python environment on Windows,
real isolated PostgreSQL, Kafka, Elasticsearch and Redis, a separate HTTPS uvicorn
API, and separate production-lifecycle worker processes. The fixture provides
synthetic normally authorized sessions and trusted Kafka admission routing. It
does not exercise OIDC, Kong/Ingestion HTTP, browser rendering or OpenShift pods.
The final enabled-feature profile is explicitly synthetic, not production-qualified.

The user authorized temporarily pausing three unrelated BI containers. The lead
performed that pause and later reported restarting the exact three containers;
the pipeline worker did not manage them. PostgreSQL was recreated by the lead with
`init: true` using its preserved volume after an observed PID1 child-crash recovery.
The two final runs below used that recovered PostgreSQL and the paused BI state.
Application, Kafka admission, native CEL and query deadlines were unchanged.

Both final runs applied migration `0050_alert_notes_projection_lookup.sql`, SHA256
`a8c22652cc097fe54685841145db8cc63fcf994387afa473780bdb22863d7f33`.
The worker health implementation used SHA256
`8a9094785272b4d6fa210f58e5cf456b39392dd6a1bcbc87272273cb2dac12a3`.

## Final enabled-feature diagnostic

From the delivery repository, using the existing private local wrapper:

```powershell
.venv/Scripts/python.exe .local/run_scale_load.py --worker-mode processes --features --seconds 10 --rate 2 --users 2 --tenants 2 --seed 20 --seed-batch 5 --partitions 4 --processors 2 --indexers 1 --correlators 1 --api-pool 8 --drain-seconds 180 --output status/evidence/scalability-features-small-final.json
```

Evidence: [scalability-features-small-final.json](scalability-features-small-final.json).
Exit 1 because latency criteria failed; correctness passed:

- 20 seed and 20 measured arrivals acknowledged; 40 receipts/applied alerts and
  searchable documents, zero rejected inputs.
- All 40 source events consumed by correlation; 8/8 expected suppressed alerts,
  2 incidents and 32 active ownership records.
- Both tenants had exact final counts, zero watermark lag, zero pipeline faults
  and zero diagnostic errors; both SSE connections stayed active.
- Source application p99 2.95s, API read p99 2.86s, sampled search visibility p99
  10.75s. Search visibility is a one-second-sampled upper bound, not browser latency.
- Summed sampled worker peak RSS: 266.5 MiB. All owned fixtures/processes cleaned.

Earlier feature setup attempts are preserved separately. They exposed cold native
compile deadlines (about 1.025–1.153s), an interrupted PostgreSQL CREATE DATABASE,
and a fixture-only local-time formatting bug. The helper now uses the supported
seconds precision. One explicitly recorded same-process setup compilation retry is
permitted; any such failure keeps overall qualification false. The final feature
run above needed no compilation retry and does not erase earlier cold failures.
The exact ambiguous database `keep_scale_8fc710e7b4074595a1a9064b8d2a285c` was
subsequently checked and confirmed absent.

## Final requested-profile attempt

```powershell
.venv/Scripts/python.exe .local/run_scale_load.py --worker-mode processes --seconds 60 --rate 83.33333333333333 --users 100 --tenants 50 --seed 5000 --seed-batch 10 --partitions 32 --processors 24 --indexers 2 --api-pool 32 --drain-seconds 600 --output status/evidence/scalability-target-core-init-bi-paused.json
```

Evidence: [scalability-target-core-init-bi-paused.json](scalability-target-core-init-bi-paused.json).
Exit 1. API and all workers started, then preload stopped after 19.14s:

- 2,020 seed offered, 2,011 acknowledged, nine admission 503s associated with Kafka
  request timeouts. Measured arrivals offered/accepted: **zero**.
- 199 confirmed source worker turns; this is not an exact final PostgreSQL count.
- Two source-worker Kafka exceptions. Source PostgreSQL apply p50 647ms/p99 3.16s;
  full source turn p50 1.19s/p99 6.44s.
- Summed sampled worker peak RSS: 1,570.4 MiB. Initial available host RAM: 2.78 GiB.
- All owned worker/API processes, database, roles, index, topics and temporary
  fixture files completed cleanup. No forced worker termination was recorded.

Seed offering was unpaced setup traffic (about 105.5/s average before abort), so
this is failed setup/recovery stress evidence, not a completed steady 83.3/s test.
The local PostgreSQL limit was 100 connections, three reserved. This topology's
maximum pools were 84 connections (26 workers at two plus API32), leaving room for
fixture administration. Adding replicas did not establish better local latency.

The earlier [15-second combined run](scalability-harness-100-users.json) did reach
100 active users with 50 tenants and 1,250 measured accepted alerts. It failed
latency and fault criteria. The subsequent eight-source full setup
[attempt](scalability-target-core-processes-bounded-setup.json) acknowledged all
5,000 seed but observed only 3,887 applied after its 600-second processing drain;
no measured cohort started. That older runner started its drain deadline after
seed offering. The current runner has one deadline spanning offering, authority
drain and search drain. Neither eventual recovery nor smaller diagnostic success
constitutes requested-profile capacity qualification.

## Focused correctness checks

```powershell
.venv/Scripts/python.exe .local/run_scale_checks.py keep-delivery tests/integration/test_scalability_pipeline.py tests/integration/test_kafka_first.py -q --disable-warnings --tb=short
```

Initial result: 17 passed, two failed (81.39s). The failures were a new test's
transaction-isolation fixture setup and an existing Elasticsearch index-creation
timeout. The former was corrected to select REPEATABLE READ before any query;
the latter now gives only create/delete fixture administration 30s, leaving the
runtime projection client deadline unchanged. Focused reruns passed the corrected
live-note test and the existing lost-manual-hint test (the latter 1 passed in
8.53s). Thus all 19 cases passed across the focused runs, not one clean 19-case run.

The same focused rerun passed both parameters of
`test_decision_scope_excludes_expired_and_old_epoch_evidence_but_keeps_targets`.
This validates the domain's current/live evidence lock scope using real PostgreSQL.

Coverage includes >1MiB note projections with all tokens searchable, skipping
empty/deleted note streams, failed-tenant isolation, replica ownership and takeover,
idle-tenant scheduling fairness, lost hints and behind-cursor writes, independent
periodic audits, audit error isolation, source gate false-to-true rollback, immutable
source replay, real Kafka output/offset transactions and retained-gap handling.

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_scalability_pipeline_fixture.py -q --basetemp=.local/pytest-scale-final-fixture
```

Four passed in 19.98s; one pytest-cache permission warning. This covers bounded
clock-file sharing retries, preserving the previous complete sample, and the
private process adapter retaining the production lifecycle. Later cleanup/progress
diagnostic changes were exercised by the real feature and target attempts above.
Fresh Ruff checks passed all owned pipeline/indexer/source/harness files; Git diff
whitespace checks passed, with existing Dockerfile CRLF warnings only.
Broader coordinated units, frontend/browser and migration qualification are tracked
by the lead; earlier broad results must not be reused as fresh evidence for later edits.

## Follow-up paced 100-user baseline

The user asked to continue measuring this workstation with its original BI services
running. After the lead verified service readiness and completed its checks:

```powershell
.venv/Scripts/python.exe .local/run_scale_load.py --worker-mode processes --seconds 30 --rate 2 --users 100 --tenants 50 --seed 50 --seed-rate 2 --seed-batch 10 --preload-seconds 600 --partitions 8 --processors 4 --indexers 2 --api-pool 32 --read-pause 2 --drain-seconds 120 --output status/evidence/scalability-baseline-100-users-paced.json
```

Evidence: [scalability-baseline-100-users-paced.json](scalability-baseline-100-users-paced.json).
Exit 1; end-to-end correctness and latency flags false because user requests failed.
The API is one uvicorn process; worker counts are explicit fixture topology, not
a claim about equivalent production pods.

- All 50 seed and 60 measured arrivals acknowledged. The 60 arrivals completed in
  29.53s; all 100 users were ready beforehand and minimum active SSE connections
  remained 100 throughout the measured window.
- All 110 receipts/alerts and search documents present, zero rejected inputs,
  all 50 final tenant counts exact, zero pipeline faults and watermark lag.
- Admission p99 156.8ms, received-to-recorded p99 364.1ms, sampled search visibility
  p99 4.468s. All 60 search samples observed; generator lateness p99 61.1ms.
- 400 measured-cohort read requests, 197 HTTP 503 `request_timeout` responses
  (49.25%); read p99 19.072s. The client-observed duration includes scheduling and
  transport effects and must not be presented as a changed server deadline.
- Startup took 38.31s after preload. Requests begun in the measurement window
  finished afterward, extending combined elapsed time to 46.22s. The offered alert
  window remained 30s; the tail must not be counted as additional steady traffic.
- Host available physical RAM was about 548 MiB at start and 350 MiB at measurement
  start. This is a materially memory-constrained workstation observation, not proof
  of the cause of each request timeout.
- All owned processes, database, roles, index, topics and local temporary fixtures
  completed cleanup. The three BI containers remained running. The heavy slot was
  released to the lead for persistent OIDC/browser work; no further load retry ran.

The optional `--seed-rate` now paces setup without accumulating catch-up bursts;
omitting it preserves unpaced batches. `--preload-seconds` records a separate
600-second setup budget, while this run's drain budget is 120s. Six deterministic
fixture tests passed in 2.16s and Ruff passed before this baseline. The evidence JSON
records the paced harness SHA256. The table below was captured before this follow-up
pacing change and remains provenance for the earlier final feature/target runs.

## Final instrumented repeat after clock I/O offload

After the lead's coordinated 780-test pass (65 skips), the same paced baseline ran
once with `--response-timings` and output
`status/evidence/scalability-baseline-100-users-clock-offload.json`. All other
workload arguments above were unchanged. The original three BI containers were
running; unused Keep Keycloak/Kong database services had been stopped. Production
request, admission and compiler deadlines were unchanged.

Evidence: [scalability-baseline-100-users-clock-offload.json](scalability-baseline-100-users-clock-offload.json).
Exit 1; both overall flags false. All 100 users were ready before the window and
remained SSE-connected; all 60 measured arrivals and 50 seed were accepted and
applied, all 110 documents searchable, all 50 final counts exact, zero pipeline
faults and watermark lag. Admission p99 was 188.7ms, source p99 397.3ms and sampled
search visibility p99 4.895s. The source pipeline kept up at this low arrival rate.

User reads still failed: 195 of 382 responses were HTTP 503 (51.05%), including
167 `request_timeout` and 28 `clock_untrusted`. There were no client transport
exceptions or missing diagnostic headers. The 30-second arrival window completed
in 29.535s; finishing the already-started read cohort extended elapsed time to
44.258s. Available host physical RAM was about 1,486 MiB at startup and 908 MiB at
measurement start, so comparisons with the previous run are not a controlled
memory experiment.

| Measured endpoint | Successful / total | Error responses | Client headers p99 | Complete client response p99 | ASGI entry to response-start p99 |
| --- | --- | --- | --- | --- | --- |
| Feed | 42 / 191 | 149 request timeouts | 19.515s | 19.523s | 10.234s |
| Count | 145 / 191 | 18 request timeouts, 28 clock-untrusted | 10.394s | 10.415s | 10.036s |

The test-only outer wrapper delegates to the production ASGI app and its unchanged
lifespan, authorization and budgets. It adds only an elapsed-time response header;
the client separately measures headers and complete body delivery. The table shows
server response-start timing near the ten-second request deadline while some
client-visible durations are longer. It does not identify how much of that extra
time was transport, scheduling before ASGI entry, or delivery after response-start.
These are separate distributions; their p99 values must not be subtracted as if
they were matched requests. Nine deterministic diagnostic-fixture tests passed in
0.56s before this run, with Ruff clean. Wrapper/harness hashes are in the JSON.

Read-only inspection then found a concrete clock-validation race: the offloaded
check still evaluated newly read evidence against frozen request `received_at`.
If execution waited until after a new clock publication, fresh evidence could
appear more than one second in the future relative to that older timestamp. The
users/API owner corrected this with a deterministic queued-check regression and
reported 14 focused tests passing. Independent read-only review found no actionable
issue: request timestamps and deadlines remain unchanged, while actual future,
stale, bad-offset and unsynchronized evidence still fail closed. That subsequent
fix has **no new load result** here. It does not explain away the
167 observed request timeouts. The requested target remains unqualified.

Cleanup completed for every owned process and fixture. The heavy slot was released
to the lead for persistent browser work. No further performance run was started.

## Earlier handoff source hashes

These SHA256 values were read from the final working tree. They identify local
editable source, not published package artifacts or a committed release manifest.

| Workspace-relative source | SHA256 |
| --- | --- |
| keep-processor/src/keep/kafka_source.py | 84fa40dff9b0fbc7e7a808b592d10c2cedc18774e623c1cc423982acc33a9b33 |
| keep-domain/src/keep/kafka_changed.py | a72688e0a4edbb4bc864d36421f105f51f931128ce2f8dfee716a89181fef030 |
| keep-domain/src/keep/pipeline_runtime.py | 642db5363ee21c92bfc7d1fe038f82993c54d537c4294168a4ae0a3b241dc3dc |
| keep-domain/src/keep/db.py | fb2e7b93af3df145e145f7929a7f7d783f55a1c50016c9cb5640b6e06c07877e |
| keep-domain/src/keep/recovery/guard.py | ec1abfb981419d5f52c32da7c4f1f54ef0cb61ef6cd59f99771edfebbdf92633 |
| keep-domain/src/keep/search/projection.py | 8204774874e7b6a4f8c9d4a742ec63052e82b24cec917dcb643f3f9b215f85be |
| keep-indexer/src/keep/search/reconciliation.py | 7df1c5ab4e6ac3b605f38e90fa9f1ea75fca19afa65a789e722fdf9a2c03560e |
| keep-indexer/src/keep/search/writer.py | 5f11eca6599c45ff67b6929063c1f4603458ef34c86aa2e273a285b5517c2688 |
| keep-indexer/src/keep/search/consumer.py | 2e61bd369e60934c10f6179098e8d4ab40c77e60688095d534074062a385fda5 |
| keep-correlation/src/keep/correlation/consumer.py | 7f983ca8296ad945cc6854e5699ca3fbee4ea16430d0c65aedc0d2ef76a049fd |
| keep-delivery/qualification/scalability_load.py | 234533393fd78df3be137e4bd2e7eaab166f58383be6fb5788b0647b87577471 |
| keep-delivery/qualification/scalability_worker.py | 1973a15897540af8ae24d6c4dd5b10710b5ad7b89b4b41a146d564cb811ccb06 |
| keep-delivery/qualification/scalability_feature_fixture.py | 1e31e761c9a6e4d62fddf64bfa85fb1dacc97ca742f828059c832ab6d195efb3 |

## Remaining limits

Production capacity, the complete requested workload, concentrated-tenant skew,
large historical populations, and the design's 250/s sustained/500/s recovery
profiles remain unqualified. The small feature diagnostic establishes functional
suppression/correlation effects only. Cold compiler availability and observed read/
search latency remain qualification concerns. The finite single-document projection
allocation is explicit and never truncates notes; unbounded note growth still needs
qualified request/memory limits or a separately designed chunked representation.
