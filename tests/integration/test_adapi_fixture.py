# ruff: noqa: F811 -- imported reusable fixtures
"""The sign-in test doubles work: OpenLDAP binds and answers WhoAmI; the mock ADAPI serves.

Requirements: REV-73; plan A9, A11, A20, A22.
"""

import subprocess

import httpx
import ldap3
from ldap3.core.exceptions import LDAPInvalidCredentialsResult, LDAPPasswordIsMandatoryError
import pytest

from adapi_fixture import (  # noqa: F401
    CONTAINER_PREFIX,
    HEBREW_USER,
    LDAP_USERS,
    MOCK_CLIENT_ID,
    OPENLDAP_DIGEST,
    OPENLDAP_IMAGE,
    adapi_factory,
    mock_adapi,
    openldap,
)
import local_adapi

pytestmark = pytest.mark.integration


def test_container_is_the_pinned_image_on_loopback(openldap):
    """Requirements: REV-73; plan Design Local (pinned OpenLDAP image)."""
    assert openldap.container.startswith(CONTAINER_PREFIX)
    assert openldap.host == "127.0.0.1" and openldap.port not in (389, 58389)
    image = subprocess.run(
        ["docker", "inspect", "--format", "{{.Image}}", openldap.container],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    digests = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{join .RepoDigests \" \"}}", image],
        capture_output=True, text=True, check=True,
    ).stdout
    assert f"osixia/openldap@{OPENLDAP_DIGEST}" in digests
    assert OPENLDAP_IMAGE == "osixia/openldap:1.5.0"
    assert set(LDAP_USERS) <= set(openldap.users)


def test_bind_and_who_am_i_name_the_bound_entry(openldap):
    """Requirements: REV-73; plan Login API (bind with WhoAmI), A20 (dn: form passes)."""
    for username in ("local-admin", "local-editor", "local-viewer"):
        conn = openldap.connect(username, openldap.users[username])
        try:
            assert conn.extend.standard.who_am_i() == (
                f"dn:uid={username},ou=people,dc=keep,dc=local"
            )
        finally:
            conn.unbind()
    assert openldap.bind_format.format(username="x") == "uid=x,ou=people,dc=keep,dc=local"


def test_mixed_hebrew_english_password_binds_as_utf8(openldap):
    """Requirements: REV-73; plan A9 (UTF-8 bytes, mixed Hebrew/English password)."""
    password = openldap.users[HEBREW_USER]
    assert any("֐" <= c <= "׿" for c in password) and "Keep" in password
    conn = openldap.connect(HEBREW_USER, password.encode("utf-8"))
    try:
        assert conn.extend.standard.who_am_i() == f"dn:{openldap.bind_dn(HEBREW_USER)}"
    finally:
        conn.unbind()
    with pytest.raises(LDAPInvalidCredentialsResult):
        openldap.connect(HEBREW_USER, password.replace("Keep", "keep").encode("utf-8"))


def test_wrong_password_is_invalid_credentials(openldap):
    """Requirements: REV-73; plan A2 (wrong password refused)."""
    with pytest.raises(LDAPInvalidCredentialsResult):
        openldap.connect("local-admin", openldap.users["local-admin"] + "x")
    with pytest.raises(LDAPInvalidCredentialsResult):
        openldap.connect("nobody", "whatever-1")


def test_empty_password_is_never_an_authenticated_bind(openldap):
    """Requirements: REV-73; plan A3 (empty password blocked before binding).

    ldap3 refuses an empty simple-bind password itself. Sent anyway, OpenLDAP answers
    unwillingToPerform (53) for a DN with no password, while AD treats it as an anonymous
    bind; the product blocks empty passwords before binding.
    """
    for password in ("", b""):
        with pytest.raises(LDAPPasswordIsMandatoryError):
            ldap3.Connection(
                openldap.server(), user=openldap.bind_dn("local-admin"), password=password,
                raise_exceptions=True, auto_bind=True,
            )
    anonymous = ldap3.Connection(openldap.server(), raise_exceptions=True)
    assert anonymous.bind()
    assert not anonymous.extend.standard.who_am_i()
    anonymous.unbind()


def test_added_user_can_bind(openldap):
    """Requirements: REV-73; plan A20 (extra LDAP-only accounts for later tests)."""
    openldap.add_user("local-extra", "Extra-pass-1")
    openldap.add_user("local-extra", "Extra-pass-2")
    openldap.connect("local-extra", "Extra-pass-2").unbind()
    with pytest.raises(LDAPInvalidCredentialsResult):
        openldap.connect("local-extra", "Extra-pass-1")


def test_mock_adapi_fixture_serves_the_ldap_users(openldap, mock_adapi):
    """Requirements: REV-73; plan A1, A4, A20 (same usernames in LDAP and ADAPI)."""
    assert mock_adapi.client_id == MOCK_CLIENT_ID
    headers = {"ClientId": mock_adapi.client_id}
    for username in LDAP_USERS:
        response = httpx.get(mock_adapi.base_url + "/users", params={"samAccountName": username},
                             headers=headers, timeout=10)
        expected = [] if username == "local-unlisted" else [username]
        assert [r["sAMAccountName"] for r in response.json()] == expected
    admins = httpx.get(
        mock_adapi.base_url + "/groups/members/user",
        params={"samAccountName": local_adapi.SUPERADMIN_GROUP, "isRecursive": "true"},
        headers=headers, timeout=10,
    ).json()
    assert [r["sAMAccountName"] for r in admins] == ["local-admin"]
    assert httpx.get(mock_adapi.base_url + "/users", timeout=10).status_code == 401
    assert mock_adapi.count("users") == len(LDAP_USERS) + 1


def test_adapi_factory_starts_independent_servers(adapi_factory, mock_adapi):
    """Requirements: REV-73; plan A22, A25 (scale datasets with latency)."""
    big = adapi_factory(local_adapi.large_group(members=1500), client_id="other")
    big.set_latency("members", 0.05)
    rows = httpx.get(big.base_url + "/groups/members/user",
                     params={"samAccountName": "big-team", "isRecursive": "true"},
                     headers={"ClientId": "other"}, timeout=10).json()
    assert len(rows) == 1500
    assert big.port != mock_adapi.port and mock_adapi.count() == 0
