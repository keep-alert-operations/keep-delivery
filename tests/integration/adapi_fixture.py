"""Test doubles for Keep password sign-in: a pinned OpenLDAP container and the mock ADAPI.

Import the fixtures into a test module::

    from adapi_fixture import mock_adapi, openldap  # noqa: F401

``openldap`` (session scope) runs ``osixia/openldap`` pinned by tag and digest with plain
LDAP on a free 127.0.0.1 port, seeds ``uid=<name>,ou=people,dc=keep,dc=local`` users with
generated ``{SSHA}`` passwords and removes the container at teardown. Without Docker it
skips. ``mock_adapi`` (function scope) is a fresh in-process ``scripts/local_adapi.py``
server on 127.0.0.1 with the sample directory; ``adapi_factory`` starts more with other
data. The same usernames exist in both, except ``local-unlisted`` (LDAP only, so a correct
bind finds no ADAPI row).

Requirements: REV-73; plan A9, A11, A13, A19-A22, A25-A27.
"""

import base64
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import local_adapi  # noqa: E402

OPENLDAP_IMAGE = "osixia/openldap:1.5.0"
OPENLDAP_DIGEST = "sha256:18742e9c449c9c1afe129d3f2f3ee15fb34cc43e5f940a20f3399728f41d7c28"
OPENLDAP_REFERENCE = f"{OPENLDAP_IMAGE}@{OPENLDAP_DIGEST}"
CONTAINER_PREFIX = "keep-test-openldap-"
BASE_DN = "dc=keep,dc=local"
PEOPLE_DN = f"ou=people,{BASE_DN}"
BIND_FORMAT = f"uid={{username}},{PEOPLE_DN}"
MOCK_CLIENT_ID = "test-adapi-client-id"
SUPERADMIN_GROUP = local_adapi.SUPERADMIN_GROUP
# Users seeded in OpenLDAP; all but local-unlisted are in local_adapi.sample_directory().
LDAP_USERS = (
    "local-admin", "local-editor", "local-viewer", "local-disabled", "local-hebrew",
    "local-unlisted",
)
HEBREW_USER = "local-hebrew"
STARTUP_TIMEOUT = 120.0


def ssha(password):
    """An OpenLDAP ``{SSHA}`` userPassword for a str password, hashed as UTF-8 bytes."""
    salt = secrets.token_bytes(8)
    digest = hashlib.sha1(password.encode("utf-8") + salt).digest()
    return "{SSHA}" + base64.b64encode(digest + salt).decode("ascii")


def generated_passwords(users=LDAP_USERS):
    passwords = {name: f"{secrets.token_urlsafe(12)}-Kp1" for name in users}
    if HEBREW_USER in passwords:
        passwords[HEBREW_USER] = f"שלום-Keep-{secrets.randbelow(10**6):06d}"
    return passwords


def _docker(*args, timeout=60, check=True):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, check=check,
    )


class OpenLDAP:
    """A running seeded OpenLDAP. ``users`` maps username -> plaintext password."""

    def __init__(self, container, host, port, admin_password, users):
        self.container = container
        self.host = host
        self.port = port
        self.base_dn = BASE_DN
        self.people_dn = PEOPLE_DN
        self.bind_format = BIND_FORMAT
        self.admin_dn = f"cn=admin,{BASE_DN}"
        self.admin_password = admin_password
        self.users = dict(users)
        self.image = OPENLDAP_IMAGE
        self.digest = OPENLDAP_DIGEST

    @property
    def url(self):
        return f"ldap://{self.host}:{self.port}"

    def bind_dn(self, username):
        return self.bind_format.format(username=username)

    def server(self):
        import ldap3

        return ldap3.Server(self.host, port=self.port, use_ssl=False, connect_timeout=2)

    def connect(self, username, password, **options):
        """A bound ldap3 connection as ``username`` (password sent as UTF-8 bytes)."""
        import ldap3

        if isinstance(password, str):
            password = password.encode("utf-8")
        return ldap3.Connection(
            self.server(), user=self.bind_dn(username), password=password,
            receive_timeout=2, raise_exceptions=True, auto_bind=True, **options,
        )

    def _admin(self):
        import ldap3

        return ldap3.Connection(
            self.server(), user=self.admin_dn, password=self.admin_password,
            receive_timeout=5, raise_exceptions=True, auto_bind=True,
        )

    def add_user(self, username, password, display_name=None):
        """Add (or replace the password of) a person; returns the password."""
        import ldap3
        from ldap3.core.exceptions import LDAPEntryAlreadyExistsResult

        conn = self._admin()
        try:
            try:
                conn.add(
                    self.bind_dn(username),
                    ["inetOrgPerson", "organizationalPerson", "person", "top"],
                    {"cn": display_name or username, "sn": username, "uid": username,
                     "displayName": display_name or username, "userPassword": ssha(password)},
                )
            except LDAPEntryAlreadyExistsResult:
                conn.modify(self.bind_dn(username),
                            {"userPassword": [(ldap3.MODIFY_REPLACE, [ssha(password)])]})
        finally:
            conn.unbind()
        self.users[username] = password
        return password

    def seed(self):
        from ldap3.core.exceptions import LDAPEntryAlreadyExistsResult

        conn = self._admin()
        try:
            try:
                conn.add(self.people_dn, ["organizationalUnit", "top"], {"ou": "people"})
            except LDAPEntryAlreadyExistsResult:
                pass
        finally:
            conn.unbind()
        for username, password in list(self.users.items()):
            self.add_user(username, password)


