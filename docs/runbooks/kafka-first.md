# Kafka-first protocol cutover and recovery

This runbook implements [the normative pipeline](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-kafka-alert-pipeline.md).
Production/shared-service changes require separate authorization and service-owner
evidence. Do not infer production readiness from local test results.

## Offline cutover

1. Preserve backups and independently protected fences using the supplied recovery
   contract. Apply the forward migrations with the migration identity. Keep
   `APPCHI_KAFKA_ADMISSION_ENABLED=false` on all new ingress processes.
2. Fence every old ingress endpoint, including the development combined Product
   API and Grafana routes. Drain accepted old work with the compatible old image:
   source items, received/changed outboxes, correlation and maintenance effects,
   and visible projection proof. Stop all old workers after draining. Do not use
   new workers to interpret old received envelopes.
3. Install the Appchi Kong plugin in the owner-supplied Kong image and run its
   forward plugin migration. Configure Key Auth before Appchi policy, pinned private
   mTLS, credential stripping, direct serving-member proof endpoints and host clock
   evidence. Reconcile existing operators using the new binding profile and verify
   the complete serving pool. Existing legacy identity probes are insufficient.
4. With ingress and workers stopped, run `python -m appchi.kafka_cutover
   --ingress-and-legacy-workers-stopped` in the configured application environment.
   The command verifies PG drain, blocks saved rules whose compiled fields/grouping
   reference `assignee_id`, captures Kafka ends for both topics, initializes the
   three new groups and records partition counts/boundaries and both topic IDs. The
   broker must report topic IDs. A partial failure is retried while ingress remains
   fenced. Never reset these groups on a live system.
5. Supply the existing immutable Global UUID, Kafka credentials and clock-health
   binding to ingress. Set `APPCHI_KAFKA_ADMISSION_ENABLED=true` only after cutover.
   Start source, correlation and indexer roles, plus existing non-alert roles. Do
   not start alert relay or `*-hints` roles. Verify readiness, offsets, rejection
   diagnostics, actual v2 denial/allow and completed search passes before opening
   traffic. Edit/reset blocked correlation rules explicitly; never reinterpret them.

For isolated development, the cutover command also accepts
`--local-config .local/runtime/kafka-protocol.json`. This writes only enabled state
and Global UUID after success. Regenerate the local identity environment and restart
managed application processes. The launcher refuses new workers without recorded
cutover. Development without a clock file is explicitly unqualified. This task's
test database is separate from the existing local application database; test
success does not automatically cut over that application's retained data.

## Workload bindings

The ingress templates require dedicated `INGRESS_CONFIG_SECRET` and
`INGRESS_BINDING_SECRET`: Kafka and private TLS credentials only. Do not place a PG
URL, wrapping key, LDAP, Grafana management or Product API credentials in them.
Set Global UUID through the explicit template parameter. Network egress allows
only DNS, Kafka and required observability; private ingress accepts only the
configured Kong peers. No PostgreSQL rule is needed for ingress.

The owner publishes fresh per-workload clock evidence in the mounted
`CLOCK_HEALTH_CONFIGMAP` as `health.json`: synchronized boolean, numeric
`offset_seconds`, and ISO8601 UTC `measured_at`. The evidence must attest the host
running that workload, be updated within 60 seconds, and prove absolute offset at
most 1 second. Bind scheduling/evidence publication accordingly; a shared stale
timestamp is insufficient. Product API uses the same clock evidence for mutations. Product/worker mounts also contain `pg-health.json`, supplied by the PostgreSQL owner for the currently authoritative database clock; `APPCHI_DATABASE_CLOCK_HEALTH_FILE` binds it. Ingress has no PG clock dependency.
Kong uses its plugin clock-health file; only isolated local setup opts out.

Source needs read access to received, transactional write to changed and its stable
partition transactional IDs. Correlation and indexer use separate read-committed
groups. Product/maintenance/relationship workers may send best-effort changed hints.
All database workers retain their role-specific least-privilege PG identities.
Owners supply endpoints, topology and ACLs; templates do not prescribe allocations.

