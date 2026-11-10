# Kafka-first implementation record

Requested 2026-09-30. This is delivery tracking, not a runtime specification.
The authoritative contract is [Kafka-first alert pipeline](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-kafka-alert-pipeline.md),
registered as REV-33. Existing uncommitted application and design work was retained.

- [x] Read handoff, project guidance and affected design contracts.
- [x] Incorporate accepted changes and assistant-selected decisions into normative specifications.
- [x] Kafka admission, trusted gateway routing and generated HTTP/event contracts.
- [x] Transactional source consumer, immutable replay proof and migration fences.
- [x] Kafka correlation trigger, manual resolve and maintenance integration.
- [x] Best-effort search notifications, bounded reconciliation and rebuild/cutover.
- [x] Kong policy plugin, provisioning propagation and workload templates.
- [x] Focused verification, regressions, evidence and remaining qualification gaps.
- [x] Delete temporary handoff only after implementation and verification finish.

The temporary `KAFKA_FIRST_REDESIGN_HANDOFF.md` was removed after its eight open
decisions and accepted changes were incorporated into authoritative design/code
and the verification below completed. No other project document was deleted.

Production deployment and shared company services are outside this work.

## Implemented scope

Ingress has no Keep database connection. It captures receipt UTC, validates strict
native or Grafana input, publishes batch members concurrently with three bounded
attempts, and acknowledges only broker confirmation. Legacy sender IDs are syntax-only;
receipt endpoints are removed. The generated received schema is version 2.

Source workers apply delivery UUIDs once in PostgreSQL, retain immutable publication
bytes and first input coordinates, then publish each input partition's bounded batch
of changed events plus the next offset in one Kafka transaction (at most 100 inputs,
closed after 100 ms of application). Stable partition producer IDs fence prior
owners. Correlation consumes tenant-keyed changed events record by record with its
own Kafka sequence, independent of how source transactions group them. Manual commands no
longer reject queued source work; resolve clears evidence atomically. Inline
maintenance retains source provenance and returns the final committed command revision.

Search uses versioned current PostgreSQL state and permanent deletion identities.
Bounded scans repair lost manual hints; only a complete pass advances its captured
searchable lower bound. Existing fenced rebuild and retirement protections remain.
Payload cleanup preserves delivery/key/correlation fences and blocks unknown proof.

The packaged Kong OSS plugin enforces nonsecret policy generations in Kong's own
database, strips forged identity/key headers and supplies bounded trusted routing
context. Provisioning verifies prepared denial and subsequent activation; revocation
is pending until serving-node denial is proved. Deletion additionally drains captured
Kafka offsets after the authorization lifetime. Local installed Kong tests exercise
the plugin, beyond the HTTP adapter fixtures.

Forward migrations 0040, 0041, 0044 and 0045 preserve historical migrations and old
tables, add replay/reconciliation/cutover state and fence legacy alert writes. Offline
cutover blocks compiled assignee-dependent correlation rules without interpreting
their source differently. Migration 0054 (REV-41) records both topic IDs at cutover;
the source refuses another received-topic incarnation on assignment and fences a
partition whose input meets another delivery's recorded coordinate. Deployment templates separate ingress secrets from database
credentials and bind host/database clock evidence. See the [cutover runbook](runbooks/kafka-first.md).

## Executed evidence

Tests used isolated local PostgreSQL, Kafka, Elasticsearch and Kong; the application
database was not automatically cut over. JUnit suites overlap, so counts below must
not be summed as unique coverage. Report times are available in the files.

| Verification | Actual result | Evidence |
|---|---|---|
| Full backend unit directory | 530 passed; 65 optional-service tests skipped | [Unit report](../qualification/kafka-first-unit.xml) |
| Final HTTP/clock/workload checks | 19 passed | [HTTP and clock report](../qualification/kafka-first-http-clock.xml) |
| Actual Kafka client against an unavailable loopback broker | 1 passed; three attempts, identical bytes, 503 within five seconds and no PG access | [Unavailable-broker report](../qualification/kafka-first-unavailable.xml) |
| Real Kafka/PG/ES protocol | 7 passed | [Runtime report](../qualification/kafka-first-runtime.xml) |
| Offline cutover and saved CEL migration | 3 passed | [Cutover report](../qualification/kafka-first-cutover.xml) |
| Maintenance/correlation/replay focused set | 78 passed in the completed rerun | [Focused report](../qualification/kafka-first-maintenance-retry.xml) |
| API inline revision/replay, processing/warmup | 19 passed | [API report](../qualification/kafka-first-inline-api.xml) |
| Final all-source-consumed and newer-membership fences | 30 passed | [Provenance report](../qualification/kafka-first-provenance-fences.xml) |
| Receipt clock reversal across a scheduled end | 1 passed | [Reverse-clock report](../qualification/kafka-first-provenance-reverse.xml) |
| Operator policy, provisioning and installed local Kong | 69 passed, including actual Kong opt-in cases | [Operator report](../qualification/kafka-first-operators.xml) |
| Kafka retirement and restore fences | 57 passed | [Recovery report](../qualification/kafka-first-recovery.xml) |
| Real Elasticsearch search/rebuild | 8 passed | [Search report](../qualification/kafka-first-search.xml) |
| CEL editor in Chrome, controlled API responses | 7 passed | [Browser report](../qualification/kafka-first-browser.xml) |

