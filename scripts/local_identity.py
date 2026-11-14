"""Prepare the isolated Authelia + OpenLDAP dev identity stack.

Generates, as ignored local files:
  * an OpenLDAP seed LDIF (three users, four groups including a nested group),
  * an Authelia ``configuration.yml`` (LDAP auth backend, OIDC provider with the
    ``keep-web`` confidential client, PKCE S256, and a claims policy that emits the
    raw OpenLDAP ``entryUUID`` verbatim in a dedicated ``keep-directory`` scope),
  * the Authelia secret files it references,
  * a directory ``directory.json`` the production LDAP worker consumes, and
  * the ``app-environment.json`` the backend reads.

The dev directory is LDAP in every environment; here Authelia and Keep's own LDAP
worker read the SAME OpenLDAP, so dev exercises the identical production code path.
Secrets never leave the ignored ``.local/identity`` directory. Loopback binding,
no ambient proxy, and bounded reads are preserved from the earlier launcher.
"""

import argparse
import base64
import ipaddress
import json
import os
import secrets
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
# Internal state directory. The basename is historical ("identity"); it carries no
# Keycloak content any more. It stays gitignored, outside version control.
STATE = ROOT / ".local/identity"

# Authelia is the OIDC provider; its issuer is the exact base URL (discovery lives
# at ``/.well-known/openid-configuration``). Keycloak's ``/realms/...`` path is gone.
# The loopback IP is used rather than ``localhost`` because Authelia rejects a
# session cookie domain without a dot; ``127.0.0.1`` is a valid cookie domain and is
# covered by the generated localhost certificate's SANs.
ISSUER = "https://127.0.0.1:58443"
ORIGIN = "https://localhost:3443"

# The isolated directory tree. OpenLDAP generates an immutable ``entryUUID`` for
# every entry; both Authelia and Keep's LDAP worker key principals by it.
BASE_DN = "dc=keep,dc=local"
PEOPLE_RDN = "ou=people"
GROUPS_RDN = "ou=groups"
PEOPLE_DN = PEOPLE_RDN + "," + BASE_DN
GROUPS_DN = GROUPS_RDN + "," + BASE_DN
ADMIN_BIND_DN = "cn=admin," + BASE_DN
# The LDAPS listener Keep's worker reads, published on loopback by Compose. Authelia
# reaches OpenLDAP over the private Compose network instead.
LDAP_HOST = "localhost"
LDAP_LDAPS_PORT = 1636

DIRECTORY_SOURCE = "ldap-local"
DIRECTORY_SCOPE = "keep-directory"
# Authelia exposes the raw entryUUID under this custom claim name; Keep reads it as
# the immutable directory identity, matching the worker's entryUUID-keyed principals.
DIRECTORY_ID_CLAIM = "entryuuid"

# username -> (label, given name, surname, primary group common-name)
SEED_USERS = (
    ("local-admin", "Local Administrator", "Local", "Administrator", "Keep Administrators"),
    ("local-editor", "Local Editor", "Local", "Editor", "Developers"),
    ("local-viewer", "Local Viewer", "Local", "Viewer", "Observers"),
)
# group common-name -> member DNs (users by uid, nested groups by cn). "Keep
# Administrators" holds a nested group so ldap.py's nested-member traversal stays
# exercised; it is also the immutable super-admin group.
SUPERADMIN_GROUP = "Keep Administrators"


def user_dn(username):
    return f"uid={username},{PEOPLE_DN}"


def group_dn(common_name):
    return f"cn={common_name},{GROUPS_DN}"


SEED_GROUPS = {
    "Keep Administrators": [user_dn("local-admin"), group_dn("Keep Automation")],
    # Nested group: its own member reaches super-admin through parent traversal.
    "Keep Automation": [user_dn("local-admin")],
    "Developers": [user_dn("local-editor")],
    "Observers": [user_dn("local-viewer")],
}


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


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