## Failure and recovery

- A 503 publication result is uncertain: Kafka may contain the record. Clients retry
  failed members only; a resubmission receives a new UUID. No receipt lookup exists.
- PG failure stops consumers/commands but does not stop healthy Kafka admission.
  Kafka failure stops acknowledged ingress/source transactions; search authority
  reconciliation still repairs committed PG changes.
- `/api/admin/tenants/{tenant_id}/kafka` is a global-admin diagnostic snapshot of
  recent sanitized rejections, shared partition faults/observed positions and that
  tenant's search passes. It exposes no source payload or secret. Broker committed
  offsets remain authoritative; PG observed positions may lag them.
- A source receipt ahead of the database clock is held, not faulted: only that
  partition waits for the measured lead, logged as `source_receive_time_ahead` (a
  warning above 100 ms). A lead over 1 second records that partition fault and retries
  after 5 seconds; it means ingress and database clocks differ beyond the health
  contract, which the clock owner repairs.
- On an offset below the low watermark, preserve the loss interval and stop source
  or correlation recovery. Owners establish what retained data remains. A reviewed
  reset requires an explicit lost-input assessment, preservation of delivery/key
  fences and fresh correlation epoch boundaries before resuming available offsets.
  Do not silently select earliest/latest or claim lost source inputs can be rebuilt
  from PG. Indexer state can be rebuilt from PG with the existing fenced index/alias
  rebuild protocol and authority reconciliation.
- Lost broker storage (a recreated topic) restarts offsets under existing replay
  proof. The source refuses the assignment: its partitions record
  `source_topic_identity_changed` and the worker logs the recorded and observed topic
  IDs. Another delivery at a coordinate PostgreSQL recorded records
  `source_coordinate_conflict` and fences that partition without a timed retry. The
  local launcher also refuses to start workers in that state. Apply the reviewed
  reset of [REV-40](https://github.com/appchi-alert-operations/appchi-design/blob/main/decisions/design-register.md#rev-40--reviewed-reset-after-lost-local-kafka-storage-2026-10-25):
  stop writers and back up PostgreSQL and the broker log; assess lost inputs after
  the last confirmed offset; disable rules whose epoch spans the loss through the
  normal command; in one transaction relabel only the stale rows' `topic` to
  `<topic>@lost-<date>` (never delete them) and set diagnostic positions to broker
  committed offsets; with source, correlation and indexer stopped, run
  `python -m appchi.kafka_cutover --record-topic-identity`; restart, verify
  processing and re-enable those rules. The command records the new topic IDs only
  when each partition still holds PostgreSQL's recorded history, and otherwise
  fails with `kafka_topic_history_mismatch`. The
  [2026-10-25 local evidence](../../qualification/local-kafka-reset-2026-10-25.md)
  records one execution, before this check existed.
- A cutover recorded before migration 0054 has no topic IDs, and the updated source
  refuses with `kafka_topic_identity_unrecorded`. Stop source, correlation and
  indexer, apply 0054, run `python -m appchi.kafka_cutover --record-topic-identity`,
  then start the updated workers.
- Retained source failures log `source_partition_retry` (or `source_partition_fenced`)
  with partition, offset, reason, exception class and the AppError code or SQLSTATE.
  They never log exception text or payloads; `/api/admin/tenants/{tenant_id}/kafka`
  lists the recorded faults.
- Never reclaim unconfirmed source changed bytes or required correlation/rebuild
  proof. Maintenance timeline cleanup remains held until its replay boundary is
  verified. Database rollback recovery stays closed while the independent Kafka
  delivery/retired-key baseline is unverified.
- After protocol 2 admission, rollback to a legacy image requires fencing traffic
  and draining with compatible protocol 2 consumers. A database rewind is not an
  application rollback strategy.
