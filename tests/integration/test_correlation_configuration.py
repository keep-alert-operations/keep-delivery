"""Real PostgreSQL authorization/configuration tests; no correlation worker claim."""

import base64
import json
from uuid import uuid4

import psycopg
import pytest

from keep.correlation import repository as repo
from keep.correlation.evidence import GroupKey, group_key
from keep.correlation.models import grouping
from keep.shared.transactions import coordinator
from test_product import browser, admit_alert  # noqa: F401 -- shared real authenticated fixture

pytestmark = pytest.mark.integration

TEST_CAPACITY = {
    "correlation": {
        "cost_model": "keep-cel-cost-v1",
        "enabled_rules_per_tenant": 3,
        "live_groups_per_rule": 10,
        "live_evidence_per_rule": 50,
        "live_evidence_per_tenant": 100,
        "condition_cost": 10**9,
        "rule_cost": 10**10,
        "tenant_cost": 10**11,
        "digest_bucket": 5,
    },
}


@pytest.fixture
def rules(request, real_db, tenant):
    client, actor = request.getfixturevalue("browser")
    # Explicit synthetic limits exercise enforcement; they are not qualification evidence.
    client.app.state.profile = TEST_CAPACITY
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, actor),
        )
        conn.execute("UPDATE tenant_work_state SET change_order=42 WHERE tenant_id=%s", (tenant,))
    return client, actor, tenant, f"/api/tenants/{tenant}/correlation-rules"


def definition(name="Rule"):
    return {
        "name": name,
        "description": "Generated description",
        "severity": "high",
        "conditions": [
            {"id": str(uuid4()), "label": "One", "cel": "true"},
            {"id": str(uuid4()), "label": "Two", "cel": "false"},
        ],
        "group_by": [],
        "window_minutes": 10,
    }


def create(rules, body=None):
    client, _, _, base = rules
    value = {"command_id": str(uuid4()), **(body or definition())}
    response = client.post(base, json=value)
    assert response.status_code == 201, response.text
    return response.json()["data"], value


def edit(rules, row, body, expected=None):
    client, _, _, base = rules
    return client.put(
        base + "/" + row["id"],
        json={**body, "command_id": str(uuid4()), "expected_revision": expected or row["revision"]},
    )


def test_create_metadata_logic_group_and_intent_epochs(rules, real_db):
    client, _, tenant, base = rules
    row, command = create(rules)
    body = {key: command[key] for key in repo.DEFINITION_FIELDS}
    assert row["revision"] == row["epoch"] == row["grouping_generation"] == "1"
    # REV-39: correlation is always on, so a new rule starts healthy.
    assert row["enabled"] is True and row["health"] == "healthy"
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT change_order FROM correlation_consumer_state WHERE tenant_id=%s", (tenant,)
            ).fetchone()["change_order"]
            == 42
        )
        assert (
            conn.execute(
                "SELECT observed_change_order FROM correlation_rule_controls WHERE tenant_id=%s",
                (tenant,),
            ).fetchone()["observed_change_order"]
            == 42
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM correlation_evidence WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )
    original_time, boundary = row["created_at"], row["epoch_boundary_order"]
    body = {
        **body,
        "name": " Renamed\u00a0 rule ",
        "conditions": list(reversed(body["conditions"])),
    }
    changed = edit(rules, row, body)
    assert changed.status_code == 200, changed.text
    row = changed.json()["data"]
    assert row["name"] == "Renamed rule" and row["epoch"] == "1"
    assert row["created_at"] == original_time and row["epoch_boundary_order"] == boundary
    body["name"] = "Renamed rule"
    noop = edit(rules, row, body)
    assert noop.json()["meta"]["changed"] is False and noop.json()["data"]["revision"] == "2"
    assert edit(rules, row, body, expected="1").status_code == 409
    body = {**body, "window_minutes": 11}
    row = edit(rules, row, body).json()["data"]
    assert row["epoch"] == "2" and row["grouping_generation"] == "1"
    assert int(row["epoch_boundary_order"]) > int(boundary)
    action = {"command_id": str(uuid4()), "expected_revision": row["revision"]}
    disabled = client.post(base + "/" + row["id"] + "/disable", json=action)
    assert disabled.status_code == 200, disabled.text
    row = disabled.json()["data"]
    assert row["enabled"] is False and row["epoch"] == "3"
    assert client.post(base + "/" + row["id"] + "/disable", json=action).json() == disabled.json()
    row = client.post(
        base + "/" + row["id"] + "/enable",
        json={"command_id": str(uuid4()), "expected_revision": row["revision"]},
    ).json()["data"]
    assert row["epoch"] == "4" and row["grouping_generation"] == "1"
    row = edit(rules, row, {**body, "group_by": ["application"]}).json()["data"]
    assert row["grouping_generation"] == "2"
    row = edit(rules, row, body).json()["data"]
    assert row["grouping_generation"] == "3" and row["created_at"] == original_time


