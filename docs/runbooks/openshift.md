# Private OpenShift application handoff

These are application templates, not evidence of a successful cluster rollout.
The installed OpenShift version, admitted security context, workload allocation,
image digests and service bindings must be verified in private nonproduction.
No company infrastructure or production deployment was changed.

## Order of operations

1. Build each service-owned `Dockerfile` plus `keep-migrations/Dockerfile`
   and `keep-frontend/Dockerfile` from their checked locks. See
   [workspace builds](../repository-workspace.md#build-and-ci).
   Supply the frontend's trusted internal API origin at image build time. Record
   immutable image digests, dependency scan and SBOM in the release evidence.
2. Create versioned, workload-specific Secrets and service accounts under the
   deployment team's approved process. Runtime database identities must not own
   tables or bypass RLS. The migration credential belongs only to the migration
   Job. Pods do not mount Kubernetes API tokens.
3. Render `migration.template.json` with a unique release ID; run that Job and
   require successful completion before applying new application writers. Its
   image must contain the exact checked SQL migrations. Do not run the old and new
   writers concurrently unless that migration's compatibility has been verified.
4. Render `application.template.yaml` for Product API/SSE,
   `ingestion-api.template.json` for public native v1, and
   `private-ingestion.template.json` for the Kong mTLS listener. Render the
   frontend template using the same API origin used to build the image.
5. Render `processor.template.json`, `correlation.template.json` and
   `indexer.template.json` using their respective service images. Preserve the
   existing capability gates. Maintenance timers, retention, directory polling,
   assignment cleanup and inactivity workers are deferred (REV-34/35); operator
   provisioning and live publishing belong to Product API (REV-34/36). Explicit
   recovery/proof helpers remain separate authorized operational actions.
   After the first manual directory refresh (private qualification step 4), render
   `directory-refresh.template.json`: the daily refresh CronJob (REV-75, see
   [Daily directory refresh](#daily-directory-refresh-rev-75) below).
6. Render `network.template.json` separately for each workload. Supply actual
   ingress peers/ports and egress rules, including required DNS and approved
   service endpoints. No allow-all network rule is supplied. Configure company
   ingress routing and TLS with the platform team: `/auth` and product `/api`
   paths reach Product API through the frontend proxy, native v1 reaches the
   public ingestion service, and only Kong reaches the private mTLS service.
   Keep PostgreSQL must also admit `app: keep-directory-refresh`.
7. Validate rendered resources against the installed API (`oc apply --dry-run=server`)
   before applying them to nonproduction. Exercise the private qualification
   checklist and keep unsupported application capability gates closed.

## Parameters and mounted configuration

Templates require their service-specific immutable image parameter: `PRODUCT_API_IMAGE`,
`INGESTION_IMAGE`, `PROCESSOR_IMAGE`, `CORRELATION_IMAGE`, `INDEXER_IMAGE`,
`MIGRATIONS_IMAGE` or `FRONTEND_IMAGE`, including its SHA-256 digest. Replica counts and CPU/memory/ephemeral-storage requests and limits have
no fabricated allocation defaults. `TMP_SIZE_LIMIT` bounds writable `/tmp`;
the application root remains read-only. `CONFIG_SECRET` supplies role-specific
environment variables. `BINDING_SECRET` mounts read-only files at `/etc/keep`:
capacity profiles, directory/recovery bindings, trust roots and required client
certificates. Environment path values must name those exact mounted files.
The migration Job substitutes `MIGRATION_SECRET` for runtime configuration.

ServiceNow ticket panels (REV-65) need the Product API's `CONFIG_SECRET` to supply
`KEEP_TICKET_COUNT_URL`: the company's ticket counting service address, `https`
only, including any access key it needs in its query string. Supply it only to the
Product API. When the service uses a company CA, mount it with `BINDING_SECRET` and set
`KEEP_TICKET_COUNT_CA_FILE` to that file. Leave the address out to keep the panel
type disabled. The Product API must reach
that address through network policy and egress; Keep never logs its path or query.
After deploying, a super admin confirms **Admin › Integrations › Test connection**.

Grafana panels (REV-68) are on only when the Product API's `CONFIG_SECRET` supplies
`KEEP_GRAFANA_PANEL_ORIGINS`: the Grafana instances panels may frame, comma-separated
`https` addresses, each with an optional base path for a Grafana served under a
sub-path (`https://grafana.example.com,https://tools.example.com/grafana`). It is not
a secret. List only Grafana trusted as much as Keep: a framed panel runs Grafana's
scripts in the viewer's browser. Unusable entries are ignored and logged at start-up;
super admins see the list and the ignored entries on **Admin › Integrations**. The
Product API never contacts Grafana for these panels; browsers do. Ask each listed
Grafana's owners for these settings, also shown on that page:

- `[security] allow_embedding = true`, or panels stay blank.
- When Keep and Grafana are on different sites, `cookie_samesite = none` and
  `cookie_secure = true`, so framed panels get the viewer's Grafana sign-in; otherwise
  use Grafana's anonymous access.
- `content_security_policy = true` with `frame-ancestors <Keep's origin>` in
  `content_security_policy_template`, so only Keep can frame Grafana.

Single sign-on pages usually refuse to be framed; viewers sign in to Grafana in
another tab first.

ServiceNow tickets on incidents (REV-70) need the Product API's `CONFIG_SECRET` to
supply `KEEP_SERVICENOW_URL`: the company's ServiceNow address, for example
`https://<instance>.service-now.com`, `https` with an optional base path and no
query. It is not a secret. Incidents may store ticket links only into that address,
and browsers open its new incident form; Keep's server never contacts it, so no
egress is needed. Leave it out to hide ticket linking; tickets linked earlier still
show and can be unlinked. Apply migration 0069 before the Product API. A super admin
sees the address on **Admin › Integrations**.

## Daily directory refresh (REV-75)

`directory-refresh.template.json` renders one CronJob, `keep-directory-refresh`.
Once a day it runs `python -m keep.identity.worker --once --budget-seconds
${RUN_BUDGET_SECONDS}` from the Product API image. It re-reads the tracked groups and
people from ADAPI and publishes them, as the manual refresh does
([identity configuration 7–9](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-identity-configuration.md#7-membership-supply-and-the-five-minute-target)).
The team that operates the cluster deploys it and owns its alerts.

- **Inputs.** `PRODUCT_API_IMAGE` (by digest), `SERVICE_ACCOUNT` and the allocations.
  `CONFIG_SECRET` is the dedicated identity database login's environment:
  `KEEP_DATABASE_URL`, `KEEP_DEPLOYMENT_EPOCH`, the wrapping key, `KEEP_ADAPI_URL`,
  the secret `KEEP_ADAPI_CLIENT_ID` and `KEEP_SUPERADMIN_GROUP`. `BINDING_SECRET`
  mounts any company CA at `/etc/keep`. `SCHEDULE` (default `0 2 * * *`) is read in
  the cluster controller's time zone. Keep `RUN_BUDGET_SECONDS` (1,800) at least 60
  seconds below `ACTIVE_DEADLINE_SECONDS` (2,100). `STARTING_DEADLINE_SECONDS`
  (3,600) skips a run that cannot start within an hour.
- **Never bootstraps.** The job sets `KEEP_DIRECTORY_BOOTSTRAP_QUALIFIED=false` and
  never passes `--bootstrap`, so it cannot open the initial sign-in gate. Run the
  first, bootstrap refresh by hand as the private qualification checklist says, then
  apply the CronJob.
- **No overlap, no retry.** `concurrencyPolicy: Forbid` skips a run while the last
  one is active. `backoffLimit: 0` leaves a failed run failed until the next day or
  a manual run.
- **Network.** The pod accepts no connections. It needs egress to DNS, Keep
  PostgreSQL and the ADAPI address. Do not render `network.template.json` with an
  empty `INGRESS_PEERS` for it: Kubernetes reads a rule with no `from` as every
  source. Use the platform's deny-all ingress policy for `app: keep-directory-refresh`
  and supply the egress rules, or give the template one peer that matches no pod.
  Keep PostgreSQL's ingress rules must admit the label.
- **Suspend or resume.** Render with `SUSPEND=true`, or
  `oc patch cronjob/keep-directory-refresh -p '{"spec":{"suspend":true}}'` (and
  `false` to resume). Suspending keeps the CronJob and its history.
- **Run now.** After a group change that cannot wait for the next day:
  `oc create job --from=cronjob/keep-directory-refresh keep-directory-refresh-manual-$(date +%s)`.
  The job runs with the same command, budget and environment; it still never
  bootstraps.
- **Reading a run.** Exit 0 means every root published. Exit 1 means at least one
  root failed or was stopped, or the run could not start. The pod's stdout has the
  `directory_root_failed root=… reason=…` and `directory_refresh units=…` lines. Its
  stderr has the REV-74 records under service `api`, role `directory-refresh`:
  `directory_root_failed` (a team root's name, or a count of people, and the
  reason), `directory_refresh_completed` (units, published, failed roots, duration)
  and `directory_refresh_failed` (reason and error class) when it cannot start.
  `directory_stopped` means the budget ran out or the pod got SIGTERM: units read
  before the stop stay published. Repeated `directory_stopped` for the same late
  groups means the budget is too small for the directory. A Job that fails with
  `DeadlineExceeded` was ended by Kubernetes after `ACTIVE_DEADLINE_SECONDS`: its
  summary may be missing, and the units it published stay published.
  `directory_unavailable` points at ADAPI or the network; `directory_group_too_large`
  at a group above 10,000 objects.
- **Alerts to ask for.** A failed `keep-directory-refresh` Job; no successful run in
  the last 26 hours; `directory_refresh_completed` with `failed_roots` above 0 on
  two days running. A failure never removes access: missed roots keep their
  last-published memberships, at most two days old.
- **Logs.** OpenShift has no Fluent Bit for Keep yet (REV-74 item 5), so the
  records reach the pod's stderr only and last as long as the Job's pods
  (`failedJobsHistoryLimit` and `successfulJobsHistoryLimit` keep three of each).

For private ingestion, configure the four `KEEP_GATEWAY_*` TLS/peer settings
documented in `private-qualification.md`; the template binds port 8443 to all pod
interfaces. Certificate verification and approved peer fingerprints still apply.
The listener's liveness probe checks its TCP listener. Readiness executes the same
Kafka/clock checks as the HTTP readiness endpoint, without printing secrets
or weakening the listener's client-certificate requirement. Ingestion has no database connection or SQL dependency.

Workers write only ephemeral process health to `/tmp/worker-health.json`. Liveness
checks heartbeat age; readiness additionally requires the last work turn to have
completed successfully and the process not to be draining. An idle worker updates
the heartbeat while waiting, between work turns. Set
`HEARTBEAT_MAX_AGE_SECONDS` above the qualified maximum atomic work duration;
incorrectly short liveness deadlines can repeatedly interrupt valid work.
Dependency failure does not itself fail process liveness. A process probe is not
proof of zero tenant backlog or successful remote integration: inspect protected
application health and durable job diagnostics as well.

The requested termination grace is 60 seconds. Work remains fenced in PostgreSQL
if interrupted; deployment validation must still exercise termination during
the longest supported transaction, SSE delivery and producer flush.

Template fields follow the [OpenShift Template API](https://docs.redhat.com/en/documentation/openshift_container_platform/4.19/html-single/template_apis/template_apis).
Probe semantics follow the [Kubernetes probe contract](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-probes/).
These references do not assert the company's installed platform version.

Application route boundaries now enforce the configured HTTP role before body parsing: product API serves human/product routes; ingestion API serves native v1; private ingestion serves native v2/Grafana with the existing mutual-TLS gateway proof. Liveness/readiness remain available on each HTTP host. A worker role cannot start the HTTP application. Development uses the same separate application factories as deployed services. Supply KEEP_ENVIRONMENT explicitly in deployment configuration and retain network policies/TLS. Four controlled HTTP checks passed; cluster/network qualification remains required.