def oidc_signing_key():
    """A dedicated RSA key for Authelia's OIDC token signatures, kept out of git."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def ldif(users):
    """Offline seed loaded by OpenLDAP; entryUUID stays server-generated and immutable.

    User passwords are plaintext ``userPassword`` so Authelia can bind as the user in
    this isolated dev tree; the file is ignored and never leaves the workstation.
    """
    blocks = [
        "\n".join(
            [
                f"dn: {BASE_DN}",
                "objectClass: top",
                "objectClass: dcObject",
                "objectClass: organization",
                "o: Keep Local",
                "dc: keep",
            ]
        ),
        "\n".join(
            [f"dn: {PEOPLE_DN}", "objectClass: organizationalUnit", f"ou: {PEOPLE_RDN.split('=')[1]}"]
        ),
        "\n".join(
            [f"dn: {GROUPS_DN}", "objectClass: organizationalUnit", f"ou: {GROUPS_RDN.split('=')[1]}"]
        ),
    ]
    for username, label, given, surname, _group in SEED_USERS:
        blocks.append(
            "\n".join(
                [
                    f"dn: {user_dn(username)}",
                    "objectClass: inetOrgPerson",
                    f"uid: {username}",
                    f"cn: {label}",
                    f"sn: {surname}",
                    f"givenName: {given}",
                    f"mail: {username}@example.invalid",
                    # Eligibility attribute the LDAP schema maps; flip away from
                    # "active" to disable a user without deleting it.
                    "employeeType: active",
                    f"userPassword: {users[username]['password']}",
                ]
            )
        )
    for common_name, members in SEED_GROUPS.items():
        lines = [f"dn: {group_dn(common_name)}", "objectClass: groupOfNames", f"cn: {common_name}"]
        lines += [f"member: {member}" for member in members]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def authelia_config(creds):
    """Authelia document: LDAP backend plus an OIDC provider emitting entryUUID.

    Written as JSON into ``configuration.yml``; JSON is valid YAML, so it parses
    without a YAML dependency and is trivially machine-checkable. Secrets Authelia
    reads from files (LDAP bind password, session/storage/HMAC/JWT) are supplied via
    ``AUTHELIA_*_FILE`` environment variables in Compose, never inlined here.
    """
    return {
        "theme": "light",
        "server": {
            "address": "tcp://0.0.0.0:9091",
            "tls": {"certificate": "/config/tls/public.crt", "key": "/config/tls/private.key"},
        },
        "log": {"level": "info", "format": "text"},
        "authentication_backend": {
            "refresh_interval": "1m",
            "ldap": {
                # Private Compose-network reach; Keep's own worker uses LDAPS loopback.
                "address": "ldap://openldap:389",
                "implementation": "custom",
                "base_dn": BASE_DN,
                "additional_users_dn": PEOPLE_RDN,
                "additional_groups_dn": GROUPS_RDN,
                "users_filter": "(&({username_attribute}={input})(objectClass=inetOrgPerson))",
                "groups_filter": "(&(member={dn})(objectClass=groupOfNames))",
                "user": ADMIN_BIND_DN,
                "attributes": {
                    "username": "uid",
                    "display_name": "cn",
                    "mail": "mail",
                    "group_name": "cn",
                    # Declare the operational entryUUID so Authelia can surface it; the
                    # raw value is emitted verbatim, never reformatted.
                    "extra": {
                        "entryUUID": {
                            "name": DIRECTORY_ID_CLAIM,
                            "multi_valued": False,
                            "value_type": "string",
                        }
                    },
                },
            },
        },
        "access_control": {"default_policy": "one_factor"},
        "session": {
            "cookies": [
                {
                    # Authelia requires a cookie domain with a dot or an IP; the
                    # loopback IP matches the issuer host Authelia is reached on.
                    # No default_redirection_url: the OIDC flow always returns to the
                    # client's registered redirect_uri, and Authelia requires that URL
                    # to share this cookie's scope (the app lives on a different host).
                    "domain": "127.0.0.1",
                    "authelia_url": ISSUER,
                }
            ]
        },
        "storage": {"local": {"path": "/data/db.sqlite3"}},
        "notifier": {"filesystem": {"filename": "/data/notification.txt"}},
        "identity_validation": {"reset_password": {}},
        "identity_providers": {
            "oidc": {
                "jwks": [
                    {
                        "key_id": "keep",
                        "algorithm": "RS256",
                        "use": "sig",
                        "key": creds["oidc_signing_key"],
                    }
                ],
                # The linchpin: a dedicated scope + claims policy carry the raw
                # entryUUID into the ID token as a protected custom claim. The
                # ``id_token`` list forces the claim into the ID token itself (Authelia
                # otherwise exposes custom claims only at the UserInfo endpoint), which
                # is where the backend reads the immutable directory identity.
                "claims_policies": {
                    "keep_directory": {
                        "id_token": [DIRECTORY_ID_CLAIM],
                        "custom_claims": {DIRECTORY_ID_CLAIM: {"attribute": DIRECTORY_ID_CLAIM}},
                    }
                },
                "scopes": {DIRECTORY_SCOPE: {"claims": [DIRECTORY_ID_CLAIM]}},
                "clients": [
                    {
                        "client_id": "keep-web",
                        "client_name": "Keep",
                        # $plaintext$ keeps this dev secret readable; production uses a hash.
                        "client_secret": "$plaintext$" + creds["web_secret"],
                        "public": False,
                        "authorization_policy": "one_factor",
                        "consent_mode": "implicit",
                        "require_pkce": True,
                        "pkce_challenge_method": "S256",
                        "redirect_uris": [ORIGIN + "/auth/callback"],
                        # offline_access + the refresh_token grant let Authelia issue a
                        # refresh token, which the backend requires for session rotation.
                        "scopes": ["openid", "profile", "offline_access", DIRECTORY_SCOPE],
                        "claims_policy": "keep_directory",
                        "response_types": ["code"],
                        "grant_types": ["authorization_code", "refresh_token"],
                        "token_endpoint_auth_method": "client_secret_basic",
                    }
                ],
            }
        },
    }


def directory_config(creds):
    """Explicit, sanitized LDAP configuration for Keep's production directory worker.

    Consumed by ``keep.identity.worker.load_directory_config``; it contains no
    password (only the injected ``password_env`` name) and an exact schema that maps
    the immutable entryUUID, rejecting any inferred vendor/company schema.
    """
    return {
        "source": DIRECTORY_SOURCE,
        "hostname": LDAP_HOST,
        "port": LDAP_LDAPS_PORT,
        "bind_dn": ADMIN_BIND_DN,
        "password_env": "KEEP_LOCAL_DIRECTORY_SECRET",
        "ca_file": str(STATE / "tls/localhost.pem"),
        "connect_timeout": 3,
        "receive_timeout": 5,
        "page_size": 100,
        "max_pages": 500,
        "max_objects": 1000,
        "schema": {
            "base_dn": BASE_DN,
            "user_filter": "(objectClass=inetOrgPerson)",
            "team_filter": "(objectClass=groupOfNames)",
            "id_attribute": "entryUUID",
            "label_attribute": "cn",
            "member_attribute": "member",
            "eligibility_attribute": "employeeType",
            "eligibility_values": ["active"],
            "id_encoding": "uuid",
            "schema_qualified": True,
        },
    }


def prepare():
    STATE.mkdir(parents=True, exist_ok=True)
    postgres_password = ROOT / ".local/postgres-password.txt"
    if not postgres_password.exists():
        postgres_password.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
    if (STATE / "credentials.json").exists():
        print("Existing local credentials preserved.")
        return
    tls()
    users = {
        username: {"password": secrets.token_urlsafe(24), "label": label, "group": group}
        for username, label, _given, _surname, group in SEED_USERS
    }
    credentials = {
        "users": users,
        "groups": list(SEED_GROUPS),
        "superadmin_group_name": SUPERADMIN_GROUP,
        # superadmin_group (the entryUUID) is resolved after OpenLDAP seeds; see configure.
        "ldap_admin_password": secrets.token_urlsafe(32),
        "web_secret": secrets.token_urlsafe(32),
        "directory_secret": secrets.token_urlsafe(32),
        "wrapping_key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "deployment_epoch": str(uuid.uuid4()),
        # Authelia server secrets, generated once and reused across restarts.
        "session_secret": secrets.token_urlsafe(48),
        "storage_encryption_key": secrets.token_urlsafe(48),
        "oidc_hmac_secret": secrets.token_urlsafe(48),
        "jwt_secret": secrets.token_urlsafe(48),
        "oidc_signing_key": oidc_signing_key(),
    }

    # OpenLDAP seed.
    (STATE / "openldap").mkdir(exist_ok=True)
    (STATE / "openldap/seed.ldif").write_text(ldif(users), encoding="utf-8")

    # Authelia configuration and the secret files it loads.
    (STATE / "authelia").mkdir(exist_ok=True)
    secrets_dir = STATE / "authelia/secrets"
    secrets_dir.mkdir(exist_ok=True)
    save(STATE / "authelia/configuration.yml", authelia_config(credentials))
    for name, value in (
        ("ldap_password", credentials["ldap_admin_password"]),
        ("session_secret", credentials["session_secret"]),
        ("storage_encryption_key", credentials["storage_encryption_key"]),
        ("oidc_hmac_secret", credentials["oidc_hmac_secret"]),
        ("jwt_secret", credentials["jwt_secret"]),
    ):
        (secrets_dir / name).write_text(value, encoding="utf-8")
    # OpenLDAP admin password, mounted as a Compose secret. Write the exact bytes
    # with no trailing newline: a trailing CRLF (added by text-mode newline
    # translation on Windows) becomes part of the admin password, so Authelia and
    # Keep's LDAP worker, which bind with the clean value, would fail with code 49.
    (STATE / "openldap-admin-password.txt").write_bytes(
        credentials["ldap_admin_password"].encode("utf-8")
    )

    save(STATE / "directory.json", directory_config(credentials))
    save(STATE / "credentials.json", credentials)
    print(
        "Prepared the OpenLDAP seed, Authelia configuration/secrets, directory "
        "configuration and localhost TLS certificate."
    )


def environment():
    creds = json.loads((STATE / "credentials.json").read_text())
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
    if "superadmin_group" not in creds:
        raise RuntimeError(
            "Run 'configure' to resolve the super-admin group entryUUID from OpenLDAP "
            "before generating the application environment."
        )
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
        "KEEP_OIDC_ISSUER": ISSUER,
        "KEEP_OIDC_CLIENT_ID": "keep-web",
        "KEEP_OIDC_CLIENT_SECRET": creds["web_secret"],
        "KEEP_OIDC_DIRECTORY_SOURCE": DIRECTORY_SOURCE,
        "KEEP_OIDC_DIRECTORY_ID_CLAIM": DIRECTORY_ID_CLAIM,
        "KEEP_OIDC_DIRECTORY_SCOPE": DIRECTORY_SCOPE,
        # KEEP_OIDC_REQUIRED_ACR is intentionally omitted: Authelia's acr differs from
        # Keycloak's and is now optional. Password evidence (amr=pwd) stays required.
        "KEEP_OIDC_REQUIRED_AMR": '["pwd"]',
        "KEEP_OIDC_CA_FILE": str(STATE / "tls/localhost.pem"),
        "KEEP_SUPERADMIN_GROUP": creds["superadmin_group"],
        # The production LDAP worker and live directory search read this schema; the
        # bind password arrives only through the named secret environment variable.
        "KEEP_DIRECTORY_CONFIG": str(STATE / "directory.json"),
        "KEEP_LOCAL_DIRECTORY_SECRET": creds["ldap_admin_password"],
        "KEEP_ELASTICSEARCH_URL": "http://127.0.0.1:59201",
        "KEEP_ELASTICSEARCH_INDEX": "keep-local-alerts",
        "KEEP_CAPACITY_PROFILE": str(capacity_profile),
        "KEEP_KAFKA_BOOTSTRAP": "127.0.0.1:19093",
        "KEEP_REDIS_URL": "redis://127.0.0.1:56380/0",
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

    admin = os.environ.get(
        "KEEP_LOCAL_DATABASE_ADMIN_URL",
        "postgresql://keep_migrator:"
        + quote((ROOT / ".local/postgres-password.txt").read_text().strip(), safe="")
        + "@127.0.0.1:55433/postgres?connect_timeout=5",
    )
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
    creds = json.loads((STATE / "credentials.json").read_text())
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


def wait():
    """Verify Authelia's HTTPS OIDC discovery under the trusted local certificate."""
    import ssl

    import httpx

    discovery = ISSUER + "/.well-known/openid-configuration"
    with httpx.Client(
        verify=ssl.create_default_context(cafile=str(STATE / "tls/localhost.pem")),
        trust_env=False,
    ) as client:
        for _ in range(90):
            try:
                response = client.get(discovery, timeout=3)
                if response.status_code == 200 and response.json()["issuer"] == ISSUER:
                    print("Local Authelia HTTPS OIDC discovery verified.")
                    return
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            time.sleep(2)
    raise RuntimeError("Local Authelia did not become ready; inspect its container logs")


