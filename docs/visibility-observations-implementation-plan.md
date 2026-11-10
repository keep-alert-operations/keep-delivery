# Visibility observations implementation plan

Status: implementation plan with an isolated prerequisite implementation, **not a
normative supplement, completed measurement feature, or qualification claim**.
The interfaces below are assistant-selected implementation proposals. Normative
requirements remain in [alerts/history technical section 21](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-alert-history-technical.md)
and [reliability sections 4, 7 and 19](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-reliability-operations.md).

## Measurement boundary and existing capabilities

Incoming visibility starts at the original system receipt; maintenance
expiry starts at its stored scheduled end. Correct state must render in an
eligible connected foreground view within five seconds. Server receipt of a
valid rendered acknowledgement is a conservative completion bound. Hints,
projection writes, successful HTTP responses and browser fetch completion do not
establish rendering. A later correct state may satisfy an earlier input without
showing every transient state. Late completion remains a deadline miss.

The existing `LiveRegistry` is a process-local tenant hint cache. It has no
session/tab/query lifecycle history. `SearchReader` captures a useful verified
searchable watermark before opening its PIT; a later observed vector is not
evidence that this PIT contains newer changes. Current count responses create an
`observation_id` only after successful Elasticsearch work. That identifier must
not be reused as a visibility denominator or accepted render proof.

The pipeline must capture input and candidate-view evidence **before response
success**. Otherwise Elasticsearch failures, blocked heads and missing browser
responses disappear from the denominator.

## Phase 1: bounded registry implemented, explicitly configured

The new `backend/keep/visibility/` package implements only strict DTOs,
deterministic sampling, and durable reported view lifecycle. Migration
`migrations/0031_visibility_registry.sql` was published after parent review and
isolated PostgreSQL tests. The subsequent producer increment mounts the registry
in the Product API, with explicit server configuration required. Search responses
and browser rendering are not connected to observation acceptance yet.

`RegistryLimits` requires explicit positive integer allocations:

| Field | Meaning |
|---|---|
| `lease_seconds` | Server contact lease; not a session extension or telemetry lifetime |
| `max_active_session_views` | Simultaneously leased views per session and tenant |
| `max_active_tenant_views` | Simultaneously leased views per tenant |
| `max_tenant_controls` | Coordination storage cap before configured cleanup exists |
| `max_descriptor_bytes` | Canonical encoded view-intent allocation |
| `max_request_bytes` | Request allocation, at most 65,536 bytes |

Configuration is passed explicitly as `app.state.visibility_registry_limits`.
Absent configuration returns unavailable. No deployment values are supplied by
this plan. The current implementation imposes a one-day maximum on a contact
lease as an implementation safety bound; actual leases need qualification.
Reaching a capacity cap returns unavailable. It does not erase old evidence or
pretend that no views existed. A qualified metrics integration must account for
registration failures and these capacity gaps before enabling measurement.

The router has these interfaces, unavailable without configured limits:

```text
POST  /api/tenants/{tenant_id}/view-subscriptions
PATCH /api/tenants/{tenant_id}/view-subscriptions/{view_id}
GET   /api/tenants/{tenant_id}/view-subscriptions/{view_id}
```

Registration:

```json
{
  "command_id": "canonical UUID",
  "tab_id": "canonical UUID",
  "view": {
    "surface": "alert_feed",
    "expression": "true",
    "q": "",
    "include_suppressed": false,
    "sort": "timestamp",
    "direction": "desc",
    "page_mode": "current"
  },
  "state": "foreground"
}
```

`tab_id` identifies a browser tab but is not an authority token. The server creates
the view UUID. Surfaces are `alert_feed`, `alert_detail`, `preset` and `dashboard`;
the latter three require `resource_id`. Only direct feeds accept custom query
fields. Saved-resource query resolution remains a later server responsibility.
`page_mode` distinguishes current and frozen traversal; it never automatically
excludes frozen views from measurement.

Lifecycle update:

```json
{
  "command_id": "canonical UUID",
  "expected_revision": "1",
  "state": "background",
  "view": null
}
```

States are `foreground`, `background`, `closed`. A foreground renewal appends a
new control even when its query is unchanged, preserving the contact timeline.
Query replacement or renewal after lease expiry advances `view_epoch`. A closed
identity cannot reopen; register a new one. Each accepted control advances its
revision and tenant registry checkpoint. Replaying the same command returns its
original response without renewing the lease or appending evidence. Reusing its
identity with different content conflicts. Stale expected revisions conflict.
Every retry still performs current authorization and CSRF checks.

Responses contain `data.{id,tab_id,revision,view_epoch,query_digest,state,
recorded_at,lease_until,checkpoint}` and explicitly report
`meta.{eligibility:"unproven",render_proof:"unavailable"}`. Bigint fields are
decimal strings. The server-derived digest is a canonical **view intent** digest,
not a verified CEL/search query digest. This phase does not compile expressions,
establish resource existence, resolve presets, determine input eligibility, issue
observation IDs or accept rendered success. Resource UUID guesses in an intent
do not expose resource contents or grant access.

Storage:

| Table | Key and retained evidence |
|---|---|
| `visibility_registry_heads` | Tenant PK, monotonic registry checkpoint |
| `visibility_views` | Tenant/view PK, session hash, principal, deployment epoch, tab, current revision/epoch/digest/descriptor, state, server contact time/lease, checkpoint, authority snapshot |
| `visibility_view_controls` | Tenant/checkpoint PK; unique tenant/session/command; immutable view snapshot, fingerprint and original response; indexed view/server-time/checkpoint timeline |

Session hashes are internal bindings, never browser response fields. There is no
cascading session foreign key: deleting a session must not erase the historical
cohort. Tables enforce tenant RLS. API roles cannot UPDATE/DELETE immutable
controls; workers currently have SELECT only. No cleanup grant or lifetime is
introduced. A deployment epoch mismatch prevents use of an old registered view.

Transactions acquire the identity authorization guard, current session/tenant
authorization, and the existing tenant coordinator. Thus registration controls
can be ordered with future admission checkpoint capture. No network work or CEL
process startup occurs inside these locks. Captured authority includes current
tenant revision, principal evidence generation, effective role, super-admin
status and session's current idle/absolute authorization bound. This is a
registration-time snapshot, not proof of later or historical access changes.
Observation endpoints never modify `auth_sessions.activity_at`.

Implemented service interfaces:

```python
register(conn, request, tenant, RegisterView, RegistryLimits) -> response
change(conn, request, tenant, view_id, ChangeView, RegistryLimits) -> response
snapshot_at(conn, tenant, view_id, *, checkpoint: int, at: datetime) -> dict | None
selected(*, input_id: UUID, view_id: UUID, view_epoch: int,
         policy_epoch: str, kind: str, qualification: bool) -> bool
decode_acknowledgements(raw: bytes) -> AcknowledgementBatch
```

`snapshot_at` applies both original time and checkpoint bounds. Its caller must
authorize access. It returns reported lifecycle, not eligibility. Lease expiry
must be treated as uncertain contact; it is not proof that the client was already
offline. Presence changes are dated by server receipt, not an untrusted browser
clock. Missing registration/lifecycle evidence remains a coverage gap.

Sampling is SHA-256 over a domain tag, immutable input UUID, server view UUID,
view epoch and stable server policy epoch. Ordinary incoming inputs select one
tenth of hash space; maintenance expiry and server-controlled qualification mode
select all pairs. It never depends on input payload, response success or process
hash randomization. Neither qualification mode nor policy epoch is client input.

## Phase 2: durable input, cohort and authorization controls

The current phase-2 prerequisite adds `visibility/inputs.py` and migration
`migrations/0036_visibility_inputs.sql`, published after parent review and 13
focused PostgreSQL tests. This captures original boundaries, resumable
sampling and operational gaps; it does not classify eligibility or prove rendering.
Producer integration now lives in `visibility/runtime.py`, the admission/source
transaction owners and the maintenance schedule hooks described below.

