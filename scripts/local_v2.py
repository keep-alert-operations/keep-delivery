"""Isolated development v2 setup and authenticated serving-pool proof.

Run via local-v2.ps1. Uses real local Kong/Grafana and the normal SQL saga;
never fabricates remote success or enables a restored/production database.
"""

import argparse
import hashlib
import ipaddress
import json
import os
import secrets
import ssl
import sys
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/operators"
PORT_DEFAULTS = {"kong_admin": 58447, "grafana": 58448}
PORT_ENVIRONMENT = {
    "kong_admin": "KEEP_LOCAL_KONG_ADMIN_PORT",
    "grafana": "KEEP_LOCAL_GRAFANA_PORT",
}
# Other listeners in the unified local stack keep their existing contracts.
FIXED_PORTS = {3443, 55433, 56380, 58000, 58001, 58443, 58444, 58445, 58446, 59201, 19093}


def load_ports(path=None):
    path = STATE / "ports.json" if path is None else path
    values = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(values, dict) or values.keys() - PORT_DEFAULTS.keys():
        raise ValueError("Local ports accepts only kong_admin and grafana keys")
    ports = {**PORT_DEFAULTS, **values}
    if any(type(port) is not int or not 1024 <= port <= 65535 for port in ports.values()):
        raise ValueError("Local ports must be integer TCP ports from 1024 to 65535")
    if len(set(ports.values())) != len(ports) or set(ports.values()) & FIXED_PORTS:
        raise ValueError("Local ports must be distinct from each other and fixed local listeners")
    return ports


def compose_ports():
    return {PORT_ENVIRONMENT[key]: str(value) for key, value in load_ports().items()}


PORTS = load_ports()
KONG = f"https://localhost:{PORTS['kong_admin']}"
GATEWAY = "https://127.0.0.1:58445"
GRAFANA = f"https://localhost:{PORTS['grafana']}"
ORIGIN = "https://localhost:3443"
MEMBER = "local-kong-1"
REASON = "local_kong_grafana_verified"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def credentials():
    return read(STATE / "credentials.json")


def environment():
    from keep.config import Settings
    from psycopg.conninfo import conninfo_to_dict

    values = read(ROOT / ".local/identity/app-environment.json")
    values["KEEP_DATABASE_URL"] = values["KEEP_LOCAL_WORKER_DATABASE_URL"]
    values["KEEP_POOL_SIZE"] = "1"
    os.environ.update({k: str(v) for k, v in values.items()})
    settings = Settings()
    database = conninfo_to_dict(settings.database_url.get_secret_value())
    if (
        settings.environment != "development"
        or database.get("dbname") != "keep_local"
        or database.get("host") not in {"localhost", "127.0.0.1", "::1"}
        or database.get("hostaddr", database.get("host"))
        not in {"localhost", "127.0.0.1", "::1"}
        or database.get("service")
    ):
        raise RuntimeError(
            "Only the isolated keep_local development database is supported"
        )
    return settings


def database():
    from keep.db import Database

    settings = environment()
    return Database(
        settings.database_url.get_secret_value(),
        1,
        deployment_epoch=settings.deployment_epoch,
    )


def client(*, app=False, **kwargs):
    import httpx

    context = ssl.create_default_context(cafile=str(STATE / "tls/ca.crt"))
    if app:
        context.load_verify_locations(str(ROOT / ".local/identity/tls/localhost.pem"))
    return httpx.Client(verify=context, trust_env=False, timeout=105 if app else 10, **kwargs)


def checked(response):
    # Never print remote response bodies/headers, which can contain secrets.
    if not response.is_success:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path}: HTTP {response.status_code}"
        )
    return response.json() if response.content else None