def _ldap_connection():
    """One bounded, read-only LDAPS connection to the seeded OpenLDAP on loopback."""
    import ssl

    from ldap3 import Connection, Server, Tls

    creds = json.loads((STATE / "credentials.json").read_text())
    tls_config = Tls(
        validate=ssl.CERT_REQUIRED,
        ca_certs_file=str(STATE / "tls/localhost.pem"),
        valid_names=[LDAP_HOST],
        version=ssl.PROTOCOL_TLS_CLIENT,
    )
    server = Server(
        LDAP_HOST, port=LDAP_LDAPS_PORT, use_ssl=True, tls=tls_config, connect_timeout=3, get_info=None
    )
    return Connection(
        server,
        user=ADMIN_BIND_DN,
        password=creds["ldap_admin_password"],
        read_only=True,
        auto_referrals=False,
        receive_timeout=5,
        raise_exceptions=True,
    )


def configure():
    """Resolve immutable entryUUIDs from the seeded OpenLDAP.

    OpenLDAP generates entryUUID; it cannot be set from the seed LDIF. Read the
    super-admin group's entryUUID back and record it as the stable
    ``KEEP_SUPERADMIN_GROUP`` the backend and directory worker share.
    """
    from ldap3 import BASE
    from ldap3.core.exceptions import LDAPException

    creds = json.loads((STATE / "credentials.json").read_text())
    last_error = None
    for _ in range(90):
        try:
            connection = _ldap_connection()
            if not connection.bind():
                raise RuntimeError("LDAP bind to the local OpenLDAP failed")
            try:
                if not connection.search(
                    group_dn(SUPERADMIN_GROUP),
                    "(objectClass=groupOfNames)",
                    search_scope=BASE,
                    attributes=["entryUUID"],
                ):
                    raise RuntimeError("Seeded super-admin group not found in OpenLDAP")
                value = connection.entries[0].entryUUID.value
                # Normalize exactly as the worker keys principals: str(UUID(entryUUID)).
                creds["superadmin_group"] = str(uuid.UUID(str(value)))
            finally:
                connection.unbind()
            save(STATE / "credentials.json", creds)
            save(
                STATE / "configured.json",
                {
                    "configured_at": datetime.now(UTC).isoformat(),
                    "superadmin_group": creds["superadmin_group"],
                    "directory_source": DIRECTORY_SOURCE,
                },
            )
            print(
                "Resolved the super-admin group entryUUID from OpenLDAP: "
                + creds["superadmin_group"]
            )
            return
        except (LDAPException, OSError, RuntimeError) as error:
            last_error = error
            time.sleep(2)
    raise RuntimeError(
        f"Could not resolve directory identities from OpenLDAP: {last_error}"
    )


