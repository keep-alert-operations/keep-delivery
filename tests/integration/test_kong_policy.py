"""Actual installed local Kong Key Auth + Appchi policy; no Appchi DB lookup."""

import hashlib
import json
import os
import secrets
import sys
from pathlib import Path
from time import time, sleep, monotonic
from datetime import datetime, UTC, timedelta
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_actual_kong_prepared_activation_revoke_expiry_and_generation():
    if os.getenv("APPCHI_TEST_LOCAL_KONG") != "1":
        pytest.skip("Requires configured isolated local Kong")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from local_v2 import client, credentials, KONG, GATEWAY

    operator, tenant, credential = str(uuid4()), str(uuid4()), str(uuid4())
    key = "vena_" + secrets.token_urlsafe(32)
    headers = {"X-Appchi-Probe-Token": credentials()["probe_token"]}
    document = {
        "operator_id": operator,
        "tenant_id": tenant,
        "enabled": True,
        "routing_names": ["Kafka test"],
        "credentials": {credential: {"state": "prepared"}},
    }
    with client() as http:
        assert http.put(
            KONG + "/consumers/" + operator, json={"id": operator, "username": "test-" + operator}
        ).is_success
        assert http.put(
            KONG + f"/consumers/{operator}/key-auth/{credential}",
            json={"id": credential, "key": key},
        ).is_success
        try:

            def policy(generation):
                encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
                response = http.put(
                    KONG + "/appchi-policies/" + operator,
                    json={"generation": generation, "document": encoded},
                )
                assert response.is_success, response.status_code
                return encoded

            def auth():
                return http.post(
                    GATEWAY + "/__local/operator-auth-check", headers={**headers, "X-API-Key": key}
                )

            policy(1)
            assert auth().status_code == 401
            document["credentials"][credential]["state"] = "current"
            encoded = policy(2)
            response = auth()
            assert response.status_code == 200, response.status_code
            assert response.json()["tenant_id"] == tenant
            assert response.json()["policy_digest"] == hashlib.sha256(encoded.encode()).hexdigest()
            assert response.json()["admitted"] is False
            # A stale worker can neither regress generation nor change a generation's meaning.
            assert (
                http.put(
                    KONG + "/appchi-policies/" + operator,
                    json={"generation": 1, "document": encoded},
                ).status_code
                == 409
            )
            assert (
                http.put(
                    KONG + "/appchi-policies/" + operator,
                    json={"generation": 2, "document": encoded + " "},
                ).status_code
                == 409
            )
            document["credentials"][credential] = {"state": "retiring", "expires_at": time() - 1}
            policy(3)
            assert auth().status_code == 401
            document["credentials"] = {}
            policy(4)
            assert auth().status_code == 401
            proof = http.post(
                GATEWAY + "/__local/operator-check",
                headers=headers,
                json={
                    "operator_id": operator,
                    "credential_id": credential,
                    "nonce": "revoked-without-key",
                },
            )
            assert proof.status_code == 200
            assert proof.json()["allowed"] is False
            assert proof.json()["generation"] == "4"
        finally:
            document["credentials"] = {}
            policy(5)
            http.delete(KONG + f"/consumers/{operator}/key-auth/{credential}")
            http.delete(KONG + "/consumers/" + operator)


def test_actual_kong_untrusted_clock_fences_allow_but_preserves_denial():
    if os.getenv("APPCHI_TEST_LOCAL_KONG") != "1":
        pytest.skip("Requires configured isolated local Kong")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from local_v2 import client, credentials, KONG, GATEWAY

    operator, tenant, credential = str(uuid4()), str(uuid4()), str(uuid4())
    name = "clock-test-" + operator
    key = "vena_" + secrets.token_urlsafe(32)
    health_path = (
        Path(__file__).resolve().parents[3]
        / "deploy/kong/appchi-policy"
        / (".clock-test-" + operator + ".json")
    )
    container_path = "/usr/local/share/lua/5.1/kong/plugins/appchi-policy/" + health_path.name
    token = credentials()["probe_token"]
    with client() as http:
        try:
            assert http.put(
                KONG + "/consumers/" + operator, json={"id": operator, "username": name}
            ).is_success
            assert http.put(
                KONG + f"/consumers/{operator}/key-auth/{credential}",
                json={"id": credential, "key": key},
            ).is_success
            service = http.get(KONG + "/services/appchi-local-v2").json()["id"]
            assert http.put(
                KONG + "/routes/" + name,
                json={
                    "name": name,
                    "paths": ["/__local/" + name],
                    "protocols": ["https"],
                    "service": {"id": service},
                },
            ).is_success
            assert http.post(
                KONG + "/routes/" + name + "/plugins",
                json={
                    "name": "key-auth",
                    "config": {
                        "key_names": ["X-API-Key"],
                        "key_in_query": False,
                        "key_in_body": False,
                    },
                },
            ).is_success
            assert http.post(
                KONG + "/routes/" + name + "/plugins",
                json={
                    "name": "appchi-policy",
                    "config": {
                        "member_id": "test",
                        "probe_active": True,
                        "probe_token": token,
                        "clock_health_file": container_path,
                    },
                },
            ).is_success
            document = {
                "operator_id": operator,
                "tenant_id": tenant,
                "enabled": True,
                "routing_names": [name],
                "credentials": {credential: {"state": "current"}},
            }
            assert http.put(
                KONG + "/appchi-policies/" + operator,
                json={"generation": 1, "document": json.dumps(document)},
            ).is_success

            def auth():
                deadline = monotonic() + 3
                while True:
                    result = http.post(
                        GATEWAY + "/__local/" + name,
                        headers={"X-API-Key": key, "X-Appchi-Probe-Token": token},
                    )
                    if result.status_code != 404 or monotonic() >= deadline:
                        return result
                    sleep(0.1)

            assert auth().status_code == 503

            def evidence(age=0, offset=0, synchronized=True):
                health_path.write_text(
                    json.dumps(
                        {
                            "measured_at": (datetime.now(UTC) - timedelta(seconds=age)).isoformat(),
                            "offset_seconds": offset,
                            "synchronized": synchronized,
                        }
                    ),
                    encoding="utf-8",
                )

            evidence()
            assert auth().status_code == 200
            for kwargs in ({"age": 62}, {"age": -3}, {"offset": 1.1}, {"synchronized": False}):
                evidence(**kwargs)
                assert auth().status_code == 503
            document["credentials"] = {}
            assert http.put(
                KONG + "/appchi-policies/" + operator,
                json={"generation": 2, "document": json.dumps(document)},
            ).is_success
            assert auth().status_code == 401
        finally:
            http.delete(KONG + "/routes/" + name)
            http.delete(KONG + "/consumers/" + operator)
            health_path.unlink(missing_ok=True)
