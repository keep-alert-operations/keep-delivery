# Protected authority implementation prerequisite

This is a **non-normative, assistant-selected implementation proposal**, not an
amendment to the accepted recovery contracts or a qualification result. The new
[authority record models](../backend/appchi/recovery/authority_records.py) are
unconnected to runtime writers, the independent evidence adapter, database
migrations, restore collection and capability gates. Validating a record does not
prove publication, durability, complete coverage or safe recovery.

The governing requirements remain [shared system completion §20](../spec-system-completion.md),
[identity configuration §§11–12](../spec-identity-configuration.md),
[operator operations §28](../spec-operator-operations.md) and
[reliability and operations](../spec-reliability-operations.md). In particular,
permission uncertainty keeps human authorization gated; unproved credential
state requires invalidation, and unproved operator existence remains fenced.
Fresh directory data cannot reconstruct post-backup local grant revocations.

## What this increment defines

Wire contracts accept only necessary IDs, version strings, decimal revisions,
digests and enumerated effects. They forbid unknown fields and implicit scalar
coercion. UUIDs use canonical lowercase hyphenated text; counters use canonical
nonnegative decimal text within PostgreSQL bigint range; digests are lowercase
SHA-256 hex. Required fields cannot be omitted or null. They contain no roles,
grants, usernames, operator names, configuration bodies, raw keys, verifiers,
ciphertext, key-wrapping metadata or arbitrary attributes.

An `appchi-authority-attempt-v1` descriptor binds an immutable attempt ID,
producer epoch/version, tenant/resource IDs, adjacent base/after revisions,
distinct before/after digests and one typed effect. Permission effects scope the
resource ID to the tenant. Operator effects cover creation, readiness and
deletion; credential effects bind the parent operator and cover preparation,
activation, retirement, expiry and revocation. Creation starts at revision zero;
other operator/credential transitions require an existing revision. These are
proposed dedicated authority revisions, not a claim that today's row revisions
already advance for every required transition.

An `appchi-authority-evidence-v1` record binds that complete attempt to intent,
final or cancel. Its UUIDv5 ID uses the attempt UUID as namespace and the exact
name `appchi-authority-evidence-v1:{phase}`. Its digest covers the entire record
except the digest field, including phase, identity and attempt. A later collector
must reject conflicting descriptors for one attempt, final/cancel conflicts,
missing intents and broken per-resource predecessor chains. A single model
cannot validate a history, prove a transition's actual state, or establish an
acknowledged independent write. Hashes are integrity commitments, not signatures.

Coverage uses `appchi-authority-coverage-descriptor-v1` inside a digested
`appchi-authority-coverage-v1` record. Each producer epoch belongs to **one tenant
and one immutable sorted set of covered families**. The UUIDv5 namespace is that
epoch and the name is `appchi-authority-coverage-v1:{baseline|opened|closed}`.
Changing scope within an epoch therefore creates a conflicting immutable ID,
which the eventual ledger/adapter must reject; it must never overwrite it.

- A baseline commits a head inventory count and digest.
- An opened marker binds an independently established baseline ID/digest.
- A closed marker binds the same baseline, this epoch's deterministic opened ID
  and its digest, plus a change inventory count/digest.

These fields are deliberately insufficient to claim a complete or current
baseline from an empty list or maximum observed revision. Inventory encoding,
cross-record scope checks, pagination completeness, service durability,
independent freshness and the operator-controlled quiescence protocol still need
implementation and validation. A baseline reference may identify a previously
established baseline; referenced content and lineage must be fetched and checked.
No model provides a `ready` or `verified` assertion that callers can trust.

## Local digest inputs

Snapshot helpers take explicit, strict local projections. Their input models are
**not transport records**. Never serialize these inputs to the evidence service
or logs. In particular, the permission helper necessarily reads the locally
authoritative grant roles and immutable directory bindings to commit their state;
only the returned digest belongs in an exported record.