| Table | Required key/evidence |
|---|---|
| `visibility_inputs` | Implemented prerequisite: tenant/input UUID; immutable original start/order/deadline; seed and sealed cohort checkpoints; policy/deployment epoch and explicit allocations; schedule identity/disposition proof; resumable expansion cursor and gaps |
| `visibility_input_capacity` | Implemented prerequisite: serialized per-tenant input/candidate/application row and application-byte charges; seed-capacity gap attempts and original time range |
| `visibility_applications` | Implemented prerequisite: idempotent application UUID, before/after digests, non-owning source/journal locators, projection version/sequence, or a bounded rejection error code |
| `visibility_candidates` | Implemented prerequisite: unique input/view/epoch; selected historical control and intent digest; original deadline; strictly unclassified eligibility with pending/contact/background coverage reason |
| `visibility_authority_controls` | Still proposed: ordered session/principal/tenant invalidation or proof controls sufficient to determine authority at the original input boundary |

Implemented transactional prerequisite interfaces:

```python
record_input(conn, *, tenant, input_id, kind, started_at, original_order,
             registry_checkpoint, policy: SamplingPolicy, limits: InputLimits,
             schedule=None)
seal_expiry_boundary(conn, *, tenant, input_id)
dispose_schedule(conn, *, tenant, input_id, generation, disposition,
                 evidence_reference)
link_application(conn, *, tenant, input_id, application_id, alert_id, before,
                 after, projection_seq, projection_version, disposition,
                 limits=None, error_code=None, journal_order=None,
                 source_event_id=None)
expand_input(conn, *, tenant, input_id, limits=None)
expand_next(conn, *, tenant=None, limits=None)
coverage(conn, tenant)
```

These helpers use the caller's transaction and never commit. Every mutation
acquires the tenant coordinator before seed or capacity rows. Worker selection
locks only a candidate tenant coordinator with `SKIP LOCKED`, then rechecks its
seed; a busy tenant cannot block a ready one. Turns
inspect a supplied number of historical views, capped at 200, and persist their
continuation atomically with candidates. Scheduling rotates attempted pending
seeds so an allocation gap does not indefinitely monopolize the worker.

`SamplingPolicy` requires a server policy epoch, qualification boolean and
deployment epoch. `InputLimits` requires explicit positive allocations for tenant
inputs, candidates, applications, total application bytes, individual application
bytes and views per turn. Seeds preserve the admitted policy and allocations.
Input-capacity exhaustion records a bounded aggregate coverage gap and returns
`capacity_gap`, rather than implicitly rejecting otherwise valid source admission.
The producer must report that outcome. Gap attempts are not unique inputs: exact
deduplication would require additional identity storage after its allocation is full.
Candidate or application exhaustion preserves the seed as incomplete/unclassified.
There is no operational data-retention default or DELETE grant.

Historical controls from another deployment epoch remain selected and explicitly
unclassified with `deployment_epoch_mismatch` coverage, preserving their original
deadline. They cannot silently become current-epoch eligibility evidence. Missing
or malformed epoch proof stops that expansion with `registry_proof_invalid`.
The later authority classifier must resolve these gaps from adequate proof;
selection or a contact lease alone does not resolve them.

Application links retain digests and product-proof locators, never copies of alert
messages or source snapshots. They do not hold source/activity/current-alert
content against its product lifetime. If later classification cannot retrieve the
referenced proof, it must preserve a coverage gap. Rejected processing has a real
bounded error code and no fabricated alert ID, projection version or snapshot.

Admission records the input seed in the same transaction as `ingest_items`,
before worker processing/search. Duplicate receipt IDs reuse original seeds and
times. Capture a registry high watermark without scanning all views under the
coordinator. A bounded worker enumerates historical candidates with a persisted
continuation and idempotent input/view keys. Pending expansion past the deadline
is visible as coverage failure; it cannot silently reduce the denominator.

