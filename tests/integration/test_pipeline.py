"""Real SQL lease/ack/quarantine boundaries with controlled Kafka callbacks."""

# ruff: noqa: F811
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest

from appchi.ingestion.native import canonical
from appchi.ingestion.service import Admission
from appchi.pipeline import claim, head, acknowledge, relay_once, record_receipt, consume_once
from appchi.shared.errors import AppError
from test_search_pipeline import real_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def pipeline_db(real_db):
    draft = Path("migrations/0017_pipeline_leases.sql.draft")
    if draft.exists():
        with real_db.transaction() as conn:
            conn.execute(draft.read_text())
    return real_db


def seed(db, cipher, profile, v1):
    admission = Admission(db, cipher, profile)
    admission.admit(canonical(v1), "v1")
    admission.admit(canonical(dict(v1, message="second")), "v1")


def test_leases_exclude_competitors_and_fence_stale_ack(pipeline_db, cipher, profile, v1):
    db = pipeline_db
    seed(db, cipher, profile, v1)
    lease = claim(db, "epoch")
    assert lease and claim(db, "epoch") is None
    first = head(db, lease)
    with db.transaction() as conn:
        conn.execute("UPDATE pipeline_leases SET lease_until=clock_timestamp()-interval '1 second'")
    successor = claim(db, "epoch")
    assert successor.generation == lease.generation + 1
    with pytest.raises(AppError):
        acknowledge(db, lease, first)
    assert head(db, successor)["id"] == first["id"]
    acknowledge(db, successor, first)
    assert head(db, successor)["stream_order"] == first["stream_order"] + 1


class Producer:
    def __init__(self, fail=False):
        self.fail, self.values, self.callback = fail, [], None

    def produce(self, topic, *, key, value, on_delivery):
        self.values.append((topic, key, value))
        self.callback = on_delivery

    def flush(self, timeout):
        self.callback("controlled_failure" if self.fail else None, None)
        return 0


def test_relay_failed_publish_reuses_exact_bytes(pipeline_db, cipher, profile, v1):
    db = pipeline_db
    seed(db, cipher, profile, v1)
    settings = SimpleNamespace(deployment_epoch="test", environment="development")
    failed = Producer(True)
    with pytest.raises(AppError):
        relay_once(db, settings, failed)
    with db.transaction() as conn:
        assert not conn.execute(
            "SELECT published_at FROM application_outbox ORDER BY stream_order LIMIT 1"
        ).fetchone()["published_at"]
        conn.execute("UPDATE pipeline_leases SET next_attempt_at=clock_timestamp()")
    good = Producer()
    assert relay_once(db, settings, good) >= 1
    assert good.values[0] == failed.values[0]


def test_receipt_proves_exact_reference_and_quarantines_mismatch(pipeline_db, cipher, profile, v1):
    db = pipeline_db
    seed(db, cipher, profile, v1)
    lease = claim(db, "epoch")
    event = head(db, lease)
    args = dict(
        consumer="processor",
        topic="appchi.development.alerts.received",
        partition=0,
        offset=1,
        key=lease.tenant.bytes,
        value=bytes(event["envelope"]),
        environment="development",
    )
    assert record_receipt(db, **args) == lease.tenant
    assert record_receipt(db, **args) == lease.tenant
    with pytest.raises(AppError):
        record_receipt(db, **dict(args, offset=2, value=bytes(event["envelope"]) + b" "))
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM pipeline_receipts").fetchone()["n"] == 1
        assert conn.execute("SELECT blocked FROM pipeline_leases").fetchone()["blocked"]
        assert (
            conn.execute("SELECT reason FROM pipeline_quarantine").fetchone()["reason"]
            == "pipeline_authoritative_proof_missing"
        )


def test_poison_record_cannot_commit_past_failed_offset(pipeline_db):
    class Message:
        def error(self):
            return None

        def topic(self):
            return "appchi.development.alerts.received"

        def partition(self):
            return 0

        def offset(self):
            return 12

        def key(self):
            return uuid4().bytes

        def value(self):
            return b'{"bad":"event"}'

    class Client:
        committed = False
        sought = None

        def poll(self, timeout):
            return Message()

        def commit(self, **kwargs):
            self.committed = True

        def seek(self, position):
            self.sought = position

    client = Client()
    with pytest.raises(AppError):
        consume_once(pipeline_db, SimpleNamespace(environment="development"), "processor", client)
    assert not client.committed
    assert client.sought.offset == 12
