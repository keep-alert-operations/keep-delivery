"""One alert's steps through the source processor and correlation, on real PostgreSQL.

No broker: the changed event the processor returns is handed to correlation
directly, as the pipeline tests beside this one do.
"""

# ruff: noqa: F811
from uuid import uuid4

import pytest

from keep.observability.testing import (  # noqa: F401
    capture_records,
    restore_logging,
    strict_log_events,
)
from test_correlation_configuration import rules  # noqa: F401
from test_kafka_maintenance_inline import consume, receive, rule, setup  # noqa: F401
from test_product import browser  # noqa: F401
from test_search_pipeline import real_db  # noqa: F401

pytestmark = pytest.mark.integration


def trail(records):
    """The alert flow steps, in the order they were written."""
    return [r for r in records if "sequence" in r.get("event", {})]


def names(records):
    return [r["event"]["action"] for r in records]


def test_a_new_alert_leaves_its_steps_and_the_incident_it_opened(setup, real_db, v1):
    """Requirements: REV-74; observability 3."""
    _, _, tenant, config = setup
    rule(setup)
    payload = {**v1, "application": "flow-" + uuid4().hex, "message": "disk is full"}
    records = capture_records("processor", "source")

    changed = receive(real_db, config, payload)
    alert_id = str(consume(real_db, config, changed))

    steps = trail(records())
    by_logger = {}
    for record in steps:
        by_logger.setdefault(record["log"]["logger"], []).append(record)
    # The receipt helper admits the alert through ingestion's own admission code.
    ingestion = by_logger["keep.ingestion.kafka_admission"]
    processor = by_logger["keep.kafka_source"]
    correlation = by_logger["keep.correlation.consumer"]
    assert names(ingestion) == ["alert_received", "alert_validated", "alert_published"]
    assert names(processor) == ["alert_consumed", "alert_matched", "alert_applied"]
    # One delivery ID and one key digest follow the alert across the services.
    assert ingestion[-1]["keep"]["delivery_id"] == processor[0]["keep"]["delivery_id"]
    assert (
        ingestion[-1]["keep"]["alert"]["key_digest"] == processor[0]["keep"]["alert"]["key_digest"]
    )
    assert [r["event"]["sequence"] for r in processor] == [1, 2, 3]
    consumed, matched, applied = processor
    assert matched["keep"]["outcome"] == "new_alert"

    with real_db.transaction(tenant) as conn:
        stored = conn.execute(
            "SELECT delivery_id,alert_id,changed_event_id FROM kafka_source_results WHERE tenant_id=%s AND alert_id=%s",
            (tenant, alert_id),
        ).fetchone()
        alert = conn.execute(
            "SELECT status,revision FROM alerts WHERE tenant_id=%s AND id=%s", (tenant, alert_id)
        ).fetchone()
        incidents = conn.execute(
            "SELECT l.incident_id FROM incident_alert_links l WHERE l.tenant_id=%s AND l.alert_id=%s",
            (tenant, alert_id),
        ).fetchall()

    # What the records say is what PostgreSQL holds.
    for record in processor:
        assert record["keep"]["delivery_id"] == str(stored["delivery_id"])
        assert record["keep"]["alert"]["application"] == payload["application"]
        assert len(record["keep"]["alert"]["key_digest"]) == 64
    assert applied["keep"]["alert"]["id"] == alert_id
    assert applied["keep"]["changed_event_id"] == str(stored["changed_event_id"])
    assert applied["keep"]["outcome"] == "created"
    assert (applied["keep"]["status_before"], applied["keep"]["status_after"]) == (
        "none",
        alert["status"],
    )
    assert applied["keep"]["alert"]["message"] == "disk is full"
    assert "id" not in consumed["keep"]["alert"]

    assert names(correlation)[0] == "alert_change_consumed"
    assert names(correlation)[-1] in ("alert_correlated", "alert_correlation_skipped")
    # Attaching the alert changed it too (revision 2), which made a new changed event;
    # every other correlation record names the event that was consumed.
    (linked,) = [r for r in correlation if r["event"]["action"] == "alert_changed"]
    assert linked["keep"]["cause"] == "correlation-link" and linked["keep"]["revision"] == 2
    assert linked["keep"]["changed_event_id"] != str(stored["changed_event_id"])
    for record in correlation:
        if record is not linked:
            assert record["keep"]["changed_event_id"] == str(stored["changed_event_id"])
    ended = correlation[-1]
    assert ended["keep"]["alert"]["id"] == alert_id
    assert ended["keep"]["delivery_id"] == str(stored["delivery_id"])
    attached = [r for r in steps if r["event"]["action"] == "alert_incident_attached"]
    assert {r["keep"]["incident_id"] for r in attached} == {str(i["incident_id"]) for i in incidents}
    assert len(incidents) == 1
    assert names(correlation) == [
        "alert_change_consumed",
        "alert_changed",
        "alert_incident_attached",
        "alert_correlated",
    ]
    assert ended["keep"]["outcome"] == "incident_created"


