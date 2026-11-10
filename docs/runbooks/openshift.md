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
6. Render `network.template.json` separately for each workload. Supply actual
   ingress peers/ports and egress rules, including required DNS and approved
   service endpoints. No allow-all network rule is supplied. Configure company
   ingress routing and TLS with the platform team: `/auth` and product `/api`
   paths reach Product API through the frontend proxy, native v1 reaches the
   public ingestion service, and only Kong reaches the private mTLS service.
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