def publish(bootstrap=False):
    """Run ONE directory publication through the production LDAP worker.

    This is the exact production path: ``keep.identity.worker.load_directory_config``
    and ``run_cycle`` against the live OpenLDAP. Environment values are read directly
    (the launcher exports ``app-environment.json``) so no broader settings model is
    needed for this one-off cycle.
    """
    from keep.db import Database
    from keep.identity.directory import PrincipalKey
    from keep.identity.worker import load_directory_config, run_cycle

    config = load_directory_config(Path(os.environ["KEEP_DIRECTORY_CONFIG"]))
    superadmin = PrincipalKey(config.source, os.environ["KEEP_SUPERADMIN_GROUP"])
    database = Database(
        os.environ["KEEP_DATABASE_URL"],
        1,
        deployment_epoch=os.environ["KEEP_DEPLOYMENT_EPOCH"],
    )
    try:
        evidence, result = run_cycle(
            database, config, superadmin, bootstrap_qualified=bootstrap
        )
    finally:
        database.close()
    if not result.accepted:
        raise RuntimeError(
            "Directory publication was not accepted: "
            + ", ".join(evidence.failures or ("unknown",))
        )
    print("Directory publication accepted via the production LDAP worker.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("prepare", "database", "environment", "wait", "configure", "publish", "tls"),
    )
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "tls":
        tls()
    elif args.action == "database":
        database()
    elif args.action == "environment":
        save(STATE / "app-environment.json", environment())
    elif args.action == "wait":
        wait()
    elif args.action == "configure":
        configure()
    else:
        publish(bootstrap=args.bootstrap)


if __name__ == "__main__":
    main()