def test_a_repeat_and_a_clear_are_recorded_as_unchanged_then_resolved(setup, real_db, v1):
    """Requirements: REV-74; observability 3."""
    _, _, tenant, config = setup
    payload = {**v1, "application": "flow-" + uuid4().hex}
    receive(real_db, config, payload)
    records = capture_records("processor", "source")

    receive(real_db, config, payload, offset=1)
    receive(real_db, config, {**payload, "severity": "clear"}, offset=2)

    applied = [r for r in trail(records()) if r["event"]["action"] == "alert_applied"]
    matched = [r for r in trail(records()) if r["event"]["action"] == "alert_matched"]
    assert [r["keep"]["outcome"] for r in matched] == ["existing_alert", "existing_alert"]
    assert [r["keep"]["outcome"] for r in applied] == ["unchanged", "resolved"]
    assert (applied[1]["keep"]["status_before"], applied[1]["keep"]["status_after"]) == (
        "firing",
        "resolved",
    )
    assert applied[0]["keep"]["alert"]["id"] == matched[0]["keep"]["alert"]["id"]
    assert applied[1]["keep"]["revision"] == applied[0]["keep"]["revision"] + 1


def test_a_delivery_refused_after_acceptance_is_recorded_with_its_code(setup, real_db, v1):
    """Requirements: REV-74; observability 3."""
    from test_kafka_first import apply_waiting, receipt

    _, _, tenant, config = setup
    payload = {**v1, "application": "flow-" + uuid4().hex, "severity": "error"}
    receive(real_db, config, payload)
    records = capture_records("processor", "source")

    # A v1 alert keeps its first severity; a different one is an identity conflict.
    topic, key, value = receipt(config, {**payload, "severity": "major"})
    changed = apply_waiting(
        real_db, topic=topic, key=key, value=value, partition=0, offset=1,
        settings=config, profile=None,
    )
    assert changed is None
    refused = trail(records())[-1]
    assert refused["event"]["action"] == "alert_refused"
    assert refused["log"]["level"] == "warning"
    assert refused["error"]["code"] == "identity_conflict"
    assert refused["keep"]["alert"]["severity"] == "major"
    assert "id" in refused["keep"]["alert"]


def test_a_user_command_leaves_its_step_with_the_user_and_the_request(setup, real_db, v1):
    """Requirements: REV-74; observability 3."""
    from test_kafka_maintenance_inline import mutate

    _, client, tenant, config = setup
    payload = {**v1, "application": "flow-" + uuid4().hex}
    alert_id = str(consume(real_db, config, receive(real_db, config, payload)))
    records = capture_records("api", "product-api")

    response = mutate(real_db, setup, alert_id, status="resolved")
    note = mutate(real_db, setup, alert_id)

    written = records()
    changed = [r for r in written if r["event"]["action"] == "alert_changed"]
    requests = [r for r in written if r["event"]["action"] == "http_request" and r["http"]["request"]["method"] == "POST"]
    assert [r["keep"]["cause"] for r in changed] == ["set_status", "note-create"]
    resolved, noted = changed
    assert resolved["message"] == "Alert changed by set_status: status firing to resolved, revision 2"
    assert (resolved["keep"]["status_before"], resolved["keep"]["status_after"]) == (
        "firing",
        "resolved",
    )
    assert noted["keep"]["revision"] == 3
    with real_db.transaction(tenant) as conn:
        user = conn.execute(
            "SELECT actor_id FROM alert_activity WHERE tenant_id=%s AND alert_id=%s AND kind='set_status'",
            (tenant, alert_id),
        ).fetchone()["actor_id"]
    for step, answer, call in ((resolved, response, requests[0]), (noted, note, requests[1])):
        # The step, the request's own record and the response share one request ID.
        assert step["http"]["request"]["id"] == answer.headers["X-Request-ID"]
        assert call["http"]["request"]["id"] == answer.headers["X-Request-ID"]
        assert step["user"]["id"] == call["user"]["id"] == str(user)
        assert step["keep"]["alert"]["id"] == call["keep"]["alert"]["id"] == alert_id
        assert step["keep"]["alert"]["application"] == payload["application"]
        assert step["keep"]["tenant_id"] == str(tenant)
        assert step["event"]["sequence"] == 1
        # The step is written at the commit, before the request is answered.
        assert written.index(step) < written.index(call)
    assert resolved["keep"]["changed_event_id"] != noted["keep"]["changed_event_id"]


