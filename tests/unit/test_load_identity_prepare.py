import importlib.util
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[2] / "qualification/load-test-identity/prepare.py"
spec = importlib.util.spec_from_file_location("load_identity_prepare", path)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def test_real_oidc_and_bounded_native_identities():
    result = prepare.generate("https://app.example.invalid", "https://id.example.invalid", 50)
    realm = result["keep-local-realm.json"]
    assert len(realm["users"]) == 52
    web, directory = realm["clients"]
    assert not web["directAccessGrantsEnabled"] and web["standardFlowEnabled"]
    assert web["attributes"]["pkce.code.challenge.method"] == "S256"
    assert web["redirectUris"] == ["https://app.example.invalid/auth/callback"]
    assert directory["serviceAccountsEnabled"]
    assert realm["users"][-1]["clientRoles"]["realm-management"] == [
        "view-users", "query-users", "query-groups",
    ]
    assert result["local-directory.json"]["issuer"] == (
        "https://localhost:8443/realms/keep-local"
    )
    assert result["api-identity.env"]["KEEP_OIDC_ISSUER"] == (
        "https://id.example.invalid/realms/keep-local"
    )
    assert web["secret"] == result["api-identity.env"]["KEEP_OIDC_CLIENT_SECRET"]
    assert directory["secret"] == result["publisher-identity.env"]["KEEP_LOCAL_DIRECTORY_SECRET"]
    assert len({user["id"] for user in realm["users"][:-1]}) == 51
    assert sum(bool(user["groups"]) for user in realm["users"][:-1]) == 1


@pytest.mark.parametrize("value", ["http://id.test", "https://id.test/realm", "https://a:b@id.test"])
def test_reject_bad_origins(value):
    with pytest.raises(ValueError):
        prepare.generate(value, "https://id.test", 5)