def _docker_available():
    if shutil.which("docker") is None:
        return "the docker CLI is not on PATH"
    try:
        result = _docker("info", "--format", "{{.ServerVersion}}", timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"docker info failed: {error}"
    if result.returncode != 0:
        detail = (result.stderr.strip().splitlines() or ["no output"])[-1]
        return f"Docker is not running: {detail}"
    return None


def start_openldap(timeout=STARTUP_TIMEOUT):
    """Start and seed a container; the caller must ``remove_container(ldap.container)``."""
    container = CONTAINER_PREFIX + secrets.token_hex(4)
    admin_password = secrets.token_urlsafe(16)
    _docker(
        "run", "-d", "--name", container, "--ulimit", "nofile=1024:1024",
        "-e", "LDAP_ORGANISATION=Keep", "-e", "LDAP_DOMAIN=keep.local",
        "-e", f"LDAP_ADMIN_PASSWORD={admin_password}", "-e", "LDAP_TLS=false",
        "-p", "127.0.0.1::389", OPENLDAP_REFERENCE,
        timeout=300,
    )
    ldap = None
    try:
        mapping = _docker("port", container, "389/tcp").stdout.strip().splitlines()[0]
        host, _, port = mapping.rpartition(":")
        ldap = OpenLDAP(container, host, int(port), admin_password, generated_passwords())
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                ldap.seed()
                probe = next(iter(ldap.users))
                ldap.connect(probe, ldap.users[probe]).unbind()
                return ldap
            except Exception as error:  # still starting: retry until the deadline
                last = error
                time.sleep(1)
        logs = _docker("logs", "--tail", "20", container, check=False).stdout
        raise RuntimeError(f"OpenLDAP did not accept binds in {timeout}s: {last!r}\n{logs}")
    except BaseException:
        remove_container(container)
        raise


def remove_container(container):
    _docker("rm", "-f", container, timeout=60, check=False)


@pytest.fixture(scope="session")
def openldap():
    """A seeded OpenLDAP for the session (see :class:`OpenLDAP`); skips without Docker."""
    reason = _docker_available()
    if reason:
        pytest.skip(f"OpenLDAP fixture needs Docker: {reason}")
    if os.environ.get("KEEP_TEST_SKIP_PULL") != "1":
        pulled = _docker("pull", OPENLDAP_REFERENCE, timeout=600, check=False)
        if pulled.returncode != 0:
            pytest.skip(f"cannot pull {OPENLDAP_REFERENCE}: {pulled.stderr.strip()[-300:]}")
    ldap = start_openldap()
    try:
        yield ldap
    finally:
        remove_container(ldap.container)


@pytest.fixture
def adapi_factory():
    """``adapi_factory(directory=None, **LocalADAPI options)`` starts more mock servers
    (127.0.0.1, free port, ClientId ``MOCK_CLIENT_ID`` unless given); all stop at teardown.
    """
    servers = []

    def start(directory=None, **options):
        options.setdefault("client_id", MOCK_CLIENT_ID)
        data = local_adapi.sample_directory() if directory is None else directory
        server = local_adapi.LocalADAPI(data, **options).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.stop()


@pytest.fixture
def mock_adapi(adapi_factory):
    """A fresh mock ADAPI with ``local_adapi.sample_directory()``; ``.base_url``,
    ``.client_id`` and the hook methods of ``local_adapi.LocalADAPI``.
    """
    return adapi_factory()