def test_a_refused_command_leaves_only_the_request_record_with_its_code(setup, real_db, v1):
    """Requirements: REV-74; observability 3 and 5. No step claims a change that was refused."""
    _, client, tenant, config = setup
    payload = {**v1, "application": "flow-" + uuid4().hex}
    alert_id = str(consume(real_db, config, receive(real_db, config, payload)))
    records = capture_records("api", "product-api")

    response = client.post(
        f"/api/tenants/{tenant}/alerts/{alert_id}/commands",
        json={
            "command_id": str(uuid4()),
            "expected_revision": "99",
            "kind": "set_status",
            "value": "resolved",
        },
    )
    assert response.status_code == 409
    written = records()
    assert not [r for r in written if r["event"]["action"] == "alert_changed"]
    (refused,) = [r for r in written if r["event"]["action"] == "http_request"]
    assert refused["error"]["code"] == response.json()["error"]["code"]
    assert refused["keep"]["alert"]["id"] == alert_id
    assert refused["event"]["outcome"] == "failure"
    assert "id" in refused["user"]


def test_browser_events_are_accepted_only_with_the_session_and_its_csrf_token(setup, real_db):
    """Requirements: REV-74; observability 9."""
    from keep.observability.metrics import REGISTRY

    _, client, tenant, _ = setup
    settings = client.app.state.settings
    batch = {
        "events": [
            {"kind": "page_view", "view": "alerts"},
            {
                "kind": "api",
                "route": "/api/tenants/{id}/alerts/{id}/commands",
                "method": "POST",
                "outcome": "conflict",
                "count": 2,
                "buckets": [2, 0, 0, 0, 0, 0, 0, 0],
            },
            {"kind": "js_error", "name": "error", "view": "alert", "error_name": "TypeError"},
            {"kind": "live", "state": "moonwalking", "value": 3},
        ]
    }
    # Off unless configured: the endpoint does not exist.
    assert settings.rum_enabled is False
    assert client.post("/api/rum-events", json=batch).status_code == 404
    settings.rum_enabled = True
    try:
        records = capture_records("api", "product-api")
        route = "/api/tenants/{tenant_id}/alerts/{alert_id}/commands"
        labels = {"route": route, "method": "POST", "outcome": "conflict"}
        before = REGISTRY.get_sample_value("keep_rum_api_requests_total", labels) or 0.0

        response = client.post("/api/rum-events", json=batch)
        assert response.status_code == 202, response.text
        # Three events were usable; the unknown live state was counted and dropped.
        assert response.json()["data"] == {"accepted": 3}
        assert REGISTRY.get_sample_value("keep_rum_api_requests_total", labels) == before + 2
        written = records()
        (error,) = [r for r in written if r["event"]["action"] == "rum_js_error"]
        assert error["service"]["name"] == "browser" and "id" in error["user"]
        (request,) = [r for r in written if r["event"]["action"] == "http_request"]
        assert request["keep"]["sli"] == "rum" and request["keep"]["route"] == "/api/rum-events"

        # Without the CSRF token, or signed out, nothing is accepted.
        assert client.post("/api/rum-events", json=batch, headers={"X-CSRF-Token": "wrong"}).status_code == 403
        anonymous = client.post("/api/rum-events", json=batch, cookies={"__Host-keep_session": "nobody"})
        assert anonymous.status_code == 401
        # A malformed batch is refused whole; an address or text field is not accepted.
        bad = {"events": [{"kind": "page_view", "view": "alerts", "url": "https://keep.test/?q=x"}]}
        assert client.post("/api/rum-events", json=bad).status_code == 422
        # A session that sends too often is told to slow down.
        from keep import rum

        statuses = [client.post("/api/rum-events", json=batch).status_code for _ in range(rum.BATCHES_PER_MINUTE + 1)]
        assert statuses[-1] == 429 and 202 in statuses
    finally:
        settings.rum_enabled = False
