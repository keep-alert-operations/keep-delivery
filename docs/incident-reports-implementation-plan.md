# Incident reports — implementation plan (REV-53)

Status: **approved by the user on 2026-10-30**, including both open proposals. Executed evidence is in [implementation status](../status/implementation-status.md).
Approved mock: [Incident reports mock](https://claude.ai/artifact/NxBWGUChKyvq1CnRJmxoky), option A, Incidents only.
Recorded in the design register as [REV-53](https://github.com/keep-alert-operations/keep-design/blob/main/decisions/design-register.md).

## 1. Goal

A Reports page in the rail shows, for one tenant and a chosen period, how incidents were handled. It shows
headline figures with a previous-period comparison, opened and resolved per hour, day or week, how resolved
incidents ended, and a breakdown by severity, origin, correlation rule or assignee. Everything is computed at
read time from PostgreSQL. It adds no worker, scheduler or new write path (REV-34–36).

Noise reports (alert occurrences, flapping, suppression, top sources) are **out of scope for now** (user decision,
2026-10-30). The rail item is still called Reports so they can join later as a second tab.

Base revisions (origin/main, fetched 2026-10-30): api `5bb8de8`, contracts `9f4736f`, migrations `2d0e8c7`,
domain `d34e038`, frontend `1a3dfc5`, design `c973b4d`, delivery `ccd6e33`. Free numbers: **REV-53** (REV-52 is
claimed by open PR keep-design#17) and **migration 0058**.

## 2. Decisions

| Decision | Label | Source |
|---|---|---|
| Reports is a new rail item after Boards; incident report only, noise later | user decision, 2026-10-29/30 | chat; mock option A |
| Breakdown is one panel with a Severity / Origin / Correlation rule / Assignee switcher (option A) | user decision, 2026-10-30 | chat |
| Time to first response = earliest of first assignment or first move to Investigating within the cycle | user decision, 2026-10-29 | chat |
| Every tenant reader (view, edit, admin) can open it, including the per-assignee breakdown | user decision, 2026-10-29 | chat |
| Periods: 24 hours, 7, 30, 90 days, custom date range; compare with the previous period | user decision, 2026-10-29 | chat |
| Breakdowns by severity, origin, correlation rule and assignee | user decision, 2026-10-29 | chat |
| Figures count incident **cycles**, so reopenings count as opened ("43 new · 5 reopened") | assistant choice, shown in approved mock | spec-incident-operations 4 |
| 7/30/90 days are calendar days in the viewer's zone including today; 24 hours is rolling | assistant choice, shown in mock | — |
| Reports reach back at most 90 days (retention horizon), even though purge is not deployed | assistant choice | incident ops 13; alert ops 22; worker scope 2 |
| An assignee carried over a reopening is not a response in the new cycle | assistant choice | follows from "within the cycle" |
| Assignee breakdown uses the assignee when the cycle resolved (open cycles: current, masked) | assistant choice | shown in mock footnote |
| Compute at read time; add indexes in migration 0058; no stored counters | assistant choice | user preference for read time |
| Manual refresh only; no polling or SSE reload | assistant choice | §7 |
| No feature flag; additive read-only endpoint | assistant choice | §11 |
| Breakdown rows link to the Incidents list filtered by group and created time | open proposal | §8, counts can differ |
| Deleted rules show their last name tagged Deleted while their history is retained | open proposal | §5.5 |

## 3. Final metric definitions

Period `[from, to)`. Only incidents whose identity is not retired count. Times are in the viewer's zone.

- **Opened**: incident cycles whose `opened_at` falls in the period. The sub-line says how many are new
  (no earlier cycle) and how many are reopenings.
- **Resolved**: cycles whose `resolved_at` falls in the period, whatever the cause. The sub-line gives manual
  and `alerts_resolved` counts.
- **Time to first response** (cycles opened in the period): from `opened_at` to the earlier of the cycle's
  `investigating_at` and its first `assignment` activity with a non-null assignee, recorded between `opened_at`
  and `resolved_at` (or now). Median and 90th percentile. Cycles without a response yet are left out and counted
  as "waiting".
- **Time to resolve** (cycles resolved in the period): `resolved_at − opened_at`. Median and 90th percentile.
- **Reopen rate**: of the cycles resolved in the period, the share followed by another cycle of the same
  incident that opened before the period ended.
- **Resolution cause**: resolved cycles by cause: Manual, All alerts resolved, Inactivity. Inactivity is shown at
  zero with "automatic inactivity resolution is off" (REV-34).
- **Series**: opened and resolved counts per bin. The bin is an hour for periods of at most 25 hours, a local
  calendar day for at most 45 days, and 7 local days from the period start beyond that.
- **Breakdowns**: severity (fixed at creation), origin (manual or correlation rule), correlation rule (the rule
  of the incident's context), and assignee. Each row shows Opened, Resolved, median First response, median
  Time to resolve and Reopened, with the same definitions applied within the group. Rule and assignee lists
  are capped at 50 rows plus an "Other" row.
- **Assignee for a cycle**: for a resolved cycle, the value of the latest `assignment` or `eligibility_lost` activity
  recorded at or before `resolved_at` (none means Unassigned; incidents start unassigned). For an open cycle,
  the incident's current assignee with the existing read-time eligibility masking.
- **Coverage**: `covered_from = max(from, H)`, where `H` is the first local midnight at or after `now − 90 days`
  (database clock). The previous period is the same number of local days (or 24 h) before `from`. It is only
  available when it starts at or after `H`, so never for 90 days.

## 4. Acceptance criteria (tied to the approved mock)

1. The rail shows **Reports** after Boards for every tenant reader. `/tenants/{id}/reports` opens it, and the
   breadcrumb shows Tenant / Reports with the role chip. View-only readers see everything; there are no write actions.
2. The period control offers 24 hours, 7 days, 30 days (default), 90 days and Custom. Custom has From and To
   dates (To at most today, From on or before To), a note when From is past retention, and Apply/Cancel.
   The range reads "30 Sep – 29 Oct 2026", with the exact instants and zone in its tooltip.
3. "Compare with previous period" is on by default. Each figure then shows its change from the previous value.
   Counts are neutral; time and reopen rate are coloured better or worse with text arrows. When the previous
   period is past retention, the line reads "No comparison: the previous period is past retention".
4. A range starting before the horizon reports from the horizon and shows the info note naming the covered and
   requested ranges.
5. The figure strip shows the five figures in §3, each with an ⓘ popover giving its definition. "How these figures are
   measured" at the foot lists every definition plus the retention and time-zone line.
6. The "Opened and resolved, per day/hour/week" chart uses validated colours (light `#0a6fb0`/`#2e9e62`,
   dark `#2f8fcf`/`#33a06c`), a legend with totals and per-bar tooltips. "View as table" gives an accessible table.
7. The Resolution cause panel lists Manual, All alerts resolved and Inactivity with bars, counts and shares.
8. The breakdown panel has the four-way switcher and the five columns. Rule view includes deleted rules tagged
   Deleted and a footer counting manual incidents. Assignee view includes Unassigned and marks "(you)".
   Each name links to the Incidents list (§8).
9. States: the first load shows placeholders and never claims empty. A refresh keeps the figures and says
   "Refreshing…". An empty period says so, and 24 hours offers "Show last 7 days". If assignment history can't
   be read in time, first response and the assignee breakdown show as unavailable with a warning note and
   Retry, and everything else is shown. A failure shows an error panel with the request ID and Retry, keeping
   period and settings.
10. "Computed 2 minutes ago" is relative to now, with the exact time in the tooltip, plus a Refresh button. Nothing
    refreshes by itself.
11. Light and dark follow the Ocean tokens. At 390 px the rail moves to the top, figures form two columns,
    tables scroll inside their panel and popovers fit the screen. Every control works by keyboard and has
    a label, and the chart has a text alternative.
12. API: tenant readers get 200, a caller without access gets 404, and other tenants' data never appears (RLS plus
    explicit tenant predicates). Invalid parameters return 422 with field errors. p99 stays within two seconds
    (N3; reliability operations 5) for the qualification tenant (§5.4).

## 5. Data source and query design

### 5.1 Tables (verified observations)

- `incident_cycles(tenant_id, incident_id, id, opened_at, investigating_at, resolved_at, cause)`: hash
  partitioned by `incident_id` (32), with only the PK `(incident_id, id)`. Rows are inserted on create
  (api `incidents/routes.py:74`), on reopen (`:193`) and on correlation create (domain `correlation/runtime.py:729`).
  Resolution happens in api `:208`, domain `incidents/state.py:211` and `inactivity.py:91`. Nothing deletes cycles.
- `incident_activity(..., kind, recorded_at, actor_id, evidence)`: hash partitioned, PK only. `assignment`
  evidence is `{"assignee_id": <uuid|null>, "effective_at"}`, written only on a real change (api `routes.py:149,236,251`).
  `eligibility_lost` clears an assignee (domain `identity/cleanup.py:76-115`).
- `incidents(severity, origin, context_id, assignee_id, created_at)` with `incidents_tenant_context`.
  `incident_identities.retired_at` hides retired incidents. `correlation_contexts.rule_id` →
  `correlation_rules(name, deleted_at)`.

### 5.2 Migration 0058 (keep-migrations)

```sql
CREATE INDEX incident_cycles_tenant_opened   ON incident_cycles(tenant_id, opened_at);
CREATE INDEX incident_cycles_tenant_resolved ON incident_cycles(tenant_id, resolved_at) WHERE resolved_at IS NOT NULL;
CREATE INDEX incident_activity_assignee      ON incident_activity(incident_id, recorded_at)
  WHERE kind IN ('assignment','eligibility_lost');
```

These are plain `CREATE INDEX` on the partitioned parents, following the 0001/0028 precedent. The runner's single
transaction rules out `CONCURRENTLY`, so the build blocks incident cycle and activity writes for one scan. It
belongs in the stopped-writer migration window, like 0050–0052. Add the Alembic wrapper, the SQL, a
`checksums.json` entry and a README paragraph. The API is correct without the indexes, only slower.

### 5.3 Query (keep-api, new `reports/incidents.py`)

One `REPEATABLE READ READ ONLY` transaction runs after `authorize(..., "view")`, which sets
`keep.tenant_id` for RLS. Every statement also filters `tenant_id` explicitly.

1. **Clock and bins in Python.** Read `now()` once. Compute `H`, the covered range, the previous range and
   the **bin edges as UTC instants** with `zoneinfo`, using the pinned zone list (`maintenance/timezones.zone`).
   SQL then uses `width_bucket(ts, edges)` and never depends on PostgreSQL's own zone database. That keeps
   DST days at 23 or 25 hours correct.
2. **Query A (no activity).** One scan of cycles opened or resolved in `[prev_from or covered_from, to)`.
   It uses a `UNION` of the two new index ranges, joined to `incidents` and `incident_identities`, with
   `period` / `previous` flags. Its results are:
   - Summary counts per period, plus new vs reopened, using `NOT EXISTS` on an earlier cycle by PK prefix.
   - Cause counts and reopened, using `EXISTS` on a later cycle opened before `to`.
   - Time-to-resolve median and p90 via `percentile_cont ... FILTER`.
   - Series via `width_bucket`.
   - Severity, origin and rule groups via `GROUPING SETS`.
   Statement timeout: `SET LOCAL statement_timeout` to `min(1200 ms, request budget)`. A timeout here is 503
   `query_deadline` (count_routes precedent).
3. **Query B (assignment history), inside a savepoint.** One statement reads every `assignment` and
   `eligibility_lost` event of the period's incidents through `incident_activity_assignee`
   (`incident_id = ANY(...)`, with the id array passed through a scalar subquery so the planner does not
   estimate and prune each element against all 32 partitions). Pure Python then derives each cycle's first
   assignment within the cycle and its assignee at resolution by binary search. Timeout `min(700 ms, remaining)`;
   on `QueryCanceled` roll back to the savepoint and return `unavailable: ["first_response", "assignee"]` with
   everything else. (Implementation change after measurement: per-cycle correlated lookups took about 2 s on
   20,000 cycles because each paid partition pruning; planning a literal 20,000-element array took 0.55 s.)
4. **Names.** `principal_names` resolves assignee names in one batch. Current assignees of open cycles use the
   `ASSIGNEE_QUALIFIES` masking. Rule names come from `correlation_rules`; deleted rules are handled under §5.5.

### 5.4 Bounds and cost

- Inputs: at most 366 days requested, and coverage at most 90 days, so a scan covers at most 180 days (current
  plus previous).
- Index range scans touch only the tenant's cycles in range. Each query-B lookup is a bounded index probe
  per cycle. Breakdown output is capped (§3).
- Target: under 2 s p99 under the qualification workload. Measure with a seeded tenant of **20,000 cycles in 90
  days** and **200,000 activity rows**, including incidents with 2,000+ activity rows. Expect under 1 s locally,
  with indexes visible in `EXPLAIN`. That size is an assistant-selected test size, not a product cap.
- No caching and no stored aggregates. Revisit only if the measured p99 misses.

### 5.5 Deleted rules (open proposal)

The current read API nulls deleted rule names (`shared/display_names.py:143`). Proposal: the report shows the last
name with a Deleted tag while the rule's final revision is retained (`correlation_revision_retained`, which
stays true while any incident references its context). Otherwise it shows "Deleted rule". The alternative is to
always show "Deleted rule", matching the detail page.

## 6. API endpoint and contract

`GET /api/tenants/{tenant_id}/reports/incidents`, on a new router with prefix `/api/tenants/{tenant_id}/reports`.
It does not go under `/incidents`, where it would collide with `/{incident_id}` UUID validation. `permits_http`
already allows `/api/tenants`.

| Parameter | Rule |
|---|---|
| `from`, `to` | RFC 3339 with `Z`. `from < to ≤ now + 5 min`, and `to − from ≤ 366 days`; naive times are rejected (422). |
| `time_zone` | IANA name in the pinned list (422 `invalid_timezone`). |
| `compare` | `true` (default) or `false`. |

Unknown or repeated parameters keep the existing 422/400 handling.

The response is `{data, meta}`, with `meta` carrying `request_id` and `computed_at`. Counts are decimal strings
(system completion 5); durations are whole seconds, also as decimal strings.

```text
data.period      requested_from, requested_to, covered_from, covered_to, retention_from, time_zone, bin(hour|day|week)
data.previous    {from, to} | null ; data.previous_unavailable = "past_retention" | "not_requested" | null
data.summary     opened, opened_new, opened_reopened, resolved, resolved_by_cause{manual, alerts_resolved, inactivity},
                 reopened, first_response{median_seconds, p90_seconds, responded, waiting} | null,
                 time_to_resolve{median_seconds, p90_seconds} | null
data.previous_summary   same shape | null
data.series      [{start, opened, resolved}]
data.breakdowns  severity[], origin[], rule[{rule_id, name|null, deleted}], assignee[{assignee_id|null, display_name|null}],
                 each row: opened, resolved, first_response_median_seconds|null, time_to_resolve_median_seconds|null, reopened;
                 rule/assignee: other{...} | null
data.unavailable ["first_response", "assignee"] | []
```

The response is documented with the existing `documented()` models so the OpenAPI includes the shape.
Contracts are regenerated with `keep-delivery/qualification/generate_http_contracts.py` into
`keep-contracts/schemas/*.json`. The frontend then re-pins `@keep/contracts` and runs `npm run generate`;
generated files are never hand-edited. The endpoint is a read-only GET with no audit or command identity. Errors
are 404 for no access, 422 for validation, and 503 `query_deadline` or `database_unavailable` with Retry-After.

## 7. Refresh behaviour

Manual only. The report loads on open and when the period or Compare changes; the Refresh button reruns it.
It does not reload on live incident hints (`scope.version`), because a 90-day aggregate per incident change
would cost far more than it helps. A refresh keeps the previous figures (`useResource`) and says
"Refreshing…". "Computed …" uses `meta.computed_at`.

## 8. Frontend (keep-frontend)

- `workspace.tsx`: add `["reports", "Reports"]` after dashboards. `resources.tsx`: a `reports` case, because the
  default falls to admin. `icons.tsx`: a `reports` bar-chart icon.
- New `components/reports-pages.tsx` (page, period control, figure strip, chart, cause list, breakdown, definitions)
  and `app/styles/reports.css` imported in `layout.tsx`. Tokens only; no new colours beyond the two validated
  chart pairs, added to `theme.css` as `--chart-1/--chart-2`.
- Data: `request<T>()` with types from `generated.ts`, and `useResource` for stale-while-refresh. Period maths
  uses the browser's local calendar (the viewer's zone), sending UTC instants and
  `Intl.DateTimeFormat().resolvedOptions().timeZone`.
- **Deep links (open proposal):** `incident-list.tsx` seeds its filters from the URL query, following the
  `presets-pages.tsx` `useSearchParams` precedent. Report rows link to
  `/tenants/{id}/incidents?view=all&created_from=…&created_to=…&severity|origin|rule_id|assignee=…`.
  The list filters by incident creation and current assignee, so counts there can differ from the report's
  (reopenings, later reassignment). The link tooltip says "Incidents created in this period with …".
- Docs: `docs/ui-design.md` (reference mapping row plus a REV-53 paragraph) and `DESIGN.md` (figure strip, bar
  chart and bar list under Components).

## 9. Repositories, order and ownership

Integration owner: this session (`/eng-coordinate`). Work happens in worktrees on `codex/incident-reports`
branches; the main checkouts stay untouched.

1. **keep-design**: REV-53 entry; spec-incident-operations new §20 "Incident reports (I5, N3; REV-53)" plus
   a preamble note; spec-system-completion §3 dated rail note and §10 route; spec map Incidents row;
   verification-plan "### Incident reports (REV-53)"; `scripts/check_docs.py` REV range to 53; context
   `task-map.json` adds §20 to the incidents route, then `context.py render`. **Lands after the REV-52 PR**
   (contiguous REV check), rebased on it.
2. **keep-migrations**: 0058, as in §5.2.
3. **keep-domain**: no change expected. It reuses `authorize`, `zone`, `principal_names` and the masking helper.
4. **keep-api**: `reports/incidents.py`, the router, models, unit tests and real-PostgreSQL integration tests.
   It depends on contracts/domain pins only for existing helpers.
5. **keep-contracts**: regenerated schemas from the API at its new head (producer tool).
6. **keep-frontend**: re-pin contracts, `npm run generate`, Reports page, deep links, styles, docs, Playwright specs.
7. **keep-delivery**: this plan in `docs/`, implementation-status entry, delivery-readiness rollout paragraph,
   `verify_documents.py` REV range, `schema-producers.json` hashes if they change, and manifest plus lock
   pins for the changed repositories.

Ingestion, processor, correlation and indexer are not touched.

## 10. Compatibility, rollout and rollback

- Additive: a new GET route, new OpenAPI paths and new indexes. Existing contracts are unchanged, so the
  breaking-diff check should pass. The incident list keeps its behaviour without query parameters.
- Rollout: apply **0058** in the stopped-writer migration window, deploy the API, then deploy the frontend.
  The API works without 0058 but may miss p99 on large tenants.
- Rollback: redeploy the previous frontend, which removes the rail item, and then the previous API. The indexes
  can stay; they are harmless and only cost a little write overhead on cycle and activity inserts.
- No feature flag. The feature is read-only, so turning it off is a frontend rollback; a flag would add
  configuration without protecting any data.

## 11. Risks

- **Index build blocks incident writes during migration.** Mitigation: stopped-writer window; incident tables
  are small relative to alert tables. Measure build time on the local stack.
- **Incidents with heavy activity slow query B.** Mitigation: the partial index, plus partial results instead
  of failure.
- **Per-assignee figures are visible to view-only readers.** This is the user's decision; it is recorded in the
  register.
- **Link counts differ from report counts.** The tooltip states what the list shows (open proposal).
- **Clock skew between `recorded_at` (clock_timestamp) and the cycle times (transaction time).** Milliseconds,
  irrelevant at minute precision.
- **REV numbering.** A parallel session already holds REV-52. Re-check origin/main before committing and
  renumber if needed.
- **No frontend lint script exists.** The checks are `format:check`, `typecheck`, `build` and Playwright. I'll
  say so rather than claim a lint pass.

## 12. Verification (planned, not yet run)

Test docstrings name `Requirements: REV-53; incident operations 20.`, plus `I5`, `N3; reliability operations 5`
or `I2; incident operations 4` where relevant.

- **Migrations:** unit and checksum tests. Alembic integration: 0057 → 0058 upgrade, indexes present on every
  partition, runtime roles unchanged, history and checksums intact.
- **API unit:** parameter validation (zone, naive times, length, order, future); bin choice and edges across DST
  (Asia/Jerusalem 25 Oct 2026, Europe/London 25 Oct 2026); coverage clamp; previous-period availability; response
  shape; deleted-rule naming; cap and Other.
- **API on real PostgreSQL:** a seeded tenant with manual and correlation incidents, reopenings, investigating,
  assign, reassign, unassign, `eligibility_lost`, `alerts_resolved`, a deleted rule, a retired incident, and a
  second tenant for RLS isolation. Assert every figure, percentile, breakdown and series bin against hand-computed
  values. Force query B to time out and assert the partial result. Assert 404 and 403/200 by role. Run the
  20,000-cycle performance case with timing and `EXPLAIN`.
- **Contracts:** regenerate, then `qualification/check_contracts.py` (additive diff) and
  `verify_boundaries.py`.
- **Frontend:** `npm run format:check`, `typecheck` and `build`. Playwright with the synthetic API covers:
  - presets and custom range under `timezoneId` Asia/Jerusalem across the DST week
  - Compare on and off, and past retention
  - loading, refreshing, empty, partial and error states
  - the switcher, row links opening the filtered Incidents list, and the incident list seeding from the URL
  - light/dark, 390 px, keyboard and aria
- **Docs:** design `scripts/check_docs.py`; delivery `qualification/verify_documents.py`, `check_context.py`
  (after `context.py render`) and `check_contracts.py`.
- **Local stack:** baseline S1–S4 (development runbook "Repeatable verification workflow"). Then the browser
  journey: create, assign, investigate, resolve and reopen incidents, open Reports, and check the figures, light
  and dark, desktop and phone width, and the empty, loading and error states.
- Everything not run is reported as not run.

## 13. Proposed register entry (draft)

```markdown
## REV-53 — Incident reports by period (2026-10-30)

**User decision.** Add a Reports page to the rail with an incident report for one tenant and a chosen period,
as in the [incident reports mock](https://claude.ai/artifact/NxBWGUChKyvq1CnRJmxoky), option A
([incident operations 20](../specs/spec-incident-operations.md#20-incident-reports-i5-n3-rev-53)). Noise reports are left for later.

- **Location and access (user decision, 2026-10-29).** A Reports rail item after Dashboards; every tenant reader
  (view, edit, admin) can open it, including the breakdown by assignee.
- **Periods (user decision, 2026-10-29).** 24 hours, 7, 30 and 90 days and a custom date range, compared with the
  previous period of the same length.
- **First response (user decision, 2026-10-29).** Time from a cycle opening to its first assignment or first move
  to Investigating, whichever comes first.
- **Breakdowns (user decisions, 2026-10-29 and 2026-10-30).** Severity, origin, correlation rule and assignee, in one panel with a switcher.
- **Assistant-selected details.** Figures count lifecycle cycles, so reopenings count as opened; days are calendar
  days in the viewer's zone; reports reach back at most 90 days, the retention horizon, and compare only when the
  previous period is within it; the assignee of a resolved cycle is the one at resolution; figures are computed at
  read time with no worker or stored counter; migration 0058 indexes cycles by tenant and time and assignment
  activity by incident, and runs before the API in the stopped-writer window; assignment history that cannot be
  read within its time budget leaves first response and the assignee breakdown unavailable rather than failing the
  report; the report refreshes only on request.

Original issue IDs and REV numbering are unchanged. Executed evidence belongs in delivery.
```

## 14. Approval

The user approved this plan on 2026-10-30, including both open proposals (breakdown rows link to the
filtered Incidents list; deleted rules keep their last name while their history is retained).
