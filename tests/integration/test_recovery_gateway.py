from dataclasses import asdict
from uuid import uuid4
import pytest
from appchi.ingestion.gateway import GatewayIdentity
from appchi.ingestion.native import canonical
from appchi.ingestion.service import Admission
from appchi.recovery.retirement import retire_alert
from appchi.recovery.evidence import DurableAck
from appchi.shared.errors import AppError
from test_product import admit_alert

pytestmark = pytest.mark.integration


class IndependentStore:
    def __init__(self):
        self.records = {}
        self.fail = None
        self.on_intent = None

    def append(self, record):
        if record.state == self.fail:
            raise RuntimeError("injected unavailable evidence service")
        value = asdict(record)
        assert self.records.setdefault(record.id, value) == value
        if record.state == "intent" and self.on_intent:
            callback, self.on_intent = self.on_intent, None
            callback()
        return DurableAck(record.id, "test", str(len(self.records)), record.digest)


def eligible(real_db, cipher, profile, v1):
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='recovery'")
        conn.execute(
            "UPDATE alerts SET status='resolved',resolved_at=clock_timestamp()-interval '91 days',retention_at=clock_timestamp()-interval '1 day' WHERE id=%s",
            (alert,),
        )
    return tenant, alert


def test_retirement_final_failure_resume_does_not_resurrect(real_db, cipher, profile, v1):
    tenant, alert = eligible(real_db, cipher, profile, v1)
    store = IndependentStore()
    store.fail = "final"
    with pytest.raises(RuntimeError):
        retire_alert(real_db, store, tenant, alert)
    with real_db.transaction() as conn:
        assert conn.execute(
            "SELECT retired_at FROM alert_identities WHERE id=%s", (alert,)
        ).fetchone()["retired_at"]
        assert (
            conn.execute("SELECT count(*) AS n FROM alerts WHERE id=%s", (alert,)).fetchone()["n"]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM alert_key_claims WHERE alert_id=%s", (alert,)
            ).fetchone()["n"]
            == 0
        )
    store.fail = None
    assert retire_alert(real_db, store, tenant, alert)
    assert retire_alert(real_db, store, tenant, alert)
    assert {r["state"] for r in store.records.values()} == {"intent", "final"}
    assert len(store.records) == 2
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='recovery'")


def test_retirement_intent_failure_and_revision_race(real_db, cipher, profile, v1):
    tenant, alert = eligible(real_db, cipher, profile, v1)
    store = IndependentStore()
    store.fail = "intent"
    with pytest.raises(RuntimeError):
        retire_alert(real_db, store, tenant, alert)
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT retired_at FROM alert_identities WHERE id=%s", (alert,)
            ).fetchone()["retired_at"]
            is None
        )

    def reopen():
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE alerts SET status='firing',retention_at=NULL,resolved_at=NULL,revision=revision+1 WHERE id=%s",
                (alert,),
            )

    store.fail = None
    store.on_intent = reopen
    assert not retire_alert(real_db, store, tenant, alert)
    assert {r["state"] for r in store.records.values()} == {"intent", "cancel"}
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT retired_at FROM alert_identities WHERE id=%s", (alert,)
            ).fetchone()["retired_at"]
            is None
        )
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='recovery'")


def test_v2_authoritative_binding_revocation_and_receipt(real_db, cipher, profile, tenant):
    op, cred, consumer, external = [uuid4() for _ in range(4)]
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='v2_ingress'")
        conn.execute(
            "INSERT INTO operators(id,tenant_id,name,name_key,lifecycle) VALUES(%s,%s,%s,%s,'ready')",
            (op, tenant, str(op), str(op)),
        )
        conn.execute(
            "INSERT INTO operator_credentials(id,tenant_id,operator_id,state,verifier,kong_consumer_id,kong_credential_id) VALUES(%s,%s,%s,'current',%s,%s,%s)",
            (cred, tenant, op, cred.bytes, str(consumer), str(external)),
        )
    gateway = GatewayIdentity(str(consumer), str(external))
    raw = canonical(
        {
            "application": "test",
            "component": "cpu",
            "message": "test",
            "severity": "warning",
            "status": "firing",
            "site": "lab",
            "impact": "test",
            "runbook_url": "https://example.invalid/runbook",
        }
    )
    service = Admission(real_db, cipher, profile)
    code, result = service.admit(raw, "v2", gateway=gateway)
    assert code == 202
    assert service.receipt(result["request_id"], result["receipt_token"], "v2", gateway)["items"]
    with real_db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT tenant_id FROM ingest_requests WHERE id=%s", (result["request_id"],)
            ).fetchone()["tenant_id"]
            == tenant
        )
        conn.execute(
            "UPDATE operator_credentials SET state='revoked',revoked_at=clock_timestamp() WHERE id=%s",
            (cred,),
        )
    with pytest.raises(AppError) as failure:
        service.admit(raw, "v2", gateway=gateway)
    assert failure.value.status == 401
    with pytest.raises(AppError):
        service.receipt(result["request_id"], result["receipt_token"], "v2", gateway)
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='v2_ingress'")


def test_retirement_can_retry_cancelled_attempt_with_unchanged_versions(
    real_db, cipher, profile, v1
):
    from appchi.alerts.processor import process_one

    tenant, alert = eligible(real_db, cipher, profile, v1)
    store = IndependentStore()

    def pending_conflict():
        Admission(real_db, cipher, profile).admit(canonical({**v1, "severity": "major"}), "v1")

    store.on_intent = pending_conflict
    assert not retire_alert(real_db, store, tenant, alert)
    while process_one(real_db, tenant):
        pass
    assert retire_alert(real_db, store, tenant, alert)
    assert len([r for r in store.records.values() if r["state"] == "intent"]) == 2
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='recovery'")


def test_retirement_cancellation_protection_resumes_after_reopen(real_db, cipher, profile, v1):
    tenant, alert = eligible(real_db, cipher, profile, v1)
    store = IndependentStore()

    def reopen():
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE alerts SET status='firing',revision=revision+1,retention_at=NULL,resolved_at=NULL WHERE id=%s",
                (alert,),
            )
        store.fail = "cancel"

    store.on_intent = reopen
    with pytest.raises(RuntimeError):
        retire_alert(real_db, store, tenant, alert)
    store.fail = None
    assert not retire_alert(real_db, store, tenant, alert)
    assert {r["state"] for r in store.records.values()} == {"intent", "cancel"}
    with real_db.transaction() as conn:
        assert conn.execute(
            "SELECT cancel_ack FROM retirement_jobs WHERE resource_id=%s", (alert,)
        ).fetchone()["cancel_ack"]
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='recovery'")
