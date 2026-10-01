# Configuration and command retention

Current scope: automated retention workers are deferred under REV-34. The
procedures below preserve historical implementation context and do not authorize
deploying those roles. See [current worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md).

Run migrations through 0034 before these workers. Normal runtime Settings, a
current deployment epoch and the worker database role are required. The recovery
capability remains closed until the supplied independent evidence contract is
qualified. Do not open it just to run cleanup.

```powershell
.venv/Scripts/python.exe -m appchi.worker command-retention --once
.venv/Scripts/python.exe -m appchi.worker configuration-retention --once
.venv/Scripts/python.exe -m appchi.worker correlation-retention --once
.venv/Scripts/python.exe -m appchi.worker maintenance-retention --once
```

Without `--once`, each worker scans bounded pages and waits one hour after a full
pass. Check worker health and capacity: this cadence alone does not establish the
24-hour cleanup objective. OpenShift uses the existing worker template with the
corresponding role. `--once` performs one bounded page, not a complete cleanup.
These workers do not configure infrastructure retention. The maintenance/rule
workers use the already protected logical-deletion jobs; the
`protected-deletions` worker continues incomplete evidence acknowledgements.

## Ordinary command results

The command worker recognizes the existing ordinary command namespaces and
tracks their original operator/deletion job dependencies. The 90-day clock cannot
start before required work completes. Unknown families and missing dependency
proof stay held. Alert/incident results continue to follow their parent's lifetime.

Replay compares the original fingerprint first. An expired same-body command
returns `409 command_result_expired`, even before physical cleanup; a different
body returns `409 idempotency_conflict`. Neither reexecutes the operation.
Explicit command holds delay expiry. The worker verifies independent intent and
final acknowledgements before removing response bodies and copied deletion-job
receipts. Fingerprints, command identities and protected evidence remain.

## Preset and dashboard history

New create/update commands record immutable activity with the original command,
actor display snapshot, changed fields and before/after revision references.
No-ops and retries add no event. Existing revision-only records are not backfilled
with invented command or display-name evidence. History detail exposes the stored
activity and before definition when retained; absent activity is `null`.

Supersession timestamps are recorded separately from later row presence. Removing
an intermediate revision cannot extend an earlier revision's lifetime. Explicit
configuration holds retain only the selected revision/event and its required
before/after content, or the whole resource when the hold has no revision. Hold
insertion and cleanup share the configuration guard. Hold APIs are internal worker
operations, not product actions. New or reactivated holds require already retained
content; they cannot revive an expired snapshot or event still awaiting physical
cleanup. Whole-resource holds additionally check every remaining revision/event
and reject widening into expired leftovers. This internal operation is bounded
by the database statement timeout; qualify large histories or use explicit
revision scopes after eligible cleanup.

The configuration worker preserves active definitions, live references, pending
deletions and unacknowledged cancellations. Missing final acknowledgement also
keeps the exact base/final definitions and the pending deletion event available
past ordinary expiry. This does not reconstruct an already erased creation event.
It erases an expired, unheld current body after matching independent final
protection even when an older revision remains held. That older history resolves
through the permanent identity and stays readable without retaining the expired
final name/query/presentation. Cleanup revisits surviving revisions after the
body is gone. Minimal tenant/kind/UUID/deletion-revision/time identities survive
and become immutable after deletion; reusing a retired UUID fails. Completed job
display labels are scrubbed after their own retention and receipt/hold dependencies
end. An unrelated old-revision hold does not extend a later deletion actor's
lifetime. This scan revisits job metadata even after the resource body is removed.

Configuration list cursors now reject a changed collection with `409 list_changed`.
`state=deleted` uses the newest retained snapshot, not an expired final name.
Name filtering and sorting use the same exact NFC/full-casefold profile as active
configuration. Legacy historical name keys warm in committed batches of at most
100; an incomplete required name index returns
`503 configuration_history_name_index_pending` instead of partial or approximate
results. Retry to advance warmup. Hold release or expiry that changes the retained
list invalidates its cursor even without a user configuration edit. Historical dashboard
references remain provenance; they do not block preset removal or recreate content.

## Evidence and limits

Seven isolated real PostgreSQL cases passed before publishing migration 0030,
including worker-role erasure, API hold denial, immutable retired fences,
event/revision holds, original supersession clocks, command-body dependencies,
expired reads and collection cursor conflicts. Independent acknowledgements in
these tests are controlled fixtures. The broader integrated run is recorded in
[implementation status](../../status/implementation-status.md).

Migration 0034's focused isolated PostgreSQL suite passed 15 cases before
publication, including older-only preset/dashboard holds, restartable current-body
erasure, history after body removal, exact historical name filtering, bounded
name-index progress, hold-release cursor invalidation, no revival by new holds,
pending deletion proof reads and narrower deletion-actor retention. The API-role
tests use synthetic sessions; independent protection uses controlled fixtures.

Maintenance/rule cleanup contracts and local verification are detailed in
[maintenance and correlation retention](maintenance-correlation-retention.md).
Capacity, general completed-job diagnostics and rollback evidence reconstruction
still require their own qualification. These workers
do not reopen a restored deployment; see [restore fencing](restore-fencing.md).
