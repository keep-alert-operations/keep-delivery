"""Prepare isolated native Keycloak identities; secrets stay in ignored local files."""

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import struct
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/keycloak"
ISSUER = "https://localhost:58443/realms/keep-local"
ORIGIN = "https://localhost:3443"
BACKCHANNEL = "https://host.docker.internal:3443/auth/backchannel-logout"


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def mapper(name, provider):
    return {
        "name": name,
        "protocol": "openid-connect",
        "protocolMapper": provider,
        "config": {"id.token.claim": "true", "access.token.claim": "true"},
    }


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


def prepare():
    STATE.mkdir(parents=True, exist_ok=True)
    postgres_password = ROOT / ".local/postgres-password.txt"
    if not postgres_password.exists():
        postgres_password.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
    if (STATE / "credentials.json").exists():
        print("Existing local credentials preserved.")
        return
    (STATE / "import").mkdir(exist_ok=True)
    tls()
    groups = {
        name: str(uuid.uuid4())
        for name in ("Keep Administrators", "Developers", "Observers")
    }
    users = {}
    for username, label, group in (
        ("local-admin", "Local Administrator", "Keep Administrators"),
        ("local-editor", "Local Editor", "Developers"),
        ("local-viewer", "Local Viewer", "Observers"),
    ):
        users[username] = {
            "id": str(uuid.uuid4()),
            "password": secrets.token_urlsafe(24),
            "label": label,
            "group": group,
        }
    credentials = {
        "users": users,
        "groups": groups,
        "console_username": "bootstrap-admin",
        "console_password": secrets.token_urlsafe(32),
        "web_secret": secrets.token_urlsafe(32),
        "directory_secret": secrets.token_urlsafe(32),
        "wrapping_key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "deployment_epoch": str(uuid.uuid4()),
    }
    realm = {
        "realm": "keep-local",
        "enabled": True,
        "displayName": "Keep local development",
        "sslRequired": "all",
        "registrationAllowed": False,
        "rememberMe": False,
        "resetPasswordAllowed": False,
        "bruteForceProtected": True,
        "failureFactor": 5,
        "waitIncrementSeconds": 900,
        "maxFailureWaitSeconds": 900,
        "ssoSessionIdleTimeout": 1800,
        "ssoSessionMaxLifespan": 86400,
        "accessTokenLifespan": 300,
        "revokeRefreshToken": True,
        "refreshTokenMaxReuse": 0,
        "attributes": {"CreateDefaultClientScopes": "true"},
        "browserFlow": "keep-password",
        "authenticationFlows": [
            {
                "alias": "keep-password",
                "providerId": "basic-flow",
                "topLevel": True,
                "builtIn": False,
                "authenticationExecutions": [
                    {
                        "authenticator": "auth-username-password-form",
                        "requirement": "REQUIRED",
                        "priority": 10,
                        "authenticatorFlow": False,
                        "authenticatorConfig": "keep-password-evidence",
                    },
                ],
            }
        ],
        "authenticatorConfig": [
            {
                "alias": "keep-password-evidence",
                "config": {
                    "default.reference.value": "pwd",
                    "default.reference.maxAge": "86400",
                },
            },
        ],
        "groups": [{"id": ident, "name": label} for label, ident in groups.items()],
        "clientScopes": [
            {
                "name": "keep-directory",
                "protocol": "openid-connect",
                "attributes": {"include.in.token.scope": "true"},
            }
        ],
        "clients": [
            {
                "clientId": "keep-web",
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "secret": credentials["web_secret"],
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "implicitFlowEnabled": False,
                "serviceAccountsEnabled": False,
                "redirectUris": [ORIGIN + "/auth/callback"],
                "webOrigins": [ORIGIN],
                "attributes": {
                    "login_theme": "keep",
                    "pkce.code.challenge.method": "S256",
                    "post.logout.redirect.uris": ORIGIN + "/signed-out",
                    "backchannel.logout.url": BACKCHANNEL,
                    "backchannel.logout.session.required": "true",
                },
                "defaultClientScopes": ["basic", "profile", "keep-directory"],
                "optionalClientScopes": [],
                "protocolMappers": [
                    mapper("amr", "oidc-amr-mapper"),
                    mapper("acr", "oidc-acr-mapper"),
                ],
            },
            {
                "clientId": "keep-directory",
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "secret": credentials["directory_secret"],
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "implicitFlowEnabled": False,
                "serviceAccountsEnabled": True,
                "defaultClientScopes": ["roles"],
            },
        ],
        "users": [
            {
                "username": username,
                "id": user["id"],
                "enabled": True,
                "firstName": user["label"].split(" ", 1)[0],
                "lastName": user["label"].split(" ", 1)[1],
                "email": username + "@example.invalid",
                "emailVerified": True,
                "groups": ["/" + user["group"]],
                "credentials": [
                    {"type": "password", "value": user["password"], "temporary": False},
                ],
            }
            for username, user in users.items()
        ]
        + [
            {
                "username": "service-account-keep-directory",
                "enabled": True,
                "serviceAccountClientId": "keep-directory",
                "clientRoles": {
                    "realm-management": ["view-users", "query-users", "query-groups"]
                },
            }
        ],
    }
    save(STATE / "import/keep-local-realm.json", realm)
    (STATE / "bootstrap.env").write_text(
        "KC_BOOTSTRAP_ADMIN_USERNAME=bootstrap-admin\nKC_BOOTSTRAP_ADMIN_PASSWORD="
        + credentials["console_password"]
        + "\n",
        encoding="utf-8",
    )
    save(
        STATE / "directory.json",
        {
            "issuer": ISSUER,
            "source": "keycloak-local",
            "client_id": "keep-directory",
            "client_secret_env": "KEEP_LOCAL_DIRECTORY_SECRET",
            "ca_file": str(STATE / "tls/localhost.pem"),
            "superadmin_group": groups["Keep Administrators"],
            "max_objects": 1000,
            "page_size": 100,
            "max_pages": 1000,
            "local_only": True,
        },
    )
    save(STATE / "credentials.json", credentials)
    print(
        "Prepared private local credentials, realm import and localhost TLS certificate."
    )


