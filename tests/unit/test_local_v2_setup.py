"""Development bootstrap cannot change nonlocal/recovery authority or leak probe keys."""

from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
local = importlib.import_module("local_v2")
verification = importlib.import_module("local_v2_verify")


def test_local_ports_default_and_partial_override(tmp_path):
    path = tmp_path / "ports.json"
    assert local.load_ports(path) == {"kong_admin": 58447, "grafana": 58448}
    path.write_text('{"kong_admin":58647}', encoding="utf-8")
    assert local.load_ports(path) == {"kong_admin": 58647, "grafana": 58448}


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"kong": 58647},
        {"kong_admin": True},
        {"grafana": "58648"},
        {"grafana": None},
        {"grafana": 1023},
        {"grafana": 65536},
        {"grafana": 58447},
        {"grafana": 58445},
    ],
)
def test_local_ports_reject_invalid_or_conflicting_configuration(tmp_path, value):
    path = tmp_path / "ports.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        local.load_ports(path)


def test_local_ports_malformed_json_fails_closed(tmp_path):
    path = tmp_path / "ports.json"
    path.write_text('{"grafana":', encoding="utf-8")
    with pytest.raises(ValueError):
        local.load_ports(path)


def test_persisted_ports_drive_python_and_compose_endpoints(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copied = scripts / "local_v2.py"
    copied.write_bytes(Path(local.__file__).read_bytes())
    state = tmp_path / ".local/operators"
    state.mkdir(parents=True)
    (state / "ports.json").write_text(
        '{"kong_admin":58647,"grafana":58648}', encoding="utf-8-sig"
    )
    # A stale shell variable must not split Python and launcher configuration.
    monkeypatch.setenv("KEEP_LOCAL_KONG_ADMIN_PORT", "60000")
    loaded = runpy.run_path(str(copied))
    assert loaded["KONG"] == "https://localhost:58647"
    assert loaded["GRAFANA"] == "https://localhost:58648"
    assert loaded["compose_ports"]() == {
        "KEEP_LOCAL_KONG_ADMIN_PORT": "58647",
        "KEEP_LOCAL_GRAFANA_PORT": "58648",
    }
    compose = (local.ROOT / "deploy/local/compose.yaml").read_text()
    assert "127.0.0.1:${KEEP_LOCAL_KONG_ADMIN_PORT:-58447}:8444" in compose
    assert "127.0.0.1:${KEEP_LOCAL_GRAFANA_PORT:-58448}:3000" in compose
    for launcher in ("local-v2.ps1", "local-identity.ps1"):
        source = (local.ROOT / "scripts" / launcher).read_text()
        assert source.index("scripts/local_v2.py ports") < source.index("docker compose")


def test_prepare_before_database_bootstrap_preserves_credentials(monkeypatch, tmp_path):
    state = tmp_path / "operators"
    monkeypatch.setattr(local, "STATE", state)
    monkeypatch.setattr(local, "ROOT", tmp_path)
    monkeypatch.setattr(local, "certificates", lambda: None)

    def unavailable_environment():
        raise AssertionError("Preparation must not require a database environment")

    monkeypatch.setattr(local, "environment", unavailable_environment)
    local.prepare()
    expected = local.credentials()
    assert not (tmp_path / ".local/identity/app-environment.json").exists()
    assert (state / "kong-password.txt").read_text().strip() == expected["kong_password"]
    assert (state / "grafana-password.txt").read_text().strip() == expected["grafana_password"]
    local.prepare()
    assert local.credentials() == expected


def test_login_posts_the_password_sign_in_and_keeps_the_csrf_token(monkeypatch, tmp_path):
    """Requirements: REV-73; identity configuration 14 (password sign-in, no provider)."""
    state = tmp_path / ".local/identity"
    state.mkdir(parents=True)
    (state / "credentials.json").write_text(
        json.dumps({"users": {"local-admin": {"password": "s3cret-ש"}}}), encoding="utf-8"
    )
    monkeypatch.setattr(local, "ROOT", tmp_path)
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert request.url.path == "/auth/login"
            assert request.headers["Origin"] == local.ORIGIN
            assert request.headers["Content-Type"] == "application/json"
            assert json.loads(request.content) == {
                "username": "local-admin", "password": "s3cret-ש", "return_to": "/"}
            return httpx.Response(200, json={"data": {"return_to": "/"}})
        assert request.url.path == "/auth/session"
        return httpx.Response(200, json={"data": {"csrf_token": "csrf-1", "tenants": []}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        session = local.login(http)
        assert http.headers["X-CSRF-Token"] == "csrf-1"
        assert http.headers["Origin"] == local.ORIGIN
    assert session["csrf_token"] == "csrf-1"
    assert calls == [("POST", "/auth/login"), ("GET", "/auth/session")]


def operator_api(monkeypatch, tmp_path, operators):
    """Local Demo's operator API; a retry or create leaves that operator ready."""
    tenant = str(uuid4())
    base = f"/api/tenants/{tenant}/operators"
    posts = []

    def respond(request):
        if request.method == "GET":
            assert request.url.path == base
            return httpx.Response(200, json={"data": operators})
        posts.append(request.url.path)
        if request.url.path == base:
            name = json.loads(request.content)["name"]
            operators.append(
                {"id": str(uuid4()), "name": name, "lifecycle": "ready", "revision": 1})
            return httpx.Response(201, json={"data": {}})
        operator = next(
            o for o in operators if request.url.path.startswith(f"{base}/{o['id']}/"))
        operator["lifecycle"] = "ready"
        operator.pop("operation")
        return httpx.Response(202, json={"data": {}})

    monkeypatch.setattr(local, "STATE", tmp_path)
    monkeypatch.setattr(
        local, "client", lambda **kwargs: httpx.Client(transport=httpx.MockTransport(respond))
    )
    monkeypatch.setattr(
        local, "login", lambda _: {"tenants": [{"id": tenant, "name": "Local Demo"}]})
    monkeypatch.setattr(local.time, "sleep", lambda _: None)
    return tenant, posts


def pending(name, error_code):
    return {"id": str(uuid4()), "name": name, "lifecycle": "setup_pending", "revision": 2,
            "operation": {"id": str(uuid4()), "error_code": error_code}}


def test_setup_resumes_the_sender_named_in_demo_json(monkeypatch, tmp_path):
    """Requirements: operator operations 29; verification plan, Operators (repeated
    Start preserves credentials, operator IDs and remote data)."""
    stuck = pending("Local Demo Sender", "operator_capabilities_unverified")
    chosen = pending("Local Demo Sender 2", "v2_activation_fenced")
    retry = f"/{chosen['id']}/operations/{chosen['operation']['id']}/retry"
    tenant, posts = operator_api(monkeypatch, tmp_path, [stuck, chosen])
    demo = {"tenant_id": tenant, "operator_id": chosen["id"]}
    local.save(tmp_path / "demo.json", demo)
    local.setup_operator()
    assert posts == [f"/api/tenants/{tenant}/operators" + retry]
    assert stuck["lifecycle"] == "setup_pending"
    assert local.read(tmp_path / "demo.json") == demo


def test_first_setup_creates_local_demo_sender(monkeypatch, tmp_path):
    """Requirements: operator operations 29."""
    operators = []
    tenant, posts = operator_api(monkeypatch, tmp_path, operators)
    local.setup_operator()
    assert posts == [f"/api/tenants/{tenant}/operators"]
    assert [o["name"] for o in operators] == ["Local Demo Sender"]
    assert local.read(tmp_path / "demo.json") == {
        "tenant_id": tenant, "operator_id": operators[0]["id"]}


@pytest.mark.parametrize("field", ["operator_id", "tenant_id"])
def test_setup_refuses_demo_json_from_another_database(monkeypatch, tmp_path, field):
    """Requirements: operator operations 29; verification plan, Operators (repeated
    Start preserves operator IDs; setup does not adopt or recreate a remote fleet)."""
    sender = {"id": str(uuid4()), "name": "Local Demo Sender", "lifecycle": "ready",
              "revision": 1}
    tenant, posts = operator_api(monkeypatch, tmp_path, [sender])
    demo = {"tenant_id": tenant, "operator_id": sender["id"], field: str(uuid4())}
    local.save(tmp_path / "demo.json", demo)
    with pytest.raises(RuntimeError, match="demo.json"):
        local.setup_operator()
    assert posts == []
    assert local.read(tmp_path / "demo.json") == demo


def test_send_verifies_kafka_admission_search_and_tenant_isolation(monkeypatch):
    demo = {"tenant_id": str(uuid4()), "operator_id": str(uuid4())}
    global_id = str(uuid4())
    rows, calls, evidence = [], [], {}

    def respond(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/api/ingest/v2/grafana":
            # REV-55: one Grafana member with the operator's Bearer key and routing label.
            assert request.headers["Authorization"] == "Bearer vena_test"
            member = json.loads(request.content)["alerts"][0]
            labels = member["labels"]
            assert labels["Keep - Local Demo"] == "true"
            rows.append({"id": str(uuid4()), "component": labels["component"],
                         "name": labels["alertname"], "node_name": labels["node_name"],
                         "provider": "grafana", "alert_rule_url": member["generatorURL"]})
            return httpx.Response(202, json={
                "request_id": str(uuid4()),
                "results": [{"index": 0, "status": 202, "disposition": "accepted"}],
            })
        if request.method == "POST":
            assert request.url.path == "/api/ingest/v2/alerts"
            assert "Idempotency-Key" not in request.headers
            payloads = json.loads(request.content)["alerts"]
            rows.extend({"id": str(uuid4()), **item["payload"], "provider": "api", "alert_rule_url": None}
                        for item in payloads)
            return httpx.Response(202, json={
                "request_id": str(uuid4()),
                "results": [{"index": index, "status": 202, "disposition": "accepted"}
                            for index in range(len(payloads))],
            })
        if request.url.path == f"/api/tenants/{demo['tenant_id']}/operators/{demo['operator_id']}":
            return httpx.Response(200, json={"data": {"id": demo["operator_id"], "name": "Local Demo"}})
        if request.url.path == f"/api/tenants/{demo['tenant_id']}/alerts":
            assert request.url.params["cel"] == 'alert.node_name == "' + rows[0]["node_name"] + '"'
            return httpx.Response(200, json={"data": rows})
        identifier = request.url.path.rsplit("/", 1)[-1]
        assert identifier in {row["id"] for row in rows}
        if request.url.path.startswith(f"/api/tenants/{global_id}/"):
            return httpx.Response(404)
        assert request.url.path.startswith(f"/api/tenants/{demo['tenant_id']}/")
        return httpx.Response(200, json={"data": next(row for row in rows if row["id"] == identifier)})

    monkeypatch.setattr(local, "environment", lambda: None)
    monkeypatch.setattr(local, "client", lambda **kwargs: httpx.Client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(local, "login", lambda _: {"tenants": [{"id": global_id, "kind": "global"}]})
    monkeypatch.setattr(verification, "demo_key", lambda _: (demo, "vena_test"))
    monkeypatch.setattr(local, "save", lambda _, value: evidence.update(value))
    verification.send()
    assert [path for method, path in calls if method == "POST"] == [
        "/api/ingest/v2/alerts", "/api/ingest/v2/grafana"]
    assert evidence["count"] == 4 and evidence["kafka_admitted"] and evidence["searchable"]
    assert evidence["providers"] == ["api", "api", "api", "grafana"]
    assert evidence["global_detail_denied"]
    assert "idempotent_retry" not in evidence
    assert "vena_test" not in json.dumps(evidence)


@pytest.mark.parametrize(
    "scope,url",
    [
        ("production", "postgresql://worker@127.0.0.1/keep_local"),
        ("development", "postgresql://worker@company.example/keep_local"),
        ("development", "postgresql://worker@localhost/shared"),
        ("development", "dbname=keep_local host=localhost hostaddr=192.0.2.1"),
        ("development", "dbname=keep_local host=localhost service=shared"),
    ],
)
def test_only_isolated_development_database(monkeypatch, scope, url):
    monkeypatch.setattr(local, "read", lambda _: {"KEEP_LOCAL_WORKER_DATABASE_URL": url})
    monkeypatch.setattr(
        "keep.config.Settings",
        lambda: SimpleNamespace(environment=scope, database_url=SecretStr(url)),
    )
    monkeypatch.setenv("KEEP_DATABASE_URL", "preserved-by-monkeypatch")
    with pytest.raises(RuntimeError, match="isolated"):
        local.environment()


@pytest.mark.parametrize(
    "restore,gate",
    [
        ({"fenced": True, "run_id": None}, {"ready": False, "reason": "trusted_gateway_required"}),
        (
            {"fenced": False, "run_id": "past-restore"},
            {"ready": False, "reason": "trusted_gateway_required"},
        ),
        ({"fenced": False, "run_id": None}, {"ready": False, "reason": "recovery_required"}),
        ({"fenced": False, "run_id": None}, {"ready": True, "reason": "production_qualified"}),
        (None, {"ready": False, "reason": "trusted_gateway_required"}),
    ],
)
def test_bootstrap_refuses_restore_and_foreign_gates(restore, gate):
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [restore, gate]
    with pytest.raises(RuntimeError):
        verification.check_initial_gate(conn)
    assert not any(
        call.args[0].startswith(("UPDATE", "INSERT")) for call in conn.execute.call_args_list
    )


PREPARED_KEY, ACTIVE_KEY = "vena_prepared", "vena_active"
# Checks that back the initial gate: none of them needs an active key.
GATE_EVIDENCE = [
    "missing_key_rejected",
    "forged_identity_rejected",
    "invalid_key_rejected",
    "duplicate_native_key_rejected",
    "duplicate_grafana_bearer_rejected",
    "query_key_rejected",
    "body_key_rejected",
    "native_bearer_rejected",
    "public_listener_has_no_v2_route",
    "product_api_has_no_ingestion_route",
    "private_listener_requires_client_certificate",
    "normal_operator_saga_verified_remote_setup",
    "readiness_probe_requires_worker_secret",
    "proof_service_requires_worker_secret",
]
AFTER_ENABLE = [
    "kong_mtls_identity_and_secret_stripping",
    "authenticated_native_validation_after_enable",
    "grafana_bearer_adapter_authenticated",
]


def fake_local_stack(monkeypatch, *, ready):
    """Local Kong as verify() meets it: keep-policy denies a prepared key before upstream."""
    demo = {"tenant_id": str(uuid4()), "operator_id": str(uuid4())}
    stack = SimpleNamespace(ready=ready, active=ready, events=[], audits=[], saved={})

    def respond(request):
        port, path = request.url.port, request.url.path
        if port == 58444:
            raise httpx.ConnectError("client certificate required", request=request)
        if port in {58001, 3443}:
            return httpx.Response(404)
        if port == 58446 or path == "/__local/operator-check":
            return httpx.Response(401)
        if path == "/api/ingest/v2/grafana":
            values = request.headers.get_list("authorization")
            if len(values) != 1:
                return httpx.Response(400, json={"message": "Bad request"})
            key = values[0].removeprefix("Bearer ")
        else:
            values = request.headers.get_list("x-api-key")
            if len(values) != 1:
                return httpx.Response(401)
            key = values[0]
        if key not in {PREPARED_KEY, ACTIVE_KEY}:
            return httpx.Response(401)
        if key == PREPARED_KEY or not stack.active:
            return httpx.Response(401, json={"message": "Credential inactive"})
        if "x-consumer-id" in request.headers:
            stack.events.append("stripping_checked")
        if not stack.ready:
            return httpx.Response(503, json={"error": {"code": "v2_ingest_not_ready"}})
        status = 422 if path.endswith("/alerts") else 400
        return httpx.Response(status, json={"error": {"code": "invalid_request"}})

    class Connection:
        def execute(self, sql, params=None):
            if sql.startswith("UPDATE capability_gates"):
                stack.ready = True
                stack.events.append("gate_opened")
            elif sql.startswith("INSERT INTO audit_events"):
                # psycopg serializes the evidence when the row is written.
                stack.audits.append(list(params[0].obj["checks"]))
            return self

    class Database:
        @contextmanager
        def transaction(self, *args):
            yield Connection()

        def close(self):
            pass

    def demo_key(_, *, allow_prepared=False):
        if stack.active:
            return demo, ACTIVE_KEY
        if allow_prepared:
            return demo, PREPARED_KEY
        raise RuntimeError("Local operator setup is not verified")

    def setup_operator():
        if not stack.ready:
            raise AssertionError("Activation is fenced while the v2 gate is closed")
        stack.active = True
        stack.events.append("sender_activated")

    monkeypatch.setattr(local, "environment", lambda: None)
    monkeypatch.setattr(local, "database", Database)
    monkeypatch.setattr(
        local, "client", lambda **kwargs: httpx.Client(transport=httpx.MockTransport(respond))
    )
    monkeypatch.setattr(local, "login", lambda _: {})
    monkeypatch.setattr(
        local, "read", lambda _: {"kong_version": "3.9.1", "grafana_version": "11.6.0"}
    )
    monkeypatch.setattr(local, "save", lambda _, value: stack.saved.update(value))
    monkeypatch.setattr(local, "setup_operator", setup_operator)
    monkeypatch.setattr(
        verification,
        "check_initial_gate",
        lambda _: {
            "ready": stack.ready,
            "reason": local.REASON if stack.ready else "trusted_gateway_required",
        },
    )
    monkeypatch.setattr(verification, "demo_key", demo_key)
    return stack


def test_first_bootstrap_proves_stripping_with_the_activated_key(monkeypatch):
    """Requirements: operator operations 29; verification plan, Operators (prepared keys
    stay unusable until normal activation; public headers cannot replace pinned mTLS)."""
    stack = fake_local_stack(monkeypatch, ready=False)
    verification.verify()
    assert stack.events == ["gate_opened", "sender_activated", "stripping_checked"]
    assert stack.audits == [GATE_EVIDENCE]
    assert stack.saved["checks"] == [
        *GATE_EVIDENCE, "prepared_key_rejected_before_activation", *AFTER_ENABLE]
    assert PREPARED_KEY not in json.dumps(stack.saved)
    assert ACTIVE_KEY not in json.dumps(stack.saved)


def test_rerun_with_open_gate_leaves_gate_and_sender_unchanged(monkeypatch):
    """Requirements: operator operations 29; verification plan, Operators (repeated Start
    preserves operator identity)."""
    stack = fake_local_stack(monkeypatch, ready=True)
    verification.verify()
    assert stack.events == ["stripping_checked"]
    assert stack.audits == []
    assert stack.saved["checks"] == [*GATE_EVIDENCE, *AFTER_ENABLE]


@pytest.mark.parametrize("change", ["generation", "policy_generation", "consumer", "missing"])
def test_proof_rejects_stale_or_mismatched_candidate(change):
    operator = uuid4()
    key = "vena_" + "a" * 43
    body = SimpleNamespace(
        operator_id=operator, consumer_id=operator, credential_id=uuid4(), generation="1", key=key,
        policy_generation="2",
    )
    row = {
        "generation": 1,
        "lifecycle": "setup_pending",
        "policy_generation": 2,
    }
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row
    assert local.validate_probe(conn, body)
    if change == "generation":
        body.generation = "2"
    elif change == "policy_generation":
        body.policy_generation = "3"
    elif change == "consumer":
        body.consumer_id = uuid4()
    else:
        conn.execute.return_value.fetchone.return_value = None
    assert not local.validate_probe(conn, body)


def test_probe_errors_never_echo_secret(monkeypatch):
    monkeypatch.setattr(local, "credentials", lambda: {"probe_token": "local-test-proof"})
    app = local.proof_app()
    http = TestClient(app)
    secret = "vena_" + "s" * 43
    response = http.post("/operator-check", json={"key": secret})
    assert response.status_code == 401 and secret not in response.text
    response = http.post(
        "/operator-check",
        json={"key": secret},
        headers={"Authorization": "Bearer local-test-proof"},
    )
    assert response.status_code == 422 and secret not in response.text
