# Local Kafka storage loss and reviewed reset — 2026-10-25

Scope: the isolated Windows development stack only. The user approved the recovery
plan and asked to re-enable the Local Demo correlation rule afterwards. No company
service or production deployment was changed. Tested sources: delivery `0f30cc2` and
design `ec40ad4` working trees with uncommitted REV-39 and REV-40 changes; the full
inventory and run ledger are under ignored
`.local/verification/17be4d91-b62f-4ebe-8535-266854a47ce6/`.

## Finding

The S3 smoke `send` was admitted (202) but never became searchable. A rolled-back
replay of `appchi.development.alerts.received:0:4` failed with `UniqueViolation` on
`kafka_source_results UNIQUE(topic,partition,input_offset)`.

- The broker used the image default `log.dir=/tmp/kafka-logs` in the container layer.
  Compose mounted `kafka_data` at `/var/lib/kafka/data`, which was empty. Broker
  storage was formatted on 2026-10-05 17:17:48 GMT when the container was recreated.
- `scripts/local_runtime.py probe()` then recreated both application topics empty,
  although the 2026-10-04 cutover was recorded (source boundary received offset 4,
  correlation/indexer boundary changed offset 17).
- PostgreSQL still held six confirmed source results at offsets 4-9 from 2026-10-04.
  Today's offsets 0-3 matched their rows by delivery UUID; offset 4 collided, and the
  single partition stopped all admission behind it.
- Diagnostic positions were stale: source 10 and indexer 34, while the broker ended at
  received 7 and changed 12.

## Lost-input assessment

PostgreSQL applied and confirmed every input of the lost received incarnation through
offset 9 (last receipt 2026-10-04 09:38:53 local). Inputs admitted after that and
before 2026-10-05 17:17:48 GMT, if any, are unrecoverable; PostgreSQL holds no
evidence of such admissions and process logs from that period were not retained. The
lost changed incarnation was consumed by the indexer through 34 and repaired from
PostgreSQL by search reconciliation; correlation was gated then and consumed none of
it. The current topics had low watermark 0, so nothing was lost from them.

## Changes

- `deploy/local/compose.yaml`: `KAFKA_LOG_DIRS: /var/lib/kafka/data` and Kafka
  `stop_grace_period: 60s`.
- `scripts/local_runtime.py`: after the recorded cutover `probe()` refuses to create
  missing application topics, and refuses to start workers when the broker ends below
  an offset PostgreSQL observed or holds another delivery at the highest recorded
  source offset. Unit tests: `tests/unit/test_local_runtime_kafka_history.py`.
- Runbook and design: [Kafka failure and recovery](../docs/runbooks/kafka-first.md#failure-and-recovery),
  [local runtime](../docs/local-runtime.md) and REV-40 in the design register.

## Executed recovery

1. Recorded broker state: received high 7, changed high 12; committed offsets source 4,
   correlation 12, indexer 12.
2. Disabled Local Demo rule `fdsfa` through the Product API command as local-admin
   (revision 2, epoch 2, reset reason `disabled`).
3. Stopped the stack. Backups: `.local/runtime/pre-kafka-reset-20261025.dump`
   (`pg_dump -Fc`, 411 table-data entries) and `.local/runtime/kafka-logs-20261025.tar`
   (the stopped container's log directory).
4. Extracted the archive into `kafka_data` as uid 1000. Cluster id and node id were
   unchanged.
5. One PostgreSQL transaction as the migration identity, with row-count assertions:
   relabelled exactly the six stale rows to `appchi.development.alerts.received@lost-2026-10-05`
   and set the three diagnostic positions to the captured broker offsets. Before/after
   comparison of all ten source results: no column other than `topic` changed.
6. Recreated Kafka on the volume: `log.dirs = /var/lib/kafka/data`, `/tmp/kafka-logs`
   empty, high watermarks and all three committed offsets unchanged. Removed the
   container and recreated it again: same watermarks and offsets.
7. Started the stack. The new probe reported that topics match recorded offsets; all
   three workers completed a healthy turn; 17 local v2 gateway checks passed.
8. Re-enabled `fdsfa` through the Product API command after the pending inputs were
   applied (revision 3, epoch 3, reset reason `enabled`). The first attempts returned
   `cel_compilation_unavailable` because native compilation exceeded its one-second
   deadline while the host had under 1 GB free memory.

## Results

| Check | Result |
|---|---|
| Guard on the broken stack before repair | Refused: received ends at 7 below 10; changed ends at 12 below 34 |
| Guard unit tests | 6 passed |
| Pending S3 inputs (offsets 4-6) | Applied to Local Demo and confirmed; source committed 7 |
| S3 `send` after recovery (offsets 7-9) | Passed: admitted, applied, searchable in Local Demo, 404 under Global |
| S2 browser suite after recovery | First run failed at Assign to me: `request_timeout` while CEL compiles exceeded 1 s under host memory pressure (v1 admission, processing at offset 10, Feed, Live and detail passed). After memory was freed it passed (alert `69fb051e…`); the assignee stayed the acting user after reload |
| S3 browser inspection | Passed: the three post-recovery alerts appear in the Local Demo Feed (CEL `node_name` filter) and detail; under Global the API returns 404 and no record renders |
| S4 manual incident lifecycle | Passed on incident `efa9dd2b…` in Global: created open/high/unassigned, linked the S2 alert, note and link persisted after reload, resolve left the alert `firing`, reopen started cycle 2 and kept the link, activity lists link/resolve/reopen. Cleanup unlinked the alert while active and resolved the incident; alert status unchanged |
| Search versus PostgreSQL | Global 27/27, Local Demo 18/18 |
| Pipeline faults after recovery | None |
| Kafka clean stop | Exit 143 in 12 s within the 60 s grace period; the first stop after the copy was killed at 62 s while trimming pre-allocated index files |

## Limits

S3 inspection and S4 were driven through the real HTTPS UI in Chrome with a real
Keycloak sign-in by a scratch Playwright driver, not a committed suite; S4 still has
no automated end-to-end test. S1's group-removal/logout case first failed because
`.local/keycloak/credentials.json` held a stale `local-viewer` id (every other user and
group id matched Keycloak). After that single id was corrected to Keycloak's
`b29f39fa…` (backup `credentials.before-viewer-id-20261026.json`), S1 passed 5/5,
including removal 404, restoration 200 and signed provider logout 401; the viewer is
back in Observers in Keycloak and the Appchi directory. Detection inside the processor
(topic identity at cutover, a specific coordinate-conflict fault, exception class in
logs) remains an open proposal. Retained synthetic fixtures: Local Demo alerts
`93eca115…`, `b79c308c…`, `a5bf9b5b…`, `3a634caf…`, `5b0c1800…`, `7ba9b355…`; Global
alerts `b5683e6f…`, `bd616451…`, `f66683fe…` (unassigned) and `69fb051e…`; resolved
Global incident `efa9dd2b…`. This is not release or production qualification.