def environment():
    creds = json.loads((STATE / "credentials.json").read_text())
    capacity_profile = ROOT / "qualification/development-capacity.json"
    operator_path = ROOT / ".local/operators/credentials.json"
    operator_values = {}
    if operator_path.exists():
        operator_credentials = json.loads(operator_path.read_text(encoding="utf-8"))
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
        "KEEP_OIDC_ISSUER": ISSUER,
        "KEEP_OIDC_CLIENT_SECRET": creds["web_secret"],
        "KEEP_OIDC_DIRECTORY_SOURCE": "keycloak-local",
        "KEEP_OIDC_DIRECTORY_ID_CLAIM": "sub",
        "KEEP_OIDC_DIRECTORY_SCOPE": "keep-directory",
        "KEEP_OIDC_REQUIRED_ACR": "1",
        "KEEP_OIDC_REQUIRED_AMR": '["pwd"]',
        "KEEP_OIDC_CA_FILE": str(STATE / "tls/localhost.pem"),
        "KEEP_SUPERADMIN_GROUP": creds["groups"]["Keep Administrators"],
        "KEEP_LOCAL_DIRECTORY_SECRET": creds["directory_secret"],
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
    import ssl

    import httpx

    with httpx.Client(
        verify=ssl.create_default_context(cafile=str(STATE / "tls/localhost.pem")),
        trust_env=False,
    ) as client:
        for _ in range(90):
            try:
                response = client.get(
                    ISSUER + "/.well-known/openid-configuration", timeout=3
                )
                if response.status_code == 200 and response.json()["issuer"] == ISSUER:
                    print("Local Keycloak HTTPS discovery verified.")
                    return
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            time.sleep(2)
    raise RuntimeError(
        "Local Keycloak did not become ready; inspect its container logs"
    )


def configure_password_login(client, origin):
    """Upgrade the isolated Keep realm, including already enrolled users."""
    realm_url = origin + "/admin/realms/keep-local"
    response = client.get(realm_url)
    response.raise_for_status()
    alias = response.json()["browserFlow"]
    if alias not in {"keep-password", "keep-password-otp"}:
        raise RuntimeError("Unexpected local browser flow; refusing to alter it")
    executions_url = realm_url + f"/authentication/flows/{alias}/executions"
    response = client.get(executions_url)
    response.raise_for_status()
    for execution in response.json():
        if execution.get("providerId") == "auth-otp-form":
            execution["requirement"] = "DISABLED"
            response = client.put(executions_url, json=execution)
            response.raise_for_status()

    action_url = realm_url + "/authentication/required-actions/CONFIGURE_TOTP"
    response = client.get(action_url)
    response.raise_for_status()
    action = response.json()
    action.update(enabled=False, defaultAction=False)
    response = client.put(action_url, json=action)
    response.raise_for_status()
    first = 0
    while True:
        response = client.get(realm_url + "/users", params={"first": first, "max": 100})
        response.raise_for_status()
        users = response.json()
        for user in users:
            actions = user.get("requiredActions", [])
            if "CONFIGURE_TOTP" in actions:
                response = client.put(
                    realm_url + "/users/" + user["id"],
                    json={
                        "requiredActions": [a for a in actions if a != "CONFIGURE_TOTP"]
                    },
                )
                response.raise_for_status()
        if len(users) < 100:
            break
        first += len(users)

    # Existing installations keep their credentials and historical flow alias.
    import_path = STATE / "import/keep-local-realm.json"
    realm = json.loads(import_path.read_text(encoding="utf-8"))
    for flow in realm["authenticationFlows"]:
        for execution in flow["authenticationExecutions"]:
            if execution.get("authenticator") == "auth-otp-form":
                execution["requirement"] = "DISABLED"
    for user in realm["users"]:
        user["credentials"] = [
            item for item in user.get("credentials", []) if item["type"] != "otp"
        ]
        user["requiredActions"] = [
            action
            for action in user.get("requiredActions", [])
            if action != "CONFIGURE_TOTP"
        ]
    save(import_path, realm)


