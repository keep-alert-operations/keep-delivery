# Private-cluster load generator

Use the [OpenShift load-testing runbook](../../docs/runbooks/openshift-load-testing.md)
for service deployment, readiness, resources, observability, recovery and final
acceptance. These scripts generate traffic inside the private network. They are
an initial executable toolkit, not previously executed capacity evidence.

## What is executable

| Script | Behavior |
| --- | --- |
| `seed.js` | Admit a run-specific fixture of 5,000 unresolved alerts, in batches of up to 100 distinct keys. Fail on any invalid/uncertain acknowledgement. |
| `verify-seed.js` | Using a normal signed-in session, poll the exact run-specific search count until it equals the seed count with no projection lag. It does not replace authoritative PostgreSQL/proof checks. |
| `ingestion.js` | Native v1/v2 arrival-rate workload, single-object or batched, bounded or growing lifecycle, individual member accounting, JSON summary and failure thresholds. |
| `reads.js` | Real authorized dashboard-count and alert-search HTTP requests, with normal session activity/refresh and actual saved dashboard/preset revisions. |

The k6 generator does **not** automate browser rendering/SSE, Grafana webhook
formatting, management commands, tenant-isolation attacks, PostgreSQL replay-proof
verification, dependency outages or exact count comparison to PostgreSQL. Execute
those checks from the main runbook and record coverage separately. Dashboard
responses are checked for the expected panel set/revisions, a shared observation
envelope and equal counts for repeated preset references. This cannot prove the
server used one Elasticsearch PIT or one database clock; capture server/ES
instrumentation for that requirement. No receipt-status endpoint is assumed.

## Build and transfer

Use a build machine with access to the approved k6 base image and your private
registry. From `keep-delivery`:

```sh
docker build --build-arg K6_IMAGE=APPROVED_K6_IMAGE_AT_SHA256_DIGEST \
  -t PRIVATE_REGISTRY/keep-load-test:REVIEWED_TAG qualification/load-test
docker push PRIVATE_REGISTRY/keep-load-test:REVIEWED_TAG
```

Record the resulting immutable digest in the Job. If the build machine cannot
reach the registry, export with `docker save`, transfer through your approved
network path, then import/tag/push from the private-network build host. Mirror the
base image as well if building there. No public network access, npm install,
downloaded script, Grafana Cloud account or secret build argument is needed at
runtime. The image requires k6's standard HTTP/scenario APIs; validate your
approved version with a smoke test and record it. TLS verification stays enabled.
Mount the cluster CA bundle as `/trust/ca-bundle.pem`; do not add `--insecure-skip-tls-verify`.

## Accounts, operator and resource fixtures

1. Complete the runbook's migrations, Kafka/search/gateway readiness and trusted
   identity publication. Use an isolated synthetic-data namespace/environment.
2. For v2 create a real operator through Keep, complete provisioning and copy
   its current active key through the normal authorized UI/API. Save only its
   value in a protected local file `private/operator-key`. The endpoint must be
   the public Kong route, never private ingestion or forged gateway headers.
   V1 uses the public v1 endpoint and the actual fixed Global tenant UUID.
3. Sign each dedicated Keycloak test user in through the real Keep browser
   `/auth/login` flow after trusted publication and grants. Obtain the
   `__Host-keep_session` cookie value using the browser's protected developer
   tools and that user's `principal.id` from `/auth/session`. Do not create a
   database session, accept a raw Keycloak token as a Keep session, or change
   the application to bypass OIDC. Do not share one session between simulated
   users.
4. Create `private/sessions.json` as an array with one `{ "principal_id": "...",
   "cookie": "..." }` per concurrent read user; see the placeholder-only
   [example](profiles/sessions.example.json). Copy secrets directly to the file;
   do not echo them or commit them. A seed-visibility check uses the first user.
5. Through Keep create a 24-panel dashboard with repeated preset references and
   another with 24 distinct valid queries, including relative-time queries.
   Preserve their UUIDs and the actual tenant UUID. Use synthetic fixtures whose
   expected counts you can independently calculate. Keep definitions unchanged
   during a measured throughput run; concurrent-edit behavior is a separate test.

