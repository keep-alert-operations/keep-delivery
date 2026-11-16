# Local sign-in and directory

Local development replaces Active Directory and ADAPI with two local services
([identity configuration 14](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-identity-configuration.md#14-local-directory-development-exception), REV-73):

- **OpenLDAP** checks each password with one LDAP bind.
- **A mock ADAPI** serves the same people and groups that ADAPI would.

The application runs its normal sign-in, sessions, grants and directory
publication against them. There is no test login route and no injected session.
These services stand in for the company directory; they don't qualify it.

| Service | Local address | What it does |
|---|---|---|
| Keep HTTPS UI | https://localhost:3443 | Serves the `/sign-in` page and the workspace. |
| OpenLDAP | ldap://127.0.0.1:58389 | Compose service `openldap` (pinned image). Plain LDAP, no TLS. |
| Mock ADAPI | http://127.0.0.1:58092 | Host process running `scripts/local_adapi.py`. |

## Start, sign in and stop

From `keep-delivery` in PowerShell 7:

```powershell
.\scripts\local-identity.ps1 -Action Start
.\scripts\local-identity.ps1 -Action Status
.\scripts\local-identity.ps1 -Action Refresh
.\scripts\local-identity.ps1 -Action Start -Restart
.\scripts\local-identity.ps1 -Action Stop
```

`local-runtime.ps1` calls these actions as part of the whole stack; see
[local runtime](local-runtime.md). Start does this, in order:

1. Prepares `.local/identity/` and keeps every value that already exists.
2. Starts the `keep-local-stack` Compose project, including OpenLDAP.
3. Migrates the `keep_local` database and runs the one-time principal remap.
4. Writes `app-environment.json` and starts the mock ADAPI.
5. Waits for both services, then seeds the OpenLDAP users from `credentials.json`.
6. Runs one directory refresh with the bootstrap flag.
7. Starts the Product API (58000), the Ingestion API (58001) and the HTTPS UI (3443),
   and checks that `/auth/session` answers 401.

Start runs no directory worker. Repeating it reuses the running processes;
`-Restart` reloads them. Status shows the Compose containers and whether each
owned process (Adapi, Api, Ingestion, Frontend) runs. Stop ends the owned processes
and the OpenLDAP container. It keeps credentials and data.

To sign in, open https://localhost:3443. A signed-out visit opens `/sign-in`.
Enter a username and its password from `.local/identity/credentials.json`
(`users.<username>.password`). Read that file locally; don't copy passwords into
reports or terminal logs. Sign-in needs only a username and password. A session
lasts 24 hours from sign-in, with no idle timeout. **Sign out** ends the session
in Keep only.

The certificate `.local/identity/tls/localhost.pem` covers `localhost`,
`host.docker.internal` and `127.0.0.1`. The launcher never changes Windows trust,
so a fresh workstation may show a certificate warning. On this workstation the
user approved trusting it in `CurrentUser\Root` on 2026-09-23 (thumbprint
`3FFC616039134C802F5AAECEC737B91CEBF27D80`). The file was later copied byte for
byte, so that trust still applies. To remove the entry when retiring the local
environment:

```powershell
Remove-Item -LiteralPath 'Cert:\CurrentUser\Root\3FFC616039134C802F5AAECEC737B91CEBF27D80'
```

## Accounts and groups

| Username | Display name | Direct group | Purpose |
|---|---|---|---|
| `local-admin` | Local Administrator | `keep-administrators` | Global super-admin through `KEEP_SUPERADMIN_GROUP`. |
| `local-editor` | Local Editor | `keep-developers` | Ordinary member. |
| `local-viewer` | Local Viewer | `keep-observers` | Ordinary member. |
| `local-hebrew` | משתמש Local Hebrew | `keep-developers` | Hebrew label and a mixed Hebrew/English password; checks the UTF-8 bind. |
| `local-distribution` | Local Distribution | `keep-dev-announce` | Reached from `keep-developers` only through a distribution group. |
| `local-disabled` | Local Disabled | `keep-dev-announce` | `userAccountControl` 514. Its right password gets the same refusal as a wrong one. |

| Group | Label | Notes |
|---|---|---|
| `keep-administrators` | Keep Administrators | The super-admin group. |
| `keep-developers` | Developers | Nests `keep-dev-announce`. |
| `keep-dev-announce` | Developer Announcements | Distribution group (`groupType` 8). |
| `keep-observers` | Observers | |

Usernames and groups don't give tenant roles by themselves. Global gives every
eligible person edit access. Other tenant roles come from Keep grants to users or
groups. A grant to `keep-developers` reaches `local-editor`, `local-hebrew` and
`local-distribution`; `local-disabled` stays ineligible. Start doesn't create the
`Local Demo` tenant. The S1 smoke journey creates it with admin, edit and view
grants to `keep-administrators`, `keep-developers` and `keep-observers` when it
is missing.

## Add or change a test user

`credentials.json` feeds OpenLDAP: passwords and names. `adapi-directory.json`
feeds the mock ADAPI: people, groups, membership and the disabled flag. Start
writes `adapi-directory.json` only when it is missing, so after that you edit
both files.

1. Generate a password locally, for example with
   `.venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(24))"`.
2. In `.local/identity/credentials.json`, add the user under `users`:

   ```json
   "local-tester": {"password": "GENERATED_PASSWORD", "label": "Local Tester", "groups": []}
   ```

   A username is 1–64 characters, lowercase, with no `@`, control characters or
   any of `" / \ [ ] : ; | = , + * ? < >`.
3. In `.local/identity/adapi-directory.json`, add the person to `users` and the
   name to a group's `members` (a member is a user or another group):

   ```json
   {"sAMAccountName": "local-tester", "displayName": "Local Tester"}
   ```

   Add `"disabled": true` for a disabled account (514).
4. Run `.\scripts\local-identity.ps1 -Action Refresh`. It seeds OpenLDAP again,
   loads `adapi-directory.json` into the mock and runs one directory refresh.

The new user can then sign in; sign-in saves a person Keep doesn't know yet. An
administrator can also pick the person in the access picker, which saves them on
pick. Seeding adds or updates OpenLDAP entries and never deletes one.

To move someone between groups or disable them, edit `adapi-directory.json` and
run Refresh. The refresh rereads what Keep tracks: the super-admin group, granted
groups and users, users with open sessions, assignees and pending saves. A
disabled person keeps access until that refresh, which ends their sessions, or
until their session ends.

## Mock ADAPI behavior and test hooks

The mock copies the routes and behavior described in
[identity configuration 6](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-identity-configuration.md#6-ldap-bind-adapi-directory-and-stable-identity):
`/users` and `/groups` with OR'ed parameters, `/groups/members/user` with
recursive members, the silent 1,000-row search cap and non-200 errors. Every
route needs the `ClientId` header, which is `adapi_client_id` in
`credentials.json`; a missing or wrong value answers 401. Each request writes one
JSON log line with no ClientId, parameter values or usernames to
`.local/identity/Adapi.out.log`.

On loopback the mock also serves test hooks, which don't need the ClientId:

- `GET /__log` lists recent requests and a count per route (`users`, `groups`,
  `members`).
- `POST /__set` takes a JSON object with any of these keys:

| Key | Effect |
|---|---|
| `fail`, `clear_failures` | `[{"route": "users", "status": 503, "name": "local-admin", "times": 1}]` makes matching requests fail; `name` and `times` are optional. |
| `latency` | `{"users": 2.5}` delays a route; `"*"` delays all of them. |
| `pause`, `release`, `release_all` | Hold matching requests until released. |
| `disable_user`, `enable_user`, `remove_user`, `remove_group` | Lists of names. |
| `add_user`, `add_group` | Lists of entries in the `adapi-directory.json` form. |
| `add_member`, `remove_member` | `[{"group": "keep-observers", "member": "local-viewer"}]`. |
| `rename` | `[{"name": "local-viewer", "displayName": "New Name"}]`. |
| `search_cap`, `data`, `reset_log` | Change the cap, replace all data, clear the log. |

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:58092/__set -ContentType application/json `
  -Body '{"remove_member": [{"group": "keep-observers", "member": "local-viewer"}]}'
```

Hook changes live in memory. Refresh and a restart load `adapi-directory.json`
again, which undoes data changes but keeps failure, latency and pause rules until
you clear them. To publish a hook change without reloading the file, run only the
refresh, with the environment loaded:

```powershell
(Get-Content .local/identity/app-environment.json -Raw | ConvertFrom-Json).PSObject.Properties |
  ForEach-Object { [Environment]::SetEnvironmentVariable($_.Name, [string]$_.Value, 'Process') }
$env:KEEP_DATABASE_URL = $env:KEEP_LOCAL_WORKER_DATABASE_URL
.venv/Scripts/python.exe -m keep.worker directory --once
```

The refresh prints `directory_root_failed root=<name> reason=<code>` for each
root it couldn't read, then
`directory_refresh units=N published=N failed_roots=N bootstrap_ready=true|false`.
It exits non-zero when any root failed; rerun it after the cause is fixed.

## Files in `.local/identity/`

The folder is ignored by Git. Keep it with the Docker volumes; a new file doesn't
change a password already stored in PostgreSQL.

| File | Contents |
|---|---|
| `credentials.json` | User passwords, labels and groups; the OpenLDAP admin password; the ADAPI ClientId; the API and worker database passwords; the wrapping key; the deployment epoch; the remap table. Never regenerated once present. |
| `openldap-admin-password.txt` | The Compose secret for the OpenLDAP admin. Changing it needs a container recreate. |
| `adapi-directory.json` | The mock's people and groups. No passwords. Written only when missing. |
| `tls/localhost.pem`, `tls/localhost-key.pem` | The local certificate and key, also used by the frontend and the Grafana panels proxy. |
| `app-environment.json` | Settings generated by `scripts/local_identity.py environment`. |
| `processes.json`, `*.out.log`, `*.err.log` | Launcher process records and logs. |

The generated environment points the API and the refresh at the local services:
`KEEP_LDAP_HOST=127.0.0.1`, `KEEP_LDAP_PORT=58389`, `KEEP_LDAP_USE_TLS=false`,
`KEEP_LDAP_BIND_FORMAT=uid={username},ou=people,dc=keep,dc=local`,
`KEEP_ADAPI_URL=http://127.0.0.1:58092`, `KEEP_ADAPI_CLIENT_ID` and
`KEEP_SUPERADMIN_GROUP=keep-administrators`. OpenLDAP doesn't accept
`user@domain`, so the bind format is a DN, and WhoAmI answers in the `dn:` form.

OpenLDAP keeps no volume. Recreating its container empties it, and the next Start
or Refresh seeds it again.

The steps behind the launcher are `scripts/local_identity.py` actions:
`prepare`, `tls`, `database`, `remap [--dry-run]`, `environment`, `wait`,
`configure` (seed OpenLDAP) and `reload` (load the mock's data file).

## Moving from the earlier local setup

Keycloak was removed by REV-73. On the first Start after REV-73, `prepare` copies these values from the earlier `.local/keycloak/credentials.json` into `.local/identity/` unchanged: the wrapping key, the deployment epoch, the API and worker database passwords, and the passwords and labels of `local-admin`, `local-editor` and `local-viewer`. It also copies the TLS files and process records, and leaves `.local/keycloak/` in place as a backup (Keycloak removed by REV-73).

The copy stops with nothing written when the wrapping key or the epoch is missing. Restore the old credentials file from a backup first.

Start then runs `remap` after migration 0070 and before the first ADAPI refresh. It moves each principal of the old source `keycloak-local` (removed by REV-73) to its `adapi` username with an UPDATE, so ids, grants, memberships, assignments and history stay with the same person or group.

Groups map by label: Keep Administrators → `keep-administrators`, Developers →
`keep-developers`, Observers → `keep-observers`. Check the plan first:

```powershell
.venv/Scripts/python.exe scripts/local_identity.py remap --dry-run
```

The dry run prints every planned id → name pair and changes nothing. Once done,
remap prints that there is nothing to remap. It refuses, with nothing changed,
when:

- migration 0070 is missing;
- a group label or a user has no mapping;
- a mapped name is missing from `adapi-directory.json`;
- two ids map to one name, or a kind doesn't match;
- an `adapi` principal already exists for a mapped name.

The last case means a refresh ran before the remap. Restore the database backup
taken before that refresh, then start again.

A principal outside the mapping, such as a user or group added by hand to the earlier setup (Keycloak removed by REV-73), is reported as left unmapped and keeps its old source. Add it to both files and remap it by hand if you still need it.

Start stops containers left by the Keycloak setup removed by REV-73 (the `keycloak` service of `keep-local-stack` and the `keep-local-identity` project). It never deletes them or their volumes; deleting them and `.local/keycloak/` is the user's decision.

## Browser suite and limits

The opt-in local identity browser suite runs against this stack. After Start,
from `keep-frontend`:

```powershell
$env:KEEP_LOCAL_IDENTITY_TEST='1'
node node_modules/@playwright/test/cli.js test --config playwright.local-identity.config.ts
```

It reads passwords from `.local/identity/credentials.json` without printing them,
and keeps traces, screenshots and video off. Run it serially, with no other
identity test changing the same local directory.

Limits:

- The mock copies the response shapes the user described; no real ADAPI sample
  exists yet. Before company use, one real call per route and one real sign-in
  confirm the shapes, the bind name format and the WhoAmI answer
  ([identity configuration 13](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-identity-configuration.md#13-operation-and-qualification)).
- OpenLDAP answers WhoAmI as `dn:`; AD answers `u:DOMAIN\name`.
- This setup doesn't complete recovery, visibility measurement, ingestion or
  company-service qualification. Executed results are in
  [implementation status](../status/implementation-status.md); other services and
  verification commands are in the [development runbook](runbooks/development.md).
