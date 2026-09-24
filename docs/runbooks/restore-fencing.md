# Rollback restore: fencing and evidence collection

The current coordinator implements the first recovery boundary: it drains guarded
application transactions, persists a new deployment epoch, closes all capability
gates, invalidates restored sessions and login transactions, and stages an
independently supplied evidence checkpoint. **It has no reopen command.**
`collected` means transport completeness, never restored application correctness.

Only run this during a coordinated recovery on the intended restored database.
The infrastructure owners perform backup/restore and supply the independently
current recovery authority. Keep ingress and old workloads quiesced, especially
operator workers: the SQL guard cannot cancel an HTTP request already sent to
Kong or Grafana. This procedure does not authorize a company deployment or remote
configuration change.

Use a separately controlled database identity with the migration-created
`appchi_recovery` role. Runtime API/worker identities cannot change restore control
or enumerate staged evidence. `APPCHI_RECOVERY_DATABASE_URL` belongs only to the
recovery command, never an API or worker deployment. Ordinary runtimes pass their
configured deployment epoch into the database boundary. Direct `Database` use
without an epoch is reserved for administrative tools and synthetic test fixtures.

```sh
python -m appchi.recovery.restore_cli begin \
  --run-id '<new operation UUID>' \
  --previous-epoch '<independently established previous epoch>' \
  --deployment-epoch '<fresh independently established UUID>' \
  --operation-reference '<controlled deployment operation reference>'
python -m appchi.recovery.restore_cli status '<operation UUID>'
python -m appchi.recovery.restore_cli collect '<operation UUID>'
python -m appchi.recovery.restore_cli assess '<operation UUID>' \
  --deployment-epoch '<current recovery deployment UUID>'
```

The operation reference must contain no secrets. A retry of `begin` uses exactly
the same inputs. A different active operation or conflicting epoch is rejected.
The old runtime epoch cannot resume after the control epoch changes. All sessions
are revoked, retained OAuth tokens erased, refresh generations advanced and
pending login transactions removed in the same transaction as the gate closure.
Fresh SSO is still unavailable until the later identity recovery checks exist.

The runtime guard uses a bounded preliminary transaction and shared session
advisory lock around the business transaction. This retains existing repeatable
read semantics. Recovery takes the matching exclusive lock before changing
control. Failed/uncertain lock release discards the connection. Readiness probes,
native v1/v2 admission, human paths and SQL workers use this boundary. Its extra
database round trips need inclusion in capacity qualification.

`collect` requires the normal supplied recovery binding/credential settings and
the explicit [paged checkpoint contract](recovery-checkpoints.md). It freezes the
namespace checkpoint and binding digest, stores ordered minimal records under a
separate collection identity, and records a digest over accepted page digests.
Each page and its progress commit together. An outage leaves an incomplete scan
staged and all paths fenced. Retrying starts a new frozen scan; old incomplete
rows remain minimal evidence and are never mixed into the new result. A competing
scan supersedes older callbacks through the durable collection identity.

`assess` is a read-only diagnostic over the current complete collection. It
requires the expected recovery deployment epoch and rejects incomplete, stale or
inconsistent staging. It returns bounded family counts and explicit capability
blockers; it does not expose resource IDs, digests, namespace/checkpoint tokens or
product content. Counts of final records report collected evidence, not verified
or applied retirement fences. Matching intent/final tuples do not establish the
missing family-specific attempt linkage.

The current schema has no independently established authority baseline or
producer-coverage proof. Assessment therefore always reports those blockers,
including for an empty collection, and always returns `reopen_permitted: false`.
Permission and credential records remain unsupported for reconstruction. The
diagnostic also identifies unmatched intent groups, conflicting final/cancel
groups and unknown evidence families. No assessment result changes restore state,
capability gates, grants, credentials, content or the required recovery procedure.

The current automatic reopening blockers are:

- Protected post-backup permission and global identity-configuration producers
  and reconciliation, followed by a fresh complete directory checkpoint.
- Independent surviving-operator and credential lifecycle proof; remote readiness
  alone cannot establish that a restored key was not revoked.
- Family-specific reconstruction for final fences and unresolved intents whose
  original jobs or scoped command receipts are absent from the backup.
- Independently established accepted-work boundaries, pending-order/consumer
  reconciliation, trusted clocks, current retention, and search rebuild/verification.

An empty complete checkpoint does not prove any unsupported family had no
changes. Do not manually set a capability to ready: a database trigger also blocks
that while recovery is active. Do not clear restore control to bypass missing
proof. The later coordinator must implement the missing reconciliation before
this runbook gains a reopening procedure.

Six local PostgreSQL cases verify transaction draining, replay/conflict checks,
epoch rejection, lock release, role separation, session invalidation, native-v1
and human HTTP fencing, and incomplete/complete evidence staging without reopening.
These use synthetic databases and controlled evidence; no infrastructure rollback,
private evidence service or company recovery guarantee has been qualified.

The read-only assessment has 16 additional passing PostgreSQL checks: empty
collection safety, unsupported authority families, unmatched/conflicting evidence,
stale run/epoch rejection, incomplete or inconsistent staging, bounded sanitized
output, recovery-role access and unchanged restore/gate state. These checks do
not implement reconstruction or authorize reopening.