Create the namespace-scoped inputs without putting credentials in YAML or command
arguments (commands below run on the private-network operator workstation):

```sh
oc -n KEEP_TEST_NAMESPACE create secret generic keep-load-auth \
  --from-file=operator-key=private/operator-key \
  --from-file=sessions.json=private/sessions.json
oc -n KEEP_TEST_NAMESPACE create configmap keep-load-trust \
  --from-file=ca-bundle.pem=private/ca-bundle.pem
oc -n KEEP_TEST_NAMESPACE create configmap keep-load-profile \
  --from-file=profile.json=qualification/load-test/profiles/diagnostic-10.json
```

Use unique per-run ConfigMap/Secret names when overlapping runs, and change the
Job references accordingly. Do not change a mounted profile during a run. Read
traffic needs a separate profile ConfigMap from ingestion traffic.

## Seed, verify, then measure

Fill [job.example.yaml](job.example.yaml) with the real namespace context, image
digest, HTTPS origins, tenant and dashboard UUIDs, unique DNS-valid Job name and
run identity. `RUN_ID` is 1-40 letters/digits/hyphens, for example
`lt-20261005-v2-10a`. All synthetic alerts carry
`application = "load-<RUN_ID>"`; v1 additionally uses an explicit key containing
the run identity. Use the same `RUN_ID`, version, VU count and batch size for the
seed, verification and **one** measured ingestion run. Use a fresh identity for
the next measured run; rerunning one identity can turn intended inserts into
updates. Keep the fixture/profile/image hashes with the results.

Use a separate `REPLACE_STAGE_RESULT_PVC` per Job when read/ingestion Jobs may
overlap on different nodes. The example uses ReadWriteOnce; do not share that
volume concurrently across nodes. An owner-supplied ReadWriteMany class is an
alternative. Preserve/export each PVC separately. The Job uses the runbook's
`keep-load-test` service account and its private-registry pull secret.
Run seed and verification before ingestion. `backoffLimit: 0` and
`restartPolicy: Never` are intentional: a restarted generator cannot safely
continue its in-memory ordering/FIFO state.

| Stage | Job args | Other changes |
| --- | --- | --- |
| Seed | `[run, /work/seed.js]` | Ingestion profile; keep `FIXTURE_READY=0`. |
| Verify | `[run, /work/verify-seed.js]` | Same ingestion profile/run; use appropriate tenant and real account. |
| Ingestion | `[run, /work/ingestion.js]` | Unique Job name; set `FIXTURE_READY=1` only after seed visibility and authoritative checks succeed. |
| Reads | `[run, /work/reads.js]` | Separate reads profile/Job; distinct real sessions; `DASHBOARD_IDS` is comma-separated. |

Start each Job with `oc -n KEEP_TEST_NAMESPACE apply -f YOUR_FILLED_JOB.yaml`.
Inspect status and logs with `oc get jobs,pods` and `oc logs job/JOB_NAME`. Wait
for Job completion and preserve its termination exit code, not just its logs.
Each script writes sanitized JSON into the result PVC, keyed by run and stage.
Copy results using your approved PVC-access pod or storage export procedure;
`oc cp` requires a running container with `tar` and may not work after the k6 Job
exits. Do not delete the PVC before exporting evidence. Missing/truncated output,
an OOM, interrupted iteration or nonzero exit means incomplete/failed evidence.

The example generator allocation (1 CPU/1 GiB request, 2 CPU/2 GiB limit) is a
diagnostic starting point. Monitor its CPU throttling/memory/network alongside
the application. The 500/2,000-VU profiles may need substantially more allocation;
measure and adjust before concluding Keep has a bottleneck. Keep one generator
process (`parallelism: 1`); distributing a shared `RUN_ID` across Jobs breaks
key ownership and unique ordinals. Use separate profiles/run identities for a
deliberately partitioned multi-generator experiment.

## Profiles and rate accounting

Start with [diagnostic-10.json](profiles/diagnostic-10.json), then 25, 50, 100 and
250 items/second. Each plateau is a separate five-minute run with seeded fixtures,
fresh identity and recorded baseline. Run v1 and v2 separately: copy a profile,
change `version` and use the matching public endpoint. Keep traffic in one tenant
for the concentrated profile, then add explicitly scoped fairness tests.

