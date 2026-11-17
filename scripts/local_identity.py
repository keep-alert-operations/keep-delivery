"""Prepare local OpenLDAP and mock ADAPI identities (REV-73); secrets stay in ignored files.

Local sign-in binds against OpenLDAP and reads people and groups from the mock ADAPI
(``scripts/local_adapi.py``). Everything private lives in ``.local/identity/``:
``credentials.json`` (user passwords, the OpenLDAP admin password, the ADAPI ClientId,
database role passwords, the wrapping key and the deployment epoch), ``tls/``,
``adapi-directory.json`` (the mock's data) and ``app-environment.json``.

An older setup kept these under ``.local/keycloak/``. ``prepare`` copies their values
once, unchanged, and leaves that folder in place; it never regenerates a key, password
or epoch that already exists. ``remap`` then moves the old local principals to their
ADAPI keys by UPDATE, so every grant, assignment and history row keeps its principal.
"""

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/identity"
ORIGIN = "https://localhost:3443"
LDAP_HOST = "127.0.0.1"
LDAP_PORT = 58389
BASE_DN = "dc=keep,dc=local"
PEOPLE_DN = "ou=people," + BASE_DN
ADMIN_DN = "cn=admin," + BASE_DN
BIND_FORMAT = "uid={username}," + PEOPLE_DN
ADAPI_URL = "http://127.0.0.1:58092"
SOURCE = "adapi"
SUPERADMIN_GROUP = "keep-administrators"
# The mock's groups: keep-developers reaches keep-dev-announce, a distribution group.
GROUPS = (
    {"sAMAccountName": SUPERADMIN_GROUP, "cn": "Keep Administrators",
     "displayName": "Keep Administrators"},
    {"sAMAccountName": "keep-developers", "cn": "Developers", "displayName": "Developers",
     "nested": ("keep-dev-announce",)},
    {"sAMAccountName": "keep-dev-announce", "cn": "Developer Announcements",
     "distribution": True},
    {"sAMAccountName": "keep-observers", "cn": "Observers", "displayName": "Observers"},
)
# username, label, direct groups. REV-76: Keep doesn't handle disabled people, so there
# is no disabled fixture user; an earlier setup's local-disabled stays in .local/identity
# as an ordinary person until it is removed by hand.
USERS = (
    ("local-admin", "Local Administrator", (SUPERADMIN_GROUP,)),
    ("local-editor", "Local Editor", ("keep-developers",)),
    ("local-viewer", "Local Viewer", ("keep-observers",)),
    ("local-hebrew", "משתמש Local Hebrew", ("keep-developers",)),
    ("local-distribution", "Local Distribution", ("keep-dev-announce",)),
)
# One-time move from the earlier local identity provider (REV-73). Its principals used
# source "keycloak-local" and UUID object ids; its groups are keyed by their real labels.
LEGACY_SOURCE = "keycloak-local"
LEGACY_GROUPS = {
    "Keep Administrators": SUPERADMIN_GROUP,
    "Developers": "keep-developers",
    "Observers": "keep-observers",
}
# Values that must survive the move unchanged; a missing one is restored, never invented.
PRESERVED = ("wrapping_key", "deployment_epoch")
COPIED = ("api_password", "worker_password")


class RemapError(RuntimeError):
    """The remap refused before changing anything."""