def configure_login_theme(client, origin):
    """Apply Keep branding only to its client, preserving all other settings."""
    clients_url = origin + "/admin/realms/keep-local/clients"
    response = client.get(clients_url, params={"clientId": "keep-web"})
    response.raise_for_status()
    matches = [item for item in response.json() if item["clientId"] == "keep-web"]
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one local keep-web client")
    client_url = clients_url + "/" + matches[0]["id"]
    response = client.get(client_url)
    response.raise_for_status()
    config = response.json()
    attributes = config.get("attributes", {})
    if attributes.get("login_theme") != "keep":
        config["attributes"] = {**attributes, "login_theme": "keep"}
        response = client.put(client_url, json=config)
        response.raise_for_status()

    import_path = STATE / "import/keep-local-realm.json"
    realm = json.loads(import_path.read_text(encoding="utf-8"))
    for item in realm["clients"]:
        if item["clientId"] == "keep-web":
            item.setdefault("attributes", {})["login_theme"] = "keep"
    save(import_path, realm)


def configure():
    """Configure Keep password login and separate console administrator MFA."""
    import ssl

    import httpx

    marker = STATE / "configured.json"
    previous = json.loads(marker.read_text()) if marker.exists() else {}
    if (
        previous.get("keep_login") == "username-password"
        and previous.get("login_theme") == "keep"
    ):
        print("Local username/password login and Keep theme configuration preserved.")
        return
    creds = json.loads((STATE / "credentials.json").read_text())
    origin = "https://localhost:58443"
    with httpx.Client(
        verify=ssl.create_default_context(cafile=str(STATE / "tls/localhost.pem")),
        trust_env=False,
    ) as client:
        login = {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": creds["console_username"],
            "password": creds["console_password"],
        }
        if creds.get("console_otp_secret"):
            login["totp"] = otp_value(creds["console_otp_secret"])
        token = client.post(
            origin + "/realms/master/protocol/openid-connect/token",
            data=login,
        )
        token.raise_for_status()
        client.headers["Authorization"] = "Bearer " + token.json()["access_token"]
        configure_login_theme(client, origin)
        if previous.get("keep_login") != "username-password":
            configure_password_login(client, origin)
        if previous:
            save(
                marker,
                {
                    **previous,
                    "keep_login": "username-password",
                    "login_theme": "keep",
                },
            )
            print("Local Keep password login and theme configured.")
            return
        for username, user in creds["users"].items():
            response = client.put(
                origin + "/admin/realms/keep-local/users/" + user["id"],
                json={
                    "firstName": user["label"].split(" ", 1)[0],
                    "lastName": user["label"].split(" ", 1)[1],
                    "email": username + "@example.invalid",
                    "emailVerified": True,
                },
            )
            response.raise_for_status()
        admins = client.get(
            origin + "/admin/realms/master/users",
            params={"username": creds["console_username"], "exact": "true"},
        )
        admins.raise_for_status()
        admin = admins.json()[0]
        response = client.put(
            origin + "/admin/realms/master/users/" + admin["id"],
            json={
                "requiredActions": sorted(
                    set(admin.get("requiredActions", [])) | {"CONFIGURE_TOTP"}
                )
            },
        )
        response.raise_for_status()
    save(
        marker,
        {
            "configured_at": datetime.now(UTC).isoformat(),
            "console_totp_enrollment_required": True,
            "keep_login": "username-password",
            "login_theme": "keep",
        },
    )
    print(
        "Custom user profiles ready; Keycloak console administrator must enroll TOTP."
    )


def otp_value(secret):
    counter = struct.pack(">Q", int(time.time()) // 30)
    digest = hmac.new(base64.b32decode(secret), counter, hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f"{(struct.unpack('>I', digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def otp(username):
    credentials = json.loads((STATE / "credentials.json").read_text())
    if username != credentials["console_username"]:
        raise ValueError("Keep users sign in with username and password only")
    print(otp_value(credentials["console_otp_secret"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "prepare",
            "database",
            "environment",
            "otp",
            "wait",
            "configure",
            "tls",
        ),
    )
    parser.add_argument("username", nargs="?", default="bootstrap-admin")
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
        otp(args.username)


if __name__ == "__main__":
    main()