The protocol set covers acknowledgement-before-202, immutable retry bytes/UUID/time,
same-delivery duplicates, independent resubmission, terminal semantic rejection,
PG-commit/Kafka-commit replay, lost commit confirmation, a real broker low-watermark
gap, lost manual search notification and concurrent scan watermark safety. Cutover
tests start the new groups after legacy records and reject undrained legacy work.

Reports describe the code at each execution. The later API/provenance checks cover
the final narrow changes after the earlier full unit and 78-case runs; no aggregate
full-system or production qualification run is claimed.

OpenAPI/event regeneration and drift checks, generated TypeScript checking, scoped
Ruff, Python compilation, seven OpenShift template parses/substitution checks and
three local PowerShell script parses passed. The [documentation audit](../qualification/kafka-first-doc-check.json)
records local link/fence checks and preservation of all 55 original issue IDs,
68 original numbered decisions and contiguous REV-01–REV-33 entries. A separate
check resolved all 47 referenced local Markdown anchors. Git whitespace checking
passed. No external-link or production runtime verification is implied.

An earlier maintenance run hit the existing one-second spawned CEL compiler deadline
on Windows before executing the target behavior: 13 passed, 1 failed. Its
[failed report](../qualification/kafka-first-maintenance.xml) is retained; the 78-case
rerun passed without loosening the compiler limit. Cold-start/load qualification
remains necessary. Browser assertions initially exposed one stale label expectation
and a correlation reference panel still requesting the maintenance catalog; both
were corrected. A Windows development-server teardown stalled; the final browser
run reused that server and completed successfully before its owning runner was stopped.

## Remaining release work and qualification

- **Pre-existing implementation gap, still fenced:** protected rollback reconstruction
  and safe reopening are incomplete. Independent recovery evidence must additionally
  reconstruct Kafka delivery UUID/digest/coordinates, retired-key UTC boundaries and
  projection sequences. Recovery assessment explicitly rejects the missing baseline;
  restoring PG does not authorize stateless ingress. This task did not declare that
  pre-existing restore capability complete.
- **Conservative maintenance retention hold:** timeline cleanup stays blocked in Kafka
  mode until a verified replay/retention boundary is supplied. No unverified time-only
  cleanup was enabled. Operational proof and safe hold release remain qualification work.
- **Production/service-owner qualification:** multi-node Kong membership/propagation
  and authenticated proof aggregation; production TLS, ACLs, topic topology and clock
  feeds; cold/full-batch deadline behavior; rebalances/leader loss/network partitions;
  full operator deletion chaos with live Kafka; near-boundary cross-clock scenarios.
  The extended multi-window/manual-reset reverse-clock matrix remains unexecuted;
  the specific reverse-clock regression above passed.
- **Capacity and observability:** concentrated 250/s for 24 hours, 500/s recovery,
  search pass duration/cost, retention headroom, rendered p99/SLO evidence, and actual
  broker lag/low-watermark/receive-age monitoring are unqualified. Tenant health
  reports Kafka source status as not measured; PG observations are not broker offsets.
- **Existing local application:** its retained database/consumer groups and operator
  binding require the documented fenced cutover and renewed evidence. (The former
  correlation preview profile is no longer used; REV-39 makes correlation and
  maintenance always on.) Disposable test success does not migrate that user's state. The
  updated launcher refuses unsafe old/new worker mixing.
- **Topic incarnation (REV-41):** the correlation and search groups do not yet verify
  the changed topic's identity. Topic ID support and a broker-storage loss drill on the
  supplied broker remain qualification work.

No production deployment, shared-service change, production allocation or retention
guarantee is claimed. Kafka retention can lose acknowledged unprocessed inputs by
the user's accepted contract.
