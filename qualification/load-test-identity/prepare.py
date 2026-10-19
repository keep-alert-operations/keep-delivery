"""Generate a private, isolated Keycloak test realm; never contact a running service."""

import argparse
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from uuid import uuid4


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("Origins must be HTTPS scheme/host/optional port only")
    return value.rstrip("/")


def generate(app_origin, keycloak_origin, users):
    """Native UUIDs, protected password evidence and ordinary authorization-code login."""
    app_origin, keycloak_origin = origin(app_origin), origin(keycloak_origin)
    if not 1 <= users <= 200:
        raise ValueError("Choose 1..200 load users; configure larger realms separately")
    group_id = str(uuid4())
    web_secret, directory_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    accounts = [{"username": "load-admin", "id": str(uuid4()),
                 "password": secrets.token_urlsafe(24), "admin": True}]
    accounts += [{"username": f"load-user-{i:03d}", "id": str(uuid4()),
                  "password": secrets.token_urlsafe(24), "admin": False}
                 for i in range(1, users + 1)]
    realm = {
        "realm": "appchi-local", "displayName": "Appchi isolated load test",
        "enabled": True, "sslRequired": "all", "registrationAllowed": False,
        "rememberMe": False, "resetPasswordAllowed": False,
        "bruteForceProtected": True, "failureFactor": 5,
        "ssoSessionIdleTimeout": 1800, "ssoSessionMaxLifespan": 86400,
        "accessTokenLifespan": 300, "revokeRefreshToken": True,
        "refreshTokenMaxReuse": 0,
        "attributes": {"CreateDefaultClientScopes": "true"},
        "browserFlow": "appchi-password",
        "authenticationFlows": [{
            "alias": "appchi-password", "providerId": "basic-flow",
            "topLevel": True, "builtIn": False,
            "authenticationExecutions": [{
                "authenticator": "auth-username-password-form", "requirement": "REQUIRED",
                "priority": 10, "authenticatorFlow": False,
                "authenticatorConfig": "appchi-password-evidence",
            }],
        }],
        "authenticatorConfig": [{"alias": "appchi-password-evidence", "config": {
            "default.reference.value": "pwd", "default.reference.maxAge": "86400",
        }}],
        "groups": [{"id": group_id, "name": "Appchi Load Administrators"}],
        "clientScopes": [{"name": "appchi-directory", "protocol": "openid-connect",
                          "attributes": {"include.in.token.scope": "true"}}],
        "clients": [{
            "clientId": "appchi-web", "enabled": True, "protocol": "openid-connect",
            "publicClient": False, "secret": web_secret, "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False, "implicitFlowEnabled": False,
            "serviceAccountsEnabled": False,
            "redirectUris": [app_origin + "/auth/callback"], "webOrigins": [app_origin],
            "attributes": {
                "login_theme": "appchi", "pkce.code.challenge.method": "S256",
                "post.logout.redirect.uris": app_origin + "/signed-out",
                "backchannel.logout.url": app_origin + "/auth/backchannel-logout",
                "backchannel.logout.session.required": "true",
            },
            "defaultClientScopes": ["basic", "profile", "appchi-directory"],
            "optionalClientScopes": [],
            "protocolMappers": [{"name": name, "protocol": "openid-connect",
                                 "protocolMapper": f"oidc-{name}-mapper", "config": {
                                     "id.token.claim": "true", "access.token.claim": "true",
                                 }} for name in ("amr", "acr")],
        }, {
            "clientId": "appchi-directory", "enabled": True,
            "protocol": "openid-connect", "publicClient": False,
            "secret": directory_secret, "standardFlowEnabled": False,
            "directAccessGrantsEnabled": False, "implicitFlowEnabled": False,
            "serviceAccountsEnabled": True, "defaultClientScopes": ["roles"],
        }],
        "users": [{
            "username": account["username"], "id": account["id"], "enabled": True,
            "firstName": "Load", "lastName": account["username"],
            "email": account["username"] + "@example.invalid", "emailVerified": True,
            "groups": ["/Appchi Load Administrators"] if account["admin"] else [],
            "credentials": [{"type": "password", "value": account["password"],
                             "temporary": False}],
        } for account in accounts] + [{
            "username": "service-account-appchi-directory", "enabled": True,
            "serviceAccountClientId": "appchi-directory",
            "clientRoles": {"realm-management": ["view-users", "query-users", "query-groups"]},
        }],
    }
    directory = {
        "issuer": "https://localhost:8443/realms/appchi-local", "source": "keycloak-local",
        "client_id": "appchi-directory", "client_secret_env": "APPCHI_LOCAL_DIRECTORY_SECRET",
        "ca_file": "/etc/appchi/keycloak-ca.crt", "superadmin_group": group_id,
        "max_objects": 1000, "page_size": 100, "max_pages": 1000, "local_only": True,
    }
    api = {
        "APPCHI_PUBLIC_ORIGIN": app_origin,
        "APPCHI_OIDC_ISSUER": keycloak_origin + "/realms/appchi-local",
        "APPCHI_OIDC_CLIENT_ID": "appchi-web", "APPCHI_OIDC_CLIENT_SECRET": web_secret,
        "APPCHI_OIDC_DIRECTORY_SOURCE": "keycloak-local",
        "APPCHI_OIDC_DIRECTORY_ID_CLAIM": "sub",
        "APPCHI_OIDC_DIRECTORY_SCOPE": "appchi-directory",
        "APPCHI_OIDC_REQUIRED_ACR": "1", "APPCHI_OIDC_REQUIRED_AMR": '["pwd"]',
        "APPCHI_OIDC_CA_FILE": "/etc/appchi/keycloak-ca.crt",
        "APPCHI_SUPERADMIN_GROUP": group_id,
    }
    return {"appchi-local-realm.json": realm, "local-directory.json": directory,
            "credentials.json": {"users": accounts}, "api-identity.env": api,
            "publisher-identity.env": {"APPCHI_LOCAL_DIRECTORY_SECRET": directory_secret}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-origin", required=True)
    parser.add_argument("--keycloak-origin", required=True)
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    files = generate(args.app_origin, args.keycloak_origin, args.users)
    if args.output.exists():
        parser.error("Output must be a new private directory; refusing to replace identities")
    args.output.mkdir(parents=True, mode=0o700)
    for name, value in files.items():
        path = args.output / name
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            if name.endswith(".env"):
                stream.write("".join(f"{key}={item}\n" for key, item in value.items()))
            else:
                json.dump(value, stream, indent=2)
                stream.write("\n")
        os.chmod(path, 0o600)
    print("Created private realm, credentials, directory config and identity env fragments.")
    print("No services contacted. Merge env fragments into workload-specific Secrets.")


if __name__ == "__main__":
    main()