def test_idempotency_names_conditions_capacity_and_disable_without_profile(rules, real_db):
    client, _, tenant, base = rules
    row, command = create(rules, definition("Straße"))
    assert client.post(base, json=command).json()["data"]["id"] == row["id"]
    conflict = client.post(base, json={**command, "name": "Other"})
    assert (
        conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
    )
    assert (
        client.post(base, json={"command_id": str(uuid4()), **definition("STRASSE")}).status_code
        == 409
    )
    same_ids = {**command, "command_id": str(uuid4()), "name": "Cannot reuse IDs"}
    conflict = client.post(base, json=same_ids)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "condition_identity_unavailable"
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM correlation_rules WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 1
        )
    create(rules, definition("Second"))
    create(rules, definition("Third"))
    result = client.post(base, json={"command_id": str(uuid4()), **definition("Fourth")})
    assert (
        result.status_code == 409
        and result.json()["error"]["code"] == "correlation_capacity_exceeded"
    )
    # REV-39: without configured budgets correlation capacity is unlimited.
    client.app.state.profile = None
    unlimited = client.post(base, json={"command_id": str(uuid4()), **definition("No budget")})
    assert unlimited.status_code == 201, unlimited.text
    disabled = client.post(
        base + "/" + row["id"] + "/disable",
        json={"command_id": str(uuid4()), "expected_revision": "1"},
    )
    assert disabled.status_code == 200, disabled.text
    enable = client.post(
        base + "/" + row["id"] + "/enable",
        json={"command_id": str(uuid4()), "expected_revision": "2"},
    )
    assert enable.status_code == 200, enable.text


def test_listing_history_read_authorization_and_protected_delete(rules, real_db, cipher):
    client, actor, tenant, base = rules
    first, body = create(rules, definition("Zed"))
    second, _ = create(rules, definition("Alpha"))
    listed = client.get(base, params={"limit": 1})
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"][0]["id"] == first["id"]
    token = listed.json()["next_cursor"]
    next_page = client.get(base, params={"limit": 1, "cursor": token})
    assert next_page.json()["data"][0]["id"] == second["id"]
    assert client.get(base, params={"sort": "name"}).json()["data"][0]["id"] == second["id"]
    changed = edit(
        rules,
        first,
        {key: ("Edited" if key == "name" else body[key]) for key in repo.DEFINITION_FIELDS},
    )
    assert changed.status_code == 200, changed.text
    assert client.get(base, params={"cursor": token}).status_code == 409
    history_url = base + "/" + first["id"] + "/history"
    page = client.get(history_url, params={"limit": 1})
    assert page.status_code == 200, page.text
    assert page.json()["data"][0]["revision"] == "2"
    old = json.loads(cipher.open(base64.urlsafe_b64decode(page.json()["next_cursor"]), "cursor:v1"))
    assert old["upper"] == "2"
    assert client.get(history_url + "/1").json()["data"]["definition"]["name"] == "Zed"
    deleted = client.delete(
        base + "/" + first["id"], headers={"Idempotency-Key": str(uuid4()), "If-Match": '"2"'}
    )
    assert deleted.status_code == 503
    assert client.get(base + "/" + first["id"]).status_code == 200
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE tenant_grants SET role='view' WHERE tenant_id=%s AND principal_id=%s",
            (tenant, actor),
        )
    diagnostic = client.get(base + "/" + first["id"] + "/diagnostics")
    assert diagnostic.status_code == 200, diagnostic.text
    assert "processing_ready" not in diagnostic.json()["data"]
    assert edit(rules, first, {key: body[key] for key in repo.DEFINITION_FIELDS}).status_code == 403
    with real_db.transaction() as conn:
        conn.execute(
            "DELETE FROM tenant_grants WHERE tenant_id=%s AND principal_id=%s", (tenant, actor)
        )
    assert client.get(history_url).status_code == 404
    assert client.get(base + "/" + first["id"] + "/diagnostics").status_code == 404


