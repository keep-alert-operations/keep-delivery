"""Local identity setup: OpenLDAP and mock ADAPI data, and a value-preserving move.

Requirements: REV-73; identity configuration 14 (local directory development exception);
plan A16, A18.
"""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import local_adapi  # noqa: E402

LEGACY = {
    "users": {
        "local-admin": {"id": "0f6c8f9e-2b8a-4d8e-9a4f-7f8c3a1e0001", "password": "admin-pw",
                        "label": "Local Administrator", "group": "Keep Administrators"},
        "local-editor": {"id": "0f6c8f9e-2b8a-4d8e-9a4f-7f8c3a1e0002", "password": "editor-pw",
                         "label": "Local Editor", "group": "Developers"},
        "local-viewer": {"id": "0f6c8f9e-2b8a-4d8e-9a4f-7f8c3a1e0003", "password": "viewer-pw",
                         "label": "Local Viewer", "group": "Observers"},
    },
    "groups": {
        "Keep Administrators": "5d1d3a52-8c11-4f0e-8a3b-1b2c3d4e0001",
        "Developers": "5d1d3a52-8c11-4f0e-8a3b-1b2c3d4e0002",
        "Observers": "5d1d3a52-8c11-4f0e-8a3b-1b2c3d4e0003",
    },
    "console_username": "bootstrap-admin",
    "console_password": "legacy-console",
    "web_secret": "legacy-web",
    "directory_secret": "legacy-directory",
    "wrapping_key": "d3JhcHBpbmcta2V5LWZvci10aGUtdGVzdC1vbmx5LTAwMDA=",
    "deployment_epoch": "7e0d2c3b-0a49-4a9f-8f2a-000000000001",
    "api_password": "legacy-api-role",
    "worker_password": "legacy-worker-role",
}


def load(name="local_identity_setup"):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/local_identity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup(monkeypatch, tmp_path):
    module = load()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "STATE", tmp_path / ".local/identity")
    (tmp_path / ".local").mkdir()
    monkeypatch.setattr(module, "tls", lambda: None)
    return module


def write_legacy(root, values=LEGACY):
    legacy = root / ".local/keycloak"
    (legacy / "tls").mkdir(parents=True)
    (legacy / "credentials.json").write_text(json.dumps(values), encoding="utf-8")
    (legacy / "tls/localhost.pem").write_bytes(b"legacy certificate bytes\n")
    (legacy / "tls/localhost-key.pem").write_bytes(b"legacy private key bytes\n")
    (legacy / "processes.json").write_text('[{"Role":"Api","Id":1,"StartTime":"x"}]')
    return legacy


