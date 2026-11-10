"""REV-55 alert provider and Grafana rule URL through admission, source application and backfill."""

import json
from uuid import uuid4

import psycopg
import pytest

from keep.alerts.presentation import present_alert
from keep.alerts.provider_backfill import backfill_tenant

from test_alert_name import Source

pytestmark = pytest.mark.integration

RULE = "https://grafana.example.org/alerting/grafana/cpu-rule/view?orgId=1"


def member(application, **extra):
    return {
        "status": "firing",
        "startsAt": "2026-10-31T12:00:00Z",
        "labels": {
            "Keep - Payments": "true",
            "alertname": "High CPU",
            "application": application,
            "component": "cpu",
            "severity": "critical",
            "site": "lab",
        },
        "annotations": {
            "message": "CPU above 90%",
            "impact": "Slow checkout",
            "runbook_url": "https://example.invalid/runbook",
        },
        **extra,
    }


def native(application, **extra):
    """The native v2 payload with the same identity as member(application)."""
    return {
        "application": application,
        "component": "cpu",
        "severity": "critical",
        "site": "lab",
        "message": "CPU still above 90%",
        "impact": "Slow checkout",
        "runbook_url": "https://example.invalid/runbook",
        "name": "High CPU",
        "status": "firing",
        **extra,
    }


def grafana(source, *members):
    raw = json.dumps(
        {"version": "1", "status": "firing", "truncatedAlerts": 0, "alerts": list(members)}
    ).encode()
    status, body, records = source.admit(raw, grafana=True)
    assert status == 202, body
    return [source.apply_record(record) for record in records]


def projections(db, tenant, alert):
    with db.transaction(tenant) as conn:
        return conn.execute(
            "SELECT version,snapshot FROM projection_work WHERE tenant_id=%s AND alert_id=%s ORDER BY seq",
            (tenant, alert),
        ).fetchall()


def test_grafana_alerts_keep_their_rule_url_and_api_alerts_have_none(real_db, tenant):
    """Requirements: alert operations 3; operator operations 18 (REV-55)."""
    source = Source(real_db, tenant)
    app = "test-" + uuid4().hex
    linked, unlinked = grafana(
        source,
        member(app, generatorURL=RULE),
        member("test-" + uuid4().hex, generatorURL="javascript:alert(1)"),
    )
    row = source.row(linked)
    assert (row["provider"], row["alert_rule_url"]) == ("grafana", RULE)
    assert present_alert(row)["provider"] == "grafana"
    # An invalid generatorURL still admits the alert, without a rule URL.
    row = source.row(unlinked)
    assert (row["provider"], row["alert_rule_url"]) == ("grafana", None)
    api = source.apply(native("test-" + uuid4().hex))
    assert (source.row(api)["provider"], source.row(api)["alert_rule_url"]) == ("api", None)
    # Providers never reach maintenance proof.
    assert all("provider" not in item["after_snapshot"] for item in source.inputs(linked))
    snapshot = projections(real_db, tenant, linked)[-1]["snapshot"]
    assert (snapshot["provider"], snapshot["alert_rule_url"]) == ("grafana", RULE)


def test_the_provider_follows_the_latest_delivery_of_the_same_alert(real_db, tenant):
    """Requirements: alert operations 3, 7 (REV-55)."""
    source = Source(real_db, tenant)
    app = "test-" + uuid4().hex
    (alert,) = grafana(source, member(app, generatorURL=RULE))
    before = source.row(alert)
    assert source.apply(native(app)) == alert
    after = source.row(alert)
    assert (after["provider"], after["alert_rule_url"]) == ("api", None)
    assert after["key_field"] == before["key_field"]
    assert grafana(source, member(app, generatorURL=RULE + "&from=now-1h")) == [alert]
    assert source.row(alert)["alert_rule_url"] == RULE + "&from=now-1h"


def test_backfill_restores_rows_stored_before_providers(real_db, tenant):
    """Requirements: alert operations 3; alerts technical 26 (REV-55)."""
    source = Source(real_db, tenant)
    (linked,) = grafana(source, member("test-" + uuid4().hex, generatorURL=RULE))
    api = source.apply(native("test-" + uuid4().hex))
    orphan = source.apply(native("test-" + uuid4().hex))
    with real_db.transaction(tenant) as conn:
        # Rows as an older processor stored them; the orphan's source event is gone.
        conn.execute(
            "UPDATE alerts SET provider=NULL,alert_rule_url=NULL WHERE tenant_id=%s", (tenant,)
        )
        conn.execute(
            "UPDATE alerts SET history_id=%s WHERE tenant_id=%s AND id=%s",
            (uuid4(), tenant, orphan),
        )
    before = {alert: source.row(alert) for alert in (linked, api)}
    assert present_alert(before[linked])["provider"] is None
    result = backfill_tenant(real_db, tenant, limit=1)
    assert result == {"tenant_id": str(tenant), "filled": 2, "unresolved": [str(orphan)]}
    row = source.row(linked)
    assert (row["provider"], row["alert_rule_url"]) == ("grafana", RULE)
    assert source.row(api)["provider"] == "api"
    assert source.row(orphan)["provider"] is None
    for alert, old in before.items():
        new = source.row(alert)
        # Non-semantic: same revision, one more projection version for search.
        assert new["revision"] == old["revision"]
        assert new["projection_version"] == old["projection_version"] + 1
        latest = projections(real_db, tenant, alert)[-1]
        assert latest["version"] == new["projection_version"]
        assert latest["snapshot"]["provider"] == new["provider"]
    with real_db.transaction(tenant) as conn:
        activity = conn.execute(
            "SELECT count(*) AS n FROM alert_activity WHERE tenant_id=%s AND kind='provider-backfill'",
            (tenant,),
        ).fetchone()["n"]
    assert activity == 0
    # A rerun finds nothing new to fill.
    assert backfill_tenant(real_db, tenant)["filled"] == 0


def test_migration_checks_reject_unknown_providers_and_api_rule_urls(real_db, tenant):
    """Requirements: alert operations 3; migration 0060 (REV-55)."""
    source = Source(real_db, tenant)
    alert = source.apply(native("test-" + uuid4().hex))
    for assignment in ("provider='kafka'", f"provider='api',alert_rule_url='{RULE}'"):
        with pytest.raises(psycopg.errors.CheckViolation):
            with real_db.transaction(tenant) as conn:
                conn.execute(
                    f"UPDATE alerts SET {assignment} WHERE tenant_id=%s AND id=%s",
                    (tenant, alert),
                )