Maintenance schedule commit creates an expiry seed with the original end and
schedule generation. Supersession/cancellation explicitly disposes that seed.
After a delayed worker resumes, reconstruct the cohort at the scheduled end;
never create the first evidence only after a successful expiry reconciliation.
Future expiry seeds remain unsealed until the end is due. Sealing captures the
then-current registry checkpoint, while expansion still uses controls strictly
before the original end (scheduled order zero). Thus later controls cannot
replace the historical cohort. Proven cancellation/supersession is permitted only
before that end; it cannot retroactively erase a due observation.

The maintenance producer is implemented in `visibility/maintenance.py` and
`maintenance/repository.py`. `persist(..., profile=None)` records each new
`scheduled_end` job as the input identity in that same transaction, with its
original expected end, schedule revision and generation. A coverage edit
supersedes a replaced future seed; disable/delete cancels it. The disposition
uses the old seed's generation and the newly committed maintenance-control UUID.
Disposition continues even if capture configuration was subsequently removed.
Metadata, timezone-only and same-value changes create no new expiry seed.
If the original end becomes due before disposition is recorded, the seed remains
pending rather than retroactively cancelled. The normal maintenance control can
still commit. Fanout steps now link their actual projection version beside the
cursor commit, including unchanged materializations; a failed step rolls both
back. Missing projection proof remains an explicit gap. Job completion retains
its existing required projection sequence. These links do not prove historical
query eligibility or rendering; a recorded or disposed seed is not a successful
render.

Candidate selection precedes successful query evaluation. Later evaluation uses
the originally registered, server-resolved query and appropriate input-time
state/clock. Current filters and permissions decide eligibility; source before
and after membership and count/resource dependencies must be represented.
Blocked processing or unprovable CEL/auth/query state remains unclassified
coverage, not automatic ineligibility. Backend outcomes still count completely.

Unresolved integration gaps include grant and directory publication invalidation,
session revocation/expiry ordering, resource query revision changes, relative
query clocks, connection evidence and multi-process registry reconciliation.
Registration authority snapshots alone do not solve these. Later backgrounding,
revocation or query replacement cannot retroactively delete an eligible cohort;
loss of the ability to acknowledge leaves a missing observation.

The historical-authority implementation review found that equality of current
fields is insufficient proof of stability. Migration
`migrations/0038_visibility_authority_fences.sql` now adds a monotonic
`authorization_guards.directory_revision`, advanced under the existing identity
guard whenever accepted/applicable directory publication derives memberships and
configured global-admin mapping. This catches away-and-back authority changes even
when the individual user's evidence generation or content semantic revision does
not advance. A separate row-local capability-gate generation advances on every
human-auth ready transition. Its trigger ignores supplied generation resets and
does not acquire the identity guard after locking a gate row. Runtime roles
cannot delete/truncate these fences; the directory fence cannot be reset or skip
forward. Publication also now includes changed derived memberships and
configured global-admin flags when finalizing content semantic revision, before
revision-bound audit events. Unchanged re-verification preserves that content
revision; every accepted/applicable derivation still advances the conservative
authority fence.

Required snapshot bindings include tenant and principal identity, tenant revision,
principal evidence generation, global directory semantic revision, session
identity/deployment epoch, immutable authentication/absolute bounds and the
original idle authorization bound. A current heartbeat cannot extend an old
snapshot's historical proof interval. Missing legacy fields, any revision/binding
mismatch, current loss of access, revocation, gate uncertainty or expired session
proof remain unclassified; none is a retroactive exclusion. Any guarded helper
must take the identity guard before the tenant coordinator.

`visibility/authority.py` implements `prove_authority(conn, tenant=...,
input_id=..., view_id=..., view_epoch=...)` and `recheck_authority(conn, token)`.
They load the durable seed/candidate/historical control, take the identity guard
before the coordinator, and share-lock the human-auth gate while deciding.
Only matching fences, current authority and immutable session bindings within
the original authorization bound produce `AuthorityProof(proven=True, ...)`.
The internal token binds input fingerprint and historical snapshot identity for
a later transaction recheck. It is not a browser credential or render offer.
The helper never changes session activity, candidate eligibility or completion;
query correctness, foreground connection and rendering remain separate proofs.
Older snapshots lacking the versioned evidence cannot be inferred complete.

