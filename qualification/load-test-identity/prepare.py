"""Generate private, isolated load-test identities (REV-73); never contact a running service.

People sign in with a username and password checked by one LDAP bind against the
test OpenLDAP; the mock ADAPI (scripts/local_adapi.py) supplies the same people and
their groups. The output directory holds:

- ``credentials.json``: the plaintext passwords, for signing the load users in;
- ``openldap-users.ldif``: the OpenLDAP seed, with ``{SSHA}`` hashes only;
- ``adapi-directory.json``: the mock ADAPI's data (no passwords);
- ``api-identity.env``, ``publisher-identity.env`` and ``adapi-mock.env``: settings
  to merge into the Product API, identity-tooling and mock ADAPI Secrets;
- ``openldap-admin-password``: the OpenLDAP admin password for its image settings.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

BASE_DN = "dc=keep,dc=local"
PEOPLE_DN = "ou=people," + BASE_DN
BIND_FORMAT = "uid={username}," + PEOPLE_DN
ADMIN_GROUP = "keep-load-administrators"
USER_GROUP = "keep-load-users"
# Service names rendered by deploy/openshift/load-test/render.py.
LDAP_HOST = "keep-openldap"
LDAP_PORT = 389
ADAPI_URL = "http://keep-adapi-mock:8080"


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("Origins must be HTTPS scheme/host/optional port only")
    return value.rstrip("/")


def ssha(password):
    """An OpenLDAP ``{SSHA}`` userPassword; the password is hashed as UTF-8 bytes."""
    salt = secrets.token_bytes(8)
    digest = hashlib.sha1(password.encode("utf-8") + salt).digest()
    return "{SSHA}" + base64.b64encode(digest + salt).decode("ascii")


def ldif(accounts):
    """Base, people unit and one inetOrgPerson per account; values are plain ASCII."""
    entries = [
        [f"dn: {BASE_DN}", "objectClass: top", "objectClass: dcObject",
         "objectClass: organization", "o: Keep", "dc: keep"],
        [f"dn: {PEOPLE_DN}", "objectClass: top", "objectClass: organizationalUnit",
         "ou: people"],
    ]
    for account in accounts:
        name = account["username"]
        entries.append([
            "dn: " + BIND_FORMAT.format(username=name), "objectClass: top",
            "objectClass: person", "objectClass: organizationalPerson",
            "objectClass: inetOrgPerson", f"uid: {name}", f"cn: {account['label']}",
            f"sn: {name}", f"displayName: {account['label']}",
            "userPassword: " + ssha(account["password"]),
        ])
    return "\n\n".join("\n".join(lines) for lines in entries) + "\n"


def generate(app_origin, users):
    """Usernames as samAccountNames; one admin in the global super-admin group."""
    app_origin = origin(app_origin)
    if not 1 <= users <= 200:
        raise ValueError("Choose 1..200 load users; configure larger directories separately")
    accounts = [{"username": "load-admin", "label": "Load Administrator",
                 "password": secrets.token_urlsafe(24), "admin": True}]
    accounts += [{"username": f"load-user-{i:03d}", "label": f"Load User {i:03d}",
                  "password": secrets.token_urlsafe(24), "admin": False}
                 for i in range(1, users + 1)]
    client_id = secrets.token_urlsafe(32)
    directory = {
        "users": [{"sAMAccountName": a["username"], "displayName": a["label"]}
                  for a in accounts],
        "groups": [
            {"sAMAccountName": ADMIN_GROUP, "cn": "Keep Load Administrators",
             "displayName": "Keep Load Administrators",
             "members": [a["username"] for a in accounts if a["admin"]]},
            # Grant the test tenant to this group instead of one person at a time.
            {"sAMAccountName": USER_GROUP, "cn": "Keep Load Users",
             "displayName": "Keep Load Users",
             "members": [a["username"] for a in accounts if not a["admin"]]},
        ],
    }
    directory_env = {"KEEP_ADAPI_URL": ADAPI_URL, "KEEP_ADAPI_CLIENT_ID": client_id,
                     "KEEP_SUPERADMIN_GROUP": ADMIN_GROUP}
    api = {
        "KEEP_PUBLIC_ORIGIN": app_origin,
        "KEEP_LDAP_HOST": LDAP_HOST, "KEEP_LDAP_PORT": str(LDAP_PORT),
        "KEEP_LDAP_USE_TLS": "false", "KEEP_LDAP_BIND_FORMAT": BIND_FORMAT,
        **directory_env,
    }
    return {
        "credentials.json": {"users": [
            {"username": a["username"], "password": a["password"], "admin": a["admin"]}
            for a in accounts]},
        "openldap-users.ldif": ldif(accounts),
        "adapi-directory.json": directory,
        "api-identity.env": api,
        # The bootstrap flag is set only on the first refresh's command, never here.
        "publisher-identity.env": directory_env,
        "adapi-mock.env": {"KEEP_ADAPI_CLIENT_ID": client_id},
        "openldap-admin-password": secrets.token_urlsafe(32),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app-origin", required=True)
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    files = generate(args.app_origin, args.users)
    if args.output.exists():
        parser.error("Output must be a new private directory; refusing to replace identities")
    args.output.mkdir(parents=True, mode=0o700)
    for name, value in files.items():
        path = args.output / name
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            if name.endswith(".env"):
                stream.write("".join(f"{key}={item}\n" for key, item in value.items()))
            elif isinstance(value, str):
                stream.write(value)
            else:
                json.dump(value, stream, indent=2)
                stream.write("\n")
        os.chmod(path, 0o600)
    print("Created private credentials, OpenLDAP seed, mock ADAPI data and env fragments.")
    print("No services contacted. Merge env fragments into workload-specific Secrets.")


if __name__ == "__main__":
    main()
