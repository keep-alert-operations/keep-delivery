import importlib.util
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[2] / "qualification/load-test-identity/prepare.py"
spec = importlib.util.spec_from_file_location("load_identity_prepare", path)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def test_real_oidc_and_bounded_ldap_identities():
    result = prepare.generate("https://app.example.invalid", "https://id.example.invalid", 50)

    ldif = result["seed.ldif"]
    assert ldif.count("objectClass: inetOrgPerson") == 51  # load-admin + 50 users
    assert "dn: cn=Keep Load Administrators,ou=groups,dc=keep,dc=local" in ldif
    assert "member: uid=load-admin,ou=people,dc=keep,dc=local" in ldif
    assert ldif.count("employeeType: active") == 51

    oidc = result["configuration.yml"]["identity_providers"]["oidc"]
    client = oidc["clients"][0]
    assert not client["public"] and client["require_pkce"]
    assert client["pkce_challenge_method"] == "S256"
    assert client["redirect_uris"] == ["https://app.example.invalid/auth/callback"]
    assert client["token_endpoint_auth_method"] == "client_secret_basic"
    # The raw entryUUID is carried verbatim by the dedicated scope + claims policy.
    assert oidc["scopes"]["keep-directory"]["claims"] == ["entryuuid"]
    assert oidc["claims_policies"]["keep_directory"]["custom_claims"]["entryuuid"][
        "attribute"
    ] == "entryuuid"
    assert client["client_secret"] == "$plaintext$" + result["api-identity.env"][
        "KEEP_OIDC_CLIENT_SECRET"
    ]

    directory = result["directory.json"]
    assert "password" not in directory
    assert directory["password_env"] == "KEEP_LOCAL_DIRECTORY_SECRET"
    assert directory["schema"]["id_attribute"] == "entryUUID"
    assert directory["schema"]["schema_qualified"] is True

    api = result["api-identity.env"]
    assert api["KEEP_OIDC_ISSUER"] == "https://id.example.invalid"
    assert "KEEP_OIDC_REQUIRED_ACR" not in api
    assert api["KEEP_OIDC_DIRECTORY_SOURCE"] == "ldap-local"
    assert api["KEEP_OIDC_DIRECTORY_ID_CLAIM"] == "entryuuid"
    # The immutable super-admin group id is resolved from OpenLDAP after seeding.
    assert "RESOLVE_AT_DEPLOY" in api["KEEP_SUPERADMIN_GROUP"]
    assert result["publisher-identity.env"]["KEEP_LOCAL_DIRECTORY_SECRET"]


@pytest.mark.parametrize("value", ["http://id.test", "https://id.test/realm", "https://a:b@id.test"])
def test_reject_bad_origins(value):
    with pytest.raises(ValueError):
        prepare.generate(value, "https://id.test", 5)
