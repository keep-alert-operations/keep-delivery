# Kafka topic incarnation detection — 2026-10-26 (REV-41)

Scope: isolated local checks of the processor change that refuses a recreated received
topic (REV-41). Tested working trees, all uncommitted on
`codex/always-on-correlation-maintenance` over uncommitted REV-39/REV-40 work:
processor `476320f`, migrations `e34fdea` (0054 chained after uncommitted 0053),
design `ec40ad4`, delivery `0f30cc2`. Python 3.13 in delivery's coordinated `.venv`,
confluent-kafka 2.15.1, PostgreSQL 18.2 and Kafka 4.0.0 from the local Compose stack.
The local application database `keep_local` was not migrated or changed.

## Finding

confluent-kafka 2.15.1 builds a topic ID's two 64-bit halves through a C `long`. On
this Windows host (`sizeof(long) == 4`) it reports only the low 32 bits of each half,
sign-extended, even for a `Uuid` constructed from full values:

| Topic | `kafka-topics.sh --describe` | confluent-kafka on this host |
|---|---|---|
| `keep.development.alerts.received` | `YnmTZeuHT_iG2FXpjd3lUQ` | `/////+uHT/j/////jd3lUQ` |
| `keep.development.alerts.changed` | `XvLjZHK5QdW5sr9DukPJTg` | `AAAAAHK5QdX/////ukPJTg` |

The client's own text uses the standard base64 alphabet; the processor encodes the
halves itself in Kafka's URL-safe form and compares only the low 32 bits of each half.

## Results

| Check | Result |
|---|---|
| Processor `ruff check src tests` | Passed |
| Processor unit suite (`tests/unit`) | 49 passed, including 19 new cases |
| Processor integration suite on disposable PostgreSQL | 3 passed; the new case proves the real constraint name, full rollback and replay at the original coordinate |
| Migrations `ruff check src tests` and suite on disposable databases | 32 passed (34 min on a saturated disk); includes the 0054 upgrade and CHECK case |
| Delivery launcher unit tests | 9 passed (new `test_local_runtime_topic_identity.py` and existing history guard) |
| Delivery unit suite (`tests/unit`) | 255 passed, 65 skipped (optional services), 1 failed: `test_maintenance_temporal.py::test_reducer_runs_the_shared_native_cel_program` hit the unchanged one-second native CEL compile deadline (`elapsed_ms=1022`) under host load, as in the REV-40 evidence; the code under test is unchanged here |
| Design `python scripts/check_docs.py` | Passed, REV-01..REV-41 |
| Delivery `qualification/verify_documents.py`, `check_context.py`, `verify_boundaries.py` | Passed |
| Delivery `tests/integration/test_kafka_first.py` on disposable PostgreSQL and the local broker | 5 passed, 1 skipped (no Elasticsearch), 3 blocked: see below |
| Delivery `tests/integration/test_kafka_cutover.py` | Not run: needs a healthy broker |

## Blocked real-broker cases

`test_actual_broker_ack_and_transactional_output_offset`,
`test_real_broker_low_watermark_gap_is_recorded_and_fenced` and the new
`test_real_broker_recreated_topic_is_refused_before_any_input` failed at their first
admission publish (`kafka_publish_unconfirmed`), before any changed code ran. A plain
idempotent produce to a newly created topic then waited 10 s per attempt and failed
with `UNKNOWN_TOPIC_OR_PART`; the topic never appeared in metadata while the broker
repeated "Sent auto-creation request ... to the active controller". Broker heartbeats to
its own in-container controller timed out (4.5–8.9 s) at 22:56 and 23:02 UTC, and the
transaction coordinators reloaded at epoch 188. The volume was 7% used; `docker`
commands themselves took over a minute. A repeat probe about 20 minutes later failed
the same way, and probe topics appeared only minutes after their create calls
returned. The broker was not restarted because it serves the user's running local
stack. The probe topics were deleted; the disposable databases were dropped. Rerun these three cases and the cutover case once the
broker passes the readiness probe in the [development runbook](../docs/runbooks/development.md#repeatable-verification-workflow).

## Limits

Unit stand-ins cover the source assignment refusal and the recording command's history
checks; the real-broker refusal remains unexecuted. No browser journey was run: the
change has no UI, and the local stack was not restarted on the new code. The local
rollout (stop workers, back up, apply 0054, `--record-topic-identity`, restart, smoke)
awaits the user's approval. This is not release or production qualification.
