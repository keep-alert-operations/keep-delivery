"""Generate a private, isolated Authelia + OpenLDAP load-test identity set offline.

Never contacts a running service. Produces an OpenLDAP seed LDIF, an Authelia
``configuration.yml`` whose ``keep-web`` client emits the raw ``entryUUID`` verbatim
in a dedicated ``keep-directory`` scope, a sanitized LDAP ``directory.json`` for
Keep's production directory worker, and API/publisher environment fragments. The
immutable super-admin group id (``entryUUID``) is server-generated, so it is left as
an explicit placeholder the deployment resolves after OpenLDAP seeds, exactly as the
local launcher's ``configure`` step does.
"""

import argparse
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit


BASE_DN = "dc=keep,dc=local"
PEOPLE_DN = "ou=people," + BASE_DN
GROUPS_DN = "ou=groups," + BASE_DN
ADMIN_BIND_DN = "cn=admin," + BASE_DN
DIRECTORY_SOURCE = "ldap-local"
DIRECTORY_SCOPE = "keep-directory"
DIRECTORY_ID_CLAIM = "entryuuid"
ADMIN_GROUP = "Keep Load Administrators"
# Resolved from OpenLDAP (entryUUID of the admin group) after the seed loads.
SUPERADMIN_PLACEHOLDER = "RESOLVE_AT_DEPLOY_entryUUID_of_cn=Keep Load Administrators"


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("Origins must be HTTPS scheme/host/optional port only")
    return value.rstrip("/")


