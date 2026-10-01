# Local Keycloak identities

**2026-09-30 directory scope (REV-35):** the directory worker is nice-to-have and is not started by current launchers. Explicit trusted publication remains required for bootstrap and identity changes; automatic five-minute LDAP propagation is deferred. See [current worker scope](https://github.com/appchi-alert-operations/appchi-design/blob/main/specs/spec-worker-scope.md#4-directory-synchronization-is-nice-to-have). Historical runtime evidence below does not describe a rollout of this later change.

The local development environment uses a real Keycloak realm with custom users
and nested groups, so sign-in does not need access to company LDAP. It preserves
the application's ordinary OIDC username/password login, sessions and permission checks.
Production LDAP configuration is unchanged.

This workspace runs Keycloak 26.7.4 in the pinned Docker image from
[Keycloak](https://www.keycloak.org/downloads). It is part of the unified local
Compose project and publishes only `127.0.0.1:58443`; its development database
persists in a Docker volume. Docker Desktop and the installed backend/frontend
dependencies are prerequisites.

## Start and sign in

From the repository root in PowerShell:

```powershell
.\scripts\local-identity.ps1 -Action Start
```

The application origin is `https://localhost:3443`; the isolated Keycloak issuer
is `https://localhost:58443/realms/appchi-local`. Start launches the unified
Compose stack and manages the Product API on loopback port 58000, the HTTPS UI
and performs one trusted identity publication before API/UI startup. It does not
start a directory polling worker. It uses the dedicated `appchi_local` database
on the Compose PostgreSQL instance at port 55433. Local generated credentials are
stored in `.local/keycloak/credentials.json`, outside version control. Read that
file locally for the credentials instead of copying passwords into issue reports
or terminal logs. Appchi sign-in needs only a username and password; no one-time
code or authenticator enrollment is required, including for `local-admin`.

Use the normal application sign-in page. The application receives a real signed
OIDC identity and creates its normal secure session cookie; it does not inject a
test session. Native Keycloak user IDs become the immutable `keycloak-local`
directory identities. Usernames remain labels.

Sign-in links expire after ten minutes and can be used only once. If a tab shows
an expired sign-in attempt, choose **Sign in again** to start a new one. For an old
raw callback error page, open `https://localhost:3443/auth/login` directly instead
of refreshing the callback URL.

The generated certificate is trusted explicitly by Appchi and Keycloak. The
launcher does not change Windows trust automatically, so a fresh workstation
may show a certificate warning. Browser acceptance tests use a test-only TLS
exception. They do not disable certificate validation in the application.

On 2026-09-23 the user explicitly approved trusting this workstation's generated
certificate in `CurrentUser\Root`. Its thumbprint is
`3FFC616039134C802F5AAECEC737B91CEBF27D80`. Normal Windows HTTPS checks returned
200 for both services, and the Codex browser displayed Appchi and the Keycloak
sign-in page without a certificate exception. Reopen a previously failed tab
after installing trust. To remove this specific trust entry when retiring the
local environment, run:

```powershell
Remove-Item -LiteralPath 'Cert:\CurrentUser\Root\3FFC616039134C802F5AAECEC737B91CEBF27D80'
```

The launcher preserves generated credentials and checks API/UI startup before
reporting success. It runs the API and the one-off trusted identity publication with separate
database login roles rather than the migration identity. Repeating Start reuses the
managed processes. Status is available with `-Action Status`. Keep the local
credential files and Keycloak volume together when retaining this environment.

To upgrade an existing password/TOTP installation, run Start with `-Restart`.
The configuration step disables the Appchi realm's OTP execution and enrollment
action, clears pending OTP enrollment from existing users, and preserves users,
passwords, groups and enrolled credentials. It records the completed upgrade and
updates the saved import; the restarted API requires only protected `pwd`
authentication evidence. Older installations retain their historical flow alias.
The separate `master` realm's console MFA policy is unchanged.

Stop the locally managed processes with:

```powershell
.\scripts\local-identity.ps1 -Action Stop
```

## Appchi login theme

The approved form is in
[`deploy/local/keycloak/themes/appchi`](https://github.com/appchi-alert-operations/appchi-frontend).
Compose mounts that directory read-only into Keycloak. Fresh realm imports select
it through the `appchi-web` client's `login_theme` attribute; the normal Start
configuration step upgrades existing local clients and their saved imports while
preserving credentials, groups, client settings and console administration.

To apply the theme to an already-running local installation without restarting
the application processes:

```powershell
docker compose -p appchi-local-stack -f deploy/local/compose.yaml up -d --no-deps keycloak
.venv/Scripts/python.exe scripts/local_identity.py wait
.venv/Scripts/python.exe scripts/local_identity.py configure
```

Open `https://localhost:3443/` while signed out, or `/auth/login` to start an
explicit sign-in. The username/password form is hosted by Keycloak and posts to
its generated action URL. OIDC code flow, PKCE, the one-use callback and the
server-side Appchi session remain in use. Other Keycloak authentication pages
inherit the installed parent theme. The separate console uses its existing UI.

The form includes password visibility, actual provider errors and expandable
company-support guidance. Light is the default; the appearance choice persists
under `appchi-theme` on the Keycloak origin, separately from the application's
origin. Form fields are never copied to browser storage. Geist is served locally
with its bundled SIL Open Font License; no external font or script host is needed.

On Keycloak upgrades, review the overridden form against the installed
`theme/base/login/login.ftl` and session scripts. The current implementation is
based on 26.7.4 and uses its `authChecker.js` for authentication-session checks.
Theme inheritance and installation follow the
[official theme guide](https://www.keycloak.org/ui-customization/themes).

## Users, teams and permissions

Manage custom users and groups in the local Keycloak administration console at
`https://localhost:58443/admin/`. Use the separate local Keycloak administrator
credentials from the generated file. A Keycloak console administrator is not
automatically an Appchi global administrator.

The console account is `bootstrap-admin`; it requires TOTP. In this workspace
the console factor has been enrolled and its local development seed is in the
credential file. Get its current code with:

```powershell
.venv/Scripts/python.exe scripts/local_identity.py otp bootstrap-admin
```

On a fresh setup the console instead prompts for authenticator enrollment.
Each code is single-use: wait for the next 30-second code before another login.

The initial development accounts are `local-admin` (Local Administrator),
`local-editor` (Local Editor), and `local-viewer` (Local Viewer). Their groups
are `Appchi Administrators`, `Developers` and `Observers`. Credentials contain
generated passwords. Older credential files may retain unused application TOTP
seeds; the OTP helper is only for the separate Keycloak console administrator.
Usernames do not assign application roles by themselves: Global retains its
normal edit baseline for every eligible human, and specific-tenant view/edit
roles come from Appchi grants to those users or groups.

The live acceptance run created `Local Demo`, with admin/edit/view grants to
the three corresponding groups. This tenant is created by the acceptance test,
not automatically by Start on a fresh database.

Create users in realm `appchi-local`, set their credentials, and add them to
groups as needed. Parent-group grants include eligible people in nested groups.
The deployment configuration names the global-super-admin group's immutable
UUID. Membership there grants Appchi global administration; normal tenant roles
remain Appchi grants selected in its administration screens.

Automatic directory polling is nice-to-have under REV-35. Explicit refresh reads complete bounded
pages of users, groups, subgroups and members, then publishes through the same
PostgreSQL authorization guard used by the LDAP worker. Disabled users become
ineligible. Deleting and recreating a username creates a different principal and
does not transfer old grants. A failed refresh retains the last complete trusted
state; positively observed disablements still apply.

After a change, run an explicit publication before expecting the application's
directory picker or group permissions to reflect it:

```powershell
.\scripts\local-identity.ps1 -Action Refresh
```

Refresh performs one verified publication and starts no worker. A user with
no verified directory binding cannot sign in until that binding is published.
Do not change the directory source or global group's UUID merely to repair a
login; correct the realm membership or configuration instead.

## Explicit directory publication configuration

The development-only publication helper uses a separate `appchi-directory` confidential
client with a service account. Its realm-management permissions are read-only:
`view-users`, `query-users` and `query-groups`. The browser client `appchi-web`
does not use service-account credentials.

Its JSON document has these fields; the allocation values below are illustrative
local limits and must fit the local realm size:

```json
{
  "issuer": "https://localhost:58443/realms/appchi-local",
  "source": "keycloak-local",
  "client_id": "appchi-directory",
  "client_secret_env": "APPCHI_LOCAL_DIRECTORY_SECRET",
  "ca_file": "PATH_TO_LOCAL_CA_PEM",
  "superadmin_group": "ACTUAL_NATIVE_GROUP_UUID",
  "max_objects": 1000,
  "page_size": 100,
  "max_pages": 500,
  "local_only": true
}
```

The publication helper requires explicit `APPCHI_ENVIRONMENT=development`, the named secret
environment variable, and the normal Appchi database/wrapping-key/deployment
settings. It rejects non-loopback issuers and verifies TLS using the configured
CA. It does not follow redirects or use a proxy from ambient environment values.

For a one-off cycle after the launcher has supplied those environment bindings:

```powershell
.venv/Scripts/python.exe -m appchi.identity.local_keycloak_worker --config PATH --once
```

Initial setup may also pass `--bootstrap`. This invokes the existing guarded
publication path: only an initial `directory_bootstrap_required` gate can open,
and only with complete evidence and an eligible global administrator. Repeating
this option cannot lift a restore/reconciliation gate. Without `--once`, the
worker continues polling.

## Verification and limits

The adapter has 21 passing controlled-HTTP unit tests. A focused real PostgreSQL
test with controlled Keycloak HTTP passed initial bootstrap, unchanged refresh,
outage preservation, disablement during an incomplete refresh, re-enable,
restore-gate preservation, deletion tombstones and recreated-name isolation.
These tests do not themselves verify a running Keycloak or browser login.

The additional opt-in browser suite uses the actual Keycloak, HTTPS Next.js,
FastAPI and PostgreSQL services. It exercises all three username/password logins,
group-based tenant roles, live group removal/restoration, and signed
provider-initiated logout. Run it after Start from `frontend`:

```powershell
$env:APPCHI_LOCAL_IDENTITY_TEST='1'
node node_modules/@playwright/test/cli.js test --config playwright.local-identity.config.ts
```

The provider-logout test requires the enrolled console factor in the local
credential file; it skips that test on a fresh setup without that factor.
Passwords/tokens are not printed, and traces/screenshots are disabled for these
authentication tests. A dedicated callback uses
`https://host.docker.internal:3443/auth/backchannel-logout`; the certificate
covers this name and Keycloak explicitly trusts its PEM. This keeps the host
listeners on loopback while allowing Docker Desktop to deliver signed logout.

Historical result on 2026-09-23, before the password-only change: **5 passed in 59.8 seconds**, including actual
signed backchannel logout and removal/restoration of the Observers group grant.

On 2026-09-24, after removing the application OTP step: **5 passed in 40.6
seconds** against the running local stack. All three existing users signed in
with only username/password, group roles remained correct, and signed logout
still revoked the session. Backend password-policy and fresh-setup checks:
**31 passed** across [OIDC validation](../qualification/pytest-password-login.xml)
and [local setup](../qualification/pytest-password-setup.xml). TypeScript,
changed-file Prettier and Python Ruff checks passed.

This local identity setup does not complete unfinished Appchi recovery,
visibility measurement, ingestion or external-service qualification work. See
[implementation status](../status/implementation-status.md) for the broader release
status and [development instructions](runbooks/development.md) for other
services and verification commands.
