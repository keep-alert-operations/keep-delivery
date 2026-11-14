# Local identities (Authelia + OpenLDAP)

**2026-09-30 directory scope (REV-35):** the directory worker is nice-to-have and is not started by current launchers. Explicit trusted publication remains required for bootstrap and identity changes; automatic five-minute LDAP propagation is deferred. See [current worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md#4-directory-synchronization-is-nice-to-have). Historical runtime evidence does not describe a rollout of this later change.

The local development environment runs a small **OpenLDAP** directory and **Authelia**
as the OIDC provider, so sign-in does not need company LDAP. The dev directory is
LDAP in every environment: Authelia authenticates users against OpenLDAP, and Keep's
own LDAP directory worker reads the **same** OpenLDAP. Dev therefore exercises the
identical production directory code path (`keep.identity.ldap` / `keep.identity.worker`).
Production LDAP configuration is unchanged; only the dev directory host differs.

OpenLDAP generates an immutable `entryUUID` for every entry. Authelia emits that raw
value verbatim as a protected custom claim (`entryuuid`) in a dedicated
`keep-directory` scope, and the directory worker keys its principals by the same
`entryUUID`. The two always agree, so a signed-in user and their published directory
principal are the same identity. Usernames remain labels only.

Both services are part of the unified local Compose project. OpenLDAP publishes LDAPS
only on `127.0.0.1:1636` (for Keep's worker); Authelia publishes HTTPS only on
`127.0.0.1:58443`. Authelia reaches OpenLDAP over the private Compose network.
Docker Desktop and the installed backend/frontend dependencies are prerequisites.

## Start and sign in

From the repository root in PowerShell:

```powershell
.\scripts\local-identity.ps1 -Action Start
```

The application origin is `https://localhost:3443`; the Authelia OIDC issuer is
`https://localhost:58443` with discovery at
`https://localhost:58443/.well-known/openid-configuration`. Start launches the
unified Compose stack, waits for Authelia's HTTPS discovery, resolves the super-admin
group's `entryUUID` from OpenLDAP, performs **one** trusted directory publication
through the production LDAP worker, and then manages the Product API on loopback port
58000, the Ingestion API on 58001 and the HTTPS UI on 3443. It does not start a
directory polling worker. It uses the dedicated `keep_local` database on the Compose
PostgreSQL instance at port 55433.

Local generated credentials are stored in `.local/identity/credentials.json`, outside
version control. Read that file locally instead of copying passwords into issue
reports or terminal logs. Keep sign-in needs only a username and password; no one-time
code or authenticator enrollment is required, including for `local-admin` — Authelia
is configured for a single password factor (`one_factor`) and emits `amr: ["pwd"]`.

Use the normal application sign-in page. The application receives a real signed OIDC
identity (authorization-code flow with PKCE S256) and creates its normal secure
session cookie; it does not inject a test session. The immutable OpenLDAP `entryUUID`
values become the `ldap-local` directory identities.

Sign-in links expire after ten minutes and can be used only once. If a tab shows an
expired sign-in attempt, choose **Sign in again** to start a new one.

The generated certificate is trusted explicitly by Keep, Authelia (its HTTPS
listener) and OpenLDAP (its LDAPS listener). The launcher does not change Windows
trust automatically, so a fresh workstation may show a certificate warning. Browser
acceptance tests use a test-only TLS exception; they do not disable certificate
validation in the application. The same self-signed `localhost` certificate serves
all three listeners (SAN covers `localhost`, `host.docker.internal` and `127.0.0.1`).

The launcher preserves generated credentials and checks API/UI startup before
reporting success. It runs the API and the one-off directory publication with separate
database login roles rather than the migration identity. Repeating Start reuses the
managed processes. Status is available with `-Action Status`. Keep the local credential
files and the OpenLDAP/Authelia volumes together when retaining this environment.

Stop the locally managed processes with:

```powershell
.\scripts\local-identity.ps1 -Action Stop
```

## Users, teams and permissions

The OpenLDAP tree is seeded offline from `.local/identity/openldap/seed.ldif` under
base DN `dc=keep,dc=local`. The development accounts are:

| Username | Label | Primary group |
| --- | --- | --- |
| `local-admin` | Local Administrator | `Keep Administrators` |
| `local-editor` | Local Editor | `Developers` |
| `local-viewer` | Local Viewer | `Observers` |

Groups are `groupOfNames` entries under `ou=groups`. `Keep Administrators` is the
super-admin group and additionally contains a **nested** group, `Keep Automation`, so
the directory worker's nested-member traversal stays exercised. `KEEP_SUPERADMIN_GROUP`
is that group's immutable `entryUUID`, resolved from OpenLDAP after the seed loads (it
is server-generated and cannot be set from the LDIF). Membership in the super-admin
group grants Keep global administration.

Usernames do not assign application roles by themselves: Global retains its normal
edit baseline for every eligible human, and specific-tenant view/edit roles come from
Keep grants to those users or groups. Parent-group grants include eligible people in
nested groups. A user whose `employeeType` is not `active` is treated as ineligible.

The live acceptance run creates `Local Demo`, with admin/edit/view grants to the three
corresponding groups. This tenant is created by the acceptance test, not automatically
by Start on a fresh database.

To change identities, edit `seed.ldif` (and re-seed the OpenLDAP volume) or manage the
directory with any LDAP client bound as `cn=admin,dc=keep,dc=local` using the password
in `.local/identity/credentials.json`. Do not change the directory source or the
super-admin group id merely to repair a login; correct the directory membership or
configuration instead.

## Explicit directory publication

Automatic directory polling is nice-to-have under REV-35. Explicit publication reads
complete bounded pages of users, groups, subgroups and members over LDAPS, then
publishes through the same PostgreSQL authorization guard used in production. Disabled
users become ineligible. Deleting and recreating a username creates a different
principal (a new `entryUUID`) and does not transfer old grants. A failed refresh
retains the last complete trusted state; positively observed disablements still apply.

After a directory change, run one explicit publication before expecting the
application's directory picker or group permissions to reflect it:

```powershell
.\scripts\local-identity.ps1 -Action Refresh
```

Refresh performs one verified publication **through the production LDAP worker** and
starts no worker. Under the hood it runs:

```powershell
.venv/Scripts/python.exe scripts/local_identity.py publish
```

Initial setup passes `--bootstrap` (Start does this automatically). Bootstrap invokes
the existing guarded publication path: only an initial `directory_bootstrap_required`
gate can open, and only with complete evidence and an eligible global administrator.
Repeating `--bootstrap` cannot lift a restore/reconciliation gate.

A user with no verified directory binding cannot sign in until that binding is
published.

## Directory configuration

The directory worker reads `.local/identity/directory.json`, a sanitized LDAP
configuration with no embedded password (the bind password arrives only through the
named `KEEP_LOCAL_DIRECTORY_SECRET` environment variable). Its schema maps the
immutable `entryUUID` and rejects any inferred vendor/company schema:

```json
{
  "source": "ldap-local",
  "hostname": "localhost",
  "port": 1636,
  "bind_dn": "cn=admin,dc=keep,dc=local",
  "password_env": "KEEP_LOCAL_DIRECTORY_SECRET",
  "ca_file": "PATH_TO_LOCAL_CA_PEM",
  "page_size": 100,
  "max_pages": 500,
  "max_objects": 1000,
  "schema": {
    "base_dn": "dc=keep,dc=local",
    "user_filter": "(objectClass=inetOrgPerson)",
    "team_filter": "(objectClass=groupOfNames)",
    "id_attribute": "entryUUID",
    "label_attribute": "cn",
    "member_attribute": "member",
    "eligibility_attribute": "employeeType",
    "eligibility_values": ["active"],
    "id_encoding": "uuid",
    "schema_qualified": true
  }
}
```

The publication helper requires explicit `KEEP_ENVIRONMENT=development`, the named
secret environment variable, and the normal Keep database/wrapping-key/deployment
settings. It uses TLS-verified LDAPS, binds read-only, and applies bounded paged reads.

## Verification and limits

The LDAP adapter and the publication/guard path are covered by the backend's own unit
and integration tests in `keep-domain` (`keep.identity.ldap`, `keep.identity.worker`,
`keep.identity.publication`). The delivery repo's `tests/unit/test_local_identity_setup.py`
checks that a fresh setup seeds three LDAP users, a nested super-admin group, an
Authelia OIDC client with PKCE S256, and the `entryuuid` claims policy/scope, and that
the generated environment omits the now-optional `KEEP_OIDC_REQUIRED_ACR` while keeping
`amr=pwd` mandatory.

Items that require a live stack (so they are verified on your private network, not
offline):

- Authelia must emit the `sid` claim and `amr: ["pwd"]` in the ID token; the backend
  requires both. Confirm against your Authelia version.
- The cookie `domain: localhost` and the `post_logout_redirect_uris` client field are
  version-sensitive in Authelia; verify them when first bringing the stack up.
- Container images are pinned by digest in `deploy/local/compose.yaml`. The OpenLDAP
  and Authelia digests are `sha256:PLACEHOLDER-set-at-mirror-time` until a human fills
  the real mirrored digests.

This local identity setup does not complete unfinished Keep recovery, visibility
measurement, ingestion or external-service qualification work. See
[implementation status](../status/implementation-status.md) for the broader release
status and [development instructions](runbooks/development.md) for other services and
verification commands.