def signing_key():
    """A real RSA signing key for Authelia when cryptography is present; else a
    clearly-marked placeholder the mirror/deploy step replaces offline."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        return "-----BEGIN PRIVATE KEY-----\nREPLACE_WITH_OIDC_SIGNING_KEY_AT_DEPLOY\n-----END PRIVATE KEY-----\n"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def user_dn(username):
    return f"uid={username},{PEOPLE_DN}"


def ldif(accounts):
    blocks = [
        "\n".join([f"dn: {BASE_DN}", "objectClass: top", "objectClass: dcObject",
                   "objectClass: organization", "o: Keep Load Test", "dc: keep"]),
        "\n".join([f"dn: {PEOPLE_DN}", "objectClass: organizationalUnit", "ou: people"]),
        "\n".join([f"dn: {GROUPS_DN}", "objectClass: organizationalUnit", "ou: groups"]),
    ]
    for account in accounts:
        blocks.append("\n".join([
            f"dn: {user_dn(account['username'])}",
            "objectClass: inetOrgPerson",
            f"uid: {account['username']}",
            f"cn: Load {account['username']}",
            f"sn: {account['username']}",
            "givenName: Load",
            f"mail: {account['username']}@example.invalid",
            "employeeType: active",
            f"userPassword: {account['password']}",
        ]))
    admins = [user_dn(a["username"]) for a in accounts if a["admin"]]
    group = [f"dn: cn={ADMIN_GROUP},{GROUPS_DN}", "objectClass: groupOfNames",
             f"cn: {ADMIN_GROUP}"] + [f"member: {dn}" for dn in admins]
    blocks.append("\n".join(group))
    return "\n\n".join(blocks) + "\n"


def authelia_config(web_secret, key):
    return {
        "theme": "light",
        "server": {"address": "tcp://0.0.0.0:9091",
                   "tls": {"certificate": "/config/tls/public.crt", "key": "/config/tls/private.key"}},
        "log": {"level": "info"},
        "authentication_backend": {"ldap": {
            "address": "ldap://keep-openldap:389", "implementation": "custom",
            "base_dn": BASE_DN, "additional_users_dn": "ou=people",
            "additional_groups_dn": "ou=groups",
            "users_filter": "(&({username_attribute}={input})(objectClass=inetOrgPerson))",
            "groups_filter": "(&(member={dn})(objectClass=groupOfNames))",
            "user": ADMIN_BIND_DN,
            "attributes": {"username": "uid", "display_name": "cn", "mail": "mail",
                           "group_name": "cn",
                           "extra": {"entryUUID": {"name": DIRECTORY_ID_CLAIM,
                                                   "multi_valued": False, "value_type": "string"}}}}},
        "access_control": {"default_policy": "one_factor"},
        "session": {"cookies": [{"domain": "REQUIRED_APPLICATION_COOKIE_DOMAIN",
                                 "authelia_url": "https://REQUIRED_AUTHELIA_HOST"}]},
        "storage": {"local": {"path": "/data/db.sqlite3"}},
        "notifier": {"filesystem": {"filename": "/data/notification.txt"}},
        "identity_validation": {"reset_password": {}},
        "identity_providers": {"oidc": {
            "jwks": [{"key_id": "keep", "algorithm": "RS256", "use": "sig", "key": key}],
            "claims_policies": {"keep_directory": {
                "custom_claims": {DIRECTORY_ID_CLAIM: {"attribute": DIRECTORY_ID_CLAIM}}}},
            "scopes": {DIRECTORY_SCOPE: {"claims": [DIRECTORY_ID_CLAIM]}},
            "clients": [{
                "client_id": "keep-web", "client_name": "Keep", "public": False,
                "client_secret": "$plaintext$" + web_secret,
                "authorization_policy": "one_factor", "consent_mode": "implicit",
                "require_pkce": True, "pkce_challenge_method": "S256",
                "redirect_uris": ["REPLACE_APP_ORIGIN/auth/callback"],
                "post_logout_redirect_uris": ["REPLACE_APP_ORIGIN/signed-out"],
                "scopes": ["openid", "profile", DIRECTORY_SCOPE],
                "claims_policy": "keep_directory", "response_types": ["code"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "client_secret_basic"}]}}}


def generate(app_origin, authelia_origin, users):
    """Server-generated immutable entryUUIDs; real OIDC authorization-code login."""
    app_origin, authelia_origin = origin(app_origin), origin(authelia_origin)
    if not 1 <= users <= 200:
        raise ValueError("Choose 1..200 load users; configure larger directories separately")
    web_secret, directory_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    accounts = [{"username": "load-admin", "password": secrets.token_urlsafe(24), "admin": True}]
    accounts += [{"username": f"load-user-{i:03d}", "password": secrets.token_urlsafe(24),
                  "admin": False} for i in range(1, users + 1)]
    config = authelia_config(web_secret, signing_key())
    client = config["identity_providers"]["oidc"]["clients"][0]
    client["redirect_uris"] = [app_origin + "/auth/callback"]
    client["post_logout_redirect_uris"] = [app_origin + "/signed-out"]
    directory = {
        "source": DIRECTORY_SOURCE, "hostname": "REQUIRED_OPENLDAP_HOST", "port": 636,
        "bind_dn": ADMIN_BIND_DN, "password_env": "KEEP_LOCAL_DIRECTORY_SECRET",
        "ca_file": "/etc/keep/openldap-ca.crt",
        "connect_timeout": 3, "receive_timeout": 5, "page_size": 100, "max_pages": 1000,
        "max_objects": 1000,
        "schema": {"base_dn": BASE_DN, "user_filter": "(objectClass=inetOrgPerson)",
                   "team_filter": "(objectClass=groupOfNames)", "id_attribute": "entryUUID",
                   "label_attribute": "cn", "member_attribute": "member",
                   "eligibility_attribute": "employeeType", "eligibility_values": ["active"],
                   "id_encoding": "uuid", "schema_qualified": True}}
    api = {
        "KEEP_PUBLIC_ORIGIN": app_origin,
        "KEEP_OIDC_ISSUER": authelia_origin,
        "KEEP_OIDC_CLIENT_ID": "keep-web", "KEEP_OIDC_CLIENT_SECRET": web_secret,
        "KEEP_OIDC_DIRECTORY_SOURCE": DIRECTORY_SOURCE,
        "KEEP_OIDC_DIRECTORY_ID_CLAIM": DIRECTORY_ID_CLAIM,
        "KEEP_OIDC_DIRECTORY_SCOPE": DIRECTORY_SCOPE,
        # KEEP_OIDC_REQUIRED_ACR intentionally omitted (optional for Authelia).
        "KEEP_OIDC_REQUIRED_AMR": '["pwd"]',
        "KEEP_OIDC_CA_FILE": "/etc/keep/authelia-ca.crt",
        "KEEP_SUPERADMIN_GROUP": SUPERADMIN_PLACEHOLDER,
        "KEEP_DIRECTORY_CONFIG": "/etc/keep/directory.json",
    }
    return {"seed.ldif": ldif(accounts), "configuration.yml": config,
            "directory.json": directory, "credentials.json": {"users": accounts},
            "api-identity.env": api,
            "publisher-identity.env": {"KEEP_LOCAL_DIRECTORY_SECRET": directory_secret}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-origin", required=True)
    parser.add_argument("--authelia-origin", required=True)
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    files = generate(args.app_origin, args.authelia_origin, args.users)
    if args.output.exists():
        parser.error("Output must be a new private directory; refusing to replace identities")
    args.output.mkdir(parents=True, mode=0o700)
    for name, value in files.items():
        path = args.output / name
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            if name.endswith(".env"):
                stream.write("".join(f"{key}={item}\n" for key, item in value.items()))
            elif name.endswith((".ldif",)):
                stream.write(value)
            else:
                json.dump(value, stream, indent=2)
                stream.write("\n")
        os.chmod(path, 0o600)
    print("Created private OpenLDAP seed, Authelia configuration, directory config,")
    print("credentials and identity env fragments. No services contacted.")
    print("Resolve KEEP_SUPERADMIN_GROUP from the admin group's entryUUID after seeding,")
    print("and merge env fragments into workload-specific Secrets.")


if __name__ == "__main__":
    main()