def snapshot(folder):
    return {path.relative_to(folder): path.read_bytes()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def test_fresh_prepare_creates_local_users_groups_and_secrets(setup):
    """Requirements: REV-73; identity configuration 14."""
    setup.prepare()
    state = setup.STATE
    creds = json.loads((state / "credentials.json").read_text())
    assert set(creds["users"]) == {name for name, *_ in setup.USERS}
    assert all(user["password"] for user in creds["users"].values())
    hebrew = creds["users"]["local-hebrew"]["password"]
    assert any("֐" <= c <= "׿" for c in hebrew) and any(c.isascii() and c.isalpha()
                                                                   for c in hebrew)
    for field in ("ldap_admin_password", "adapi_client_id", "wrapping_key", "deployment_epoch"):
        assert creds[field]
    assert "remap" not in creds
    secret = (state / "openldap-admin-password.txt").read_text(encoding="utf-8")
    assert secret == creds["ldap_admin_password"]

    data = json.loads((state / "adapi-directory.json").read_text())
    assert "password" not in json.dumps(data).lower()
    directory = local_adapi.Directory(data)
    names = {row["sAMAccountName"]: row for row in directory.users}
    assert names["local-disabled"]["userAccountControl"] == local_adapi.DISABLED
    assert names["local-admin"]["userAccountControl"] == local_adapi.ACTIVE
    groups = {row["sAMAccountName"]: row for row in directory.groups}
    assert set(groups) == {"keep-administrators", "keep-developers", "keep-dev-announce",
                           "keep-observers"}
    assert groups["keep-dev-announce"]["groupType"] == local_adapi.DISTRIBUTION_GROUP

    def members(group):
        return {row["sAMAccountName"] for row in directory.recursive_members(group)}

    assert members(setup.SUPERADMIN_GROUP) == {"local-admin"}
    # keep-developers -> keep-dev-announce (distribution) -> local-distribution/disabled.
    assert members("keep-developers") == {"local-editor", "local-hebrew",
                                          "local-distribution", "local-disabled"}
    assert members("keep-observers") == {"local-viewer"}


def test_prepare_moves_legacy_values_unchanged_and_leaves_the_old_folder(setup):
    """Requirements: REV-73; identity configuration 14 (preserve keys and epoch)."""
    legacy = write_legacy(setup.ROOT)
    before = snapshot(legacy)
    setup.prepare()
    assert snapshot(legacy) == before
    state = setup.STATE
    creds = json.loads((state / "credentials.json").read_text())
    for field in ("wrapping_key", "deployment_epoch", "api_password", "worker_password"):
        assert creds[field] == LEGACY[field]
    for name, user in LEGACY["users"].items():
        assert creds["users"][name]["password"] == user["password"]
        assert creds["users"][name]["label"] == user["label"]
    assert creds["users"]["local-admin"]["groups"] == ["keep-administrators"]
    assert creds["users"]["local-editor"]["groups"] == ["keep-developers"]
    assert {"local-hebrew", "local-distribution", "local-disabled"} <= set(creds["users"])
    assert creds["remap"] == {
        "source": "keycloak-local",
        "users": {name: user["id"] for name, user in LEGACY["users"].items()},
        "groups": LEGACY["groups"],
    }
    for retired in ("console_username", "console_password", "web_secret", "directory_secret"):
        assert retired not in creds
    assert (state / "tls/localhost.pem").read_bytes() == b"legacy certificate bytes\n"
    assert (state / "tls/localhost-key.pem").read_bytes() == b"legacy private key bytes\n"
    assert (state / "processes.json").read_bytes() == (legacy / "processes.json").read_bytes()


def test_repeated_prepare_preserves_every_file(setup):
    """Requirements: REV-73; identity configuration 14."""
    write_legacy(setup.ROOT)
    setup.prepare()
    before = snapshot(setup.STATE)
    setup.prepare()
    assert snapshot(setup.STATE) == before


def test_existing_identity_credentials_are_never_replaced_by_the_old_folder(setup):
    """Requirements: REV-73."""
    setup.prepare()
    creds = (setup.STATE / "credentials.json").read_bytes()
    write_legacy(setup.ROOT)
    setup.prepare()
    assert (setup.STATE / "credentials.json").read_bytes() == creds


@pytest.mark.parametrize("field", ["wrapping_key", "deployment_epoch"])
def test_move_refuses_to_invent_a_missing_key_or_epoch(setup, field):
    """Requirements: REV-73; identity configuration 14."""
    write_legacy(setup.ROOT, {k: v for k, v in LEGACY.items() if k != field})
    with pytest.raises(RuntimeError, match=field):
        setup.prepare()
    assert not (setup.STATE / "credentials.json").exists()


def test_environment_names_ldap_and_adapi_and_no_provider_settings(setup):
    """Requirements: REV-73; identity configuration 14."""
    write_legacy(setup.ROOT)
    setup.prepare()
    values = setup.environment()
    creds = setup.credentials()
    assert values["KEEP_LDAP_HOST"] == "127.0.0.1"
    assert values["KEEP_LDAP_PORT"] == "58389"
    assert values["KEEP_LDAP_USE_TLS"] == "false"
    assert values["KEEP_LDAP_BIND_FORMAT"] == "uid={username},ou=people,dc=keep,dc=local"
    assert values["KEEP_ADAPI_URL"] == "http://127.0.0.1:58092"
    assert values["KEEP_ADAPI_CLIENT_ID"] == creds["adapi_client_id"]
    assert values["KEEP_SUPERADMIN_GROUP"] == "keep-administrators"
    assert values["KEEP_WRAPPING_KEY"] == LEGACY["wrapping_key"]
    assert values["KEEP_DEPLOYMENT_EPOCH"] == LEGACY["deployment_epoch"]
    assert "legacy-api-role" in values["KEEP_DATABASE_URL"]
    assert values["KEEP_TICKET_COUNT_URL"] == "http://127.0.0.1:58090/count"
    assert values["KEEP_GRAFANA_PANEL_ORIGINS"].endswith(",http://127.0.0.1:58091")
    assert not [key for key in values if "OIDC" in key or "DIRECTORY" in key]
    assert "KEEP_SUPERADMIN_OBJECT_ID" not in values


def test_remap_mapping_comes_from_credentials_and_the_label_table(setup):
    """Requirements: REV-73; plan "One-time local remap"."""
    write_legacy(setup.ROOT)
    setup.prepare()
    creds = setup.credentials()
    directory = json.loads((setup.STATE / "adapi-directory.json").read_text())
    mapping = setup.remap_mapping(creds, directory)
    assert mapping == {
        LEGACY["users"]["local-admin"]["id"]: ("user", "local-admin"),
        LEGACY["users"]["local-editor"]["id"]: ("user", "local-editor"),
        LEGACY["users"]["local-viewer"]["id"]: ("user", "local-viewer"),
        LEGACY["groups"]["Keep Administrators"]: ("team", "keep-administrators"),
        LEGACY["groups"]["Developers"]: ("team", "keep-developers"),
        LEGACY["groups"]["Observers"]: ("team", "keep-observers"),
    }


@pytest.mark.parametrize("change,message", [
    ("label", "Platform Team"),
    ("directory", "local-viewer"),
    ("user", "local-ghost"),
])
def test_remap_mapping_stops_on_any_gap(setup, change, message):
    """Requirements: REV-73; plan "One-time local remap" (stop before changing anything)."""
    write_legacy(setup.ROOT)
    setup.prepare()
    creds = setup.credentials()
    directory = json.loads((setup.STATE / "adapi-directory.json").read_text())
    if change == "label":
        creds["remap"]["groups"]["Platform Team"] = "5d1d3a52-8c11-4f0e-8a3b-1b2c3d4e0009"
    elif change == "directory":
        directory["users"] = [u for u in directory["users"]
                              if u["sAMAccountName"] != "local-viewer"]
    else:
        creds["remap"]["users"]["local-ghost"] = "0f6c8f9e-2b8a-4d8e-9a4f-7f8c3a1e0009"
    with pytest.raises(setup.RemapError, match=message):
        setup.remap_mapping(creds, directory)


def test_start_order_and_compose_identity_services():
    """Requirements: REV-73; plan step 9 start order; A16 (no Keycloak service)."""
    source = (ROOT / "scripts/local-identity.ps1").read_text(encoding="utf-8")
    start = source.index("Invoke-Checked { & $python scripts/local_identity.py prepare }")
    order = [
        "scripts/local_identity.py prepare",
        "-f $compose up -d",
        "scripts/local_identity.py database",
        "scripts/local_identity.py remap",
        "scripts/local_identity.py environment",
        "Start-Owned $adapi",
        "scripts/local_identity.py wait",
        "scripts/local_identity.py configure",
        "Invoke-DirectoryRefresh -Bootstrap",
        "Role='Api'",
        "/auth/session",
    ]
    positions = [source.index(step, start) for step in order]
    assert positions == sorted(positions)
    # The first Start after REV-73 must still find what the earlier launcher started:
    # records come from .local/keycloak until prepare has moved them.
    reader = source[source.index("function Read-Records"):source.index("function Save-Records")]
    assert "'.local/keycloak/processes.json'" in reader
    loaded = source.index("$script:records = Read-Records")
    assert loaded < source.index("foreach ($record in $script:records) { Stop-Owned $record }")
    assert loaded < start
    assert "-m keep.worker directory --once" in source
    assert "KEEP_DIRECTORY_BOOTSTRAP_QUALIFIED" in source
    assert "keycloak.compose" not in source
    compose = (ROOT / "deploy/local/compose.yaml").read_text(encoding="utf-8")
    assert "keycloak" not in compose.lower()
    assert "osixia/openldap:1.5.0@sha256:" in compose
    assert '"127.0.0.1:58389:389"' in compose
    assert ".local/identity/tls/localhost.pem:/tls/localhost.pem:ro" in compose
    assert not (ROOT / "deploy/local/keycloak.compose.yaml").exists()
