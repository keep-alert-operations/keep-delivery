"""Loopback transport verification with ephemeral certificates and real TLS.

No company gateway is contacted. This qualifies only the application transport
binding against the installed local Uvicorn/OpenSSL, not any deployment profile.
"""

from datetime import UTC, datetime, timedelta
import hashlib
import base64
import http.client
import ipaddress
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
import pytest

pytestmark = pytest.mark.integration


def _certificate(directory, name, *, ca=None, server=False):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca[0].subject if ca else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.BasicConstraints(ca=ca is None, path_length=0 if ca is None else None),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca[1].public_key() if ca else key.public_key()
            ),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca is None,
                crl_sign=ca is None,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    if ca:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
    if server:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
    certificate = builder.sign(ca[1] if ca else key, hashes.SHA256())
    cert_path, key_path = directory / (name + ".crt"), directory / (name + ".key")
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certificate, key, cert_path, key_path


async def _probe(scope, receive, send):
    from starlette.requests import Request
    from appchi.ingestion.gateway import gateway_identity
    from appchi.shared.errors import AppError

    status = 200
    result = {"verified": bool(scope.get("appchi.gateway_peer_verified"))}
    try:
        identity = gateway_identity(Request(scope))
        result["consumer_id"] = identity.consumer_id
    except AppError as exc:
        status = exc.status
        result["error"] = exc.code
    body = json.dumps(result).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _serve(directory, pin, mode):
    import uvicorn
    from appchi.ingestion.gateway import private_protocol

    directory = Path(directory)

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            port = self.servers[0].sockets[0].getsockname()[1]
            (directory / (mode + ".ready")).write_text(str(port))

    options = {}
    if mode == "tls":
        options = {
            "ssl_certfile": str(directory / "server.crt"),
            "ssl_keyfile": str(directory / "server.key"),
            "ssl_ca_certs": str(directory / "ca.crt"),
            "ssl_cert_reqs": ssl.CERT_REQUIRED,
        }
    config = uvicorn.Config(
        _probe,
        host="127.0.0.1",
        port=0,
        http=private_protocol([pin]),
        ws="none",
        lifespan="off",
        proxy_headers=False,
        access_log=False,
        log_level="error",
        **options,
    )
    ReadyServer(config).run()


@pytest.fixture(scope="module")
def tls_gateway(tmp_path_factory):
    directory = tmp_path_factory.mktemp("gateway-tls")
    ca = _certificate(directory, "ca")
    _certificate(directory, "server", ca=ca, server=True)
    pinned = _certificate(directory, "pinned", ca=ca)
    _certificate(directory, "not-pinned", ca=ca)
    foreign = _certificate(directory, "foreign-ca")
    _certificate(directory, "foreign-client", ca=foreign)
    pin = hashlib.sha256(pinned[0].public_bytes(serialization.Encoding.DER)).hexdigest()
    processes, ports = [], {}
    try:
        for mode in ("tls", "plain"):
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--serve",
                    str(directory),
                    pin,
                    mode,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            processes.append(process)
            ready = directory / (mode + ".ready")
            deadline = time.monotonic() + 5
            while not ready.exists():
                if process.poll() is not None:
                    pytest.fail(
                        "Local transport probe failed: " + process.communicate()[1].decode()
                    )
                if time.monotonic() >= deadline:
                    pytest.fail("Local transport probe did not start within five seconds")
                time.sleep(0.025)
            ports[mode] = int(ready.read_text())
        yield directory, ports
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)


def _headers():
    return [
        ("X-Consumer-Id", str(uuid4())), ("X-Credential-Identifier", str(uuid4())),
        ("X-Appchi-Operator-Id", str(uuid4())), ("X-Appchi-Tenant-Id", str(uuid4())),
        ("X-Appchi-Policy-Generation", "1"),
        ("X-Appchi-Routing-Names", base64.b64encode(b'["Operator"]').decode()),
        ("X-Appchi-Authorized-At", str(time.time())),
    ]


def _request(gateway, certificate="pinned", headers=None, *, mode="tls"):
    directory, ports = gateway
    if mode == "tls":
        context = ssl.create_default_context(cafile=str(directory / "ca.crt"))
        if certificate:
            context.load_cert_chain(
                str(directory / (certificate + ".crt")), str(directory / (certificate + ".key"))
            )
        conn = http.client.HTTPSConnection("127.0.0.1", ports[mode], context=context, timeout=2)
    else:
        conn = http.client.HTTPConnection("127.0.0.1", ports[mode], timeout=2)
    try:
        conn.putrequest("GET", "/")
        for name, value in headers if headers is not None else _headers():
            conn.putheader(name, value)
        conn.endheaders()
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def test_real_tls_certificate_chain_and_pin_are_both_required(tls_gateway):
    headers = _headers()
    status, data = _request(tls_gateway, headers=headers)
    assert status == 200 and data["verified"] is True
    assert data["consumer_id"] == headers[0][1]
    status, data = _request(tls_gateway, certificate="not-pinned")
    assert status == 401 and data["verified"] is False
    for certificate in (None, "foreign-client"):
        with pytest.raises((ssl.SSLError, OSError, http.client.RemoteDisconnected)):
            _request(tls_gateway, certificate=certificate)


def test_headers_cannot_replace_transport_identity_or_bypass_stripping(tls_gateway):
    forged = _headers() + [("appchi.gateway_peer_verified", "true"), ("X-Forwarded-Proto", "https")]
    status, data = _request(tls_gateway, headers=forged, mode="plain")
    assert status == 401 and data["verified"] is False
    status, data = _request(tls_gateway, certificate="not-pinned", headers=forged)
    assert status == 401 and data["verified"] is False
    for forbidden in ("Authorization", "X-API-Key"):
        assert _request(tls_gateway, headers=_headers() + [(forbidden, "spoofed-secret")])[0] == 401
    duplicate = _headers()
    duplicate.append(("X-Consumer-Id", duplicate[0][1]))
    assert _request(tls_gateway, headers=duplicate)[0] == 401
    assert _request(tls_gateway, headers=[])[0] == 401
    # Plaintext bytes on the TLS listener never reach the ASGI application.
    with socket.create_connection(("127.0.0.1", tls_gateway[1]["tls"]), timeout=2) as connection:
        connection.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Consumer-Id: spoof\r\n\r\n")
        try:
            result = connection.recv(4096)
        except (ConnectionResetError, ConnectionAbortedError):
            result = b""
    assert b"200" not in result and b'"verified": true' not in result


if __name__ == "__main__":
    assert sys.argv[1] == "--serve"
    _serve(*sys.argv[2:])
