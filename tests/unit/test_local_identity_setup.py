"""Fresh local setups use password login without rotating existing credentials."""

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
    return module


def test_fresh_local_realm_uses_password_authentication_only(setup):
    setup.prepare()
    realm = json.loads((setup.STATE / "import/keep-local-realm.json").read_text())
    flow = next(f for f in realm["authenticationFlows"] if f["alias"] == realm["browserFlow"])
    required = [e for e in flow["authenticationExecutions"] if e["requirement"] == "REQUIRED"]
    assert [e["authenticator"] for e in required] == ["auth-username-password-form"]
    evidence = next(
        c for c in realm["authenticatorConfig"]
        if c["alias"] == required[0]["authenticatorConfig"]
    )
    assert evidence["config"]["default.reference.value"] == "pwd"
    humans = [user for user in realm["users"] if not user.get("serviceAccountClientId")]
    assert len(humans) == 3
    assert all([c["type"] for c in user["credentials"]] == ["password"] for user in humans)

    credentials_path = setup.STATE / "credentials.json"
    credentials = json.loads(credentials_path.read_text())
    credentials.update(api_password="test-api-password", worker_password="test-worker-password")
    setup.save(credentials_path, credentials)
    assert json.loads(setup.environment()["KEEP_OIDC_REQUIRED_AMR"]) == ["pwd"]


def test_repeated_prepare_preserves_existing_user_ids_passwords_and_groups(setup):
    setup.prepare()
    paths = [setup.STATE / "credentials.json", setup.STATE / "import/keep-local-realm.json"]
    before = [path.read_bytes() for path in paths]
    setup.prepare()
    assert [path.read_bytes() for path in paths] == before
