"""Real PostgreSQL CAS races with actual AES-GCM envelopes; no remote systems."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from threading import Barrier, Event
from uuid import uuid4

import pytest

from appchi.shared.crypto import EnvelopeCipher
from appchi.shared.rewrap import FAMILIES, rewrap_page
from appchi.shared.errors import AppError
from test_search_pipeline import real_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def secrets(request):
    database = request.getfixturevalue("real_db")
    old = EnvelopeCipher({"old": b"a" * 32}, "old")
    current = EnvelopeCipher({"old": b"a" * 32, "current": b"b" * 32}, "current")
    principal, operator, credential, receipt = (uuid4() for _ in range(4))
    handle, login = sha256(uuid4().bytes).digest(), sha256(uuid4().bytes).digest()
    records = {
        "operator-credentials": (
            credential,
            f"operator-key:{operator}:{credential}:1",
            b"existing-api-key",
        ),
        "session-tokens": (
            handle,
            "session-tokens:" + handle.hex(),
            b'{"refresh_token":"existing-refresh"}',
        ),
        "session-csrf": (handle, "session-csrf:" + handle.hex(), b"existing-csrf"),
        "login-transactions": (login, "login:" + login.hex(), b'{"state":"existing-login"}'),
        "ingest-receipts": (receipt, f"receipt:{receipt}", b'{"receipt_token":"existing-receipt"}'),
    }
    ciphertexts = {family: old.seal(value, aad) for family, (_, aad, value) in records.items()}
    with database.transaction() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
        conn.execute(
            "INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible) VALUES(%s,'test',%s,'user','Synthetic user',true)",
            (principal, str(principal)),
        )
        conn.execute(
            "INSERT INTO operators(id,tenant_id,name,name_key,lifecycle) VALUES(%s,%s,%s,%s,'ready')",
            (operator, tenant, str(operator), str(operator)),
        )
        conn.execute(
            "INSERT INTO operator_credentials(id,tenant_id,operator_id,state,verifier,ciphertext) VALUES(%s,%s,%s,'current',%s,%s)",
            (
                credential,
                tenant,
                operator,
                sha256(b"existing-api-key").digest(),
                ciphertexts["operator-credentials"],
            ),
        )
        conn.execute(
            "INSERT INTO auth_sessions(handle_hash,principal_id,issuer,subject,provider_sid,tokens,csrf_hash,csrf_cipher,auth_time,activity_at,absolute_at,deployment_epoch) VALUES(%s,%s,'https://test.invalid',%s,%s,%s,%s,%s,clock_timestamp(),clock_timestamp(),clock_timestamp()+interval '1 day','test')",
            (
                handle,
                principal,
                str(principal),
                str(uuid4()),
                ciphertexts["session-tokens"],
                sha256(b"existing-csrf").digest(),
                ciphertexts["session-csrf"],
            ),
        )
        conn.execute(
            "INSERT INTO login_transactions(state_hash,browser_hash,encrypted_state,expires_at) VALUES(%s,%s,%s,clock_timestamp()+interval '10 minutes')",
            (login, sha256(b"browser").digest(), ciphertexts["login-transactions"]),
        )
        conn.execute(
            "INSERT INTO ingest_requests(id,tenant_id,version,retry_scope,fingerprint,receipt_hash,response_cipher,response_status,admitted_at,expires_at) VALUES(%s,%s,'v1','test',%s,%s,%s,202,clock_timestamp(),clock_timestamp()+interval '1 day')",
            (
                receipt,
                tenant,
                sha256(b"request").digest(),
                sha256(b"existing-receipt").digest(),
                ciphertexts["ingest-receipts"],
            ),
        )
    return database, old, current, records


def _row(database, family, identity):
    spec = FAMILIES[family]
    with database.transaction() as conn:
        return conn.execute(
            f"SELECT * FROM {spec.table} WHERE {spec.primary}=%s", (identity,)
        ).fetchone()


def test_all_known_families_rewrap_without_changing_identity_or_lifecycle(secrets):
    database, _, current, records = secrets
    original = database.transaction

    @contextmanager
    def restricted(tenant_id=None):
        with original(tenant_id) as conn:
            conn.execute("SET LOCAL ROLE appchi_worker")
            yield conn

    database.transaction = restricted
    try:
        for family, (identity, aad, value) in records.items():
            before = _row(database, family, identity)
            page = rewrap_page(database, current, family)
            assert page.examined == page.needs_rewrap == page.rewrapped == 1
            assert page.conflicts == 0 and page.next_cursor is None
            after = _row(database, family, identity)
            column = FAMILIES[family].column
            assert current.open(bytes(after[column]), aad) == value
            assert before[column] != after[column]
            assert {k: v for k, v in before.items() if k != column} == {
                k: v for k, v in after.items() if k != column
            }
            assert rewrap_page(database, current, family, verify_only=True).already_current == 1
    finally:
        database.transaction = original


@pytest.mark.parametrize("family", ["operator-credentials", "session-tokens"])
def test_rewrap_cannot_resurrect_revocation_or_overwrite_refresh(secrets, family):
    database, _, current, records = secrets
    identity, aad, _ = records[family]
    entered, release = Event(), Event()

    class Paused(EnvelopeCipher):
        def rewrap(self, value, context):
            result = super().rewrap(value, context)
            entered.set()
            assert release.wait(10)
            return result

    paused = Paused(current.keys, current.current)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(rewrap_page, database, paused, family)
        try:
            assert entered.wait(10)
            with database.transaction() as conn:
                if family == "operator-credentials":
                    conn.execute(
                        "UPDATE operator_credentials SET state='revoked',revoked_at=clock_timestamp(),ciphertext=NULL WHERE id=%s",
                        (identity,),
                    )
                else:
                    conn.execute(
                        "UPDATE auth_sessions SET tokens=%s,refresh_generation=refresh_generation+1 WHERE handle_hash=%s",
                        (current.seal(b"new-refresh-token", aad), identity),
                    )
        finally:
            release.set()
        page = worker.result(timeout=10)
    assert page.conflicts == 1 and page.rewrapped == 0
    row = _row(database, family, identity)
    if family == "operator-credentials":
        assert row["ciphertext"] is None and row["state"] == "revoked"
    else:
        assert current.open(bytes(row["tokens"]), aad) == b"new-refresh-token"
        assert row["refresh_generation"] == 1


def test_parallel_rewrap_workers_converge_via_exact_ciphertext_cas(secrets):
    database, _, current, records = secrets
    barrier = Barrier(2)

    class Simultaneous(EnvelopeCipher):
        def rewrap(self, value, aad):
            result = super().rewrap(value, aad)
            barrier.wait(timeout=10)
            return result

    cipher = Simultaneous(current.keys, current.current)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pages = list(
            pool.map(lambda _: rewrap_page(database, cipher, "operator-credentials"), range(2))
        )
    assert sum(page.rewrapped for page in pages) == 1
    assert sum(page.conflicts for page in pages) == 1
    identity, aad, secret = records["operator-credentials"]
    assert (
        current.open(bytes(_row(database, "operator-credentials", identity)["ciphertext"]), aad)
        == secret
    )


def test_bounded_keyset_pages_and_verify_only_do_not_rewrite(secrets):
    database, old, current, records = secrets
    with database.transaction() as conn:
        for _ in range(4):
            identity = sha256(uuid4().bytes).digest()
            conn.execute(
                "INSERT INTO login_transactions(state_hash,browser_hash,encrypted_state,expires_at) VALUES(%s,%s,%s,clock_timestamp()+interval '10 minutes')",
                (
                    identity,
                    sha256(b"browser").digest(),
                    old.seal(b"login", "login:" + identity.hex()),
                ),
            )
    verification = rewrap_page(database, current, "login-transactions", limit=2, verify_only=True)
    assert verification.needs_rewrap == 2 and verification.rewrapped == 0
    assert verification.next_cursor
    with pytest.raises(AppError) as failure:
        rewrap_page(database, current, "session-csrf", cursor=verification.next_cursor)
    assert failure.value.code == "rewrap_cursor_invalid"
    count, cursor = 0, None
    for _ in range(10):
        page = rewrap_page(database, current, "login-transactions", limit=2, cursor=cursor)
        assert page.examined <= 2
        count += page.rewrapped
        cursor = page.next_cursor
        if cursor is None:
            break
    assert count == 5 and cursor is None


def test_wrong_record_aad_is_rejected_without_changing_ciphertext(secrets):
    database, old, current, records = secrets
    identity, _, _ = records["operator-credentials"]
    swapped = old.seal(b"different-credential", "operator-key:wrong:identity:1")
    with database.transaction() as conn:
        conn.execute(
            "UPDATE operator_credentials SET ciphertext=%s WHERE id=%s", (swapped, identity)
        )
    with pytest.raises(AppError) as failure:
        rewrap_page(database, current, "operator-credentials")
    assert failure.value.code == "rewrap_authentication_failed"
    assert bytes(_row(database, "operator-credentials", identity)["ciphertext"]) == swapped


def test_family_rewrap_uses_supplied_purpose_target(secrets):
    import json

    database, _, current, records = secrets
    purpose = EnvelopeCipher(
        {**current.keys, "csrf-v3": b"c" * 32},
        current.current,
        purposes={"session-csrf": "csrf-v3"},
    )
    result = rewrap_page(database, purpose, "session-csrf")
    assert result.active_key_id == "csrf-v3" and result.rewrapped == 1
    identity, aad, secret = records["session-csrf"]
    encrypted = bytes(_row(database, "session-csrf", identity)["csrf_cipher"])
    assert json.loads(encrypted)["kid"] == "csrf-v3"
    assert purpose.open(encrypted, aad) == secret