`itemsPerSecond` counts **native alert members**, not HTTP requests. Each
iteration sends `batchSize` members; iteration arrival rate is their exact
quotient. Rates support one decimal place and are represented by an exact integer
rate over a reduced time unit: 250 items/s with 100-member batches is 5 requests
per 2 seconds, and 66.7 items/s with singleton requests is 667 per 10 seconds.
The offered duration must contain a whole number of batches; invalid combinations
are rejected instead of rounded. Use [reference-66-7.json](profiles/reference-66-7.json)
and [maximum-batch-250.json](profiles/maximum-batch-250.json) for these cases. The
latter uses 8,192-character messages; Unicode-content and negative boundary cases
still need explicit fixtures. Raw objects are used at batch size one;
multi-item requests use `{"alerts":[{"payload":{...}}]}`.

Primary profiles use a 3,500-character ASCII message and pad the serialized native
object with a small amount of JSON whitespace to exactly 4,096 UTF-8 bytes. Most
of that size is actual message content that reaches history/search. The
`keep_normalized_member_bytes`, `keep_raw_member_bytes`,
`keep_envelope_bytes` and `keep_request_body_bytes_attempted` metrics report
compact field JSON, transported members, request bodies and total offered body
bytes respectively. Compare both member measurements in every report.

The separate `payload-1024.json`, `payload-16384.json` and `payload-65536.json`
profiles exercise raw transport sizes. The 16/64 KiB profiles deliberately add
substantial JSON whitespace; they **do not** represent 16/64 KiB of retained
field content. Message content remains within the actual 8,192 decoded-character
limit. Use maximum-string and representative Unicode fixtures separately to
characterize normalization, compilation and storage complexity. Every encoded
member is checked against 65,536 bytes, and every envelope against 4,194,304 bytes
before any HTTP call. A 100-member batch of 64 KiB members is invalid; seeding
automatically reduces its batch size to stay within the envelope limit.

The generator omits optional legacy event/idempotency identifiers: they do not
deduplicate new submissions under the current Kafka contract. It never retries
an uncertain admission. It checks aggregate status and every ordered result:
index, status, disposition and broker-confirmed `received_at`. Mixed 207 responses
are counted by member and fail a healthy-run threshold. Record accepted,
rejected and unconfirmed members separately; uncertainty is not proven loss.

The bounded lifecycle profiles own a FIFO per VU. Seeds are partitioned by VU;
each update only targets that owner's current active keys. Within a batch all
keys are distinct, and new keys are used only in later requests. Requests from
one VU are sequential, preserving each key's send/acknowledgement order. The
generator stops on an uncertain/failed bounded admission because it cannot
safely advance that FIFO. Application failures after 202 still need independent
PostgreSQL, replay-proof and Kafka evidence.

| Mix | Bounded behavior |
| --- | --- |
| 50% new / 50% update | Each full two-item VU cycle resolves an older key and creates one new firing key; about 5,000 unresolved remain. |
| 20% new / 80% update | Each full five-item VU cycle creates one, resolves one and changes the message of three existing firing keys. |
| 80% new / 20% update | `new-heavy.json` is explicitly growing; updates keep their seed keys firing. It cannot hold unresolved count constant. |

Bounded mixes are exact over each VU's full cycles. In singleton mode, the
aggregate partial-cycle difference is less than one new item per participating
VU; actual new/update/resolution counters are authoritative. During traffic,
unresolved count may fluctuate by up to the participating VU count plus pipeline
lag; compare after drain and account for each VU's incomplete cycle. A batch
size of 2 (50/50) or 5 (20/80) makes each complete request's mix exact. Growing
mode uses a global balanced allocation, exact over complete 100-item blocks.