## Phase 3: proof-backed response offers

### Current producer configuration and worker

The capacity profile may contain an explicit `visibility` object with exactly
`version`, `policy_epoch`, `deployment_epoch`, `qualification`, `inputs` and
`registry`. Version is `keep-visibility-capture-v1`; the two allocation objects
use the `InputLimits` and `RegistryLimits` fields above. No deployment allocation
or retention default is supplied. A missing object disables new capture; malformed
configuration or a deployment epoch mismatch prevents workload startup.
Qualification sampling is a server setting, not a claim that the profile passed.

The HTTP boundary saves a monotonic timestamp immediately after complete body
receipt and before JSON/native validation and trusted gateway checks. Admission
subtracts the elapsed interval from its PostgreSQL clock anchor, conservatively
including the database round trip. This is an assistant-selected conservative
application receipt bound, independent of host wall-clock offset; it does not
rewrite product `received_at`, tenant ordering or maintenance admission time.
Gateway latency before this application boundary still requires supplied edge
instrumentation and clock qualification for an end-to-end SLO claim.

Admission commits the seed alongside capacity reservations and accepted work.
Source processing commits application digests and projection/journal locators
beside the product outcome. Semantic rejection has a bounded error code instead
of fabricated projection evidence. Retries preserve the first seed; a source
rollback rolls its observation link back too. Existing seeds continue linking
after capture configuration is removed. Storage exhaustion records gaps while
otherwise valid product work continues.

Run `python -m keep.worker visibility-capture` after migrations and the normal
recovery gate. Each turn seals at most one due expiry boundary and expands one
bounded historical view page. Selection skips busy tenant coordinators, and the
persisted seed allocations bound resumed work. This worker neither classifies
eligibility nor issues/accepts render acknowledgements. It creates no telemetry
deletion policy and cannot report a successful visibility percentile.

Proposed `visibility_render_offers` binds server observation UUID to a selected
input/view pair, session/deployment epoch, resolved query digest, resource
revisions, exact response identity, allowed outcome, pre-PIT searchable/version
bounds, maintenance completion proof and issuance time. No offer alone changes
completion status. Use a new `visibility/offers.py` service:

```python
issue_offers(conn, *, authorized_scope, view_id, view_epoch,
             response_proof, maximum_offers) -> list[RenderOffer]
```

Extend eligible data responses with `meta.render_observations`. A later correct
response may offer completion of earlier pending candidates. Bounded offer lists
must leave excess observations durably pending. Never truncate the denominator.

Feed PIT proof uses the verified pre-PIT lower bound, not the post-query observed
vector. A missing ID on a page does not prove nonmatching: perform a targeted
same-PIT query/filter check or retain a gap. Current pagination cannot magically
prove rendered state for all matching rows. Count proof requires one coherent
batch, all successful shard results and verified query/configuration context.
Maintenance expiry additionally requires completed suppression work and enough
searchable projection coverage; generation alone is insufficient. Existing
`CountBatch.observation_id` remains separate from this offer ledger.

### Proposed detail-first increment (not implemented)

This assistant-selected implementation proposal starts with incoming inputs and
alert-detail views. It does not establish eligibility, offers, acknowledgements
or successful visibility measurements in the current implementation. Feed,
saved-query, count and maintenance-expiry candidates retain unclassified coverage
until their separate proofs are implemented.

Classify selected candidates independently of successful data fetches. Proposed
append-only classification evidence binds tenant/input/view/epoch, original
deadline, historical control checkpoint, application fingerprint, resolved query
digest and authority proof. A failed fetch must not prevent otherwise proven
eligibility. Once eligible, later revocation, backgrounding or query replacement
cannot remove that observation; missing or late completion remains visible.

