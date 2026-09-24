# Maintenance and correlation retention

Apply migrations through 0033 and use the normal runtime deployment epoch and
worker database role. Keep recovery readiness closed until the supplied
independent evidence contract is qualified. These cleanup roles consume matching
protected final deletion records; they do not bypass or create that qualification.

```powershell
backend/.venv/Scripts/python.exe -m appchi.worker correlation-retention --once
backend/.venv/Scripts/python.exe -m appchi.worker maintenance-retention --once
```

Each invocation performs a bounded page. Ongoing processes advance keyset pages
with a one-second pause, then wait one hour after a complete scan. Rows blocked by
holds and partially erased resources are revisited on later passes. Process
heartbeats continue during the wait. Measure backlog, resource size and supplied
capacity before claiming the 24-hour cleanup objective; the scan cadence alone
does not establish it. Use the existing OpenShift worker template with one of
these role names and workload-specific credentials/pool allocations.

## Correlation rules

Active current definitions remain. Superseded definitions retain 90 elapsed days
after supersession; recorded events retain 90 days and hold their before/after
definitions. Durable supersession timestamps survive intermediate-row cleanup.
The final deleted definition retains 90 days after deletion, extended by required
incident, input and recovery dependencies. New or reactivated holds cannot revive
a logically expired definition merely because physical cleanup is delayed.

Cleanup preserves retained incident decisions and contexts, current reset barriers,
revision holds, and pending journal/effect dependencies. Missing, unhealthy or
inconsistent consumer progress conservatively blocks cleanup. Expired event
display metadata is erased separately from held definition content. Rule and
condition UUID fences, protected deletion evidence and permanent command fences
survive. The command-retention worker independently protects and removes copied
command-response bodies; this worker does not erase those copies directly.

History reads and deleted discovery reapply retention before cleanup. When only
an older held revision remains, discovery uses that revision's name and exposes
its `history_revision` plus the identity's `final_revision`; it does not expose
the expired final definition's name. Historical normalized name keys warm in
batches of at most 100 under the selected Unicode profile. New revisions store
their keys atomically. A deleted-list read warms one batch and reauthorizes its
query; name search/order returns `503 correlation_history_name_index_pending`
while required keys are unavailable. Default arbitration-order discovery remains
available. Unknown name profiles are not approximated.

## Maintenance windows

Non-deleted windows retain their complete configuration timeline, including ended
windows. Explicitly deleted windows retain it for 90 elapsed days and longer while
retained alert causes/history, applicable source inputs (including negative
evaluations), pending admission/fanout work, or explicit proof holds require it.
Late hold insertion cannot revive expired or already erasing content.

Physical cleanup requires a matching independently protected final deletion and
rechecks dependencies under the coordinator/window locks on each bounded batch.
Starting erasure advances the maintenance generation so a previously compiled
catalog cannot publish against the removed timeline. Eligible fanout records,
segments, controls and revisions are removed before the current window body.
Minimal immutable identity and retirement fences remain. Alert content and its
proof are owned by alert retirement, not this cleanup role.

## Verification boundary

Correlation retention passed 13 isolated PostgreSQL checks, plus three targeted
checks after the final hold-lock adjustment. They cover current/held history,
missing final protection, consumer progress, worker privileges, a concurrent hold
handoff, no revival, and deleted-name discovery. Maintenance retention passed its
separate 10 isolated PostgreSQL checks, including negative proof, equal-time
ordering, bounded cleanup, worker privileges and stale-catalog rejection.
Independent evidence acknowledgements in these checks use controlled fixtures.
They do not qualify the company recovery service, cleanup capacity, infrastructure
media erasure, or restored-deployment reopening.