Bounded unresolved count does **not** bound stored history or total logical
alerts. At 250/s for 24h there are 21.6 million inputs; a 50/50 mix creates about
10.8 million new identities plus seeds and retained histories. Physical retention
is deferred. Measure actual disk/WAL/ES/Kafka growth in short runs and allocate
for the duration, retained baseline, indexes and recovery margin before enabling
[sustained-250.json](profiles/sustained-250.json). Never run it on the starter
storage allocation solely because the JSON exists. Old run fixtures also remain;
restore a qualified isolated baseline or count them explicitly between runs.

Keep the prescribed ten-minute startup period separately. The 24-hour sustained
window must follow readiness/warmup; do not subtract failed intervals or growing
backlog from the measured result. [overload-1000.json](profiles/overload-1000.json)
offers 1,000 items/s for 60 seconds. Its strict healthy-run thresholds remain
visible; overload/backpressure acceptance also requires the main runbook's
correctness, retained-acknowledgement and backlog-drain evidence.

For recovery, continue **250 incoming items/s** while measuring at least
**500 processed items/s** after the authorized two-hour backlog phase. 500/s is
processor catch-up throughput, not a second 500/s ingestion load. Coordinate
faults/retention with dependency owners. The main runbook defines the drain,
offset/proof checks and qualified recovery timing.

## Dashboard/search traffic and sessions

`reads.js` is a closed-user protocol workload: each VU sequentially requests one
dashboard-count batch and one search, then pauses. It exercises backend reads
without simulating the browser's refresh/coalescing or SSE. Record actual
requests/s as the server slows; a closed-user workload is not a fixed-arrival
read test. Run it alone at 5, 10, 25 and 50 accounts, then overlap healthy read
levels with ingestion. `DASHBOARD_IDS` distributes users among supplied fixtures;
also run the repeated-preset and distinct-query cases separately.

Each VU loads the actual dashboard and preset revisions on first use. Changes,
missing definitions, wrong panel count, access failure or invalid observations
produce a failure rather than a guessed revision/retry. `GET /auth/session`
refreshes normally each minute; `POST /auth/activity` represents an active user
and carries the fetched CSRF token and exact configured HTTPS Origin. No command
UUIDs or invented assignment/acknowledgement endpoints are sent.

Read chunks are capped at one hour, and the script refuses a session whose
server-reported absolute expiry cannot cover the chunk plus margin. Normal idle
and 24-hour session expiry remain in force. Refresh secrets by signing in again
normally between chunks; a mounted file is read at process initialization, so
updating a Secret does not renew an already-running generator. For 24-hour combined
testing, schedule successive fresh-login read chunks while ingestion continues,
report all gaps and separately execute expiry/login/browser scenarios. There is
no unattended 24-hour OIDC login automation in this toolkit.

## Result interpretation and verification

Initial strict thresholds: all offered items acknowledged with valid envelopes,
no dropped iterations, exact planned offered/accepted item counts, admission
p99 <1,000 ms, search/count p99 <2,000 ms, and all response-contract checks true.
These are diagnostic failure criteria, not a substitute for the complete
availability/error-budget, pipeline, correctness or five-second render SLOs.
`202` measures admission to Kafka only. HTTP timing cannot establish commit,
search visibility or browser-render time.

Use k6's built-in `dropped_iterations` and generator resource evidence to identify
under-delivery. A dropped iteration invalidates the promised arrival rate;
increasing VUs after failure is a new test with new evidence. Do not discard failed
runs from the report. Raw `--out json` time-series output can be large at 24-hour
scale; size storage first or use the organization's approved metrics collector.
Never enable HTTP debug or log bodies/headers, which would expose session/key
material. The summary excludes credentials and URLs; keep the result PVC private.

Local checks (no traffic or cluster required):

```sh
.venv/bin/python -m pytest tests/unit/test_load_test_harness.py -q
.venv/bin/python -m ruff check tests/unit/test_load_test_harness.py
```

On Windows use `.venv/Scripts/python.exe`. Node is used to execute the pure
generator contracts and syntax checks; tests explicitly skip those checks when
Node is absent. Native generated payloads are validated against the installed
Keep contracts package; record its pinned/editable revision with the test run.

Upstream behavior references: [arrival-rate dropped iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/),
[cookie jars](https://grafana.com/docs/k6/latest/javascript-api/k6-http/cookiejar/),
and [k6 options](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/).