def legacy_state():
    return ROOT / ".local/keycloak"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    """Write JSON atomically, so an interrupted run never leaves a half-written secret."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def credentials():
    return read(STATE / "credentials.json")


def tls():
    """Issue only this application's certificate; never change OS/browser trust."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    (STATE / "tls").mkdir(parents=True, exist_ok=True)
    certificate_path = STATE / "tls/localhost.pem"
    key_path = STATE / "tls/localhost-key.pem"
    now = datetime.now(UTC)
    if certificate_path.exists() and key_path.exists():
        existing = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        names = existing.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        if (
            {"localhost", "host.docker.internal"}.issubset(
                names.get_values_for_type(x509.DNSName)
            )
            and ipaddress.ip_address("127.0.0.1")
            in names.get_values_for_type(x509.IPAddress)
            and existing.not_valid_before_utc <= now < existing.not_valid_after_utc
        ):
            print("Existing local TLS certificate preserved.")
            return
    key = (
        serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if key_path.exists()
        else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Keep localhost development")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.DNSName("host.docker.internal"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certificate_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    print("Issued local TLS certificate; existing private key preserved when present.")


def generated_password(username):
    if username == "local-hebrew":
        # A mixed Hebrew/English password exercises the UTF-8 bind (REV-73).
        return "שלום-Keep-" + secrets.token_urlsafe(12)
    return secrets.token_urlsafe(24)


def moved_credentials(legacy):
    """The earlier setup's values, copied unchanged: keys, epoch, role and user passwords."""
    missing = [field for field in PRESERVED if not legacy.get(field)]
    if missing:
        raise RuntimeError(
            "The earlier local credentials lack " + ", ".join(missing)
            + "; restore .local/keycloak/credentials.json from a backup before preparing"
        )
    creds = {field: legacy[field] for field in PRESERVED}
    creds.update({field: legacy[field] for field in COPIED if legacy.get(field)})
    users = {}
    for username, user in legacy.get("users", {}).items():
        group = LEGACY_GROUPS.get(user.get("group"))
        users[username] = {
            "password": user["password"],
            "label": user.get("label") or username,
            "groups": [group] if group else [],
        }
    creds["users"] = users
    creds["remap"] = {
        "source": LEGACY_SOURCE,
        "users": {name: user["id"] for name, user in legacy.get("users", {}).items()
                  if user.get("id")},
        "groups": dict(legacy.get("groups", {})),
    }
    return creds


def complete(creds):
    """Add only values that never existed; existing ones are never replaced."""
    users = creds.setdefault("users", {})
    for username, label, groups in USERS:
        if username not in users:
            users[username] = {
                "password": generated_password(username),
                "label": label,
                "groups": list(groups),
            }
    creds.setdefault("ldap_admin_password", secrets.token_urlsafe(32))
    creds.setdefault("adapi_client_id", secrets.token_urlsafe(32))
    return creds


def directory_data(creds):
    """The mock ADAPI's people and groups, built from the local users."""
    members = {group["sAMAccountName"]: [] for group in GROUPS}
    for username, user in creds["users"].items():
        for group in user.get("groups", []):
            members.setdefault(group, []).append(username)
    groups = []
    for group in GROUPS:
        name = group["sAMAccountName"]
        entry = {key: value for key, value in group.items() if key != "nested"}
        entry["members"] = members.pop(name) + list(group.get("nested", ()))
        groups.append(entry)
    groups.extend({"sAMAccountName": name, "members": names} for name, names in members.items())
    users = [
        {"sAMAccountName": username, "displayName": user["label"]}
        for username, user in creds["users"].items()
    ]
    return {"users": users, "groups": groups}


def prepare():
    STATE.mkdir(parents=True, exist_ok=True)
    postgres_password = ROOT / ".local/postgres-password.txt"
    if not postgres_password.exists():
        postgres_password.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
    path = STATE / "credentials.json"
    legacy = legacy_state()
    if path.exists():
        creds = read(path)
        missing = [field for field in PRESERVED if not creds.get(field)]
        if missing:
            raise RuntimeError(
                "Local credentials lack " + ", ".join(missing)
                + "; restore .local/identity/credentials.json from a backup"
            )
        outcome = "Existing local credentials preserved."
    elif (legacy / "credentials.json").exists():
        creds = moved_credentials(read(legacy / "credentials.json"))
        for name in ("localhost.pem", "localhost-key.pem"):
            source, target = legacy / "tls" / name, STATE / "tls" / name
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        processes = legacy / "processes.json"
        if processes.exists() and not (STATE / "processes.json").exists():
            # Launcher records let Start/Stop find processes an earlier Start owned.
            shutil.copy2(processes, STATE / "processes.json")
        outcome = (
            "Moved local credentials, keys and TLS from .local/keycloak to .local/identity"
            " with their values unchanged; .local/keycloak was left in place."
        )
    else:
        creds = {
            "wrapping_key": base64.b64encode(secrets.token_bytes(32)).decode(),
            "deployment_epoch": str(uuid.uuid4()),
        }
        outcome = "Prepared private local credentials."
    complete(creds)
    tls()
    secret = STATE / "openldap-admin-password.txt"
    if not secret.exists() or secret.read_text(encoding="utf-8") != creds["ldap_admin_password"]:
        secret.write_text(creds["ldap_admin_password"], encoding="utf-8")
    directory = STATE / "adapi-directory.json"
    if not directory.exists():
        save(directory, directory_data(creds))
    # Credentials last: an interrupted move simply runs again from the earlier folder.
    save(path, creds)
    print(outcome)


def admin_url():
    return os.environ.get(
        "KEEP_LOCAL_DATABASE_ADMIN_URL",
        "postgresql://keep_migrator:"
        + quote((ROOT / ".local/postgres-password.txt").read_text().strip(), safe="")
        + "@127.0.0.1:55433/postgres?connect_timeout=5",
    )


def environment():
    creds = credentials()
    capacity_profile = ROOT / "qualification/development-capacity.json"
    operator_path = ROOT / ".local/operators/credentials.json"
    operator_values = {}
    operator_credentials = (
        json.loads(operator_path.read_text(encoding="utf-8")) if operator_path.exists() else {}
    )
    # A fresh workspace has prepared operator credentials but no Grafana token until
    # local_v2.py configure runs, which itself needs this environment file first.
    if "grafana_token" in operator_credentials:
        operator_values = {
            "KEEP_OPERATOR_BINDING": str(ROOT / ".local/operators/binding.json"),
            "KEEP_OPERATOR_DATABASE_URL": os.environ.get("KEEP_LOCAL_WORKER_URL",
                f"postgresql://keep_local_worker:{creds['worker_password']}@127.0.0.1:55433/keep_local?connect_timeout=5"),
            "KEEP_OPERATOR_GRAFANA_TOKEN": operator_credentials["grafana_token"],
            "KEEP_OPERATOR_KONG_TOKEN": operator_credentials["kong_token"],
            "KEEP_OPERATOR_PROBE_TOKEN": operator_credentials["probe_token"],
        }
    protocol_path=ROOT / ".local/runtime/kafka-protocol.json"
    protocol=json.loads(protocol_path.read_text(encoding="utf-8")) if protocol_path.exists() else {}
    return {
        **operator_values,
        "KEEP_KAFKA_ADMISSION_ENABLED": str(protocol.get("enabled",False)).lower(),
        **({"KEEP_GLOBAL_TENANT_ID":protocol["global_tenant_id"]} if protocol.get("global_tenant_id") else {}),
        "KEEP_ENVIRONMENT": "development",
        "KEEP_DATABASE_URL": os.environ.get(
            "KEEP_LOCAL_DATABASE_URL",
            f"postgresql://keep_local_api:{creds['api_password']}@127.0.0.1:55433/keep_local?connect_timeout=5",
        ),
        "KEEP_LOCAL_WORKER_DATABASE_URL": os.environ.get(
            "KEEP_LOCAL_WORKER_URL",
            f"postgresql://keep_local_worker:{creds['worker_password']}@127.0.0.1:55433/keep_local?connect_timeout=5",
        ),
        "KEEP_WRAPPING_KEY": creds["wrapping_key"],
        "KEEP_DEPLOYMENT_EPOCH": creds["deployment_epoch"],
        "KEEP_PUBLIC_ORIGIN": ORIGIN,
        # REV-73: one LDAP bind checks the password (plain LDAP locally); people and
        # groups come from the mock ADAPI. The refresh command reads the same values.
        "KEEP_LDAP_HOST": LDAP_HOST,
        "KEEP_LDAP_PORT": str(LDAP_PORT),
        "KEEP_LDAP_USE_TLS": "false",
        "KEEP_LDAP_BIND_FORMAT": BIND_FORMAT,
        "KEEP_ADAPI_URL": ADAPI_URL,
        "KEEP_ADAPI_CLIENT_ID": creds["adapi_client_id"],
        "KEEP_SUPERADMIN_GROUP": SUPERADMIN_GROUP,
        "KEEP_ELASTICSEARCH_URL": "http://127.0.0.1:59201",
        "KEEP_ELASTICSEARCH_INDEX": "keep-local-alerts",
        "KEEP_CAPACITY_PROFILE": str(capacity_profile),
        "KEEP_KAFKA_BOOTSTRAP": "127.0.0.1:19093",
        "KEEP_REDIS_URL": "redis://127.0.0.1:56380/0",
        # Log records go to the stack's Fluent Bit, which writes them to
        # Elasticsearch, one index per service (REV-74).
        "KEEP_LOG_SHIP_URL": os.environ.get(
            "KEEP_LOCAL_LOG_SHIP_URL",
            "http://127.0.0.1:" + os.environ.get("KEEP_LOCAL_LOG_SHIP_PORT", "59880"),
        ),
        # Real user monitoring (REV-74): the local API accepts the browser's events.
        "KEEP_RUM_ENABLED": os.environ.get("KEEP_LOCAL_RUM_ENABLED", "true"),
        "KEEP_POOL_SIZE": "2",
        "KEEP_BACKEND_URL": "http://127.0.0.1:58000",
        "KEEP_TICKET_COUNT_URL": os.environ.get(
            "KEEP_LOCAL_TICKET_COUNT_URL", "http://127.0.0.1:58090/count"
        ),
        # The local Grafana through its panels proxy, and the fake Grafana (REV-68).
        "KEEP_GRAFANA_PANEL_ORIGINS": os.environ.get(
            "KEEP_LOCAL_GRAFANA_PANEL_ORIGINS",
            "https://localhost:"
            + os.environ.get("KEEP_LOCAL_GRAFANA_PANEL_PORT", "58449")
            + ",http://127.0.0.1:58091",
        ),
        # REV-70: incident ticket links must point into this ServiceNow address.
        "KEEP_SERVICENOW_URL": os.environ.get(
            "KEEP_LOCAL_SERVICENOW_URL", "https://servicenow.example"
        ),
        "PYTHONPATH": "",
    }


def database():
    import psycopg
    from keep.migrate import migrate, migration_directory
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    admin = admin_url()
    dump = ROOT / ".local/runtime/keep-local-migration.dump"
    marker = ROOT / ".local/runtime/keep-local-migration.restored.json"
    restore_existing = dump.exists() and not marker.exists()
    for attempt in range(90):
        try:
            with psycopg.connect(admin, autocommit=True) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM pg_database WHERE datname='keep_local'"
                ).fetchone()
                if restore_existing and exists:
                    conn.execute("DROP DATABASE keep_local WITH (FORCE)")
                    exists = None
                if restore_existing:
                    for role in ("keep_api", "keep_worker"):
                        if not conn.execute(
                            "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
                        ).fetchone():
                            conn.execute(
                                sql.SQL("CREATE ROLE {} NOLOGIN").format(
                                    sql.Identifier(role)
                                )
                            )
                if not exists:
                    conn.execute("CREATE DATABASE keep_local")
            break
        except psycopg.OperationalError:
            if attempt == 89:
                raise
            time.sleep(1)
    if restore_existing:
        subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                "keep-local-stack",
                "-f",
                "deploy/local/compose.yaml",
                "exec",
                "-T",
                "postgres",
                "sh",
                "-ec",
                'PGPASSWORD="$(cat /run/secrets/postgres_password)" pg_restore --no-owner --exit-on-error --dbname=postgresql://keep_migrator@127.0.0.1:5432/keep_local',
            ],
            input=dump.read_bytes(),
            check=True,
            stdout=subprocess.DEVNULL,
            cwd=ROOT,
        )
    migrate(make_conninfo(admin, dbname="keep_local"), migration_directory())
    creds = credentials()
    with psycopg.connect(admin) as conn:
        for role, parent, field in (
            ("keep_local_api", "keep_api", "api_password"),
            ("keep_local_worker", "keep_worker", "worker_password"),
        ):
            creds.setdefault(field, secrets.token_urlsafe(32))
            if not conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
            ).fetchone():
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(creds[field])
                    )
                )
            conn.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(parent), sql.Identifier(role)
                )
            )
    if restore_existing:
        marker.write_text(
            json.dumps(
                {
                    "restored_at": datetime.now(UTC).isoformat(),
                    "source": "local-native-postgresql",
                    "database": "keep_local",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            "Existing local Keep database restored into the Compose PostgreSQL volume."
        )
    save(STATE / "credentials.json", creds)
    print("Dedicated keep_local database migrated.")


def remap_mapping(creds, directory, object_ids=None):
    """Old object id -> (kind, samAccountName) from credentials.json; refuses on gaps.

    With ``object_ids`` (the legacy principals still in the database), only those entries
    are checked, so a finished remap isn't refused later because the files changed.
    """
    legacy = creds.get("remap") or {}
    users = legacy.get("users", {})
    groups = legacy.get("groups", {})
    if object_ids is not None:
        users = {name: oid for name, oid in users.items() if str(oid) in object_ids}
        groups = {label: oid for label, oid in groups.items() if str(oid) in object_ids}
    problems = []
    unknown = sorted(label for label in groups if label not in LEGACY_GROUPS)
    if unknown:
        problems.append("group labels without a samAccountName: " + ", ".join(unknown))
    unlisted = sorted(name for name in users if name not in creds.get("users", {}))
    if unlisted:
        problems.append("users missing from credentials.json users: " + ", ".join(unlisted))
    mapping = {}
    for name, object_id in users.items():
        mapping[str(object_id)] = ("user", name.lower())
    for label, object_id in groups.items():
        if label in LEGACY_GROUPS:
            mapping[str(object_id)] = ("team", LEGACY_GROUPS[label])
    known = {
        "user": {str(row.get("sAMAccountName", "")).lower() for row in directory.get("users", [])},
        "team": {str(row.get("sAMAccountName", "")).lower() for row in directory.get("groups", [])},
    }
    absent = sorted(name for kind, name in mapping.values() if name not in known[kind])
    if absent:
        problems.append("names missing from adapi-directory.json: " + ", ".join(absent))
    targets = [name for _, name in mapping.values()]
    duplicates = sorted({name for name in targets if targets.count(name) > 1})
    if duplicates:
        problems.append("names mapped twice: " + ", ".join(duplicates))
    if problems:
        raise RemapError("Remap stopped before changing anything; " + "; ".join(problems))
    return mapping


def remap(dry_run=False, dsn=None, creds=None, directory=None, out=print):
    """Move ``keycloak-local`` principals to ``('adapi', samAccountName)`` in place.

    Runs after migration 0070 and before the first ADAPI publication: the UPDATE keeps
    each principal's id, so grants, memberships, sessions, assignments and history keep
    pointing at the same person or group. Returns ``{"planned", "unmapped", "applied"}``.
    """
    import psycopg
    from psycopg.conninfo import make_conninfo

    creds = credentials() if creds is None else creds
    if directory is None:
        directory = read(STATE / "adapi-directory.json")
    dsn = make_conninfo(admin_url(), dbname="keep_local") if dsn is None else dsn
    result = {"planned": [], "unmapped": [], "applied": False}
    with psycopg.connect(dsn) as conn:
        ledger = conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]
        if not ledger or not conn.execute(
            "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE name LIKE %s)", ("0070\\_%",)
        ).fetchone()[0]:
            raise RemapError("Remap needs migration 0070; run local_identity.py database first")
        # The identity guard first, as every directory mutation takes it.
        conn.execute("SELECT 1 FROM authorization_guards WHERE scope='identity' FOR UPDATE")
        rows = conn.execute(
            "SELECT id, object_id, kind, display_name FROM principals "
            "WHERE directory_source=%s ORDER BY kind, display_name, object_id",
            (LEGACY_SOURCE,),
        ).fetchall()
        if not rows:
            out(f"No {LEGACY_SOURCE} principals; nothing to remap.")
            return result
        mapping = remap_mapping(creds, directory, {str(row[1]) for row in rows})
        for principal_id, object_id, kind, label in rows:
            if object_id not in mapping:
                result["unmapped"].append((principal_id, object_id, kind, label))
                continue
            expected, name = mapping[object_id]
            if expected != kind:
                raise RemapError(
                    f"Remap stopped before changing anything; {object_id} is a {kind},"
                    f" but credentials.json names it as a {expected}"
                )
            result["planned"].append((principal_id, object_id, kind, name))
        mode = " (dry run)" if dry_run else ""
        out(
            f"Principal remap {LEGACY_SOURCE} -> {SOURCE}{mode}: {len(result['planned'])}"
            f" planned, {len(result['unmapped'])} left unmapped."
        )
        for _, object_id, kind, name in result["planned"]:
            out(f"  {kind} {object_id} -> {name}")
        for _, object_id, kind, label in result["unmapped"]:
            out(f"  left unmapped: {kind} {object_id} {json.dumps(label)}"
                f" (stays {LEGACY_SOURCE}; add it to the mock ADAPI and remap by hand if needed)")
        names = [name for *_, name in result["planned"]]
        existing = [
            row[0]
            for row in conn.execute(
                "SELECT object_id FROM principals WHERE directory_source=%s"
                " AND object_id = ANY(%s::text[]) ORDER BY object_id",
                (SOURCE, names),
            ).fetchall()
        ] if names else []
        if existing:
            raise RemapError(
                f"Remap refused; ({SOURCE}, name) principals already exist for "
                + ", ".join(existing)
                + ". Restore the database backup taken before the first ADAPI refresh."
            )
        if dry_run:
            conn.rollback()
            out("Dry run: nothing changed.")
            return result
        for principal_id, object_id, _, name in result["planned"]:
            changed = conn.execute(
                "UPDATE principals SET directory_source=%s, object_id=%s"
                " WHERE id=%s AND directory_source=%s AND object_id=%s",
                (SOURCE, name, principal_id, LEGACY_SOURCE, object_id),
            ).rowcount
            if changed != 1:
                raise RemapError(f"Remap stopped; principal {principal_id} changed meanwhile")
        if result["planned"]:
            # Identity keys changed: fence in-flight visibility proofs once (0038).
            conn.execute(
                "UPDATE authorization_guards SET directory_revision=directory_revision+1"
                " WHERE scope='identity'"
            )
            result["applied"] = True
            out(f"Remapped {len(result['planned'])} principals; ids and references unchanged.")
    return result


def wait(timeout=180):
    """Wait until OpenLDAP accepts the admin bind and the mock ADAPI answers."""
    import httpx
    import ldap3
    from ldap3.core.exceptions import LDAPException

    creds = credentials()
    deadline = time.monotonic() + timeout
    ready = {"ldap": False, "adapi": False}
    with httpx.Client(trust_env=False, timeout=3) as client:
        while time.monotonic() < deadline:
            if not ready["ldap"]:
                try:
                    ldap3.Connection(
                        ldap3.Server(LDAP_HOST, port=LDAP_PORT, connect_timeout=2),
                        user=ADMIN_DN, password=creds["ldap_admin_password"],
                        receive_timeout=3, raise_exceptions=True, auto_bind=True,
                    ).unbind()
                    ready["ldap"] = True
                except LDAPException:
                    pass
            if not ready["adapi"]:
                try:
                    response = client.get(
                        ADAPI_URL + "/groups",
                        params={"samAccountName": SUPERADMIN_GROUP},
                        headers={"ClientId": creds["adapi_client_id"]},
                    )
                    ready["adapi"] = response.status_code == 200 and len(response.json()) == 1
                except (httpx.HTTPError, ValueError):
                    pass
            if all(ready.values()):
                print("Local OpenLDAP bind and mock ADAPI verified.")
                return
            time.sleep(2)
    waiting = " and ".join(name for name, value in ready.items() if not value)
    raise RuntimeError(
        f"Local {waiting} did not become ready; inspect `docker compose -p keep-local-stack"
        " logs openldap` and .local/identity/Adapi.err.log"
    )


def ssha(password):
    """An OpenLDAP ``{SSHA}`` userPassword; the password is hashed as UTF-8 bytes."""
    salt = secrets.token_bytes(8)
    digest = hashlib.sha1(password.encode("utf-8") + salt).digest()
    return "{SSHA}" + base64.b64encode(digest + salt).decode("ascii")


def seed(creds):
    """Add every local user to OpenLDAP, or reset its password and name; idempotent."""
    import ldap3
    from ldap3.core.exceptions import LDAPEntryAlreadyExistsResult

    server = ldap3.Server(LDAP_HOST, port=LDAP_PORT, connect_timeout=2)
    conn = ldap3.Connection(
        server, user=ADMIN_DN, password=creds["ldap_admin_password"],
        receive_timeout=5, raise_exceptions=True, auto_bind=True,
    )
    try:
        try:
            conn.add(PEOPLE_DN, ["organizationalUnit", "top"], {"ou": "people"})
        except LDAPEntryAlreadyExistsResult:
            pass
        for username, user in creds["users"].items():
            dn = BIND_FORMAT.format(username=username)
            values = {"cn": user["label"], "displayName": user["label"],
                      "userPassword": ssha(user["password"])}
            try:
                conn.add(dn, ["inetOrgPerson", "organizationalPerson", "person", "top"],
                         {**values, "sn": username, "uid": username})
            except LDAPEntryAlreadyExistsResult:
                conn.modify(dn, {key: [(ldap3.MODIFY_REPLACE, [value])]
                                 for key, value in values.items()})
    finally:
        conn.unbind()
    for username, user in creds["users"].items():
        ldap3.Connection(
            server, user=BIND_FORMAT.format(username=username),
            password=user["password"].encode("utf-8"), receive_timeout=3,
            raise_exceptions=True, auto_bind=True,
        ).unbind()


def configure(timeout=120):
    """Seed OpenLDAP from credentials.json; retries while its first start settles."""
    from ldap3.core.exceptions import LDAPException

    creds = credentials()
    deadline = time.monotonic() + timeout
    while True:
        try:
            seed(creds)
            break
        except LDAPException as error:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Could not seed local OpenLDAP ({type(error).__name__})"
                ) from None
            time.sleep(2)
    print(f"Local OpenLDAP users ready: {len(creds['users'])} (passwords unchanged).")


def reload():
    """Load the mock ADAPI's data file again through its loopback test hook."""
    import httpx

    data = read(STATE / "adapi-directory.json")
    response = httpx.post(ADAPI_URL + "/__set", json={"data": data}, trust_env=False,
                          timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"Mock ADAPI reload failed: HTTP {response.status_code}")
    print("Mock ADAPI reloaded from .local/identity/adapi-directory.json.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "action",
        choices=(
            "prepare",
            "tls",
            "database",
            "remap",
            "environment",
            "wait",
            "configure",
            "reload",
        ),
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="remap: print the planned pairs and change nothing")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare()
        elif args.action == "tls":
            tls()
        elif args.action == "database":
            database()
        elif args.action == "remap":
            remap(dry_run=args.dry_run)
        elif args.action == "environment":
            save(STATE / "app-environment.json", environment())
        elif args.action == "wait":
            wait()
        elif args.action == "configure":
            configure()
        else:
            reload()
    except RemapError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
