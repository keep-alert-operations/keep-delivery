"""Load-test identities: OpenLDAP seed, mock ADAPI data and env fragments agree.

Requirements: REV-73; identity configuration 14 (LDAP bind for passwords, ADAPI for
people and groups).
"""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
path = ROOT / "qualification/load-test-identity/prepare.py"
spec = importlib.util.spec_from_file_location("load_identity_prepare", path)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)
sys.path.insert(0, str(ROOT / "scripts"))
import local_adapi  # noqa: E402


def entries(ldif):
    return [dict(line.split(": ", 1) for line in block.splitlines()
                 if not line.startswith("objectClass"))
            for block in ldif.strip().split("\n\n")]


def test_users_groups_and_passwords_line_up_across_every_file():
    """Requirements: REV-73; identity configuration 14."""
    result = prepare.generate("https://app.example.invalid", 50)
    users = result["credentials.json"]["users"]
    assert len(users) == 51
    assert [u["username"] for u in users if u["admin"]] == ["load-admin"]
    assert len({u["password"] for u in users}) == 51

    seeded = entries(result["openldap-users.ldif"])
    assert seeded[0]["dn"] == "dc=keep,dc=local" and seeded[1]["dn"] == "ou=people,dc=keep,dc=local"
    people = {e["uid"]: e for e in seeded[2:]}
    assert list(people) == [u["username"] for u in users]
    for user in users:
        entry = people[user["username"]]
        assert entry["dn"] == prepare.BIND_FORMAT.format(username=user["username"])
        assert user["password"] not in result["openldap-users.ldif"]
        assert entry["userPassword"].startswith("{SSHA}")
        raw = base64.b64decode(entry["userPassword"][6:])
        digest, salt = raw[:20], raw[20:]
        assert hashlib.sha1(user["password"].encode("utf-8") + salt).digest() == digest

    data = result["adapi-directory.json"]
    assert "password" not in json.dumps(data).lower()
    directory = local_adapi.Directory(data)
    assert {row["sAMAccountName"] for row in directory.users} == set(people)

    def members(group):
        return sorted(row["sAMAccountName"] for row in directory.recursive_members(group))

    assert members(prepare.ADMIN_GROUP) == ["load-admin"]
    assert members(prepare.USER_GROUP) == sorted(set(people) - {"load-admin"})

    api, publisher = result["api-identity.env"], result["publisher-identity.env"]
    mock = result["adapi-mock.env"]
    assert api["KEEP_PUBLIC_ORIGIN"] == "https://app.example.invalid"
    assert (api["KEEP_LDAP_HOST"], api["KEEP_LDAP_PORT"]) == ("keep-openldap", "389")
    assert api["KEEP_LDAP_USE_TLS"] == "false"
    assert api["KEEP_LDAP_BIND_FORMAT"] == "uid={username},ou=people,dc=keep,dc=local"
    assert api["KEEP_ADAPI_URL"] == publisher["KEEP_ADAPI_URL"] == "http://keep-adapi-mock:8080"
    assert (api["KEEP_ADAPI_CLIENT_ID"] == publisher["KEEP_ADAPI_CLIENT_ID"]
            == mock["KEEP_ADAPI_CLIENT_ID"])
    assert api["KEEP_SUPERADMIN_GROUP"] == publisher["KEEP_SUPERADMIN_GROUP"] == prepare.ADMIN_GROUP
    assert "KEEP_DIRECTORY_BOOTSTRAP_QUALIFIED" not in publisher
    fragments = json.dumps([api, publisher, mock]).upper()
    assert "OIDC" not in fragments and "KEYCLOAK" not in fragments
    assert result["openldap-admin-password"] not in json.dumps(
        [api, publisher, mock, data, result["openldap-users.ldif"]])


def test_cli_writes_new_private_files_and_never_replaces_them(tmp_path):
    """Requirements: REV-73."""
    out = tmp_path / "identity"
    command = [sys.executable, "-I", str(path), "--app-origin", "https://app.example.invalid",
               "--users", "3", "--output", str(out)]
    subprocess.run(command, check=True, capture_output=True, text=True)
    assert {p.name for p in out.iterdir()} == {
        "credentials.json", "openldap-users.ldif", "adapi-directory.json", "api-identity.env",
        "publisher-identity.env", "adapi-mock.env", "openldap-admin-password"}
    env = (out / "api-identity.env").read_text(encoding="utf-8")
    assert "KEEP_LDAP_BIND_FORMAT=uid={username},ou=people,dc=keep,dc=local\n" in env
    assert len(json.loads((out / "credentials.json").read_text())["users"]) == 4
    again = subprocess.run(command, capture_output=True, text=True)
    assert again.returncode != 0 and "refusing" in again.stderr


@pytest.mark.parametrize("value", ["http://app.test", "https://app.test/path",
                                   "https://a:b@app.test", "https://app.test/?q=1"])
def test_reject_bad_origins(value):
    with pytest.raises(ValueError):
        prepare.generate(value, 5)


@pytest.mark.parametrize("users", [0, 201])
def test_reject_unbounded_user_counts(users):
    with pytest.raises(ValueError):
        prepare.generate("https://app.test", users)