For detail, resolve query identity to the exact retained alert UUID. Produce one
coherent PostgreSQL response proof containing server response UUID/evaluation
time, rendered-data digest, alert revision and projection version, maintenance
checkpoint/head/catalog evidence and derived suppression. Bind it to the exact
application fingerprint and target projection version. A later complete response
for the same alert may cover an earlier target version. Revision alone is
insufficient: unchanged source receipts advance projection version without a
semantic revision. Missing/rejected application proof stays a gap. A stale
materialized suppression flag or an unrelated Elasticsearch watermark cannot
substitute for authoritative detail proof.

The guarded offer transaction must acquire identity protection before tenant
coordination, check current access, and keep response construction/proof binding
coherent. Product-only reads must remain side-effect-free. A candidate API is:

```python
issue_detail_offers(conn, *, authorized_scope, view_id, view_epoch,
                    detail_response_proof, maximum_offers)
```

Each bounded durable offer binds one classified candidate, session/deployment
epoch, resource/query identity, exact response proof, allowed outcome and server
issuance time. Persist metadata/digests rather than another alert-content copy.
Use tenant-scoped composite foreign keys, RLS, immutable identity and explicit
non-NULL constraints for required proof. Allocation exhaustion leaves candidates
pending with an explicit coverage gap. Offers are not completions. ACK acceptance
stores the first valid server receipt time, preserves it on replay, validates the
issued bounds and current authorization, and never refreshes session activity.

The structural ACK model currently requires Elasticsearch fields for every
resource. A proposed explicit PostgreSQL detail-proof variant must replace that
assumption before detail integration; never manufacture an index epoch or use
unrelated searchable progress as detail evidence.

Frontend integration must register the detail view before measuring subsequent
inputs, bind requests to the returned view/epoch, and acknowledge only the exact
response committed to the mounted tree after a paint opportunity. Cancel on
replacement, navigation, backgrounding or unmount. The current resource hook
retains old data during loading/error, so data presence alone cannot trigger an
ACK. Historical foreground reports and unexpired leases establish reported
contact, not uninterrupted presence. Proposed ordered lifecycle/contact evidence
and a monotonic browser lifecycle generation must detect hidden/visible,
disconnect/reconnect and disposed-view transitions. Record the accepted
operational interpretation of those client reports before enabling positive
eligibility; unknown intervals remain coverage gaps, never inferred exclusions.

Before expanding to feeds/counts, resolve saved CEL/panel revisions at the
original input boundary and distinguish original membership time from response
query time. Page absence is not nonmatching; later observed progress cannot
upgrade an old PIT. Expiry additionally needs original-end temporal relevance;
whole-count completion needs fan-out completion and searchable sequence coverage.

Proposed acceptance checks cover unchanged-source version dominance, atomic
response/offer rollback, stale retained UI data, background-before-paint,
cross-session and future-bound ACK rejection, duplicate/late receipt, allocation
exhaustion and post-classification revocation. None has been executed for this
unimplemented increment.

## Phase 4: rendered acknowledgements and browser integration

Implement the normative `POST /api/tenants/{tenant_id}/view-observations` only
after offers/proof exist. Phase 1 supplies its structural model but no route:

```json
{
  "observations": [{
    "observation_id": "server-issued UUID",
    "view_id": "server-issued UUID",
    "view_epoch": "4",
    "resource": "alert_feed",
    "resource_id": null,
    "query_digest": "64 lowercase hexadecimal characters",
    "outcome": "nonmatching",
    "observed": {
      "index_epoch": "qualified index epoch",
      "searchable_seq": "123",
      "view_generation": "51",
      "maintenance_generation": "8",
      "resources": []
    }
  }]
}
```