Permission canonicalization includes tenant ID, authority revision and each
principal ID, exact directory source/object ID and role, sorted by principal ID.
Duplicate principal IDs or duplicate immutable directory bindings are rejected.
Opaque directory identifiers retain exact case and Unicode code points; no
casefolding or Unicode normalization can merge identities.

Operator canonicalization includes tenant/operator IDs, revision, generation,
lifecycle, deletion-pending flag and all projected credentials sorted by ID.
Credential state includes its immutable parent, state, original activation,
retirement and revocation instants, paired Kong binding IDs and ciphertext
presence only. Aware database datetimes normalize to UTC with exactly six
fractional digits and `Z`; naive datetimes and datetime strings are rejected.
Explicit null instants/bindings represent local lifecycle absence and are required
fields. Revoked/expired keys cannot retain ciphertext; prepared keys cannot carry
activation timestamps; retiring/expired states require their original deadline.
Duplicate live states, parent mismatch and usable credentials on a deleted
operator are rejected. No restore helper extends or regenerates a deadline.

Both digest functions hash ASCII JSON with escaped non-ASCII characters, sorted
object keys, compact separators and no NaN, inside distinct versioned envelopes:
`appchi-permission-state-v1` and `appchi-operator-authority-state-v1`. This binds
deadlines and lifecycle metadata without exporting them. Ciphertext presence
does not prove decryption, encryption AAD, key identity or remote authorization;
those checks remain separate. Models must be constructed through validation,
not Pydantic's deliberately unsafe `model_construct` or unchecked update copies.

## Next compatible integration increments

1. Specify canonical head/change inventories and cross-record chain validation;
   add explicit proposed authority heads, immutable attempts and producer epochs
   in a reviewed migration. Include atomic multi-credential effects: activation
   also retires the old key, while revoking a current key cancels preparation.
   Current single-effect records cannot alone prove the aggregate atomic outcome.
2. Extend the one independent evidence adapter with an explicit versioned strict
   envelope. Preserve legacy records and refuse unknown versions. Establish
   actual durability, immutability, scope and complete enumeration behavior with
   the service owner; these models invent none of those guarantees.
3. Independently protect an opened epoch before enabling its covered writers.
   Protect immutable intent outside locks; recheck authorization, expected
   revision and operator generation before the authoritative transaction, then
   protect final/cancel. Commit state, authority head, receipt and pending evidence
   together. A PostgreSQL outbox alone cannot protect a lost post-backup tail.
   Known revocation must still deny locally and erase invalidated key material
   immediately; uncertainty in its independent publication keeps recovery gated.
4. Close an epoch only after writers are quiesced and the full acknowledged tail
   is independently established. A crash leaving an open or unprovable epoch is
   an explicit restore blocker, even when the collected list is empty.
5. Add restore assessment of exact baseline/epoch/attempt chains before any
   reconstruction or gate reopening. Compare a trusted candidate permission
   snapshot with the established digest and obtain fresh complete directory
   evidence. A digest cannot recreate lost grants; without such a candidate,
   operator reconciliation remains mandatory. Independently prove surviving
   operator IDs, invalidate unprovable credentials and preserve original IDs and
   deadlines. Never restore secret material from audit/operation replay.

Identity deployment mapping changes, operator rename/generation-only transitions,
aggregate credential transactions, trusted candidate acquisition and authoritative
inventory enumeration remain unsupported producers. Accepted work, command
fences, order/clock reconstruction and projection catch-up retain their separate
recovery gates. This prerequisite closes none of those gates or production
qualification items.

## Executed evidence for this prerequisite

The new pure unit file exercises canonical input rejection, immutable phase
binding, unknown/content fields, parent/revision constraints, deterministic
snapshot ordering, exact time and directory-identity handling, contradictory
lifecycle states and coverage shape validation. It does not contact PostgreSQL,
the independent service or any company integration. All 64 cases passed in the final migration-0039 full backend run. That full run
had a separate native CEL deadline failure; see implementation status. Model
validation remains prerequisite evidence and establishes no protected publication.
