# Paged recovery-evidence checkpoint contract

`recovery/evidence.py` now supports explicit versioned, snapshot-bound checkpoint
pages. This is an application adapter contract for an independently operated
service. It does not establish that the company already supplies this endpoint,
its durability/independence, a qualified recovery checkpoint, or restore readiness.
The existing `append()` and single-page `checkpoint()` contracts remain unchanged.

The normative basis is [alert-history technical §24](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-alert-history-technical.md#24-protected-evidence-and-restore-readiness)
and [system completion §20](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-system-completion.md#20-cross-resource-deletion-replay-and-restore).
An unresolved protected intent must fence reconciliation; a final fence prevents
resurrection. Enumeration alone does not interpret or apply these decisions.

## Explicit binding

Add both optional fields to the existing mounted `EvidenceBinding` JSON:

```json
{
  "paged_checkpoint_path": "/SUPPLIED-SNAPSHOT-PAGE-PATH",
  "paged_checkpoint_version": "appchi-evidence-checkpoint-v2"
}
```

This fragment is illustrative; the path must come from the service owner. Existing
origin, append/checkpoint paths, namespace, independence/durability evidence and CA
configuration remain required. Omitting both fields preserves legacy operation.
Providing only one, an unsupported version or an unsafe path rejects the binding.
The paged method never silently falls back to a complete legacy page of ≤1000 rows.

The service must qualify immutable idempotent evidence, durable ordered namespace
checkpoints independent of the PostgreSQL recovery point, an atomic snapshot upper
checkpoint, complete enumeration of the requested interval, stable continuations
and supported snapshot lifetime. The binding's evidence identifiers refer to those
external qualification records; setting them does not manufacture that evidence.

## Requests and pages

`HttpEvidenceStore.checkpoint_page(...)` GETs the supplied path using the existing
Bearer credential and verified HTTPS connection. Redirects are disabled. Initial
query parameters are `version=appchi-evidence-checkpoint-v2`, the exact supplied
`namespace`, and `limit` (1–1000). Optional `after` is an opaque exclusive lower
checkpoint; omit it to enumerate the namespace from its beginning.

The first successful response establishes an immutable inclusive upper
`checkpoint`. The interval is `(after, checkpoint]`. Every continuation sends the
same `after`, namespace and version, plus this fixed `checkpoint` and the previous
response's opaque `next_cursor` as `cursor`. Cursors and checkpoints are strings of
1–4096 characters; clients never parse them, sort them lexically or manufacture a
successor. The service must reject expired or incompatible continuations rather
than restart on a newer snapshot or silently omit records. Such failure gates the
scan and requires a deliberate restart/reconciliation of its staging output.

The strict response has exactly these fields (values below illustrate shape):

```json
{
  "version": "appchi-evidence-checkpoint-v2",
  "namespace": "SUPPLIED-NAMESPACE",
  "after": null,
  "checkpoint": "OPAQUE-FIXED-UPPER-CHECKPOINT",
  "request_cursor": null,
  "total_records": "1001",
  "records": [],
  "next_cursor": "OPAQUE-CONTINUATION",
  "complete": false,
  "page_digest": "LOWERCASE-SHA256-OF-CANONICAL-PAGE"
}
```

An actual nonterminal response must contain at least one record; the empty list
above is only a schema illustration. `request_cursor` exactly echoes the request's
cursor, or null for the initial page. `after` likewise echoes the supplied lower
checkpoint or null. `total_records` is a canonical decimal string, fixed across
all pages and equal to the complete interval's number of immutable evidence
records. Counts/revisions are bounded by signed 64-bit range, not JavaScript number
precision. `complete` must be a JSON boolean and must equal `next_cursor == null`.
A complete empty interval is valid and has `total_records: "0"`.

Each record has exactly the existing minimal `Evidence` fields: canonical UUID
`id`, `tenant_id`, `resource_id`; supported `resource_kind`; canonical decimal
string `revision`; `state` (`intent`, `final`, `cancel`); and lowercase SHA256
`digest`. No source/configuration bodies, plaintext keys, note text or actor display
names enter this API. Records are returned in the service's checkpoint order.
Duplicate evidence IDs are forbidden, including across different pages. Multiple
distinct evidence IDs for one resource remain valid and retain their service order.

`page_digest` is lowercase SHA256 of UTF-8 canonical JSON containing every response
field except `page_digest` itself. Canonical JSON sorts object keys recursively,
uses separators `,` and `:`, escapes non-ASCII characters (`ensure_ascii=True`),
rejects non-finite numbers and contains no optional whitespace. Record array order
is significant. Thus the digest binds records, namespace, interval, version, cursor,
completion and total. Duplicate JSON object properties and extra schema fields are
rejected. A matching self-reported digest is an integrity check over the trusted
service response; it is not independent proof of the service's durability or
completeness.

## Bounds and collector handoff

Each HTTP page has a caller-supplied positive deadline of at most five seconds,
with each blocking HTTP phase capped at one quarter of that budget. An outer
async cancellation deadline also covers trickled response headers. The adapter
checks elapsed time for every received transport chunk and after JSON parsing;
it does not aggregate tiny chunks before checking the deadline. It accepts at
most 4 MiB per response and requests identity encoding, rejecting compressed
responses. Limits are application safeguards, not a qualified infrastructure
capacity or service-level claim.
The public API remains synchronous for worker/restore code; async callers must
invoke it in a worker thread (for example, `asyncio.to_thread`). It must not start
a nested event loop in an existing async request handler.

```python
from appchi.recovery.evidence import collect_checkpoint

summary = collect_checkpoint(
    evidence_store,
    staging_writer.accept_page,
    after=trusted_lower_checkpoint,
    page_size=1000,
    max_pages=10000,
    max_records=1000000,
    deadline_seconds=300,
)
# Only now is this scan complete. A separate restore coordinator must verify
# and reconcile staging before it can change any affected readiness gate.
```

The collector passes `CheckpointPage` objects to a caller-supplied staging sink
and returns `CheckpointCollection(namespace, after, checkpoint, pages, records, binding_digest)`
only after exact final count validation. It retains evidence IDs and cursor IDs
for duplicate/loop detection, while the caller streams page payloads into staging;
it does not retain all evidence payloads in memory. The default one-million-record,
10,000-page and 300-second limits are explicit development safeguards and can be
supplied by the caller after qualification. Exceeding any limit fails the scan.
`binding_digest` is SHA256 of canonical JSON for the nonsecret `EvidenceBinding`
fields, including endpoint/version/namespace and qualification references. The
collector rejects binding changes during a scan; bearer credentials are never
part of this digest. The coordinator must bind this result to its durable attempt.

The collector rechecks namespace, interval, snapshot, total, digest, schema,
continuation progress and evidence identity even for a non-HTTP adapter. A page
that fails validation never reaches the sink. Previously delivered pages are
**partial staging**, not a successful checkpoint; failure returns no completion
summary. Sink calls and non-HTTP adapters must themselves be bounded. The collector
checks its deadline before/after them but cannot preempt arbitrary caller code.

A restore coordinator must segregate staging by scan/snapshot, keep readiness
closed, discard or explicitly reconcile partial scans, and enforce transaction
and lease boundaries before applying protected fences. Restarting a collector
requires a fresh validated scan unless the coordinator separately proves durable
continuation and duplicate handling. This change adds no schema migration, restore
coordinator, irreversible mutation or automatic readiness switch.
In particular, complete enumeration cannot prove that evidence families without
implemented producers have no post-backup changes. Current minimal tuples alone
cannot generally reconstruct a lost job's command scope, fingerprint or attempt
linkage. Permission/credential family support and exact producer recomputation
need explicit reconciliation; an apparently empty family cannot waive those gates.

## Executed evidence

On Windows Python 3.13, 29 controlled HTTP/collector tests passed. They cover
1001-record pagination, opaque cursor encoding, empty snapshots, changing
namespace/snapshot/total, truncated counts, duplicate IDs, looping cursors, digest
metadata tampering, explicit collection limits, non-HTTP integrity validation,
response-size bounds, slow headers/chunks, sink deadline accounting, redirects, duplicate
JSON properties, compression rejection, opt-in binding and unchanged legacy
checkpoint/append behavior. Ruff passed for the implementation and tests.
Changing the supplied connection/qualification binding during enumeration also
fails before a completion summary can be returned.

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/unit/test_recovery_checkpoint.py -q
```

These are controlled contract fixtures. No company recovery service was contacted,
no supplied snapshot guarantee was independently established, and no rollback
restore or production-readiness claim follows from this result.