Outcomes are `matching`, `nonmatching`, `counts`. Resource observations may carry
`resource_id`, `revision`, and optional `projection_version`. Counters are
canonical bounded decimal strings. Structural decoding rejects coercions,
unknown keys, duplicate observation/resource IDs, invalid UUIDs, more than 100
observations, and UTF-8 bodies over 64 KiB. It does **not** validate an offer or
establish success. Browser timestamps are not accepted as completion evidence.

The eventual service must check current session, tenant access, same origin,
CSRF, issued scope/query/outcome and version bounds. First valid receipt stores
the server timestamp; duplicates do not refresh it. Impossible future versions,
other sessions and unissued IDs cannot create success. Proposed batch semantics
are atomic validation/acceptance, with idempotent replay of previously accepted
items. Missing and late acknowledgements stay misses/coverage gaps.

Browser ownership: add a reusable registration/render queue hook, then integrate
`workspace.tsx`, `alerts.tsx`, detail views and count components. Register each
tab/surface and report lifecycle/query changes. Acknowledge only after matching
data commits to the mounted React tree and crosses a paint opportunity. Guard by
response identity and view epoch; cancel superseded/disposed callbacks. A
retained stale result during loading/error, an SSE hint, a frozen old PIT and a
successful fetch without rendering do not qualify. Batch at most 100 IDs/64 KiB;
retry the same IDs without invoking the separate activity heartbeat.

## Telemetry ownership, enablement and evidence

Observability/technology teams own telemetry storage and retention. Require an
explicit sink/binding plus acknowledgement/export checkpoint and supplied
evidence retention contract, sufficient for rolling 30-day application SLIs.
Do not create a new fixed product-history lifetime for operational observations.
The app ledger coordinates pending work; cleanup must preserve required input,
cohort, offer and completion evidence until the supplied handoff contract allows
retirement. No sink or retention default is selected here.

Exports need idempotent input, eligibility/exclusion, issued-offer, completion,
deadline-gap and complete-backend-outcome records. Metric labels must not expose
raw CEL, source values, session identifiers or unbounded resource IDs. Report
all-traffic and separately identified normal-operation cohorts, sampled coverage,
missing acknowledgements and exclusions. Label sampled percentiles estimates;
below 100 eligible observations publish raw outcomes and insufficient confidence.

Enable only after integrated tests prove durable selection survives failed
search, blocked source heads, worker outage and missing browser replies. Also
test original expiry time after recovery, duplicate receipt/ack replay,
cross-session/tenant/query forgery, old PIT/new observed vector, nonmatch versus
later-page absence, current authorization loss, background-before versus
background-after input, discarded browser responses and collapsed intermediate
versions. No phase-1 test establishes these end-to-end properties.

Implementation sequencing keeps schema review and producer integration with the
coordinator. Search/count integrations must supply response proof before frontend
render consumers can be enabled. Each subsequent phase requires focused
failure-path evidence before the measurement capability can be qualified.

Phase-1 local evidence: the final focused run passed 27 tests (19 model/sampling
unit tests and eight isolated PostgreSQL tests) in 4.41 seconds. PostgreSQL API
calls used the restricted `keep_api` role. Coverage includes lifecycle/replay,
cross-session isolation, current authorization/CSRF before replay, an authorization
writer preceding a waiting request, revision and active-capacity concurrency,
unchanged session activity, capacity/body bounds and canonical path IDs. The
isolated database was removed after the run. Ruff checks passed. No browser,
input-denominator, offer, rendered-acknowledgement or external telemetry test was
performed in this phase.

Phase-2 local evidence: 13 focused tests passed against a newly created
isolated PostgreSQL database in 7.09 seconds, then the database was removed.
After publication, the same 13 cases passed in 5.18 seconds through the normal
migrator against another fresh isolated database, which was also removed.
They exercise seed transaction rollback, immutable boundaries and replay,
concurrent input allocation, explicit capacity gaps, crash-resumable bounded
historical expansion, delayed expiry sealing, cancellation cutoff, rejected and
applied proof linkage, digest-only content retention, fair retry scheduling,
coordinator-before-seed ordering, busy-tenant skipping, and restricted API/worker
grants and tenant RLS. Four dependency deprecation warnings remain. These are
prerequisite tests, not end-to-end visibility or production qualification.
The final two cases verify SQL rejects a NULL rejection error code and preserves
historical views from another deployment as explicit unclassified coverage.

