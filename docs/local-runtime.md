# Running the local Appchi workspace

Open **https://localhost:3443**. The core local services were started and verified
on 2026-09-23. Sign in as `local-admin`; its generated password is under
`users.local-admin.password` in `.local/keycloak/credentials.json`. Appchi now
uses only username and password, with no one-time code.

The [identity guide](local-keycloak.md) covers the editor/viewer accounts, custom
users/groups and separate Keycloak console authentication. The launcher
does not change Windows trust automatically. On this workstation, the user
explicitly approved trusting the generated certificate on 2026-09-23; both
Appchi and Keycloak now load with normal browser certificate validation. If an
old tab still displays the certificate error, reopen the URL in a new tab.
Fresh workstations need their own explicit local certificate trust setup.

Select **Global → Alerts** to find the synthetic `Appchi local demo` alert. The
live browser check received this alert through native v1, waited for processing
and Elasticsearch search, then acknowledged it and added a note through the UI.
`Local Demo` is a separate tenant with admin/editor/viewer group grants. Native
v1 always routes to Global, so its alert will not appear in Local Demo.

## Start, inspect and stop

From this workspace's root in PowerShell:

```powershell
.\scripts\local-runtime.ps1 -Action Start
.\scripts\local-runtime.ps1 -Action Status
.\scripts\local-runtime.ps1 -Action Start -Restart
.\scripts\local-runtime.ps1 -Action Stop
```

Start preserves credentials and data and reuses owned running processes.
`-Restart` restarts managed application processes to reload configuration.
Stop stops the UI, API, workers, Keycloak, Kafka and Redis; it leaves the existing
native PostgreSQL and Elasticsearch processes and all data in place. Services
run as local background processes; they are not installed as Windows services.

This launcher uses the dependencies and stores already installed in **this
workspace**: Docker Desktop, PowerShell 7, backend virtualenv, frontend Node
dependencies, PostgreSQL 18 at its existing installation and `.local/pgdata`,
and `.local/elasticsearch-9.0.0` with its existing loopback configuration/data.
It can start these existing native stores when their ports are absent; the
cold-start-after-reboot path has not been exercised. For a new machine, follow
the [development runbook](runbooks/development.md) and
[Elasticsearch runbook](runbooks/local-elasticsearch.md) first.

| Service | Local address |
|---|---|
| Appchi HTTPS UI | https://localhost:3443 |
| FastAPI | http://127.0.0.1:58000 |
| Keycloak / console | https://localhost:58443/admin/ |
| PostgreSQL 18.2 | 127.0.0.1:55432 |
| Elasticsearch 9.0.0 | http://127.0.0.1:59200 |
| Kafka 4.0.0 | 127.0.0.1:19092 |
| Redis 7.4.2 | 127.0.0.1:56379 |

Kafka uses port 19092 because this machine reserves the original default port.
The launcher starts only Kafka/Redis from the runtime Compose file; do not start
its PostgreSQL/Elasticsearch containers over the existing native stores.
Container recreation is not a tested persistence/recovery procedure. Preserve
the `.local` directory and Keycloak volume; credentials must match persisted data.

The directory worker refreshes native Keycloak users/groups about every minute.
Nine additional workers handle source processing, indexing, event relay,
source/indexer/correlation hints, live updates, assignment cleanup and inactivity.
Their logs, process records and heartbeats are in `.local/runtime`; identity,
API and frontend process state/logs are in `.local/keycloak`.

## Verified scope and remaining gates

Working evidence covers real sign-in, group roles and logout (password-only login
reverified on 2026-09-24),
native v1 admission, PostgreSQL processing, Elasticsearch search, live events, browser
acknowledgement and notes. The development search bootstrap requires the actual
passing search/count compatibility report at
`qualification/pytest-local-runtime-es.xml`, exact local store bindings and an
initial search gate. It cannot clear restore fences. Initial activation and
repeated Start were exercised against the actual services.

**This is a usable core development environment, not the complete initial
release.** The following remain unavailable in this local deployment:

- Maintenance and automatic correlation require their processing/capacity gates.
- Managed operators and native v2 require configured Kong/Grafana integration.
- Protected retention/recovery require independent evidence and remaining
  recovery implementation.

The development capacity profile remains unqualified. No production readiness
or company-service compatibility is claimed. See
[implementation status](../implementation-status.md) for the complete gaps.

The opt-in real browser check runs from `frontend` and leaves a synthetic alert:

```powershell
$env:APPCHI_LOCAL_RUNTIME_TEST='1'
node node_modules/@playwright/test/cli.js test --config playwright.local-runtime.config.ts
```

Its safe result identifiers are saved to `.local/runtime/browser-evidence.json`.
It uses real login and APIs without request mocks or injected sessions.
