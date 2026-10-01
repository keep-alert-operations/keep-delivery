"""Development bootstrap cannot change nonlocal/recovery authority or leak probe keys."""

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
local = importlib.import_module("local_v2")
verification = importlib.import_module("local_v2_verify")


@pytest.mark.parametrize(
    "scope,url",
    [
        ("production", "postgresql://worker@127.0.0.1/appchi_local"),
        ("development", "postgresql://worker@company.example/appchi_local"),
        ("development", "postgresql://worker@localhost/shared"),
        ("development", "dbname=appchi_local host=localhost hostaddr=192.0.2.1"),
        ("development", "dbname=appchi_local host=localhost service=shared"),
    ],
)
def test_only_isolated_development_database(monkeypatch, scope, url):
    monkeypatch.setattr(local, "read", lambda _: {"APPCHI_LOCAL_WORKER_DATABASE_URL": url})
    monkeypatch.setattr(
        "appchi.config.Settings",
        lambda: SimpleNamespace(environment=scope, database_url=SecretStr(url)),
    )
    monkeypatch.setenv("APPCHI_DATABASE_URL", "preserved-by-monkeypatch")
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
