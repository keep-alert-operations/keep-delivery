# Alert retirement and content cleanup

The `retention` worker now follows protected intent, coordinator-guarded logical
retirement, independent final protection, protected command-result compaction and
bounded physical erasure. A closed recovery gate after an external intent call
prevents logical commit. Explicit retention holds use the tenant coordinator and
cannot be added after the identity is fenced.

Retired command parents make old command/notes/bulk results unavailable immediately,
while the original fingerprint remains to reject conflicting reuse. Each stored
command result gets its own independent command intent/final before its body is
removed. Bulk receipt content expires when any of its alert parents is retired.

Physical cleanup waits for the final fence, no active search rebuild, searchable
progress, correlation progress, maintenance proof completion, and each required
transport consumer's durable receipt. It removes at most 100 rows per transaction
and yields after a checked work deadline. Current/history/notes, maintenance source
proofs and old projection snapshots are removed; minimal alert identity, final ES
marker, command fingerprints, transport envelopes/receipts and the finite external
retry associations remain. Retired input records lose their redundant logical-key
text and identity material. A surviving incident retains only its existing link
provenance, and the incident sweep removes the link once both parents expire.

Already consumed immutable transport envelopes remain recognizable after body
cleanup. Exact persisted envelope equality and the consumer's existing durable
receipt establish that replay; a different event cannot reuse the same broker
position. No source input or correlation action is re-executed by this path.

Executed local evidence: five real PostgreSQL tests cover command privacy,
independent command-proof failure, bounded content deletion, consumer/rebuild
holds, surviving references, late retention holds, known transport replay and
recovery gate closure during intent. Consumer release is a controlled fixture in
these tests; it does not establish Kafka/company-service recovery guarantees.
Measured cleanup capacity and coordinated rollback restore remain qualification
and implementation work respectively.
