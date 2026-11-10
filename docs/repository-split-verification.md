# Repository split verification — 2026-10-01

The eleven private repositories are owned by `keep-alert-operations`. The source
monorepo is retained. Each repository has filtered Git history, its owned source,
README, scoped agent instructions and checks appropriate to its contents. Internal
dependencies and the workspace manifest pin exact commits.

The immutable source checkpoint was captured from original Git commit
`13ea15ff53364b0ef1df1b9126fd075b013333ec` plus the user's local working state.
`workspace/source-inventory.json` records file provenance and destinations;
each repository's `SOURCE.md` records extraction provenance. The final workspace
is a sibling directory, `keep-workspace`, with independent checkouts and the
system-wide `AGENTS.md` copied from delivery.

## Executed checks

- All nine Python distributions built as wheels. Ten application/tool modules
  imported from a fresh installed environment outside the original source tree.
- Five service entrypoints passed import guards rejecting other service packages.
  Ingestion additionally passed with SQL, Elasticsearch, Redis, CEL, LDAP and OIDC
  adapter imports forbidden. Its separate installed dependency closure also passed
  its unit suite and Linux image import.
- Individual locked unit suites: contracts 16, domain 365, API 70, ingestion 5,
  indexer 29 and migrations 1 passed. Processor and correlation entrypoint/handler
  behavior is covered by the boundary checks and delivery's system tests.
  Delivery's own locked unit suite passed with 76 passed and 65 skipped after
  running without the concurrent browser workload.
- The delivery PostgreSQL run recorded 228 passed, 321 skipped and one CEL compiler
  deadline failure. That one case passed in an isolated rerun, without changing
  the one-second compiler deadline. Both XML reports are retained under
  `qualification/repository-split-postgres*.xml`. Skipped service-dependent and
  deferred-scope cases remain unqualified.
- All original SQL files match their captured original bytes. Extraction initially
  normalized some line endings; the checksum test caught this, original bytes were
  restored, and `*.sql -text` prevents future checkout conversion.
- Product and ingestion OpenAPI, the frontend aggregate and all event schemas
  match their installed producers. Frontend generation, TypeScript checking and
  production build passed. npm audit reported zero vulnerabilities after the
  minimal Next.js 16.3.5 → 16.3.6 patch for the
  [maintainer's ImageResponse advisory](https://github.com/vercel/next.js/security/advisories/GHSA-vcvr-r3jv-pc5j).
- Browser regression: the full run had 134 passed, six failures and seven skipped.
  All six cases passed on targeted reruns after restoring three test files' UTF-8
  text and correcting two old test assumptions about specific-tenant administration
  and the accessible theme switch. The narrow-screen case passed on rerun without
  a code change. No product UI behavior was changed for these test corrections.
- Ingestion and migration Docker images built with temporary BuildKit credentials
  and passed imports in Linux containers with networking disabled. Other service
  images and cluster deployment have not been qualified by these checks.
- Workspace documentation target/fence checks, source destination checks and
  REV-01 through REV-37 register checks passed. Local PowerShell scripts parsed.

## CI and operational limits

The contracts GitHub Actions run passed. Service workflows requiring sibling
private repositories stop at their explicit credential check until an organization
administrator supplies `APPCHI_REPOSITORIES_TOKEN` with read-only contents access
to those repositories. No existing personal credential was saved into Actions.

Historical qualification evidence is preserved with its original scope. This
extraction did not deploy to production, modify shared company services, enable
deferred workers, or qualify actual Kafka/Elasticsearch/Kong/OpenShift bindings.
Use [workspace setup](repository-workspace.md) for local development and the
existing delivery readiness requirements for release qualification.
