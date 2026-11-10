# Pipeline load baseline on origin/main packages (2026-10-22)

The first executed pipeline-phase baseline of `qualification/scalability_load.py`.
It is the "before" side for the open performance PRs. It is a local diagnostic on
one Windows workstation, not production capacity or release qualification.
Rerun and compare as described in the
[development runbook](../docs/runbooks/development.md#pipeline-load-comparison-beforeafter).

## What ran

- **Packages:** a fresh, non-editable `poetry sync` of delivery's lock. Each pin
  matches its repository's `origin/main` source:

  | Package | Commit |
  |---|---|
  | domain | `6d4cfa6` |
  | contracts | `eb555e8` |
  | ingestion | `cc80542` |
  | processor | `f483832` |
  | correlation | `d3cd933` |
  | indexer | `c335a20` |
  | migrations | `c5f3e9c` |
  | api | `aad9bf6` |

  Consumer settings recorded by the harness:
  - Source batches: 100 records or 0.1 s.
  - Changed-consumer batch size: 50.
  - No grouped search hints (#11 not merged).
- **Harness:**
  - Run B used a clean tree at commit `a88db67`. It is the reproducible
    reference baseline.
  - Run A used commit `f2c5d3b` on a dirty tree (`run-a.json` records
    `dirty: true`). The only uncommitted change while it ran was a unit test
    being added to `tests/unit/test_scalability_pipeline_phase.py`, which the
    harness does not import. Its harness code otherwise matches run B, apart from
    the clean/valid split and per-scenario error attribution. Use run A as a
    second sample, not as the reference.
- **Workload:** `pipeline-v1` with defaults:
  - 50 tenants, warmup 100.
  - `steady`: 3/s for 60 s.
  - Bursts: 300 alerts each, 50 admissions in flight.
  - Topology: 8 partitions, 4 source, 4 indexer and 4 correlation worker
    processes.
  - `--features`: one correlation rule per tenant, maintenance windows for 20
    tenants.
- **Services:** private `keep-load-*` containers from
  `scalability_services.py`: PostgreSQL 18.2, Elasticsearch 9.0.0, Redis 7.4.2 and
  Kafka 4.0.0, configured like `deploy/local`. Kafka readiness was proven by an
  idempotent produce and a transactional init/produce/commit. The containers
  were removed afterwards with `docker rm -f -v`.
- **Host:** Windows 11, 12 logical CPUs, Python 3.13, Docker Desktop.
  - Each run held the heavy-suite lock.
  - Other sessions' idle containers were present.
  - A CEL spike saturated the Docker VM between 11:25 and 12:08 UTC. Both
    recorded runs were later: A at 12:42–12:53 UTC, B at 13:10–13:22 UTC.

```powershell
python qualification/scalability_services.py up
$env:KEEP_SCALE_ADMIN_URL = Get-Content .local/keep-load-admin-url
python qualification/scalability_load.py --phase pipeline --features `
  --kafka-bootstrap 127.0.0.1:19293 --elasticsearch-url http://127.0.0.1:59401 `
  --redis-url redis://127.0.0.1:56580/0 --drain-seconds 1800 --output <run>.json
```

Results: [run A](pipeline-load-baseline-2026-10-22/run-a.json) and
[run B](pipeline-load-baseline-2026-10-22/run-b.json).

## Validity

Both runs drained every scenario to exact processor, correlation, search and
watermark counts.

- **Run B** exited 0 with `passed=true` and `clean=false`.
- **Run A** predates the valid/clean split. It reports `passed=false` for the
  same kind of retried errors, and its drains were exact too.

Neither run is clean. Run B's 15 native CEL compile-deadline misses in source
apply (the known #10 one-second deadline) and its 13 retried inputs all fell in
the unreported warmup. No measured scenario recorded a fault. Run B also counts
four worker transport restarts on fixture clock freshness (`clock_untrusted`).
These are attributed to `burst_one_tenant` and `burst_global_v1`.

An earlier attempt used 600-alert bursts and a 900 s drain. Its one-tenant
correlation reached only 511 of 600 events, and the run lost its earlier
scenarios. That produced the partial-result fix and the 300-alert default. That
attempt is evidence for the ceiling below, not a measurement.

## Results (run A / run B)

| Scenario | Processor alerts/s | Processor p99 s | Correlation alerts/s | Correlation p99 s | Search drained s | Visible p99 s |
|---|---:|---:|---:|---:|---:|---:|
| steady (3/s offered) | 3.0 / 3.0 | 0.43 / 1.09 | 3.0 / 3.0 | 1.2 / 2.9 | 61.7 / 61.7 | 3.9 / 6.1 |
| burst_spread | 8.1 / 9.7 | 35.5 / 27.5 | 5.2 / 5.7 | 56.3 / 48.5 | 40.5 / 33.5 | 37.9 / 31.0 |
| burst_one_tenant | 4.4 / 4.4 | 66.5 / 65.2 | 1.03 / 0.91 | 288 / 325 | 70.1 / 69.9 | 66.1 / 66.4 |
| burst_global_v1 | 9.2 / 7.4 | 31.4 / 39.1 | 3.5 / 3.4 | 84.9 / 88.2 | 33.7 / 41.5 | 32.5 / 40.6 |

Between these two runs on unchanged packages, most metrics moved 10–25%. Treat
smaller before/after differences as noise unless several runs per side agree.

### Consumer behaviour (run B)

**Source**

- `consume()` returned 33–37 records per fetch in bursts (about 1 at 3/s).
- Every Kafka transaction carried exactly one record (`records_per_publish` 1.0
  in all scenarios). Each apply takes 0.3–0.8 s here, longer than the 0.1 s
  `batch_seconds`, so `_apply_batch` publishes after every record. Each alert
  therefore pays its own Kafka transaction (17–29 ms) and its own
  partition-confirmation write (35–37 ms).
- Each apply runs 5 PostgreSQL transactions: 3 for Global v1, which has no
  maintenance preparation.

**Correlation**

- One handler call and one Kafka commit per changed event.
- 0.23–0.28 s per event across tenants, rising to 0.89 s per event for one busy
  tenant.

**Indexer**

- 1.9–7.5 records per batch-handler call and 0.13–0.54 commits per record.
- Reconciliation runs 5–44 PostgreSQL transactions per indexed record, highest
  while one tenant's watermark lags.

## Inputs to the two design decisions

### Partition-confirmation write cost

- The confirmation write cost 35–37 ms per alert.
- It was 4–10% of active source turn time: 8.8% in steady, 10% in the spread
  burst, 4.5% for one tenant and 7.1% for Global v1.
- Every confirmation update was non-HOT: `kafka_source_results` HOT ratio 0.0 in
  every scenario, as 0051 predicted.
- Cluster WAL was 40–96 KB per alert. This covers everything the pipeline wrote,
  not only the confirmation.

On these packages the cost is per alert, not per batch, because batches
degenerate to one record. A per-partition "confirmed up to" offset would remove
the non-HOT index writes, but at most the 4–10% share. Batching more records
per Kafka transaction (or a batch window longer than one apply) would amortize
both the Kafka transaction and the confirmation. Measure again after the perf
merges. If `records_per_publish` stays 1.0, the window, not the row flag,
dominates.

### Per-tenant serialization ceiling

- **Processor.** One busy v2 tenant reached 4.4 alerts/s, 0.45–0.54× the spread
  burst. Lock waits rose from 0.03–0.05 to 0.54–0.63 waiting backends on average,
  dominated by `Lock:tuple` and `Lock:transactionid` (the tenant row lock).
- **Global v1 (all v1 traffic).** 7.4–9.2 alerts/s, 0.77–1.14× spread. It is
  cheaper per alert (no rules or maintenance), but had the most lock waiting:
  0.78–1.25 backends on average, waiting in 37–45% of samples.
- **Correlation.** This is the tighter ceiling. One tenant's changed events share
  one partition and one consumer. At 0.9–1.0 events/s against 5.2–5.7 spread,
  a 300-alert burst took about 5 minutes to correlate (p99 290–325 s). The cost
  per event grew as that tenant's live evidence grew. With 600 alerts it did not
  drain in 900 s.

The measurements support the stated concern. On this workstation a single busy
tenant is limited to a few alerts per second through source application and
about one per second through correlation, regardless of worker count. Whether
that matters depends on expected per-tenant peak rates, which these local
numbers cannot set.

## Rerunning after the perf PRs merge

1. After merges and repinning, install the new pins in a short-path environment
   (non-editable `poetry sync` of the updated lock), or use editable siblings at
   the merged commits.
2. Start the services, then run the exact command above at least twice (`-1`,
   `-2`), on an idle host, under the heavy-suite lock.
3. Compare:
   `python qualification/compare_pipeline_load.py --before qualification/pipeline-load-baseline-2026-10-22/run-a.json qualification/pipeline-load-baseline-2026-10-22/run-b.json --after <after-1>.json <after-2>.json`.
   The tool refuses differing workloads and invalid runs (an undrained scenario
   or warmup), and reports medians per side.
4. Watch these metrics:
   - Source `records_per_publish` and confirmation ms per record (window and
     confirmation).
   - Correlation commits per record (#7).
   - Indexer records per handler call and commits per record (#11, plus whether
     `index_groups_available` turns true).
   - Reconciliation transactions per record while idle (#15).
   - The `serialization` ratios.

Remove the services with `python qualification/scalability_services.py down`.
The packages were installed in a private short-path environment outside the
repository. It can be deleted, or kept for more "before" samples.