Maintenance producer evidence: eight new isolated PostgreSQL cases and fifteen
existing maintenance API regressions passed together (23 tests, 12.68 seconds).
New API cases use `keep_api`. They cover create/replay identity and original
end, metadata/timezone/no-op stability, end supersession with and without current
capture configuration, disable/re-enable/delete lifecycle, atomic rollback,
explicit capacity gaps without losing product schedules, and overdue seed
preservation. Six existing dependency warnings remain. The isolated databases
were removed after the run; no private or shared-company service was involved.
An additional claimed-overdue regression and its unclaimed counterpart passed
(two tests, 3.55 seconds): actual scheduled-end claiming advances the mutable
job generation, and a later disable still preserves the original pending expiry
seed. Disposition validates the job's window/kind/original end and uses the seed's
immutable schedule generation.

Historical-authority prerequisite evidence: twelve focused PostgreSQL cases
passed before publication (6.55 seconds), and all twelve passed again through
the normal migrator after publication (6.32 seconds). Coverage includes stable
positive authority and token recheck, gate close/reopen and supplied generation
resets, restricted-role fence protection, actual configured-admin mapping away
and back, failed-refresh last-known evidence, tenant/principal/session changes,
legacy snapshots, original idle bounds despite newer heartbeat, and gate-row
locking. Four dependency deprecation warnings remain. These results do not
establish overall visibility eligibility or rendered completion.
After the content semantic-revision correction, thirteen authority cases and the
existing real-publication no-op/partial-negative/stale-generation regression
passed together (14 tests, 15.36 seconds). Additional evidence covers newly
tracking an already-known nested root, configured mapping changes without
refreshing the user's generation, preserved no-op revision, and audit events
carrying the final semantic revision. Backend source was then frozen for the
integrated verification run; that run is recorded separately.


## Next integration boundary: PostgreSQL alert detail

The next assistant-selected implementation slice is incoming-alert visibility on
an already registered alert-detail view. This is still a proposal, not issued
proof or an enabled measurement path. Classification must run independently of
successful detail requests so processing failure, absent browser requests and
missing acknowledgements remain represented.

Use the existing authority token to prove uninterrupted authorization; separately
prove the historical foreground contact, unchanged view epoch and exact alert
identity at the original input boundary. An unknown lifecycle or authority interval
remains unclassified. A contact lease does not prove a browser painted anything.
An unrelated alert-detail resource may be excluded only from its immutable
registered identity and application link, not from a failed fetch.

A detail response needs a server-issued response identity and digest, its
PostgreSQL alert revision and projection version, plus the suppression proof
captured in that same repeatable-read transaction. Both version coordinates
matter: unchanged source deliveries can advance projection without advancing
semantic revision. A later response for the same identity may cover earlier
inputs only when its projection version reaches their recorded target. It may
not manufacture coverage for a maintenance expiry whose full fan-out proof is
still incomplete.

Persist offers bound to the original input/view/epoch, session and deployment,
query intent, response identity, resource versions and outcome. PostgreSQL detail
requires an explicit observed-bounds variant; do not invent an Elasticsearch
index epoch or searchable watermark for a database-only read. The structural
acknowledgement models above currently support only search bounds and must be
extended before this path is exposed.

Validate a bounded acknowledgement batch atomically against issued offers and
current authorization. The first valid server receipt is immutable, duplicates
keep its time, and late receipts remain misses. Then connect browser registration,
lifecycle reports and a response-specific post-render acknowledgement to the
mounted detail component, cancelling superseded or disposed callbacks. Preserve
all unclassified and missing evidence in the eventual explicitly configured
telemetry handoff. Feed and count proof still require their own PIT/query and
complete-batch integrations; this detail slice cannot qualify those surfaces.
