# Observability implementation plan: logs, metrics and RUM

**Status: approved by the user on 2026-11-12; implementation in progress on
`feat/observability` branches, uncommitted.** Recorded as REV-74 in the design
register (it was drafted as REV-73 until main recorded the password sign-in under
that number on 2026-11-16), with the signal contract in
[observability](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-observability.md).

| Package (section 8) | State on 2026-11-12 |
|---|---|
| 0 design | Written: REV-74, the specification (including the alert's step-by-step flow), verification scenarios, catalog source. Design and delivery document checks pass. |
| 1 domain | Partly written: `keep.observability` (field registry, event catalog, context, record formatter, shipping handler, metrics, `/metrics` listener, service level classes, the alert flow helper, shared test fixtures); the shared HTTP layer, the worker loop and the shared changed-event consumer's failure record are instrumented; the apply outcome and correlation attachments are recorded. Not yet done: `db.py`, `alert_notifications.py`, clock health, CEL compiler, the rest of the correlation and search runtime, maintenance effects as steps, the state collector, the automatic-refresh summary, the route gate for RUM. |
| 2 ingestion | Alert flow steps and startup logging written. Not yet done: the request summary, gateway rejections and the metrics of 5.3. |
| 3 processor | Alert flow steps written, including retry, fence and clock-wait as steps. Not yet done: batch, rebalance, topology and retention-gap records and the metrics of 5.4. |
| 4 correlation | Alert flow steps written. Not yet done: capacity, proof, rule-health records and the metrics of 5.6. |
| 5 indexer | Alert flow steps for changed events written. Not yet done: reconciliation, rebuild and bulk records and the metrics of 5.7. |
| 6 api | Partly written: the API sets up logging and ships its records; every change a request makes to an alert is the step `alert_changed` (noted where the change is published, so commands, notes, incident links and moves, bulk commands and maintenance effects are all covered), followed by the Kafka hint step; request records carry the user; repeated reads, searches and heartbeats are summarised once a minute; every sign-in outcome is a counted, timed event (`sign_in_completed`, `sign_in_rejected`, `sign_in_unavailable`, with `sign_in_configuration_missing` and `sign_in_publication_fenced`), added 2026-11-16. Not yet done: session, sign-out and directory events, incident, rule, maintenance, preset, dashboard and operator command records, the live stream, and the remaining metrics of 5.8. RUM is in its own row below. |
| Metrics, across packages 1 to 6 | Written: every catalogued event increments `keep_events_total{event,outcome,reason}` and, where it carries a timing, `keep_event_duration_seconds` or `keep_event_delay_seconds`, whether or not the record is written; stage timings on `alert_published`, `alert_applied`, `alert_correlated` and `alert_indexed`; Kafka lag, offsets, client queue and broker state from the client's statistics; held partitions; the build gauge. These replace the separately named pipeline metrics listed in section 5, which now reads as the list of events that are counted. Not yet done: the state collector for numbers held in PostgreSQL, database pool and dependency timings, live-stream and operator metrics, and RUM. |
| RUM, across packages 6, 7 and 9 | Written: `POST /api/rum-events` on the Product API (session and CSRF, twelve batches a minute for each session, closed label sets, `keep_rum_*` metrics, browser log records), the strict request model and regenerated OpenAPI in keep-contracts, and the browser collector in keep-frontend (page views, page load timings, API calls, live connection, JavaScript errors, tab time). Not yet done: view and panel load timings, command retries, the receipt-to-screen estimate, long tasks, session-ended counts; `src/lib/generated.ts` in the frontend, which is regenerated only when its pinned contracts revision moves. |
| 8 migrations | Not started. |
| 10 delivery metrics | Local stack done: a metrics port per process set by the launchers, Prometheus 2.54.1 in the local Compose definition scraping the five processes and Fluent Bit every 5 s, 18 recording and alert rules with unit tests in `deploy/monitoring/prometheus`, and the dashboard `deploy/monitoring/grafana/keep-pipeline.json` provisioned in the local Grafana. Not yet done: OpenShift scrape annotations and metrics ports, an example OpenTelemetry collector configuration, the remaining rules of section 6 and the other dashboards of section 7. |
| 11 delivery logs | Local stack done: one Fluent Bit service in the local Compose definition, its configuration with one Elasticsearch output and index per service, the generated `KEEP_LOG_SHIP_URL`, and the local runbook section. Not yet done: the OpenShift Deployment, Service, network policy and settings for TLS and credentials to the logs cluster; the generated event reference. |

The service repositories still pin the previous keep-domain revision. Their
tests ran against the local keep-domain through the documented editable
override; nothing is committed.

**Moved onto main on 2026-11-16.** The ten branches now sit on the heads that
carry the password sign-in and ADAPI directory (REV-73): design f3cad1e, domain
a50e628, contracts 3a6d24b, api 6569e72, ingestion 5be3ba4, processor d121486,
correlation ec92196, indexer 6c792e5, frontend d336c38, delivery c1ccf05. What
the move changed in this work: the number (REV-74); the launcher sets each
process's metrics port inside main's `Start-Owned`; the browser's API timing
wraps main's `requestStatus`; `/sign-in` counts as the sign-in view; the activity
heartbeat and the session activity clock are gone from the repeat rule and from
the RUM endpoint's description, because main removed both; the planned sign-in
events of 5.8 follow the new sign-in. Main's sign-in code wrote its outcomes as
plain log lines; since 2026-11-16 they are the catalogued `sign_in_*` events of
5.8, counted and timed like every other event.

Checked on the merged trees on 2026-11-16:

- Document checks: design `check_docs.py`, delivery `verify_documents.py`
  (REV-01..REV-74) and `check_context.py`; `check_contracts.py` after
  regenerating the OpenAPI documents (additions only); `verify_boundaries.py`.
- Lint and unit suites: keep-domain 988, keep-contracts 58, keep-api 362,
  keep-ingestion 109, keep-processor 55, keep-correlation 8, keep-indexer 96,
  keep-delivery 353 (1 skipped), all passing.
- Real PostgreSQL 18.2 (throwaway container): the six alert-flow, API-command and
  RUM endpoint tests; 26 pipeline, maintenance and correlation-runtime tests
  (2 skipped); keep-processor 3; keep-api 5 (8 skipped for other bindings).
- Frontend: typecheck and format check pass; the browser suite ran 328 passed,
  13 skipped and 1 failed. The failure (`scalability-users.spec.ts`, "one
  invalidation uses one feed and one count batch") is intermittent and fails as
  often on main's own code (2 of 8 runs each way).
- The rule unit tests and the Prometheus and Compose files validate.

- The local stack, restarted from these checkouts on 2026-11-16 (the identity
  state of the earlier sign-in worktree was copied into `.local/identity` first):
  all eleven containers and six Prometheus targets up; one alert left its eleven
  steps in order across the four pipeline indices; a known mix of 38 requests
  moved eleven counters by exactly the number sent; lag was reported for all
  three consumers; no alert rule fired; Fluent Bit reported no error, failed
  retry or drop. The new sign-in page is served, and a sign-in with a made-up
  account answered 401 `authentication_rejected`, was counted under the route
  `/auth/login`, and left the request record and the sign-in line under one
  request ID with the username in no log index. Without a session the RUM
  endpoint answers 401, so it is on.

- A signed-in browser on the same stack (the user signed in): one batch of 22
  events accepted with 202 and none dropped; page views counted by section,
  including the sign-in page; load timings; seven Product API routes as the
  browser saw them, by route template; 29 seconds connected to the live stream.
  No local username or display name is in any log index.

- Sign-in outcome events, after restarting the stack's Keep processes on
  2026-11-16: keep-api lint, format check and unit suite pass (382, with 20 new
  sign-in event tests beside main's 39 sign-in tests). Two independent review
  rounds found that a sign-in failing after the bind left no event (four tests
  reproduced it) and that a real database error was then counted as
  `internal_error`; both are fixed and tested. Known limits: a reason is any
  lower-case code a raise site passes, not a fixed list, and a sign-in that
  completes in the instant the request's time ends can be counted as
  `request_timeout` although the person was signed in. A sign-in with a made-up
  account answered 401 and left `sign_in_rejected` (info, reason
  `invalid_credentials`, no user) and its request record under one request ID
  in `keep-logs-api`; `keep_events_total` for that event, outcome and reason
  went from absent to 1 on the API's metrics port and in Prometheus; the typed
  username and password are in no log index.

- A successful sign-in on the same stack (the user signed out and in on
  2026-11-16): one `sign_in_completed` (info, success, 0.5 s) with the user ID,
  and the request record `POST /auth/login answered 200` with the same user ID
  under the same request ID; the counter and the duration histogram for the
  event each went to 1. None of the 20 usernames and display names of the local
  directory is in any log index.

Not re-checked after the move: a browser log record (no error occurred to write
one), and the dashboard's browser row by eye. Not provoked on the stack: a
sign-in failing after the bind (database down, restore gate closed, the
request's time ending); those are unit-tested with doubles.

Checked on 2026-11-12:

- Unit suites: keep-domain 916, keep-ingestion 108, keep-processor 55,
  keep-correlation 8, keep-indexer 96, all passing, with unregistered events and
  fields failing the tests in every one of them.
- Real PostgreSQL 18.2 (a throwaway container, no broker):
  `tests/integration/test_alert_flow_logs.py` sends one alert through ingestion's
  admission code, the source processor and correlation with a real rule and
  asserts the step records against what PostgreSQL holds. The existing pipeline,
  maintenance, correlation-runtime and source-processor integration suites pass
  beside it (35 passed, 5 skipped for lack of Kafka and other bindings).
- Real Fluent Bit 5.1.2: its HTTP input accepts the shipping handler's JSON-array
  batches, splits them into individual records with the application's `@timestamp`
  and nested fields intact, and routes by URL path to separate outputs.

- The local stack on 2026-11-12 (real Kafka 4.0.0, PostgreSQL 18.2,
  Elasticsearch 9.0.0, Fluent Bit 5.1.2; ingestion and the three workers running
  this code): a new v1 alert, a repeat and a clear each left eleven steps in
  order across `keep-logs-ingestion`, `-processor`, `-correlation` and `-indexer`,
  found by application, delivery ID, changed-event ID and alert ID. An alert with
  a missing and an unknown field left `alert_rejected` naming both, linked to its
  request record by the request ID. Fluent Bit wrote the application's
  `@timestamp` unchanged with its own receive time beside it as `shipped_at`, and
  reported every record written with no error, retry or drop. With Fluent Bit
  stopped, ingestion still answered 202 in under 10 ms; once it was back all
  eleven records of that alert arrived with their original `@timestamp`.

- Metrics on the same stack: all six Prometheus targets up; `/metrics` answers on
  each process's own port and 404 on the application ports; after sending a
  known mix of alerts (new, repeated, cleared, rejected) twice, eleven counters
  each moved by exactly the number sent; the stage timings and the 2-second and
  5-second ratios had values; lag was reported for the source processor,
  correlation and the indexer; the target-down alert fired while the processes
  were stopped and cleared after they started; the dashboard rendered. The rule
  file passes `promtool check rules` and its unit tests.

- RUM on the same stack: signing in, moving between views and raising one
  deliberate error in the browser produced two accepted batches; page views by
  section, first and largest contentful paint and time to first byte, the
  browser's own count of API calls under the application's route templates,
  live-connection time, and the error in `keep-logs-browser` with its class and
  the user and without its text.

- Kafka interruption: with the broker paused for 75 seconds and resumed, the
  source processor with statistics on its consumer survived, stayed ready and
  kept reporting lag.

Found by the new target-down alert and fixed: the source processor exited
silently after a Kafka session timeout. Python's fault handler showed an access
violation inside the Kafka client's poll. The statistics callback had been put
in the options every Kafka client shares, so it also reached the transactional
producers the source processor creates and drops inside its consumer's rebalance
callback. Without statistics the same pause did not crash it. Statistics are now
set only on the long-lived, regularly polled clients: the three pipeline
consumers, the ingestion publisher and the Product API notifier.

Found and fixed in that run: `alert_received` carried no delivery ID; a rejection
for missing or unknown fields did not name them; Fluent Bit drops a connection
idle for 10 seconds, which cost the next post one failed attempt.

- The same stack after the API change: assigning and then resolving an alert in
  the UI as the local editor left, for each action, `alert_changed` with the
  cause and the user, `alert_notification_published`, the request record, and the
  correlation (`not_source`) and indexer steps under the new changed-event ID:
  25 records for the alert from arrival to resolution, in order.

Not yet checked: an incident created by correlation on the running stack (only on
the real-PostgreSQL test), Fluent Bit's buffering while Elasticsearch is down, a
logs cluster separate from the alert-search cluster, TLS or credentials to the
logs cluster, and the cost of the step records at load.

D17 (the Kafka request-ID header) has no answer from the user and is not built.

Decision labels: *user decision*, *verified observation*, *verified constraint*
(already fixed by the design), *assistant choice* (my default, open until you
accept it) and *open proposal* (needs your answer).

## 1. Goal

**User decisions (2026-11-12):**

- U1. Full logs and metrics on every system service. Logs say what happened in
  each service; metrics give the numbers at each moment. Metrics cover service
  monitoring and real user monitoring (RUM). Every flow and failure path is
  monitored.
- U2. Metrics are exposed on a `/metrics` path and collected by Prometheus or an
  OpenTelemetry collector.
- U3. Logs are sent to Elasticsearch (run by ECK) through Fluent Bit.
- U4. One Fluent Bit deployment; every service sends its logs to it.
- U5. One index per service.
- U6. Logs must be indicative and all relevant: each one says something useful,
  and there is no noise.
- U7. Logs must be easy to reach in Elasticsearch. All logs of one specific
  alert must be easy to query, and the alert itself is in the logs, so they can
  also be found by application, component and the alert's other fields.
- U8. No retention or other Elasticsearch configuration in this plan; logs are
  just sent. The logs Elasticsearch and the alert-search Elasticsearch are each
  configurable and may be the same cluster or different ones.
- U9. RUM goes through the Product API.
- U10. No traces for now.

This plan also closes most of reliability specification section 20, which
already requires these signals and has no implementation, and supplies the
measurements delivery gate DEL-08 needs. One clause of that section changes by
U7; see D9.

Done means:

1. Every process writes one structured log record per event; records land in
   that service's own Elasticsearch index; a request, a delivery, an alert or a
   user action can be followed across services by one field, and an alert can
   be found by its own fields.
2. Every process exposes current numbers on `/metrics`, and the browser reports
   what users experience.
3. Every outcome and failure branch in section 5 produces a log event and moves
   a metric, proven by a test that triggers the branch.
4. No log record carries a credential or a user's name, and no metric label
   carries alert content or any unbounded value.
5. The service level indicators (SLIs) of reliability sections 2–5 and the
   alerts of section 21 are computable from these metrics.

## 2. Starting point

Verified at these revisions, all clean on `main`: api `3492b14`, contracts
`b6940ef`, correlation `5cb0c4e`, delivery `30b56ee`, design `94ba5da`, domain
`87c5ef4`, frontend `fd95d8d`, indexer `c6855ee`, ingestion `24a9bf3`, migrations
`4606530`, processor `87fb449`. Items marked (m) come from the read-only mapping
pass and were spot-checked rather than re-read line by line.

- **No metrics anywhere.** No Prometheus, OpenTelemetry or `/metrics` in any
  repository; no scrape configuration or metrics port in `deploy/openshift`.
- **Logging is unconfigured.** No service sets up Python logging. WARNING and
  above reach stderr as a bare message with no time, level or logger name; INFO
  is dropped. uvicorn access logging is off in both HTTP services. No log
  shipping exists.
- **`request_id` exists but is never logged.** `keep-domain/src/keep/http.py:128`
  creates one per request and returns it in `X-Request-ID` and error bodies.
- **Failures that leave no trace today:**
  - Product API 500s: the catch-all handler (`http.py:111`) logs nothing; every
    database failure collapses into `database_unavailable` with no record.
  - Ingestion publish failures are swallowed (`kafka_admission.py:340`), as are
    delivery-thread errors (`:173`).
  - The Redis hint listener reconnects silently (`keep-api/src/keep/live.py:134`);
    an SSE stream ends silently when its refresh fails (`live.py:263`).
  - Workers log `worker_capability_unavailable` with no error class or code
    (`pipeline_runtime.py:102`); the changed-event consumer records every
    failure as `changed_processing_failed` with the real code lost (m).
  - The Kong policy plugin drops its denial reason (`handler.lua:37`,
    `policy.lua:16-21`).
  - A failed input retries every 5 seconds with no cap and no counter, so a
    stuck partition is visible only as one PostgreSQL fault row (m).
- **Browser:** no web vitals, error reporting, error boundary or
  `unhandledrejection` handler; a render exception blanks the single-page app.
- **An alert's fields are a closed, length-limited set**
  (`keep-contracts/src/keep/ingestion/native.py:11-24`):
  - v1: `application`, `object`, `node_name`, `message`, `operator`, `severity`,
    `time_created`, and optional `key_field`.
  - v2: `application`, `component`, `message`, `severity`, `status`, `site`,
    `impact`, `runbook_url`, and optional `node_name`, `name`, `time_created`.
  - Limits: `message` 8,192 characters, `impact` 4,096, `runbook_url` 2,048,
    `key_field` 1,024, every other field 256. Unknown fields are rejected.
- **Which stages know an alert's ID:** the changed event carries `alert_id`
  (`keep-contracts/src/keep/event_models.py:56`), so the processor, correlation,
  indexer and Product API all have it. Ingestion does not: an input has no
  alert ID until the processor applies it. Ingestion and the processor do share
  the native identity digest, which `identity()` returns (`native.py:207`);
  ingestion discards it today (`kafka_admission.py:303`) and the processor uses
  it to find the alert (`keep-processor/src/keep/kafka_source.py:100`,
  `:132-135`).
- **The alert-search Elasticsearch is already configurable** through
  `KEEP_ELASTICSEARCH_URL`, `_INDEX`, `_API_KEY`, `_CA_FILE` and client
  certificate settings (`keep-domain/src/keep/config.py:37-42`).
- **Existing conventions:** log lines are a fixed event token plus `key=value`
  fields, and exception text is never logged (`kafka_source.py:521-523`).
  Processor and domain tests assert the exact current messages.
- **About 600 error raise sites** (`AppError` / `unavailable(...)`) across 102
  files. They cannot be instrumented one by one; section 4.9 covers them at the
  few points every error passes through.

## 3. Decisions

| # | Decision | Label | Why, and the alternative |
|---|---|---|---|
| D1 | Log records are JSON, using Elastic Common Schema (ECS) field names, written by the Python standard `logging` module. Every record also goes to stderr. | assistant choice | ECS names are what Elasticsearch tooling expects. stderr keeps `oc logs` useful and is the local copy if shipping is down. No log library is added. |
| D2 | Metrics: `prometheus-client`, served at `/metrics` on a dedicated metrics port in every process; pods carry the standard scrape annotations | user decision (path, collectors); assistant choice (separate port) | Prometheus and the OpenTelemetry collector's Prometheus receiver both scrape this. A separate port keeps `/metrics` off the public routes and Kong. |
| D3 | Shared code lives in a new `keep.observability` package in keep-domain | assistant choice | Both HTTP services already share `keep.http`, and the three workers share `pipeline_runtime`. The package imports no service entrypoint. |
| D4 | One Fluent Bit Deployment with an HTTP input. Every service posts its records to it in batches from a background thread; Fluent Bit writes them to Elasticsearch. | user decision (one deployment, services send to it); assistant choice (HTTP input) | Needs no host access and no cluster privilege. Fluent Bit takes the routing tag from the URL path, so each service posts to its own path. Alternative: Fluent's forward protocol, which adds a message-pack dependency to every service. |
| D5 | One index per service, `keep-logs-<service>`; the names are settings | user decision; assistant choice (names) | — |
| D6 | No Elasticsearch configuration: no index templates, retention, roles or saved objects. Elasticsearch creates each index on first write with its default field mapping. | user decision | — |
| D7 | Two independently configured Elasticsearch targets. Alert search keeps its existing `KEEP_ELASTICSEARCH_*` settings. The logs target is Fluent Bit's own setting (host, port, credentials, CA). They may name the same cluster or different ones. | user decision | — |
| D8 | Every log field is declared once in a field registry in code, and every event once in an event catalog | assistant choice | With no index template (D6), the registry is the only thing that keeps one name and one type per field, so Elasticsearch's automatic mapping never meets a conflicting value and never rejects a record. The catalog is how "indicative and relevant" is enforced rather than hoped for. |
| D9 | The alert is in the logs. Alert-level records carry the alert's own fields. | user decision | This changes one clause of reliability 20 ("redacts ... alert bodies from diagnostics") and must be recorded as a later user decision there. Credentials are still never logged. Consequence: anyone who can read the log indices can read every tenant's alerts, outside Keep's tenant permissions (section 11). |
| D10 | Identifying fields (application, component or object, node name, name, site, severity, status) go on every alert-level record. The whole alert goes once per delivery, on the processor's record, and on ingestion's record when a member is rejected or its publication is unconfirmed. | assistant choice | Every record is findable by application or component, and the full content is always in the logs, without repeating up to about 16,000 characters on four records per delivery. Alternative: the whole alert on every record. |
| D11 | Alert lookup: `keep.alert.id` on every record that concerns an alert, and `keep.alert.key_digest` on ingestion and processor records | assistant choice | See 4.3. Ingestion cannot know the alert ID (it must not read PostgreSQL), so the digest bridges the gap. |
| D12 | Every alert-level event is logged, with no sampling | assistant choice, follows from U7 | A sampled timeline would have holes. Cost in 4.5. A rate setting stays available as an emergency valve. |
| D13 | RUM: the browser batches events to a same-origin Product API endpoint, which turns them into metrics and log records | user decision | — |
| D14 | The RUM endpoint requires the session and CSRF token | assistant choice | Matches the rule already set for `view-observations`. The choice first also left the activity clock untouched; REV-73 removed the idle timeout, so there is no clock left to protect. |
| D15 | No tenant, alert, operator, user or source value as a metric label | verified constraint | Reliability 20. Alert fields live in logs only. |
| D16 | Database-derived state gauges are computed inside existing processes at scrape time, cached 15 s | assistant choice | Worker scope (REV-34 to REV-36) fixes the set of deployed components. |
| D17 | Ingestion adds a Kafka record header carrying the HTTP `request_id` | open proposal | Explained in 3.1. |
| D23 | Every catalogued event increments one counter and, where it carries a timing, one histogram, under the event's own name. | assistant choice | One vocabulary for logs and metrics, and coverage that follows the catalog. Supersedes the separately named pipeline metrics of section 5. |
| D22 | The flow of an alert inside each service is in the logs: each step is its own record, numbered within its pass, carrying the pass's identifiers. A step reporting a PostgreSQL effect is written only after the commit. One setting reduces the trail to outcome records. | user decision (the flow inside each service); assistant choice (which steps, numbering, after-commit rule, the setting) | Supersedes D10's "once per delivery" count and D12's emergency sampling. Steps are the points where an alert can stop, branch or wait; statements inside one transaction are not separate steps. Cost in 4.5. |
| D18 | Everything is additive and off by default: no metrics listener without `KEEP_METRICS_PORT`, no shipping without `KEEP_LOG_SHIP_URL`, RUM behind a flag, no Kafka value or schema change | assistant choice | Rollback is unsetting configuration or reverting a pin. |
| D19 | No distributed tracing | user decision | The 1% trace sampling in reliability 20 stays a recorded gap. |
| D20 | The design's exact "visible in the browser within 5 seconds" measurement is not built here; RUM delivers an estimate | open proposal | Explained in 3.1. |
| D21 | Alert rules and dashboards are delivered as files and run in the local stack; installing them in company monitoring stays with the owning teams | verified constraint | Reliability 21 and the operating-team boundary. |

### 3.1 The two open items, explained

**D17, the request ID in a Kafka header.** A sender's only handle on a
submission is the `request_id` in ingestion's response. Ingestion gives each
alert in that request its own internal delivery ID and publishes it to Kafka;
the processor sees the delivery ID but not the request. Without the header, the
processor's record for an alert cannot say which request brought it. Going from
a sender's request ID to what happened to its alerts then takes two searches
(ingestion: request to delivery IDs; processor: delivery IDs), and an alert's
timeline cannot show which request delivered each update. With the header, the
processor record carries the request ID, so one search by request ID shows both
services. The cost is about 50 bytes per Kafka record. Record values, digests
and replay proof are untouched, and consumers that do not read the header are
unaffected. It is a convenience, not a requirement: leaving it out loses
nothing except that one hop.

**D20, the rendered-visibility measurement.** The design sets an objective that
a new alert or status change appears on a connected user's screen within 5
seconds of Keep receiving it, 99% of the time. It also specifies exactly how to
measure that: the server attaches an observation ID to data it sends the
browser; after the browser has actually painted that data it sends an
acknowledgement; the server times it by its own clock and counts a missing
acknowledgement as a miss. The server-side groundwork exists (a registry of
open views and input sampling); issuing the IDs, the acknowledgement endpoint
and the browser half do not ([visibility plan](visibility-observations-implementation-plan.md),
phases 3–4). That is a sizeable piece of work in its own right. This plan
instead reports an estimate from RUM: for alerts newly shown on screen, the
time from Keep's receipt to paint. The estimate only counts alerts that did
appear, so it cannot see one that never showed up, and it is labelled an
estimate everywhere. Building the exact measurement can follow later on top of
the same pipeline.

## 4. How the signals are built

### 4.1 Log records

One JSON object per record, nested by ECS namespace. `KEEP_LOG_FORMAT=text`
makes the stderr copy `event key=value` for a local console. `KEEP_LOG_LEVEL`
defaults to `INFO`.

| Field | Content |
|---|---|
| `@timestamp` | UTC time from the application's clock, millisecond precision |
| `log.level`, `log.logger` | `debug`, `info`, `warning`, `error`; Python logger name |
| `message` | A plain sentence saying what happened, built from the event's catalog entry, for example "Alert input applied: firing to resolved" |
| `event.action` | The fixed snake_case event name; the stable thing to search and count |
| `event.outcome` | `success`, `failure` or `unknown` |
| `event.duration` | Nanoseconds, where the event has a duration |
| `service.name` | `ingestion`, `processor`, `correlation`, `indexer`, `api`, `frontend`, `browser` or `migrations`; decides the index |
| `service.version`, `service.environment`, `service.node.name`, `process.pid` | Build revision, environment, pod name, process ID |
| `error.code`, `error.type`, `error.stack_trace` | The same error code the API returns; exception class; file, line and function frames only |
| `http.request.id`, `http.request.method`, `http.response.status_code` | The request ID, method and status |
| `user.id` | The principal's UUID, never a name |
| `keep.role`, `keep.route` | Workload role; route template |
| `keep.tenant_id`, `keep.incident_id` | Subject identifiers |
| `keep.alert.*` | The alert; see below |
| `keep.delivery_id`, `keep.changed_event_id`, `keep.command_id`, `keep.operation_id` | Work identifiers |
| `keep.operator_id`, `keep.rule_id`, `keep.window_id`, `keep.preset_id`, `keep.dashboard_id` | Other resources |
| `keep.kafka.topic`, `keep.kafka.partition`, `keep.kafka.offset`, `keep.projection_seq` | Positions |
| `keep.outcome`, `keep.reason`, `keep.cause`, `keep.status_before`, `keep.status_after` | What exactly happened, in the domain's own terms |
| `keep.count`, `keep.attempt`, `keep.audit` | Batch size, attempt number, audit marker |

**The alert object, `keep.alert.*`:**

| Field | On which records |
|---|---|
| `id` | Every record that concerns an alert, from the processor onward |
| `key_digest` | Ingestion and processor records |
| `application`, `component` (v2), `object` (v1), `node_name`, `name`, `site`, `severity`, `status`, `native_version`, `provider` | Every alert-level record, in every service |
| `message`, `impact`, `runbook_url`, `operator`, `time_created`, `key_field` | The whole-alert records: the processor's record for each input, and ingestion's record for a rejected or unconfirmed member |

The field names are the alert's own native names, so what you type in a query
is what you see in the UI and the API. Only these registered names are ever
used: a field name supplied by a sender is never turned into a log field, so a
malformed submission cannot create new fields in the index. A member rejected
for an unknown or missing field is logged with the fields it did have under
their known names, plus the names of the offending fields in `keep.reason`.
The exact original JSON is not repeated; the parsed fields carry the same
content.

A helper `log_event(logger, event, **fields)` looks the event up in the catalog,
checks its fields, and emits the record. The Python-level message stays in
today's `token key=value` form, so the tests that assert current messages keep
passing. Request context is held in a context variable, the pattern
`request_budget.py` already uses, so every record written while handling a
request carries its `http.request.id`, tenant and principal. Route parameters
named `tenant_id`, `alert_id`, `incident_id` and so on are bound automatically,
and a handler that loads an alert binds its identifying fields the same way.

**Never logged:** request or response bodies other than the alert fields above,
credentials, tokens, cookies, session IDs, CSRF tokens, API keys, database or
service URLs, query strings, CEL and search text, user names, display names,
email addresses, and exception message text. Exception text stays excluded
because it can carry connection details and SQL as well as values.

### 4.2 Rules that keep logs indicative and relevant

1. **Every record answers four questions:** what happened (`event.action`,
   `message`), to what (the subject identifiers and alert fields), with what
   result (`event.outcome`, `keep.outcome`), and why if it failed (`error.code`,
   `keep.reason`). The catalog entry of each event lists its required fields and
   a test fails if one is missing.
2. **Every event is registered.** The catalog entry holds the level, the message
   template, when it fires and what to do about it. Emitting an unregistered
   event fails the tests. The operations runbook's event reference is generated
   from the catalog, so it cannot drift.
3. **One record per fact.** A conflict is logged once, by the command that hit
   it, not again by the layers beneath.
4. **Levels mean something.** `error`: a person needs to look (fenced partition,
   possible data loss, unhandled exception, broken invariant). `warning`:
   degraded or failed but recovering on its own, and every 5xx. `info`: a
   business or lifecycle fact. `debug`: off in production.
5. **No repetition.** A condition that persists is logged when it starts, then
   at most once a minute with a repeat count, and when it ends. No record per
   poll, heartbeat, probe or metrics scrape.
6. **Automatic refreshes do not flood.** The UI polls some routes every one or
   two seconds. A successful repeat of the same route by the same session within
   a minute is counted and written as one summary record; failures and slow
   requests are always written individually. Metrics still count every request.
7. **Library output is tamed.** uvicorn, the HTTP, Elasticsearch, Redis and
   Kafka client loggers run at `warning` through the same formatter, and
   librdkafka's own output is routed into it rather than written raw.

### 4.3 Finding logs

Every field has the same name and type in every service index, so one query
over `keep-logs-*` follows a thing across services. In Kibana query language:

| To find | Query |
|---|---|
| Everything about one alert | `keep.alert.id : "<alert UUID>"` |
| Alerts by their own fields | `keep.alert.application : "billing" and keep.alert.component : "database"` |
| The same, narrowed | add `keep.alert.severity : "critical"`, `keep.alert.site : "..."`, `keep.alert.node_name : "..."`, `keep.alert.name : "..."` or `keep.alert.status : "firing"` |
| Text inside alert messages | `keep.alert.message : "disk full"` (whole-alert records) |
| The inputs received for one alert, including ones never applied | `keep.alert.key_digest : "<digest>"` |
| One HTTP request | `http.request.id : "<ID>"` |
| One delivered input | `keep.delivery_id : "<ID>"` |
| One incident | `keep.incident_id : "<ID>"` |
| One user command | `keep.command_id : "<ID>"` |
| One operator provisioning operation | `keep.operation_id : "<ID>"` |
| What one user did | `user.id : "<principal UUID>"` |
| One tenant's failures | `keep.tenant_id : "<ID>" and event.outcome : "failure"` |
| A Kafka position | `keep.kafka.partition : 3 and keep.kafka.offset : 1234` |

**The alert's flow (D22).** Each service writes one record per step. Sorting by
`@timestamp` and then `event.sequence` gives the order.

| Service | Steps of one alert, in order | Found by |
|---|---|---|
| ingestion | `alert_received`; `alert_validated` or `alert_rejected` (code, the fields that were wrong, the alert as sent); `alert_publish_failed` per unconfirmed attempt; `alert_published` (partition, offset, attempt) or `alert_unconfirmed` (whole alert) | `keep.delivery_id`, `keep.alert.key_digest`, `http.request.id`, the alert's fields |
| processor | `alert_consumed` (delay since receipt); `alert_matched` (existing or new alert) or `alert_replayed`; `source_receive_time_ahead` when it waits; `alert_applied` (created, updated, unchanged, resolved or reopened; status before and after; revision; the whole alert) or `alert_refused` (code); `alert_change_published` or `alert_input_committed`; `source_partition_retry` or `source_partition_fenced` on failure | `keep.delivery_id`, `keep.alert.key_digest`, `keep.alert.id` from the lookup onward |
| correlation | `alert_change_consumed`; `alert_incident_attached` per attached alert (incident, rule); `alert_correlated` (incident created, attached, no incident) or `alert_correlation_skipped` (duplicate, not from a source, unchanged, retired); `changed_partition_retry` on failure | `keep.alert.id`, `keep.changed_event_id`, `keep.delivery_id` |
| indexer | `alert_change_consumed`; `alert_indexed` (document version, delay since the change) or `alert_index_deferred`; `changed_partition_retry` on failure | `keep.alert.id`, `keep.changed_event_id` |
| api | `alert_changed` for each alert a request changed (set_status, set_assignee, set_dismissal, a note, an incident link, unlink or move), with the user, the status before and after and the revision; `alert_notification_published`, or `_unconfirmed` or `_failed`; then the request's `http_request` record, which alone records a refused command with its error code. Reads of the alert are `http_request` records, summarised when repeated. | `keep.alert.id`, `http.request.id`, `user.id` |
| browser (not built yet) | Errors raised while the alert's detail view was open | `keep.alert.id` |

One arrival, end to end: `keep.delivery_id : "<ID>"` returns its ingestion,
processor and correlation steps; the processor's `alert_applied` record names the
`keep.changed_event_id` that returns the indexer's. Everything that ever happened
to an alert after ingestion: `keep.alert.id : "<ID>"`.

**Ingestion** handles an input before an alert ID exists. Its records carry the
identifying fields and `keep.alert.key_digest`, so a search by application and
component finds them directly, and the processor writes the same digest next to
`keep.alert.id`. Searching by the digest adds what only ingestion saw: members
rejected at validation and publications left unconfirmed.

The runbook documents these queries. Saved searches and dashboards in Kibana
are Elasticsearch-side setup and are not part of this plan (D6).

### 4.4 Shipping

**Path:** the service's log handler, to the Fluent Bit deployment over HTTP, to
that service's index. A copy of every record always goes to stderr.

**In each service** (one implementation in `keep.observability`, plus a small
equivalent in the frontend server):

- `KEEP_LOG_SHIP_URL` names the Fluent Bit service. Unset means stderr only.
- Records queue in memory, bounded, and a background thread posts them in
  batches to the path for their service, for example `/keep.processor`. The
  Product API posts browser records to `/keep.browser`.
- Shipping never blocks or fails the work being logged. If Fluent Bit is
  unreachable the thread retries with backoff; when the queue is full the
  oldest records are dropped from shipping and counted. This matters most for
  ingestion, whose admission path must not depend on anything but Kafka.
- The queue is flushed on shutdown and on unhandled-error exit.
- The handler does not log its own traffic, and uses the Python standard
  library so no dependency is added.
- Numbers: `keep_log_ship_records_total{outcome}`, `keep_log_ship_queue_records`,
  `keep_log_ship_request_seconds`, `keep_log_ship_failures_total`.

**The Fluent Bit deployment** (option names confirmed against its current
documentation):

- One Deployment and Service in the Keep namespace, with a network policy
  admitting only Keep workloads. Its HTTP input offers TLS and token validation
  but no simpler authentication, so the network policy is the access control.
- HTTP input: the tag is the request's URL path, which is how records are
  routed without inspecting them.
- One Elasticsearch output per service, matched by tag: a fixed `index`
  (`keep-logs-<service>`, each name a setting), `suppress_type_name on`
  (required from Elasticsearch 8), `generate_id on` so a retried batch cannot
  duplicate records, and the connection settings of D7. Fluent Bit's own
  receive time is written under a different key (`time_key`), so it cannot
  collide with the application's `@timestamp`, which stays the event time; the
  difference between the two shows shipping delay. Separate outputs keep a
  flood from one service from holding up another's logs.
- Buffers on local disk with a size limit and bounded retries, so a short
  Elasticsearch outage loses nothing.
- Exposes its own metrics for scraping.

**Logs Elasticsearch settings**, held by Fluent Bit only: host, port, TLS and
CA, and an API key or user and password. Pointing them at the alert-search
cluster or at another one is a configuration choice (D7); index names never
overlap with the alert-search index.

**What this model does not ship:** only records written through the logger
reach Elasticsearch. Raw output that bypasses it (a crash before logging is set
up, a process killed outright before its queue drains) remains in the container
log only. Confirmed against Fluent Bit 5.1.2: its HTTP input accepts a JSON array
per request and splits it into records, and with `time_key` set to another name
its Elasticsearch output leaves the record's own `@timestamp` as it is.

### 4.5 Volume

Estimate, to be measured in the load test. An accepted delivery that changes an
alert leaves about eleven step records: three in ingestion, four in the source
processor, two in correlation (one more per attached alert) and two in the
indexer. A step record is roughly 0.6 to 1.1 KiB (measured on ingestion's: 607,
804 and 886 bytes for a short v1 alert). One record, `alert_applied`, also
carries the whole alert, typically 1 to 4 KiB.

| Rate | Deliveries a day | Records a day | Raw JSON a day |
|---|---|---|---|
| Normal, 4,000 a minute | 5.76 million | about 63 million | roughly 55–85 GB |
| Sustained peak, 15,000 a minute | 21.6 million | about 240 million | roughly 210–320 GB |
| Normal, outcome records only | 5.76 million | about 29 million | roughly 30–50 GB |

This is about two and a half times the earlier one-record-per-service estimate.
The source processor now writes four records per input on its apply path; their
cost at 250 inputs a second is to be measured before the load test's result is
accepted.

User-driven records (requests, commands, sign-ins, RUM) are small beside this.
`KEEP_LOG_ALERT_STEPS=outcome` reduces the trail to each service's outcome records;
failures, refusals and rejections are always written.

### 4.6 Metrics

- Names start with `keep_`, use seconds and bytes, and counters end in `_total`.
- Labels are closed sets defined in code: `service`, `role`, `route` (the route
  template), `method`, `status`, `outcome`, `error_code`, `consumer`,
  `partition`, `dependency`, `operation` and similar. A test fails the build if
  a metric registers a label outside the allowed list (D15).
- Histogram buckets include each objective's threshold exactly (1 s, 2 s, 5 s).
- Each Python process serves `GET /metrics` on `KEEP_METRICS_PORT`. Unset means
  no listener. The public application port answers 404 for `/metrics`.
- Pods carry `prometheus.io/scrape`, `prometheus.io/port` and
  `prometheus.io/path` annotations, which both Prometheus and an OpenTelemetry
  collector's Prometheus receiver can discover. A ServiceMonitor is an optional
  extra template.
- State that lives in PostgreSQL (backlogs, watermarks, blocked rules) is read
  by bounded aggregate queries when scraped, cached 15 s, with a 1 s statement
  limit. A failed read sets `keep_state_collector_up` to 0 rather than failing
  the scrape. Grants verified so far: the API role already reads
  `kafka_consumer_positions`, `kafka_pipeline_faults` and `search_reconciliation`
  (migration 0044) and the hint outbox (0020, 0047). Any aggregate a role cannot
  read gets one forward migration adding a function that returns counts only.
- Kafka client numbers come from librdkafka's statistics callback, which
  reports lag, offsets, queue depth and broker state without extra broker calls.
  Only a client that lives as long as its process and is polled regularly takes
  the callback; a client made or dropped inside another client's callback must
  not (it crashes the Kafka library), and a test keeps it out of the shared
  client options.
  On the changed topic a caught-up consumer shows a lag of 1: the last entry is
  the transaction marker, which is never delivered as a message.
- Every catalogued log event is also a counter and, where it has a timing, a
  histogram, under the event's own name (D23): the event catalog is the list of
  what is counted, so a logged flow or failure cannot be missing from the
  metrics.
- "At every moment" means the scrape interval: 15 s proposed, 5 s locally.

### 4.7 RUM pipeline

1. A small collector in the frontend records events (section 5.11) into a
   bounded in-memory buffer backed by `sessionStorage`, so events from before
   sign-in or during an API outage are sent later.
2. It flushes every 30 seconds, when the tab is hidden and on page hide, using
   `fetch` with `keepalive` (a beacon cannot carry the CSRF header). A batch is
   at most 100 events and 64 KiB, the same bounds as `view-observations`.
3. High-frequency timings (API calls, the one-second polling loops) are
   aggregated in the browser into fixed buckets; only failures and rare events
   are sent individually.
4. `POST /api/rum-events` on the Product API validates strictly, rate-limits per
   session, converts events to metrics, and writes error events as log records
   with `service.name: browser` and the authenticated `user.id`. Invalid or
   excess batches are counted and dropped. The endpoint is exempt from the clock
   gate that guards other mutating requests and is excluded from the API's own
   SLIs. The role route gate in `workload.py` is extended to admit this path.
5. Events carry route templates and resource IDs, never URLs, query text or
   content. Error messages are truncated and stacks keep same-origin frames
   only.

### 4.8 Configuration summary

| Setting | Where | Meaning |
|---|---|---|
| `KEEP_LOG_LEVEL`, `KEEP_LOG_FORMAT` | every service | Level; `json` or `text` for the stderr copy |
| `KEEP_LOG_SHIP_URL` | every service | The Fluent Bit service; unset disables shipping |
| `KEEP_LOG_ALERT_STEPS` | pipeline services | `all` (default) writes every step of an alert's flow; `outcome` writes only each service's outcome records and every failure |
| `KEEP_METRICS_PORT` | every service | Port serving `/metrics`; unset disables it |
| `KEEP_RUM_ENABLED` | Product API | Turns the RUM endpoint and browser collector on |
| `KEEP_ELASTICSEARCH_*` | Product API, indexer | The alert-search cluster (existing settings, unchanged) |
| Logs Elasticsearch host, port, TLS, credentials; index name per service | Fluent Bit | The logs cluster and its indices |

### 4.9 Coverage by construction

Listing branches by hand will miss some, so four mechanisms guarantee coverage:

1. **HTTP:** every error response is built by `error_response` in `http.py`.
   Instrumenting that one function and the `protocol` middleware records every
   current and future error code, with its route and request.
2. **Workers:** every turn failure passes through `pipeline_runtime.run`, and
   every record failure through `_retain` (processor) or `_turn_head` (changed
   consumer). Instrumenting those records class, code and position.
3. **Nothing fails silently:** process-level hooks (`sys.excepthook`,
   `threading.excepthook`, the asyncio exception handler, uvicorn's error
   logger) log and count anything unhandled. The places that swallow an
   exception today (about fifteen found by search and mapping, in ingestion
   publishing, the live listener and streams, logout, directory search, the
   proof helper, correlation evidence, search cleanup and worker shutdown) each
   get a counter and a log record, and a lint rule rejects a new swallow
   without one.
4. **Tests enumerate:** one test asserts every registered route has an SLI
   class; one table-driven test per service triggers each branch in section 5
   and asserts its log event and metric.

## 5. Catalog: flows, failures and their signals

Each table row is an outcome. "Log" is the event name and level (D debug, I
info, W warning, E error); existing event names are kept. "Metric" names omit
the `keep_` prefix. Every record about an alert carries the alert fields of
4.1; "whole alert" marks the records that carry all of them.

### 5.1 Every Python process

| What | Log | Metric |
|---|---|---|
| Start, configuration summary (names only, no values) | `process_started` I | `build_info{service,role,version}`, `process_start_time_seconds` |
| Shutdown signal, drain, exit | `process_stopping` I, `process_stopped` I | `process_draining` |
| Unhandled exception in any thread or task | `process_unhandled_error` E | `process_unhandled_errors_total{origin}` |
| Log volume; shipping sent, failed, dropped; queue depth | — | `log_records_total{level}`, `log_ship_records_total{outcome}`, `log_ship_queue_records`, `log_ship_failures_total` |
| CPU, memory, file descriptors, GC | — | standard `process_*`, `python_gc_*` |
| Clock evidence trusted / untrusted / stale / missing | `clock_untrusted` W (on change) | `clock_health_checks_total{outcome}`, `clock_offset_seconds{source}`, `clock_evidence_age_seconds{source}` |
| Database pool: size, in use, waiting | — | `db_pool_connections{state}`, `db_pool_waiting_requests` |
| Pool acquire, including timeout | `db_pool_timeout` W | `db_pool_acquire_seconds`, `db_pool_acquire_timeouts_total` |
| Connection opened / failed / lost | `db_connection_failed` W | `db_connections_opened_total`, `db_connection_errors_total`, `db_connections_lost_total` |
| Transaction committed / rolled back / failed | `db_transaction_slow` W (over 1 s) | `db_transactions_total{outcome}`, `db_transaction_seconds` |
| Database error by kind: statement timeout, lock timeout, deadlock, serialization, unique violation, connection, other | `db_error` W with SQLSTATE | `db_errors_total{kind}` |
| Restore guard rejects work | `restore_guard_rejected` W | `restore_guard_rejections_total{code}` |
| Request budget expires at pool, statement, commit or handler | in the request's own record | `request_budget_expired_total{stage}` |
| Outbound call to a dependency (Elasticsearch, Redis, Kafka, LDAP, ADAPI, Kong admin, Grafana, ticket service, recovery evidence store) | `dependency_call_failed` W | `dependency_requests_total{dependency,operation,outcome}`, `dependency_request_seconds{dependency,operation}` |
| Kafka client error callback, broker up/down | `kafka_client_error` W | `kafka_client_errors_total{client,code}`, `kafka_brokers_up{client}` |
| Best-effort changed notification after a manual commit: confirmed / unconfirmed / failed | `alert_notification_unconfirmed` W, `alert_notification_failed` W | `best_effort_notifications_total{outcome}` |
| CEL compile ok / invalid / deadline / unavailable; cache hit, miss, eviction | `cel_compile_unavailable` W, `cel_cache_evicting` W | `cel_compilations_total{outcome}`, `cel_compile_seconds`, `cel_cache_lookups_total{cache,outcome}`, `cel_cache_evictions_total{cache}` |

### 5.2 Shared HTTP layer (Product API and ingestion)

| Flow or failure | Log | Metric |
|---|---|---|
| Every request, at completion: route template, method, status, error code, duration, body size (automatic refreshes summarised, rule 6 of 4.2) | `http_request` I (W for 5xx) | `http_requests_total{service,route,method,status}`, `http_request_seconds{service,route,method}`, `http_request_body_bytes{service,route}`, `http_requests_in_flight{service}` |
| Any error envelope, by code | fields on `http_request` | `http_errors_total{service,route,code}` |
| Handler budget exceeded (`request_timeout`, `ingress_timeout`) | `http_request` W | `http_errors_total{code}`, `request_budget_expired_total{stage="handler"}` |
| Unhandled exception becomes 500 `internal_error` | `http_unhandled_error` E with class and stack | `http_unhandled_errors_total{service,route}` |
| Client disconnects before the response | `http_client_closed` I | `http_client_disconnects_total{service,route}` |
| Rejected before routing by the bounded HTTP parser: oversized head, bad Host, bad Transfer-Encoding, upgrade, stalled body | `http_protocol_rejected` W | `http_protocol_rejections_total{service,reason}` |
| Role route gate returns 404 | `http_request` | `http_errors_total{code="not_found",route="unmatched"}` |
| Clock gate returns 503 `clock_untrusted` | `http_request` W | `http_errors_total{code="clock_untrusted"}` |
| Duplicate parameter or header, encoding, media type, body too large, invalid JSON, duplicate key, unknown query parameter, non-canonical ID, field validation | `http_request` I | `http_errors_total{code}` |
| Liveness and readiness probes | `readiness_failed` W (on change only) | `readiness_checks_total{service,outcome,code}`, `ready{service}` |
| SLI classification of each request | — | `sli_requests_total{sli,outcome,reason}`, `sli_latency_seconds{sli}` |

`sli` is one of `ingest_v1`, `ingest_v2`, `ingest_grafana`, `read`, `command`,
`search`, `operator_command`, `validate`, `auth`, `live`, `rum`, `admin`,
`health`. `outcome` follows reliability section 3: `good`; `bad` for 5xx,
capacity 429/503, timeouts and disconnects after full receipt; `excluded` for
malformed input, invalid credentials, permission denials, business conflicts,
probes and identified load tests, which are still counted and reported. The
`validate` class for dry-run endpoints (CEL, preset, rule and maintenance
validation and tests) is an assistant choice: they are POSTs that commit
nothing, and the design sets no objective for them.

### 5.3 Ingestion API (v1, v2, Grafana) and the Kong policy plugin

| Flow or failure | Log | Metric |
|---|---|---|
| Request summary: API version, members, accepted / rejected / unconfirmed, status 202 / 207 / 422 / 503 | `ingest_request` I | `ingest_batch_members{api}`, `ingest_members_total{api,disposition}` |
| Time from complete body to response (the 1-second objective) | on `ingest_request` | `ingest_acceptance_seconds{api}` |
| Admission disabled or unconfigured (`kafka_cutover_required`, `kafka_admission_unconfigured`) | `http_request` W | `http_errors_total{code}`, `ingest_admission_enabled` |
| Gateway context rejected: peer unverified, header missing or malformed, leftover credentials, context malformed, authorization time out of range, routing incomplete | `ingest_gateway_rejected` W with reason | `ingest_gateway_rejections_total{reason}` |
| Age of the gateway authorization when used (10-second lifetime) | — | `ingest_gateway_authorization_age_seconds` |
| Envelope rejected: too large, invalid envelope, batch size, invalid retry ID | `http_request` I | `http_errors_total{code}` |
| Member read from the request | `alert_received` I with index and count (step) | — |
| Member valid: delivery ID and key digest assigned | `alert_validated` I (step) | `ingest_time_defaulted_total{api}` |
| Member rejected by native or Grafana validation, by code | `alert_rejected` I with index, code, the fields that were wrong and the alert as submitted (step) | `ingest_member_rejections_total{api,code}` |
| Member accepted after broker acknowledgement | `alert_published` I with delivery ID, key digest, partition, offset and attempt (step) | `ingest_members_total{disposition="accepted"}`, `ingest_member_bytes{api}` |
| Publish attempt: confirmed, timeout, delivery error, local queue full, publisher closed, deadline exhausted | `alert_publish_failed` W with attempt and reason (step) | `ingest_publish_attempts_total{outcome}`, `ingest_publish_attempt_seconds`, `ingest_publish_attempts_per_member` |
| Member unconfirmed after three attempts (`kafka_publish_unconfirmed`) | `alert_unconfirmed` W with delivery ID, key digest, reason and the whole alert (step) | `ingest_members_total{disposition="publish_failed"}` |
| Delivery-report thread error | `ingest_delivery_poll_failed` W | `ingest_delivery_poll_errors_total` |
| Producer queue depth, pending acknowledgements, broker latency | — | `kafka_producer_queue_messages`, `ingest_publish_pending`, `kafka_client_request_latency_seconds` |
| `time_created` defaulted to the receive time | — | `ingest_time_defaulted_total{api}` |
| Readiness: Kafka topic missing or errored | `readiness_failed` W | `readiness_checks_total{code="kafka_admission_unavailable"}` |
| Shutdown flush leaves unconfirmed records | `ingest_shutdown_unconfirmed` W with count | `ingest_shutdown_unconfirmed_total` |
| Kong plugin: no credential, credential inactive, policy unavailable, policy invalid, clock untrusted, proof mode outcomes | `keep_policy_denied reason=...` in Kong's log | Kong's own per-route status metrics (section 5.12) |

### 5.4 Source processor

| Flow or failure | Log | Metric |
|---|---|---|
| Batch per partition: first and last offset, counts by outcome, apply / Kafka transaction / confirm durations | `source_batch` I | `source_batch_records`, `source_batch_seconds{stage}` |
| Input read and its payload valid | `alert_consumed` I with delay since receipt (step) | `source_receive_lead_seconds` |
| Identity lookup: existing alert or new | `alert_matched` I (step) | — |
| Record applied, after the PostgreSQL commit | `alert_applied` I with the whole alert, alert ID, key digest, delivery ID, changed-event ID, outcome, status before and after, revision (step) | `source_records_total{outcome="applied"}`, `source_apply_seconds` |
| Kafka transaction committed for the input | `alert_change_published` I, or `alert_input_committed` I when there was no change to publish (step) | `source_changed_events_total` |
| Receipt to PostgreSQL commit (the load test's "processing p99") | on the record | `source_receive_to_applied_seconds` |
| Terminal rejection after 202: `identity_conflict`, `operator_deleted`, `retired_receipt`, other business codes | `alert_refused` W with code and the whole alert (step) | `source_records_total{outcome="rejected"}`, `source_rejections_total{error_code}` |
| Replay at the same coordinate returns the stored result | `alert_replayed` I with outcome `replayed` (step) | `source_records_total{outcome="replayed"}` |
| Same delivery at another coordinate (duplicate) | `alert_replayed` I with outcome `duplicate` (step) | `source_records_total{outcome="duplicate"}` |
| Receipt ahead of the tenant clock, within 1 s | `source_receive_time_ahead` I/W (existing) | `source_receive_lead_seconds`, `source_records_total{outcome="waiting_clock"}` |
| Retained for retry, by reason (proof invalid, topic or scope mismatch, maintenance proof unavailable, database error, restore guard, clock, anything else) | `source_partition_retry` W (existing) | `source_retries_total{reason}`, `source_faults_total{reason}` |
| Fenced until repair (`source_coordinate_conflict`) | `source_partition_fenced` E (existing) | `source_partition_state{partition,state}`, `source_faults_total{reason}` |
| How long a partition has been held at one offset | `source_partition_blocked` W each minute | `source_partition_blocked_seconds{partition}` |
| Kafka transaction committed / failed / uncertain; consumer recreated | `source_kafka_transaction_failed` W with code | `source_kafka_transactions_total{outcome}`, `worker_transport_restarts_total` |
| Changed events published | on `source_batch` | `source_changed_events_total` |
| PostgreSQL confirmation fails after a Kafka commit; repaired at next assignment | `source_confirmation_deferred` W, `source_uncertain_commits_repaired` I | `source_confirm_failures_total`, `source_uncertain_commits_repaired_total` |
| Rebalance: assigned, revoked, lost | `source_partitions_assigned` I, `source_partitions_revoked` I | `kafka_consumer_rebalances_total{consumer,kind}`, `kafka_consumer_assigned_partitions` |
| Producer initialisation per partition (fences the previous owner) | `source_producer_initialised` I | `source_producer_init_seconds` |
| Topic identity unrecorded / changed / unverifiable | `source_topic_identity_unrecorded` E, `source_topic_identity_changed` E (existing) | `source_topic_identity_failures_total{reason}` |
| Partition count differs from the recorded topology | `source_topology_changed` E | `source_topology_mismatch_total` |
| Committed offset below the broker's low watermark (accepted inputs lost to retention) | `source_retention_gap` E with partition and gap size | `source_retention_gaps_total`, `source_retention_gap_messages` |
| Lag, committed offset, low and high watermark per partition | — | `kafka_consumer_lag_messages{consumer,partition}`, `kafka_consumer_committed_offset`, `kafka_partition_low_watermark`, `kafka_partition_high_watermark` |
| Age of the oldest unapplied input | — | `source_oldest_unapplied_age_seconds{partition}` |
| Maintenance preparation fails; inline maintenance effect applied to an alert | `maintenance_prepare_failed` W, `maintenance_effect` I with alert and window | `maintenance_prepare_failures_total{code}`, `maintenance_inline_effects_total{effect}` |
| Consume error from the broker | `worker_capability_unavailable` W with code | `worker_capability_failures_total{role,error_code}` |

### 5.5 Worker lifecycle and the shared changed-event consumer

| Flow or failure | Log | Metric |
|---|---|---|
| Turn: progress, idle or failed, and how long | — | `worker_turns_total{role,outcome}`, `worker_turn_seconds{role}`, `worker_last_turn_timestamp_seconds{role}` |
| Turn fails: class and code now recorded | `worker_capability_unavailable` W (existing, with new fields) | `worker_capability_failures_total{role,error_code}` |
| Transport discarded and recreated | `worker_transport_restarted` W | `worker_transport_restarts_total{role}` |
| Ready / not ready / draining; heartbeat file age | `worker_ready_changed` I | `worker_ready{role}`, `worker_draining{role}`, `worker_heartbeat_age_seconds{role}` |
| Cutover not enabled (`kafka_cutover_required`) | `worker_capability_unavailable` W | as above |
| Changed records handled, single / batch / grouped | `changed_batch` I | `changed_records_total{consumer,outcome}`, `changed_handler_seconds{consumer,mode}`, `changed_batch_records{consumer}` |
| Record fails: the real error code is now kept in the log, metric and fault row | `changed_partition_retry` W (existing, now with offset, alert, changed event, class and code) | `changed_retries_total{consumer,error_code}`, `changed_faults_total{consumer,reason}` |
| Partition retrying / fenced, and for how long | `changed_partition_blocked` W each minute | `changed_partition_state{consumer,partition,state}`, `changed_partition_blocked_seconds{consumer,partition}` |
| Grouped batch fails; partitions fall back to solo handling | `changed_groups_batch_failed` W (existing) | `changed_group_fallbacks_total`, `changed_solo_partitions{consumer}` |
| Invalid progress count from a handler | `changed_batch_progress_invalid` E | `worker_capability_failures_total{error_code}` |
| Retention gap on assignment: correlation fences, indexer skips ahead | `changed_retention_gap` E | `changed_retention_gaps_total{consumer}` |
| Partition topology changed | `changed_topology_changed` E | `worker_capability_failures_total{error_code}` |
| Offset commit: duration, failure on turn / revoke / close | `changed_revoke_commit_failed` W, `changed_close_commit_failed` W (existing) | `kafka_commit_seconds{consumer}`, `kafka_commit_failures_total{consumer,stage}` |
| Lag and offsets per partition | — | same `kafka_consumer_*` gauges as 5.4 |

### 5.6 Correlation

| Flow or failure | Log | Metric |
|---|---|---|
| Change read | `alert_change_consumed` I with cause and delay since the change (step) | `changed_records_total{consumer,outcome}` |
| Change not evaluated: duplicate, not from a source, unchanged, alert retired | `alert_correlation_skipped` I with the reason (step) | `correlation_events_total{outcome}` |
| Decision, after the PostgreSQL commit: each attached alert, then how it ended (incident created, attached, no incident) | `alert_incident_attached` I with alert, incident and rule; `alert_correlated` I with outcome and passes (steps) | `correlation_decisions_total{decision}`, `correlation_incidents_created_total`, `correlation_alerts_attached_total`, `correlation_attachment_size` |
| Change commit to end of the correlation pass (the 2-second objective) | on the record | `correlation_commit_to_pass_seconds` |
| Pass duration and number of follow-up iterations | — | `correlation_pass_seconds`, `correlation_pass_iterations` |
| Capacity refusal: enabled rules, evidence, cost, tenant cost | `correlation_capacity_refused` W with code | `correlation_capacity_rejections_total{code}` |
| Proof failure: envelope proof invalid, transition proof missing or invalid | `correlation_proof_failed` E with code | `correlation_proof_failures_total{code}` |
| Compiled revision changed, maintenance catalog changed, clock moved backwards | `changed_partition_retry` W with code | `changed_retries_total{error_code}` |
| Rule becomes blocked or unblocked; reason | `correlation_rule_blocked` W, `correlation_rule_unblocked` I | `correlation_rules{state}` |
| Tenant consumer health: healthy / blocked / pending | `correlation_tenant_health_changed` W | `correlation_tenants{health}` |
| Tenant key does not match the envelope | `correlation_tenant_key_invalid` E | `changed_retries_total{error_code}` |
| Evidence state falls back after an internal error (two swallow sites in `evidence.py`) | `correlation_evidence_fallback` W | `correlation_evidence_fallbacks_total` |

### 5.7 Search indexer

| Flow or failure | Log | Metric |
|---|---|---|
| Hint batch projected: grouped / batch / single; covered and uncovered counts | `search_hint_batch` I | `search_hint_batches_total{mode,outcome}`, `search_hints_total{outcome}` |
| Change read; its alert confirmed searchable, or not reached by a shared bulk | `alert_change_consumed` I; `alert_indexed` I with document version and delay since the change, or `alert_index_deferred` I (steps) | `search_documents_written_total{source,op}` |
| Hint not covered by authority (`search_hint_authority_missing`) | `changed_partition_retry` W with code | `changed_retries_total{error_code}` |
| Bulk write by source (hint, reconcile, audit, rebuild) and outcome: ok, version conflict, partial, incomplete, failed, not visible, coverage unproven, unavailable, fenced, batch limit, document capacity | `search_bulk_failed` W with code | `search_bulk_requests_total{source,outcome}`, `search_bulk_seconds{source}`, `search_bulk_documents{source}`, `search_bulk_bytes{source}` |
| Receipt to searchable; change commit to searchable, by cause | on the document record | `search_receive_to_visible_seconds`, `search_change_to_visible_seconds{cause}` |
| Writer fenced or writers paused during a rebuild drain | `search_writer_fenced` W | `search_writers_paused`, `search_writer_leases` |
| Search capability gate closed (`search_not_ready`) | `search_reconciliation_degraded_hints_active` W (existing) | `search_gate_open` |
| Kafka unavailable, reconciliation continues | `search_kafka_degraded_reconciliation_active` W (existing; on change and once a minute, not every second) | `worker_transport_restarts_total`, `search_kafka_degraded` |
| Reconciliation page and pass: delta, full, audit; completed, failed, fenced, state changed | `search_pass_completed` I with tenant, boundary and duration | `search_reconcile_pages_total{mode,outcome}`, `search_reconcile_passes_total{mode,outcome}`, `search_reconcile_pass_seconds{mode}` |
| Tenant reconciliation fails, by reason; retry scheduled | `search_tenant_reconciliation_failed` W (existing, with tenant and epoch) | `search_reconcile_failures_total{reason}` |
| Audit repairs a document (a hint was missed) | `search_audit_repaired` I with the alert | `search_reconcile_repairs_total` |
| Watermark advance; how far search is behind | `search_watermark_lagging` W per lagging tenant each minute | `search_watermark_lag_seq{stat}`, `search_tenants{state}`, `search_oldest_verified_age_seconds` |
| Document capacity profile missing | `worker_capability_unavailable` W with code | `worker_capability_failures_total{error_code}` |
| Rebuild: each stage (registered, copying, catching up, draining, validating, switching, complete, cancelled), duration and documents | `search_rebuild_stage` I | `search_rebuild_state{state}`, `search_rebuild_stage_seconds{stage}`, `search_rebuild_documents_total{stage}` |
| Rebuild failure by code: binding mismatch, busy, fenced, index conflict, snapshot gap, validation incomplete, scope / parity / count mismatch, alias ambiguous, dependency | `search_rebuild_failed` E with code | `search_rebuild_failures_total{error_code}` |
| Point-in-time cleanup fails (two swallow sites) | `search_pit_cleanup_failed` D | `search_pit_cleanup_failures_total` |

### 5.8 Product API

**Sign-in and sessions**

| Flow or failure | Log | Metric |
|---|---|---|
| Sign-in (`POST /auth/login`, REV-73): signed in; rejected for a wrong password, a WhoAmI mismatch or a disabled account; not in the directory or ambiguous; refused before the bind (origin, body size, username form, empty password) | Built: `sign_in_completed` I with the user ID (also on the request record), `sign_in_rejected` I with the reason and no user. Never the typed username or the password. A request refused for its origin, body or the restore gate leaves only its request record | Built: `keep_events_total{event,outcome,reason}`, `keep_event_duration_seconds{event}` |
| Sign-in unavailable: LDAP or ADAPI down, sign-in settings missing, too little of the request's time left, or a failure after the bind (database, restore gate, unexpected error, the request's time ending first) | Built: `sign_in_unavailable` W with the reason and the dependency (`ldap`, `adapi` or `postgresql`); a failure after the bind is counted under the error code the person was answered with; `sign_in_configuration_missing` W once for each process, by setting name only | Built: the same two event metrics. Not built: `dependency_requests_total{dependency}` for `ldap` and `adapi`, and an alert rule |
| A person saved or changed at sign-in (new, renamed, enabled or disabled) | Built: `sign_in_publication_fenced` I when a newer publication already held the person. Not built: `auth_person_published` I with the user ID and what changed | `auth_person_publications_total{outcome}` |
| Session check: valid, past its 24-hour deadline, revoked, missing; directory evidence missing | `auth_session_ended` I with reason, `auth_directory_evidence_required` W | `auth_session_checks_total{outcome}`, `auth_active_sessions` |
| Sign-out (local only) | `auth_logout` I | `auth_logouts_total` |
| Denials: unauthenticated, forbidden, CSRF rejected, origin rejected, access changed, tenant not visible | `auth_denied` W with reason | `auth_denials_total{reason}` |
| Directory search: answered by ADAPI, answered from the Redis cache, more than 1,000 rows, ADAPI unavailable | `directory_search_degraded` W | `directory_search_total{outcome}` |
| Save on pick: saved in the request, left to the background as pending, failed | `directory_object_saved` I, `directory_save_failed` W | `directory_saves_total{outcome}`, `directory_verification_requests_pending` |

**Reads, commands, search and counts**

| Flow or failure | Log | Metric |
|---|---|---|
| Reads of alerts, history, incidents, presets, dashboards, maintenance, rules, operators, audit, reports | `http_request` I with the resource IDs from the route | `http_*` and `sli_*` with `sli="read"` |
| Command committed | `command` I with resource, action, command ID, and the alert or incident | `commands_total{resource,action,outcome="committed"}`, `command_seconds{resource}` |
| Idempotent replay; idempotency conflict; stored result expired | `command` I with outcome | `commands_total{outcome}` |
| Expected-revision conflict; domain rejection (409/422); denied | `command` I with the error code | `commands_total{outcome}`, `http_errors_total{code}` |
| Bulk alert command: item count and per-item outcomes | `bulk_command` I listing the alert IDs (split into several records above 200) | `bulk_command_items{action}`, `bulk_command_item_outcomes_total{action,outcome}` |
| Incident membership: link, unlink, move alerts | `command` I with incident and alert IDs | `commands_total{resource="incident_membership"}` |
| Feed, groups, counts, dashboard counts, previews: ok, search not ready, search unavailable, cursor expired, index changed, query deadline, partial result, invalid CEL | `search_query_failed` W with kind and code | `search_queries_total{kind,outcome}`, `search_query_seconds{kind}`, `count_batch_panels` |
| Deletions that need recovery evidence; evidence store unavailable | `deletion` I, `recovery_evidence_unavailable` W | `deletions_total{resource,outcome}`, `dependency_requests_total{dependency="recovery_evidence"}` |
| Image upload: stored, quota exceeded, invalid type, too large; image read 200 / 304 / 404 | `image_upload` I | `image_uploads_total{outcome}`, `image_upload_bytes`, `image_reads_total{outcome}` |
| Privileged actions: tenant created or renamed, permissions changed, source-processing retry, operator and key actions | `audit_event` I with `keep.audit: true` | `audit_events_total{kind}` |

**Live updates (SSE, Redis, hint publisher)**

| Flow or failure | Log | Metric |
|---|---|---|
| Stream opened, denied or unavailable at setup | `live_stream_opened` I | `live_streams_opened_total{outcome}`, `live_streams`, `live_tenants_connected` |
| Stream closed, by reason: client disconnect, authorization expired, 30-minute lifetime, refresh failed (silent today), send timeout, vector too large, shutdown | `live_stream_closed` I (W for failures) with duration and reason | `live_streams_closed_total{reason}`, `live_stream_seconds` |
| Events sent: state, resync, invalidate, heartbeat | — | `live_events_total{type}` |
| Reauthorization during a stream: ok, denied, unavailable | `live_reauthorization_failed` W | `live_reauthorizations_total{outcome}` |
| View-state read: cache hit or database read | — | `live_vector_reads_total{outcome}` |
| Redis listener connected, disconnected, reconnecting (silent today) | `live_redis_disconnected` W, `live_redis_connected` I | `live_redis_listener_connected`, `live_redis_reconnects_total` |
| Redis message: invalidated a tenant, no listeners, invalid channel | — | `live_redis_messages_total{outcome}` |
| Hint publisher turn: published, unavailable | `live_hint_publication_unavailable` W (existing, with class) | `live_hint_publish_turns_total{outcome}`, `live_hints_published_total` |
| Outbox depth and age; publish delay; delete deferred | `live_hint_delete_deferred` I (existing) | `live_hint_outbox_rows`, `live_hint_outbox_oldest_age_seconds`, `live_hint_publish_delay_seconds`, `live_hint_delete_deferred_total` |

**Operators (provisioning inside the request)**

| Flow or failure | Log | Metric |
|---|---|---|
| Operation by kind (create, rename, rotate, create key, revoke, revoke all, delete, copy, retry, cancel): completed, failed, partial, conflict, superseded, request budget exhausted | `operator_operation` I/W with the operation ID | `operator_operations_total{kind,outcome}`, `operator_operation_seconds{kind}` |
| Each provisioning step and its outcome | `operator_step` I/W | `operator_steps_total{step,outcome}` |
| Remote call to Kong admin, Grafana or the proof helper: ok, needs attention (401/403), version conflict, unavailable, uncertain | `operator_remote_call_failed` W | `operator_remote_calls_total{target,operation,outcome}`, `operator_remote_call_seconds{target}` |
| Operations left pending or needing attention, and the oldest age (the 15-minute signal) | `operator_operation_stale` W each minute | `operator_operations_pending{state}`, `operator_oldest_pending_age_seconds` |
| Kafka drain proof for deletion: proven, pending, unverified | `operator_kafka_drain` I/W | `operator_kafka_drain_total{outcome}` |
| Proof helper request: ok, proof unavailable (swallowed today) | `operator_proof_unavailable` W | `operator_proof_requests_total{outcome}` |

**Integrations, administration and state**

| Flow or failure | Log | Metric |
|---|---|---|
| Ticket-count call: ok, cache hit, timeout, connection error, HTTP status, invalid JSON, busy, not configured | existing rate-limited warning, as `ticket_count_failed` W without the address | `ticket_count_requests_total{outcome}`, `ticket_count_seconds`, `ticket_count_slots_in_use` |
| Grafana panel origins accepted or ignored at startup; invalid ServiceNow address | existing warnings, as events | `grafana_panel_config_entries{state}`, `integration_config_invalid{integration}` |
| Visibility registry: register, change, read; capacity or conflict | `http_request` | `visibility_registrations_total{outcome}`, `visibility_views{state}` |
| Capability gates, restore state, pipeline enabled, tenant count | `capability_gate_changed` W | `capability_gate_open{capability}`, `restore_active`, `pipeline_enabled`, `tenants` |
| State collector itself | `state_collector_failed` W | `state_collector_up`, `state_collector_seconds` |
| RUM intake: accepted, invalid, rate limited, unauthenticated, too large | `rum_batch_rejected` W | `rum_batches_total{outcome}`, `rum_events_total{kind,outcome}`, `rum_event_age_seconds` |

### 5.9 Migration job and command-line tools

Short-lived processes cannot be scraped, so these are logs only, flushed to
Fluent Bit before exit. Their stdout results stay unchanged.

| Flow or failure | Log |
|---|---|
| Migration started, each revision applied with duration, completed at head | `migration_started` I, `migration_revision_applied` I, `migration_completed` I |
| Chain or checksum invalid, gap, wrong head, lock unavailable, SQL failure | `migration_failed` E with revision, class and SQLSTATE |
| Kafka cutover, topic identity record, provider backfill, search bootstrap, rebuild commands, restore, key rewrap, directory publication | `cli_started` I, `cli_completed` I, `cli_failed` E with the controlled error code |

### 5.10 Frontend server

| Flow or failure | Log | Metric |
|---|---|---|
| Start, version | `frontend_started` I | `frontend_build_info`, Node process metrics on its own `/metrics` |
| Server-side request error (Next.js `onRequestError`) | `frontend_server_error` E | `frontend_server_errors_total` |

### 5.11 Browser (RUM)

All names below are `keep_rum_*` metrics produced by the Product API from
browser events. `route_group` is the section (alerts, alert detail, incidents,
presets, dashboards, rules, maintenance, reports, administration, sign-in).
Logged events go to the `browser` index.

| What users experience | Metric | Also logged |
|---|---|---|
| Page load: time to first byte, first and largest contentful paint, interaction delay, layout shift | `web_vital_seconds{name,route_group}`, `layout_shift{route_group}` | — |
| Application start: page open to session known to first data shown | `app_boot_seconds{phase}` | — |
| Moving between sections: click to data shown | `route_change_seconds{to_group}`, `page_views_total{route_group}` | — |
| A view loading or refreshing its data, and what triggered it (live hint, polling, own command, manual, tab return) | `view_load_seconds{view,outcome}`, `view_refresh_total{view,trigger}` | — |
| API calls as the browser sees them, including failures the server never sees: timeout, network error, unreadable response | `api_request_seconds{route,method}`, `api_requests_total{route,method,outcome}` | `rum_api_error` W with the request ID |
| Commands: ok, rejected, conflict, denied, uncertain outcome, retried | `commands_total{kind,outcome}`, `command_seconds{kind}`, `command_retries_total` | `rum_command_uncertain` W with the command ID |
| Live connection: share of time connected, connecting, reconciling, degraded, unavailable, hidden | `live_state_seconds_total{state}` | — |
| Live disconnects by cause: watchdog, stream end, HTTP status, network, parse error, denied | `live_disconnects_total{reason}`, `live_reconnect_delay_seconds` | `rum_live_disconnect` I |
| Live hint to refreshed data on screen | `live_hint_to_render_seconds` | — |
| Fallback polling used while the stream is down | `fallback_polls_total{outcome}` | — |
| Estimated receipt-to-screen delay for newly shown alerts (server times plus a local monotonic interval; labelled an estimate, never the SLI) | `alert_visibility_estimate_seconds` | — |
| Dashboard panels by type (count, incident count, table, HTML, image, Grafana, ServiceNow): load time and outcome | `panel_load_seconds{panel_type,outcome}` | — |
| JavaScript errors: uncaught error, unhandled rejection, failed chunk load | `js_errors_total{kind,route_group}` | `rum_js_error` E with truncated message, stack, and the ID of the alert or incident being viewed |
| Errors shown to the user | `error_notices_total{code_class}` | — |
| Session ended or access lost while working; redirect to sign-in | `session_ended_total{reason}`, `reauth_redirects_total` | — |
| Slow main thread | `long_tasks_total`, `long_task_seconds` | — |
| Time with the tab visible or hidden | `tab_seconds_total{visibility}` | — |
| Events lost in the browser buffer | `events_dropped_total` | — |

Not covered: the exact rendered-visibility measurement (D20). The sign-in page is
Keep's own since REV-73, but the browser can send events only with a session, so
what it collects there arrives after a successful sign-in; failed sign-ins are seen
by the Product API's request records and metrics, not by RUM. Its metric names are
reserved as `keep_visibility_observations_total{outcome}` for that later work.

### 5.12 Signals from services Keep does not operate

Reliability 20 has each owning team monitor its service. These are requested
from them and filled locally by optional exporters in the local stack.

| Service | Needed | Why |
|---|---|---|
| OpenShift router | Status codes and latency per route at the edge | The SLIs are defined at the public boundary; requests that never reach Keep are invisible to it |
| Kong | Per-route and per-consumer status and latency (its bundled Prometheus plugin) | Denials and policy failures happen before ingestion |
| Kafka | Broker log-end offsets, low watermarks, retention headroom, transaction aborts | The Kafka-first specification section 8 requires broker-authoritative offsets |
| PostgreSQL | Connections, lock waits, deadlocks, slow statements, storage growth | Explains Keep's pool and timeout numbers |
| Elasticsearch (alert search, and logs if separate) | Cluster health, refresh and merge time, heap, disk | Explains search latency; log indices grow without bound under D6 |
| Redis | Memory, clients, pub/sub | Explains live-hint delay |
| The LDAP directory and ADAPI (operated by their owners) | Their owners' own monitoring; Keep adds the bind and call outcomes and timings it sees | Sign-in and directory failures outside the application |
| Fluent Bit (deployed by this plan) | Records received, written, retried and dropped per output | The log pipeline must itself be watched |
| External probe | One request every 10 s to each public path; three failures in a row is an outage | Reliability 21; covers "no traffic" and "edge down" |

## 6. Indicators and alerts

**SLIs** (recording rules over 5.2's `sli_*` metrics, 30-day rolling window):

| SLI | Source | Objective |
|---|---|---|
| Availability: v1 ingestion, v2 ingestion, Grafana adapter, API reads, API commands, search and counts, each separately | `sli_requests_total` good / (good + bad) | 99.9% |
| Durable acceptance | `ingest_acceptance_seconds` within 1 s | 99% |
| Feed, list, search, count requests | `sli_latency_seconds{sli="search"}` within 2 s | 99% |
| Detail reads and local commands | `sli_latency_seconds{sli=~"read\|command"}` within 1 s | 99% |
| Change commit to correlation pass | `correlation_commit_to_pass_seconds` within 2 s | 99% |
| Incoming visibility within 5 s | Not measurable exactly until the work in D20. Interim stage numbers: `source_receive_to_applied_seconds`, `search_receive_to_visible_seconds`, `live_hint_publish_delay_seconds`, RUM `live_hint_to_render_seconds` and `alert_visibility_estimate_seconds`. Stage percentiles do not add up to an end-to-end percentile. | 99% |

Coverage gap to record under reliability 20: application metrics start at
Keep's own listener, not the public boundary; the router and Kong numbers in
5.12 close it.

**Alert rules** (files plus rule unit tests). Thresholds marked * are fixed by
the design; the rest are my starting values to tune.

| Condition | Severity |
|---|---|
| Burn rate 14.4× over 1 h and 5 m, or 6× over 6 h and 30 m, per SLI, with at least 100 observations in the long window and 20 in the short * | Urgent |
| Burn rate 1× over 3 d and 6 h * | Work item |
| External probe fails three times in a row * | Urgent |
| Retention gap, topic identity changed, coordinate conflict (possible accepted-data loss or unknown proof) * | Immediate |
| A partition held at one offset, or a proof gap, for over 1 minute * | Urgent |
| Operator operation pending or needing attention for over 15 minutes * | Warning |
| Clock offset over 100 ms / over 1 s; evidence older than 60 s * | Warning / urgent |
| Worker not ready over 2 minutes; transport restarting repeatedly | Urgent |
| Consumer lag or oldest unapplied age growing for 10 minutes | Warning |
| Publish unconfirmed rate above 1% for 5 minutes | Urgent |
| Search not verified for a tenant over 5 minutes; reconciliation failing; writers paused with no rebuild running; rebuild failed | Warning / urgent |
| Database pool timeouts, deadlocks, or restore guard rejecting work | Warning |
| Unhandled 500s above a low rate | Warning |
| Redis listener down 5 minutes; hint outbox older than 1 minute | Warning |
| Sign-in unavailable (LDAP or ADAPI); CEL compiler unavailable | Warning |
| RUM: JavaScript error rate spike; share of time connected drops; uncertain commands rise | Warning |
| Log pipeline: a service failing to ship or dropping records; Fluent Bit output errors, retries or drops; Fluent Bit down | Warning |
| A metrics target is down; state collector down | Warning |

## 7. Collection, dashboards and the local stack

**OpenShift templates** gain, per workload: a named metrics port, the scrape
annotations, `KEEP_METRICS_PORT`, the log settings including
`KEEP_LOG_SHIP_URL`, and network-policy rules for the collector and for egress
to Fluent Bit. New templates: the Fluent Bit Deployment, Service, configuration
and network policy, with the logs Elasticsearch connection and the index names
as parameters; and an optional ServiceMonitor. An example OpenTelemetry
collector configuration with a Prometheus receiver is documented beside the
Prometheus scrape configuration.

**Local stack:** a separate, opt-in compose file adds Prometheus and one Fluent
Bit container; the launchers point each process at it. The logs Elasticsearch
defaults to the local alert-search instance and can be pointed elsewhere, which
exercises both arrangements of D7. Before that default is adopted, the search
bootstrap is checked to confirm it inspects only its own alias and index; if it
does not, the local default becomes a second instance. Kibana is an optional
extra for looking at the indices. Ports come from delivery's local port plan
after checking what is free.

**Dashboards as files** in keep-delivery, for metrics: overview and SLOs;
ingestion; pipeline (source, correlation, search, with lag and blocked
partitions); Product API; live updates; operators and integrations;
dependencies as Keep sees them; user experience (RUM); per-process runtime; log
pipeline health. They are written for Grafana; say so if metrics should be
charted somewhere else.

## 8. Work packages

| # | Repository | Work | Size |
|---|---|---|---|
| 0 | design | REV-74 entry, including the change to reliability 20's alert-body clause (D9); `spec-observability.md` (log schema and field registry authority, alert fields in logs, quality rules, what is never logged, label rules, SLI classification, RUM endpoint, shipping contract); verification-plan rows; bump the REV bounds in the checks | S |
| 1 | domain | `keep.observability` (ECS logging, event catalog, field registry, alert-field binding, context, shipping handler, metrics, `/metrics` exposition, Kafka statistics, pool statistics, SLI classes, state collector); settings; instrument `http.py`, `db.py`, `pipeline_runtime.py`, `kafka_changed.py`, `alert_notifications.py`, clock health, CEL compiler, correlation and search runtime code; route gate for the RUM path; add `prometheus-client` | L |
| 2 | ingestion | Entry-point logging; admission, gateway and parser instrumentation; alert fields and identity digest on member records; request-ID header (D17, if accepted); Kong plugin reasons | M |
| 3 | processor | `kafka_source.py` instrumentation with the whole alert, alert ID, digest and status transition; statistics callback; CLI logging | M |
| 4 | correlation | Consumer outcomes and decisions with alert fields (most logic is in domain) | S |
| 5 | indexer | Hints, per-document records with alert fields, writer, reconciliation, rebuild, CLI | M |
| 6 | api | Entry-point logging; sign-in and session events; commands with alert fields and resource IDs; live; operators; integrations; images; audit records; state collector; RUM endpoint; proof helper; regenerate OpenAPI | L |
| 7 | contracts | Regenerated HTTP snapshots only (RUM endpoint), from the API's producer tools | S |
| 8 | migrations | Job logging; one forward migration only if an aggregate needs a grant | S |
| 9 | frontend | RUM collector, buffer and transport; `instrumentation-client.ts`; web vitals; instrument `api.ts`, `hooks.ts`, `live-stream.ts`, `search-resource.ts`, the coalescer and panels; server `instrumentation.ts` with `/metrics` and log shipping; regenerated client; browser tests | L |
| 10 | delivery, metrics | Templates (metrics port, annotations, network policy); Prometheus and collector configuration; alert rules and their tests; metric dashboards; launchers; delivery's own `keep.worker` launcher logging | M |
| 11 | delivery, logs | Fluent Bit Deployment, Service, configuration and network policy; logs Elasticsearch connection and index-name parameters; local Fluent Bit; `docs/runbooks/observability.md` with the generated event reference and the queries of 4.3; status documents; pins | M |

**Order:** 0, then 1. Packages 2–6 and 8 depend only on 1 and are independent of
each other (at most three in parallel). 7 follows 6; 9 follows 7. Package 11 can
start alongside 1 and is usable from the first service that lands. 10 pins the
final heads. Each repository gets its own `feat/observability` branch and pull
request; nothing merges, publishes or deploys without your authorization.

**Rollback:** unset `KEEP_METRICS_PORT` or `KEEP_LOG_SHIP_URL`, set the RUM flag
off, remove the Fluent Bit workload, or revert a pin. No Kafka value, API
response or stored data changes shape. Old images ignore the new environment
variables (they are read the way `KEEP_BIND_PORT` already is), so configuration
can be set before or after the image update.

## 9. Acceptance criteria

1. Each Python process writes only valid ECS JSON records with the fields of
   4.1; `text` format gives the same events on stderr; existing
   message-asserting tests pass unchanged.
2. Every emitted event is in the catalog with its required fields present, and
   every field is in the registry with one type.
3. On the local stack, each service's records arrive only in that service's
   index, and a full test run ends with zero records rejected by Elasticsearch.
4. An alert sent through the local stack and then acknowledged and resolved in
   the UI is fully recovered by `keep.alert.id : "<UUID>"` over `keep-logs-*`:
   records from processor, correlation, indexer and API in time order.
5. The same alert is found by `keep.alert.application` and
   `keep.alert.component` alone, in every service's index including ingestion,
   and its processor record contains every field that was submitted, unchanged.
6. A member rejected at validation and a member whose publication was
   unconfirmed each leave an ingestion record with the whole alert.
7. Each HTTP request yields exactly one request record whose `http.request.id`
   equals the `X-Request-ID` response header, and all other records of that
   request carry the same ID.
8. An idle dashboard left open for ten minutes produces only summary records
   for its automatic refreshes, not one per poll.
9. A test submits credentials, session cookies, tokens and user names
   containing canary strings through every flow and finds none in any log
   record. Alert fields appear only under `keep.alert.*`, never in a metric
   label and never in an error or stack field.
10. Every row in section 5 has a test that triggers it and asserts the log
    event and the metric change. Every registered route has exactly one SLI
    class.
11. No metric has a label outside the allowed list; no label value set grows
    with tenants, alerts or users.
12. `GET /metrics` answers on the metrics port of each process, is scraped by
    the local Prometheus, and returns 404 on every public application port.
13. With Fluent Bit stopped for five minutes: every service keeps working,
    ingestion's admission latency is unchanged, stderr has every record,
    records within the queue limit arrive after it returns, and records beyond
    the limit are counted as dropped. With Elasticsearch stopped for the same
    five minutes instead, no record is lost or duplicated.
14. The logs target and the alert-search target work when they name the same
    cluster and when they name different ones.
15. Browser journeys produce the expected RUM numbers: a page load, a section
    change, a forced API failure, a dropped live connection and a thrown
    JavaScript error.
16. The RUM endpoint rejects unauthenticated, oversized and malformed batches,
    and rate-limits a session.
17. The SLI recording rules and each alert rule pass rule unit tests, including
    the minimum-observation guards.
18. With instrumentation on, the existing pipeline load comparison shows
    admission p99 and processor throughput within its normal run-to-run spread,
    and reports the measured log volume per delivery.
19. With every new setting unset, behavior is unchanged except that stderr logs
    are structured.

## 10. Planned checks (none run yet)

- Per repository: unit and component tests, including the new branch tables;
  `ruff`; type and build checks in the frontend. New tests of specified
  behavior cite their governing IDs and sections in the docstring
  (`Requirements: REV-74; reliability 20.`).
- Delivery: `qualification/check_contracts.py`, `verify_boundaries.py`,
  `scripts/workspace.py test`, `verify_documents.py`, `check_context.py`;
  design `scripts/check_docs.py`.
- The four-journey smoke baseline from the development runbook, then browser
  journeys for RUM and the alert journeys of criteria 4 to 6 against the
  running local stack.
- Fault injection on the local stack: stop Kafka, PostgreSQL, Elasticsearch,
  Redis and Fluent Bit in turn and confirm the expected events, metrics and
  alerts.
- Fluent Bit configuration validated with its dry run; rule unit tests with
  `promtool`; the pipeline load comparison before and after.
- Not checkable locally, stays open: the real ECK cluster, the company
  collector, Kong and router metrics, and real 30-day windows (DEL-08).

## 11. Risks

- **Alert content is readable outside Keep's permissions.** Keep limits each
  user to their tenants. The log indices hold every tenant's alerts, and by D6
  this plan sets up no access control on them. Whoever can query the logs
  cluster can read all of it. Restricting that is Elasticsearch-side work for
  whoever runs the cluster.
- **Indices grow without bound.** By D6 nothing rolls over or deletes the log
  indices, and they use Elasticsearch's default settings. At the volumes of
  4.5 this needs attention from whoever runs the cluster soon after go-live.
- **Automatic field mapping.** Without a template, Elasticsearch decides each
  field's type from the first value it sees. The registry (D8) keeps that
  stable; a field added outside the registry could cause rejected records,
  which the tests of criterion 2 and the Fluent Bit error counters catch.
- **Push shipping can miss the last records.** A process killed outright loses
  whatever was still queued, and output that bypasses the logger is not shipped
  (4.4). Both remain in the container log.
- **One Fluent Bit deployment is a single path.** Services buffer and keep
  working while it is down, and the stderr copy remains; a long outage drops
  the oldest queued records from Elasticsearch.
- **Log volume.** The numbers in 4.5 are estimates; criterion 18 measures them.
- **Leak through an exception.** Mitigated by never logging exception text and
  by the canary test, which must cover unhandled-exception paths too.
- **Overhead on the admission path.** Counters are cheap; logging and shipping
  are queued. Criteria 13 and 18 measure it.
- **Scale of change.** Eleven repositories. The shared package and the
  choke-point approach keep service changes thin; it lands in the order of
  section 8 so each step is usable on its own.
- **Frontend framework drift.** Next.js 16.3.6 differs from older versions; the
  bundled documentation confirms `instrumentation-client.ts`,
  `onRouterTransitionStart` and `useReportWebVitals`. Server `instrumentation.ts`
  and `onRequestError` still need reading before use.

## 12. Traceability to reliability section 20

| Section 20 asks for | Covered by |
|---|---|
| Outcomes and latency | 5.2 `http_*`, `sli_*`; per-service outcome counters |
| Kafka partition lag, age, retention risk | `kafka_consumer_lag_messages`, `source_oldest_unapplied_age_seconds`, watermarks, `*_retention_gaps_total`; broker-side numbers in 5.12 |
| Non-alert job depth | `live_hint_outbox_rows`, `operator_operations_pending`, `directory_verification_requests_pending`, `search_rebuild_state` |
| Blocked processing and proof | `source_partition_state`, `*_partition_blocked_seconds`, `correlation_proof_failures_total` |
| Correlation health | `correlation_tenants{health}`, `correlation_rules{state}`, `correlation_commit_to_pass_seconds` |
| Scheduler and projection lag | Scheduler deferred (REV-34). Projection: `search_watermark_lag_seq`, `search_oldest_verified_age_seconds` |
| UI acknowledgement coverage | Not in this plan (D20); RUM gives labelled estimates |
| Prometheus-compatible metrics | D2 |
| 1% trace sampling | Left out by user decision (D19); recorded as a gap |
| Redacts secrets and alert bodies from diagnostics | Secrets: 4.1 and criterion 9. Alert bodies: changed by user decision (D9); the clause is amended when REV-74 is recorded |
| No high-cardinality metric labels, tenant-scoped health | D15, criterion 11 |
| Recovery and privileged-action audit events to a destination | `audit_event` records, queryable by `keep.audit : true` |
| Burn-rate alerting, probes, escalation (section 21) | Section 6 |

## 13. Behavior noticed while mapping, not changed by this plan

These are existing behaviors the new signals will expose. Changing any of them
is separate work and a separate decision.

- A deterministically invalid input retries every 5 seconds forever and holds
  its partition; only `source_coordinate_conflict` fences.
- The processor has no lost-partition callback and does not abort or close its
  transactional producers on revoke or close.
- A long worker turn does not refresh the heartbeat file.
- Ingestion readiness creates a new Kafka admin client on every probe.
- The frontend has no error boundary; a render exception blanks the page. RUM
  will report it, but showing a fallback screen is a UI decision.
- The Product API has no request rate limiting; 429 exists only in unwired code.
