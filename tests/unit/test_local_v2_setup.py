"""Development bootstrap cannot change nonlocal/recovery authority or leak probe keys."""

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
    assert not (tmp_path / ".local/keycloak/app-environment.json").exists()
    assert (state / "kong-password.txt").read_text().strip() == expected["kong_password"]
    assert (state / "grafana-password.txt").read_text().strip() == expected["grafana_password"]
    local.prepare()
    assert local.credentials() == expected


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