def certificates():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory = STATE / "tls"
    directory.mkdir(exist_ok=True)
    if (directory / "kong.crt").exists():
        for name in ("ca", "server", "kong"):
            cert = x509.load_pem_x509_certificate(
                (directory / f"{name}.crt").read_bytes()
            )
            if (
                not cert.not_valid_before_utc
                <= datetime.now(UTC)
                < cert.not_valid_after_utc
            ):
                raise RuntimeError(
                    "Local v2 certificate expired; explicit certificate renewal required"
                )
        return
    ca = None
    for name in ("ca", "server", "kong"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "Keep local " + name)]
        )
        now = datetime.now(UTC)
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca[0].subject if ca else subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(
                x509.BasicConstraints(
                    ca=ca is None, path_length=0 if ca is None else None
                ),
                True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca[1].public_key() if ca else key.public_key()
                ),
                False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=ca is None,
                    crl_sign=ca is None,
                    encipher_only=False,
                    decipher_only=False,
                ),
                True,
            )
        )
        if ca:
            builder = builder.add_extension(
                x509.ExtendedKeyUsage(
                    [
                        ExtendedKeyUsageOID.SERVER_AUTH
                        if name == "server"
                        else ExtendedKeyUsageOID.CLIENT_AUTH
                    ]
                ),
                False,
            )
        if name == "server":
            builder = builder.add_extension(
                x509.SubjectAlternativeName(
                    [
                        *[
                            x509.DNSName(n)
                            for n in (
                                "localhost",
                                "host.docker.internal",
                                "kong",
                                "grafana",
                            )
                        ],
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                False,
            )
        cert = builder.sign(ca[1] if ca else key, hashes.SHA256())
        (directory / f"{name}.crt").write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
        )
        (directory / f"{name}.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        if ca is None:
            ca = cert, key


def prepare():
    # File preparation runs before the local database/environment is bootstrapped.
    # Database-bound actions validate the isolated environment when they execute.
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / "credentials.json"
    if not path.exists():
        save(
            path,
            {
                name: secrets.token_urlsafe(32)
                for name in (
                    "kong_password",
                    "grafana_password",
                    "probe_token",
                    "kong_token",
                )
            },
        )
    creds = credentials()
    for name in ("kong", "grafana"):
        (STATE / f"{name}-password.txt").write_text(
            creds[name + "_password"], encoding="utf-8"
        )
    (STATE / "kong.env").write_text(
        "KONG_DATABASE=postgres\nKONG_PG_HOST=kong-db\nKONG_PG_USER=kong\nKONG_PG_DATABASE=kong\n"
        + "KONG_PG_PASSWORD="
        + creds["kong_password"]
        + "\n",
        encoding="utf-8",
    )
    certificates()
    print("Local v2 credentials and certificates ready; existing values preserved.")


def plugin(http, route, name, config):
    rows = checked(http.get(KONG + f"/routes/{route}/plugins"))["data"]
    existing = next((p for p in rows if p["name"] == name), None)
    body = {"name": name, "config": config}
    if existing:
        checked(http.patch(KONG + "/plugins/" + existing["id"], json=body))
    else:
        checked(http.post(KONG + f"/routes/{route}/plugins", json=body))


def configure():
    import httpx
    from keep.operators.binding import EXTRA_CAPABILITIES
    from keep.operators.remote import REQUIRED_CAPABILITIES

    environment()
    creds = credentials()
    with client() as http:
        for _ in range(60):
            try:
                kong = checked(http.get(KONG + "/"))
                grafana = checked(http.get(GRAFANA + "/api/health"))
                break
            except (httpx.HTTPError, RuntimeError):
                time.sleep(1)
        else:
            raise RuntimeError("Local Kong/Grafana did not become ready")
        ca_path = STATE / "ca-id.json"
        if not ca_path.exists():
            save(ca_path, {"id": str(uuid4())})
        ca = read(ca_path)
        checked(
            http.put(
                KONG + "/ca_certificates/" + ca["id"],
                json={
                    "cert": (STATE / "tls/ca.crt").read_text(),
                },
            )
        )
        service = checked(
            http.put(
                KONG + "/services/keep-local-v2",
                json={
                    "name": "keep-local-v2",
                    "protocol": "https",
                    "host": "host.docker.internal",
                    "port": 58444,
                    "tls_verify": True,
                    "ca_certificates": [ca["id"]],
                },
            )
        )
        for route, path in (
            ("native", "/api/ingest/v2/"),
            ("grafana", "/api/ingest/v2/grafana"),
            ("probe", "/__local/operator-check"),
            ("active-probe", "/__local/operator-auth-check"),
        ):
            name = "keep-local-" + route
            checked(
                http.put(
                    KONG + "/routes/" + name,
                    json={
                        "name": name,
                        "service": {"id": service["id"]},
                        "paths": [path],
                        "strip_path": False,
                        "protocols": ["https"],
                    },
                )
            )
            pre = """
for _, name in ipairs({'X-Consumer-Id', 'X-Credential-Identifier', 'X-Consumer-Custom-Id', 'X-Consumer-Username', 'X-Anonymous-Consumer'}) do
  kong.service.request.clear_header(name)
end
"""
            if route == "grafana":
                pre += """
local auth = kong.request.get_headers()['authorization']
if type(auth) ~= 'string' or kong.request.get_header('x-api-key') then
  return kong.response.exit(401, {message='One Bearer credential required'})
end
local key = auth:match('^[Bb][Ee][Aa][Rr][Ee][Rr] (vena_[%w_-]+)$')
if not key or #key ~= 48 then return kong.response.exit(401, {message='Invalid credential'}) end
ngx.req.set_header('X-API-Key', key)
ngx.req.clear_header('Authorization')
"""
            else:
                pre += "if kong.request.get_header('authorization') then return kong.response.exit(401, {message='Use X-API-Key'}) end\n"
            if route == "probe":
                pre += (
                    "if kong.request.get_header('x-keep-probe-token') ~= "
                    + json.dumps(creds["probe_token"])
                    + " then return kong.response.exit(401, {message='Private proof required'}) end\n"
                )
            plugin(http, name, "pre-function", {"access": [pre]})
            previous = checked(http.get(KONG + f"/routes/{name}/plugins"))["data"]
            for old in previous:
                if old["name"] == "post-function" or (route == "probe" and old["name"] == "key-auth"):
                    checked(http.delete(KONG + "/plugins/" + old["id"]))
            if route != "probe":
                plugin(http, name, "key-auth", {"key_names": ["X-API-Key"], "hide_credentials": True,
                    "key_in_header": True, "key_in_query": False, "key_in_body": False})
            plugin(http, name, "keep-policy", {"member_id": MEMBER, "probe": route == "probe",
                "probe_active": route == "active-probe", "probe_token": creds["probe_token"],
                "unqualified_local_clock": True})
        if "grafana_token" not in creds:
            auth = ("local-bootstrap", creds["grafana_password"])
            accounts = checked(
                http.get(GRAFANA + "/api/serviceaccounts/search", auth=auth)
            )["serviceAccounts"]
            account = next(
                (a for a in accounts if a["name"] == "keep-local-provisioner"), None
            )
            if not account:
                account = checked(
                    http.post(
                        GRAFANA + "/api/serviceaccounts",
                        auth=auth,
                        json={"name": "keep-local-provisioner", "role": "Admin"},
                    )
                )
            token = checked(
                http.post(
                    GRAFANA + f"/api/serviceaccounts/{account['id']}/tokens",
                    auth=auth,
                    json={"name": "local-" + str(uuid4())},
                )
            )
            creds["grafana_token"] = token["key"]
            save(STATE / "credentials.json", creds)
        headers = {"Authorization": "Bearer " + creds["grafana_token"]}
        tree = checked(
            http.get(GRAFANA + "/api/v1/provisioning/policies", headers=headers)
        )
        marker = STATE / "wrapper.json"
        if not marker.exists():
            # This Grafana is dedicated to local Keep. Preserve its entire initial branch.
            intent = STATE / "wrapper-intent.json"
            if not intent.exists():
                if tree.get("routes"):
                    raise RuntimeError(
                        "Refusing to bootstrap an existing Grafana routing tree"
                    )
                save(intent, {"company_branch": tree})
            branch = read(intent)["company_branch"]
            desired = deepcopy(branch)
            desired["routes"] = [deepcopy(branch)]

            # An uncertain prior PUT may already have committed this exact tree.
            def without_provenance(value):
                return {k: v for k, v in value.items() if k != "provenance"}

            if without_provenance(tree) not in (
                without_provenance(branch),
                without_provenance(desired),
            ):
                raise RuntimeError("Local Grafana changed during wrapper setup")
            checked(
                http.put(
                    GRAFANA + "/api/v1/provisioning/policies",
                    headers=headers,
                    json=desired,
                )
            )
            actual = checked(
                http.get(GRAFANA + "/api/v1/provisioning/policies", headers=headers)
            )
            if without_provenance(actual) != without_provenance(desired):
                raise RuntimeError("Local Grafana wrapper verification failed")
            save(marker, {"company_branch": branch})
        elif tree["routes"][-1] != read(marker)["company_branch"]:
            raise RuntimeError("Local Grafana fallback branch changed")
        if (
            checked(
                http.get(GRAFANA + "/api/v1/provisioning/alert-rules", headers=headers)
            )
            != []
        ):
            raise RuntimeError(
                "Local profile requires a dedicated Grafana with no alert rules"
            )
        organization = checked(http.get(GRAFANA + "/api/org", headers=headers))["id"]
        binding = {
            "profile": "grafana-provisioning-v1-kong-keep-policy-v1",
            "grafana_url": GRAFANA,
            "kong_url": KONG,
            "probe_url": "https://localhost:58446/operator-check",
            "webhook_url": "https://kong:8443/api/ingest/v2/grafana",
            "grafana_version": grafana["version"],
            "kong_version": kong["version"],
            "organization": organization,
            "serving_members": [checked(http.get(KONG + "/keep-policy-node"))["member_id"]],
            "capabilities": sorted(REQUIRED_CAPABILITIES | EXTRA_CAPABILITIES),
            "ca_file": str(STATE / "tls/ca.crt"),
            **{
                k: "isolated-local-development-" + k
                for k in (
                    "verified_evidence_id",
                    "controlled_writer_evidence_id",
                    "wrapper_evidence_id",
                    "inventory_evidence_id",
                    "namespace_evidence_id",
                    "serving_pool_evidence_id",
                )
            },
        }
        save(STATE / "binding.json", binding)
        print(
            f"Configured local Kong {kong['version']} and Grafana {grafana['version']}."
        )


def validate_probe(conn, body):
    """Nonsecret current-operation and exact-policy fence for local serving proof."""
    row = conn.execute("""SELECT o.generation,p.generation AS policy_generation FROM operators o
        JOIN operator_gateway_policies p ON p.operator_id=o.id WHERE o.id=%s AND EXISTS
        (SELECT 1 FROM operator_operations j WHERE j.operator_id=o.id AND j.generation=o.generation
         AND j.completed_at IS NULL AND j.state='running' AND j.lease_until>clock_timestamp())""",
        (body.operator_id,)).fetchone()
    return bool(row and str(row["generation"]) == body.generation
                and str(row["policy_generation"]) == body.policy_generation
                and body.consumer_id == body.operator_id)


def proof_app():
    from uuid import UUID

    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, ConfigDict, Field

    class Proof(BaseModel):
        model_config = ConfigDict(extra="forbid")
        operator_id: UUID
        consumer_id: UUID
        credential_id: UUID
        generation: str = Field(pattern=r"^[0-9]+$")
        nonce: str = Field(min_length=1, max_length=128)
        key: str | None = Field(default=None, pattern=r"^vena_[A-Za-z0-9_-]{43}$", repr=False)
        policy_generation: str = Field(pattern=r"^[0-9]+$")
        expected_allowed: bool

    @asynccontextmanager
    async def lifespan(app):
        app.state.db = database()
        yield
        app.state.db.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    token = credentials()["probe_token"]

    @app.get("/readyz")
    def ready():
        return {"scope": "local-operator-proof"}

    @app.post("/operator-check")
    async def proof(request: Request):
        if len(
            request.headers.getlist("authorization")
        ) != 1 or not secrets.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + token
        ):
            return JSONResponse({"error": "unauthorized"}, 401)
        raw = await request.body()
        if len(raw) > 4096:
            return JSONResponse({"error": "invalid_proof"}, 422)
        try:
            body = Proof.model_validate_json(raw)
        except ValueError:
            return JSONResponse({"error": "invalid_proof"}, 422)
        # No request validation response can reflect the supplied key.
        with app.state.db.transaction() as conn:
            valid = validate_probe(conn, body)
        if not valid:
            return JSONResponse({"error": "stale_proof"}, 409)
        with client() as http:
            response = http.post(GATEWAY + "/__local/operator-check",
                headers={"X-Keep-Probe-Token": token},
                json={"operator_id":str(body.operator_id),"credential_id":str(body.credential_id),"nonce":body.nonce})
            observed = response.json() if response.status_code == 200 else {}
            if body.key:
                authenticated = http.post(GATEWAY + "/__local/operator-auth-check",
                    headers={"X-API-Key":body.key,"X-Keep-Probe-Token":token})
                if body.expected_allowed:
                    actual = authenticated.json() if authenticated.status_code == 200 else {}
                    if any(actual.get(k) != observed.get(k) for k in ("operator_id","credential_id","generation","policy_digest")):
                        return JSONResponse({"error":"gateway_auth_proof_failed"},503)
                elif authenticated.status_code != 401:
                    return JSONResponse({"error":"prepared_key_not_denied"},503)
        if observed.get("nonce") != body.nonce or observed.get("allowed") is not body.expected_allowed or observed.get("generation") != body.policy_generation:
            return JSONResponse({"error":"gateway_proof_failed"},503)
        with app.state.db.transaction() as conn:
            if not validate_probe(conn, body):
                return JSONResponse({"error": "stale_proof"}, 409)
        return {
            "nonce": body.nonce,
            "generation": body.generation,
            "admitted": False,
            "members": [{**observed, "ready":True,
                         "consumer_id":observed["operator_id"],"policy_generation":observed["generation"]}],
        }

    return app


def serve(role):
    import uvicorn

    environment()
    if role == "private":
        from keep.private_ingestion import main
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        cert = x509.load_pem_x509_certificate((STATE / "tls/kong.crt").read_bytes())
        os.environ.update(
            {
                "KEEP_GATEWAY_CERT_FILE": str(STATE / "tls/server.crt"),
                "KEEP_GATEWAY_KEY_FILE": str(STATE / "tls/server.key"),
                "KEEP_GATEWAY_CA_FILE": str(STATE / "tls/ca.crt"),
                "KEEP_GATEWAY_PEER_SHA256": hashlib.sha256(
                    cert.public_bytes(serialization.Encoding.DER)
                ).hexdigest(),
                "KEEP_BIND_HOST": "127.0.0.1",
                "KEEP_BIND_PORT": "58444",
            }
        )
        main()
    elif role == "proof":
        uvicorn.run(
            proof_app(),
            host="localhost",
            port=58446,
            access_log=False,
            ssl_certfile=str(STATE / "tls/server.crt"),
            ssl_keyfile=str(STATE / "tls/server.key"),
        )
    else:
        from keep.worker import main

        creds = credentials()
        os.environ.update(
            {
                "KEEP_OPERATOR_BINDING": str(STATE / "binding.json"),
                "KEEP_OPERATOR_GRAFANA_TOKEN": creds["grafana_token"],
                "KEEP_OPERATOR_KONG_TOKEN": creds["kong_token"],
                "KEEP_OPERATOR_PROBE_TOKEN": creds["probe_token"],
                "KEEP_WORKER_HEALTH_PATH": str(STATE / "operators.health.json"),
            }
        )
        sys.argv = [sys.argv[0], "operators"]
        main()


def login(http):
    # Authelia's portal is a single-page app that authenticates through its JSON
    # first-factor API rather than a server-rendered form. Starting the OIDC flow
    # redirects an unauthenticated browser to the Authelia portal with the pending
    # authorization request carried in the `rd` query parameter.
    response = http.get(ORIGIN + "/auth/login", follow_redirects=True)
    target = parse_qs(urlsplit(str(response.url)).query).get("rd", [None])[0]
    if not str(response.url).startswith("https://localhost:58443/") or not target:
        raise RuntimeError("Expected the local Authelia sign-in portal")
    creds = read(ROOT / ".local/identity/credentials.json")
    # Password-only first factor; Authelia sets its session cookie and returns the
    # authorization URL to resume. Implicit consent then redirects to /auth/callback.
    first = checked(
        http.post(
            "https://localhost:58443/api/firstfactor",
            json={
                "username": "local-admin",
                "password": creds["users"]["local-admin"]["password"],
                "keepMeLoggedIn": False,
                "targetURL": target,
                "requestMethod": "GET",
            },
            headers={"Accept": "application/json"},
        )
    )
    http.get(first["data"]["redirect"], follow_redirects=True)
    session = checked(http.get(ORIGIN + "/auth/session"))["data"]
    http.headers.update({"X-CSRF-Token": session["csrf_token"], "Origin": ORIGIN})
    return session


def setup_operator():
    with client(app=True) as http:
        session = login(http)
        tenant = next(t for t in session["tenants"] if t["name"] == "Local Demo")
        base = ORIGIN + f"/api/tenants/{tenant['id']}/operators"
        rows = checked(http.get(base))["data"]
        operator = next((o for o in rows if o["name"] == "Local Demo Sender"), None)
        if not operator:
            checked(
                http.post(
                    base, json={"command_id": str(uuid4()), "name": "Local Demo Sender"}
                )
            )
        elif operator["lifecycle"] != "ready" and operator.get("operation"):
            checked(
                http.post(
                    base
                    + f"/{operator['id']}/operations/{operator['operation']['id']}/retry",
                    json={
                        "command_id": str(uuid4()),
                        "expected_revision": operator["revision"],
                    },
                )
            )
        for _ in range(90):
            rows = checked(http.get(base))["data"]
            operator = next(o for o in rows if o["name"] == "Local Demo Sender")
            if operator["lifecycle"] == "ready":
                break
            if (
                operator.get("operation", {}).get("error_code")
                == "v2_activation_fenced"
            ):
                break
            time.sleep(1)
        else:
            raise RuntimeError(
                "Operator setup pending; inspect sanitized operator operation diagnostics"
            )
        save(
            STATE / "demo.json",
            {"tenant_id": tenant["id"], "operator_id": operator["id"]},
        )
    print(
        "Local Demo Sender remote setup verified; local readiness controls activation."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "ports",
            "prepare",
            "configure",
            "private",
            "proof",
            "worker",
            "setup-operator",
        ),
    )
    args = parser.parse_args()
    if args.action == "ports":
        print(json.dumps(compose_ports()))
    elif args.action in {"private", "proof", "worker"}:
        serve(args.action)
    else:
        {"prepare": prepare, "configure": configure, "setup-operator": setup_operator}[
            args.action
        ]()


if __name__ == "__main__":
    main()
