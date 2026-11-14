"""Fresh local setups seed OpenLDAP + Authelia and reuse existing secrets on repeat."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def setup(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[2] / "scripts/local_identity.py"
    spec = importlib.util.spec_from_file_location("local_identity_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE", tmp_path)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / ".local").mkdir()
    monkeypatch.setattr(module, "tls", lambda: None)
    # The OIDC signing key is only needed as opaque PEM text for the Authelia config.
    monkeypatch.setattr(
        module, "oidc_signing_key", lambda: "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n"
    )
    return module


def test_fresh_setup_seeds_ldap_and_authelia_with_password_only_evidence(setup):
    setup.prepare()

    ldif = (setup.STATE / "openldap/seed.ldif").read_text()
    # Three human users, each an inetOrgPerson with an eligibility attribute.
    assert ldif.count("objectClass: inetOrgPerson") == 3
    assert ldif.count("employeeType: active") == 3
    for username in ("local-admin", "local-editor", "local-viewer"):
        assert f"uid={username},ou=people,dc=keep,dc=local" in ldif
    # The super-admin group holds a nested group so nested traversal stays exercised.
    assert "dn: cn=Keep Administrators,ou=groups,dc=keep,dc=local" in ldif
    assert "member: cn=Keep Automation,ou=groups,dc=keep,dc=local" in ldif
    assert ldif.count("objectClass: groupOfNames") == 4

    authelia = json.loads((setup.STATE / "authelia/configuration.yml").read_text())
    oidc = authelia["identity_providers"]["oidc"]
    client = oidc["clients"][0]
    assert client["client_id"] == "keep-web" and not client["public"]
    assert client["require_pkce"] and client["pkce_challenge_method"] == "S256"
    assert client["redirect_uris"] == ["https://localhost:3443/auth/callback"]
    assert client["post_logout_redirect_uris"] == ["https://localhost:3443/signed-out"]
    assert "keep-directory" in client["scopes"]
    # The linchpin: a dedicated scope + claims policy emit the raw entryUUID verbatim.
    assert oidc["scopes"]["keep-directory"]["claims"] == ["entryuuid"]
    assert oidc["claims_policies"]["keep_directory"]["custom_claims"]["entryuuid"][
        "attribute"
    ] == "entryuuid"
    assert (
        authelia["authentication_backend"]["ldap"]["attributes"]["extra"]["entryUUID"]["name"]
        == "entryuuid"
    )
    # Authelia secret files it loads by reference exist outside version control.
    for name in ("ldap_password", "session_secret", "oidc_hmac_secret", "jwt_secret"):
        assert (setup.STATE / "authelia/secrets" / name).read_text()

    directory = json.loads((setup.STATE / "directory.json").read_text())
    assert directory["source"] == "ldap-local"
    assert directory["password_env"] == "KEEP_LOCAL_DIRECTORY_SECRET"
    assert "password" not in directory
    assert directory["schema"]["id_attribute"] == "entryUUID"
    assert directory["schema"]["id_encoding"] == "uuid"
    assert directory["schema"]["schema_qualified"] is True

    # The application environment omits required_acr (optional for Authelia) and keeps
    # password evidence mandatory; the super-admin group id comes from configure().
    credentials_path = setup.STATE / "credentials.json"
    credentials = json.loads(credentials_path.read_text())
    credentials.update(
        api_password="test-api-password",
        worker_password="test-worker-password",
        superadmin_group="00000000-0000-0000-0000-000000000010",
    )
    setup.save(credentials_path, credentials)
    env = setup.environment()
    assert "KEEP_OIDC_REQUIRED_ACR" not in env
    assert json.loads(env["KEEP_OIDC_REQUIRED_AMR"]) == ["pwd"]
    assert env["KEEP_OIDC_ISSUER"] == "https://localhost:58443"
    assert env["KEEP_OIDC_CLIENT_ID"] == "keep-web"
    assert env["KEEP_OIDC_DIRECTORY_SOURCE"] == "ldap-local"
    assert env["KEEP_OIDC_DIRECTORY_ID_CLAIM"] == "entryuuid"
    assert env["KEEP_OIDC_DIRECTORY_SCOPE"] == "keep-directory"
    assert env["KEEP_SUPERADMIN_GROUP"] == "00000000-0000-0000-0000-000000000010"
    assert env["KEEP_DIRECTORY_CONFIG"].endswith("directory.json")


def test_environment_requires_resolved_superadmin_group(setup):
    setup.prepare()
    credentials_path = setup.STATE / "credentials.json"
    credentials = json.loads(credentials_path.read_text())
    credentials.update(api_password="a", worker_password="w")
    setup.save(credentials_path, credentials)
    with pytest.raises(RuntimeError, match="super-admin group"):
        setup.environment()


def test_repeated_prepare_preserves_existing_secrets_and_seed(setup):
    setup.prepare()
    paths = [
        setup.STATE / "credentials.json",
        setup.STATE / "openldap/seed.ldif",
        setup.STATE / "authelia/configuration.yml",
        setup.STATE / "directory.json",
    ]
    before = [path.read_bytes() for path in paths]
    setup.prepare()
    assert [path.read_bytes() for path in paths] == before
