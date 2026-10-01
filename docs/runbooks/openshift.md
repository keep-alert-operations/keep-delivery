# Private OpenShift application handoff

These are application templates, not evidence of a successful cluster rollout.
The installed OpenShift version, admitted security context, workload allocation,
image digests and service bindings must be verified in private nonproduction.
No company infrastructure or production deployment was changed.

## Order of operations

1. Build `deploy/backend.Dockerfile` and `frontend/Dockerfile` from the checked locks.
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
5. Render `worker.template.json` once per ongoing worker role with separate
   credentials and pool allocations. Supported roles include `source`, `relay`,
   `indexer`, `correlation`, `maintenance`, `inactivity`, `directory`, `operators`,
   `live`, `assignment-cleanup`, `retention`, `incident-retention`, `audit-retention`,
   `command-retention`, `configuration-retention`, `correlation-retention`,
   `maintenance-retention`, `operator-retention`,
   `protected-deletions`, and the three `*-hints` consumers. Run maintenance
   warmup/proof as explicit one-off commands with `--tenant`; they do not enable
   the maintenance gate.
   The [operator diagnostics cleanup](./operator-retention.md) role requires
   migration 0037 and preserves pending remote reconciliation journals.
   Optional `visibility-capture` expands recorded input cohorts after migration
   0036; configure explicit server allocations as described in the
   [capture implementation plan](../visibility-observations-implementation-plan.md).
   It does not yet accept rendered acknowledgements or qualify visibility SLOs.
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

Backend templates require an immutable `BACKEND_IMAGE` including its SHA-256
digest. Replica counts and CPU/memory/ephemeral-storage requests and limits have
no fabricated allocation defaults. `TMP_SIZE_LIMIT` bounds writable `/tmp`;
the application root remains read-only. `CONFIG_SECRET` supplies role-specific
environment variables. `BINDING_SECRET` mounts read-only files at `/etc/appchi`:
capacity profiles, directory/recovery bindings, trust roots and required client
certificates. Environment path values must name those exact mounted files.
The migration Job substitutes `MIGRATION_SECRET` for runtime configuration.

For private ingestion, configure the four `APPCHI_GATEWAY_*` TLS/peer settings
documented in `private-qualification.md`; the template binds port 8443 to all pod
interfaces. Certificate verification and approved peer fingerprints still apply.
The listener's liveness probe checks its TCP listener. Readiness executes the same
PG/gate/profile checks as the HTTP readiness endpoint, without printing secrets
or weakening the listener's client-certificate requirement. Include its one
temporary database connection in the deployment connection budget.

Workers write only ephemeral process health to `/tmp/worker-health.json`. Liveness
checks heartbeat age; readiness additionally requires the last work turn to have
completed successfully and the process not to be draining. An idle worker updates
the heartbeat while waiting, including hourly retention waits. Set
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

Application route boundaries now enforce the configured HTTP role before body parsing: product API serves human/product routes; ingestion API serves native v1; private ingestion serves native v2/Grafana/receipts with the existing mutual-TLS gateway proof. Liveness/readiness remain available on each HTTP host. A worker role cannot start the HTTP application. Isolated development product-api keeps a combined route surface for local integration; staging/production do not. Supply APPCHI_ENVIRONMENT explicitly in deployment configuration and retain network policies/TLS. Four controlled HTTP checks passed; cluster/network qualification remains required.