def test_typed_full_context_handles_digest_collision_and_rls(rules, real_db, monkeypatch):
    _, _, tenant, _ = rules
    row, _ = create(rules, {**definition(), "group_by": ["application"]})
    monkeypatch.setattr(GroupKey, "digest", property(lambda self: b"x" * 32))
    first = group_key(grouping(["application"]), {"application": "Exact value"})
    second = group_key(grouping(["application"]), {"application": "exact value"})
    with real_db.transaction() as conn:
        coordinator(conn, tenant)
        locked = repo.get_rule(conn, tenant, row["id"], lock=True)
        one = repo.ensure_context(conn, locked, first, max_bucket=5)
        two = repo.ensure_context(conn, locked, second, max_bucket=5)
        assert one != two
        assert repo.ensure_context(conn, locked, first, max_bucket=5) == one
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM correlation_contexts WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 2
        )
    with real_db.transaction() as conn:
        conn.execute("SET LOCAL ROLE keep_api")
        conn.execute("SELECT set_config('keep.tenant_id',%s,true)", (str(uuid4()),))
        assert (
            conn.execute("SELECT * FROM correlation_rules WHERE id=%s", (row["id"],)).fetchall()
            == []
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with real_db.transaction() as conn:
            conn.execute("SET LOCAL ROLE keep_api")
            conn.execute("SELECT set_config('keep.tenant_id',%s,true)", (str(uuid4()),))
            conn.execute(
                "INSERT INTO correlation_rule_identities(id,tenant_id) VALUES(%s,%s)",
                (uuid4(), tenant),
            )


def test_validation_and_selected_alert_test_never_produce_evidence(
    rules, real_db, cipher, profile, v1
):
    client, actor, _, _ = rules
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    base = f"/api/tenants/{tenant}/correlation-rules"
    candidate = {**definition(), "group_by": ["acknowledged"]}
    candidate["conditions"][0]["cel"] = 'alert.application == "' + v1["application"] + '"'
    response = client.post(base + "/validate", json=candidate)
    assert response.status_code == 200 and response.json()["data"]["valid"] is True, response.text
    bad = {
        **candidate,
        "conditions": [{"id": str(uuid4()), "label": "Time", "cel": "alert.time_created < now()"}],
    }
    invalid = client.post(base + "/validate", json=bad)
    assert invalid.status_code == 200 and invalid.json()["data"]["valid"] is False
    response = client.post(base + "/test", json={**candidate, "alert_id": alert})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["group"] == [["acknowledged", "bool", False]]
    assert response.json()["data"]["conditions"][0]["matches"] is True
    assert response.json()["data"]["qualification_proven"] is False
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM correlation_evidence WHERE tenant_id=%s", (tenant,)
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM correlation_rules WHERE tenant_id=%s AND created_by=%s",
                (tenant, actor),
            ).fetchone()["n"]
            == 0
        )
