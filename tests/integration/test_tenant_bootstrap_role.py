"""Exercise tenant bootstrap with the actual restricted application role."""

from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest

from test_product import browser  # noqa: F401

pytestmark = pytest.mark.integration


def test_superadmin_can_create_tenant_under_api_role(browser, real_db, monkeypatch):  # noqa: F811
    client, principal = browser
    with real_db.transaction() as conn:
        conn.execute("UPDATE principals SET super_admin=true WHERE id=%s", (principal,))
    transaction = real_db.transaction

    @contextmanager
    def restricted(tenant_id=None):
        with transaction(tenant_id) as conn:
            conn.execute("SET LOCAL ROLE appchi_api")
            yield conn

    body = {
        "command_id": str(uuid4()),
        "name": "Role bootstrap " + str(uuid4()),
        "initial_grants": [{"principal_id": str(principal), "role": "admin"}],
    }
    with monkeypatch.context() as patch:
        patch.setattr(real_db, "transaction", restricted)
        response = client.post("/api/tenants", json=body)
        assert response.status_code == 201, response.text
        assert client.post("/api/tenants", json=body).json() == response.json()
    tenant_id = response.json()["data"]["id"]
    with transaction(tenant_id) as conn:
        conn.execute("SET LOCAL ROLE appchi_api")
        assert conn.execute("SELECT count(*) AS n FROM live_hint_outbox").fetchone()["n"] == 1
        assert conn.execute("SELECT tenant_id FROM live_hint_outbox").fetchone()[
            "tenant_id"
        ] == UUID(tenant_id)
