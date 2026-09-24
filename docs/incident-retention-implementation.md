# Incident retention implementation

`recovery/incidents.py` implements the protected incident retirement sequence.
`recovery/cleanup.py` removes incident-owned content in bounded batches after
independent final protection, and separately protects erasure of successful
command result bodies. The `incident-retention` worker connects both stages and
resumes incomplete attempts. Real storage independence and restore recovery
remain private-environment qualification requirements.

The retirement unit takes the tenant coordinator, correlation contexts, incident
and linked alert locks in order. It rechecks the latest resolved deadline,
incident revision, pending linked source work, explicit proof holds and absence
of active alert/context ownership after the external intent acknowledgement.
It does not release active ownership as a shortcut. A racing reopen or hold
cancels the attempt; the cancellation must be protected before another attempt.
Known hold creation after logical retirement is rejected under the coordinator.

Logical retirement records an indefinite minimal incident identity/final revision
and advances tenant freshness generations. API reads become unavailable. Final
evidence failure retains inaccessible content and retries the same evidence UUID.
Linked alerts retain their own lifecycle, content, notes and projection state.

Migration 0024 attaches incident commands to their exact owning incident(s),
including existing initial-release create/edit/move/note receipts. Every new
incident command records its parents transactionally. A replay after any parent
retires returns `409 command_result_expired`, even while its original result body
awaits cleanup. The original fingerprint still rejects reuse with different input.

Before removing a result body, cleanup protects its minimal command intent and
final evidence using deterministic operation IDs, the original fingerprint and
result digest. It preserves the command identity, scope, fingerprint and status
after clearing the body. Source content and notes never enter the evidence service.
Cleanup of incident notes/revisions, activity, cycles, search tokens and automatic
decision proof uses a shared row budget of at most 100 per transaction. Partition
deletion addresses include table identity as well as tuple identity. Rule revision
holds release only after incident-owned proof is gone. Minimal link provenance
remains with surviving alerts; it is removed here only when the alert counterpart
has also retired. The incident identity and replay fences remain indefinitely.

The scheduler yields between bounded units. Partial cleanup continues promptly;
complete scans return to the hourly retention sweep. External evidence calls run
outside SQL locks. Application pool, evidence-service and workload capacity still
require qualification against the supplied allocation.

Executed: eight isolated PostgreSQL tests covering successful protection,
inaccessible command replay, final-service outage, reopen/hold races, duplicate
workers, command-fence failure, bounded cleanup, partition/counterpart preservation
and the scheduled worker. Those tests use controlled evidence acknowledgements;
they do not prove the independent service's durability or rollback survival.

This completes an incident path, not all-resource retention. Alert/configuration
physical cleanup, generalized consumer/proof holds and coordinated database
restore/search rebuilding remain separately tracked in implementation status.
