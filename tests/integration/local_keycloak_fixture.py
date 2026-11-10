import ssl
from uuid import UUID
import httpx
import pytest
from keep.identity.keycloak_directory import LocalKeycloakConfig

def uid(number):
    return str(UUID(int=number))

@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("KEEP_ENVIRONMENT", "development")
    monkeypatch.setenv("KEEP_LOCAL_DIRECTORY_SECRET", "test-secret")
    context = ssl.create_default_context()
    monkeypatch.setattr(
        "keep.identity.keycloak_directory.ssl.create_default_context", lambda **_: context
    )
    return LocalKeycloakConfig(
        issuer="https://localhost:58443/realms/keep-local",
        source="keycloak-local",
        client_id="keep-directory",
        client_secret_env="KEEP_LOCAL_DIRECTORY_SECRET",
        ca_file="trusted-local.pem",
        superadmin_group=uid(10),
        max_objects=50,
        page_size=2,
        max_pages=100,
        local_only=True,
    )

class Realm:
    def __init__(self):
        self.users = [
            {"id": uid(1), "username": "admin", "enabled": True},
            {"id": uid(2), "username": "disabled", "enabled": False},
            {
                "id": uid(3),
                "username": "service-account",
                "enabled": True,
                "serviceAccountClientId": "keep-directory",
            },
        ]
        self.groups = [{"id": uid(10), "name": "admins"}]
        self.children = {uid(10): [{"id": uid(11), "name": "nested"}], uid(11): []}
        self.members = {uid(10): [self.users[1]], uid(11): [self.users[0]]}
        self.fail = None
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        if request.method == "POST":
            assert request.url.path.endswith("/protocol/openid-connect/token")
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "test-token"})
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer test-token"
        path = request.url.path.removeprefix("/admin/realms/keep-local")
        if path == self.fail:
            return httpx.Response(503, text="sensitive upstream failure")
        if path == "/users":
            rows = self.users
        elif path == "/groups":
            rows = self.groups
        elif path.endswith("/children"):
            rows = self.children[path.split("/")[2]]
        elif path.endswith("/members"):
            rows = self.members[path.split("/")[2]]
        else:
            return httpx.Response(404)
        first, limit = int(request.url.params["first"]), int(request.url.params["max"])
        return httpx.Response(200, json=rows[first : first + limit])
