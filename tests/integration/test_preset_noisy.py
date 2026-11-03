"""REV-58 noisy presets through the Product API: real PostgreSQL, auth and native CEL."""

from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401 -- shared authenticated session fixture

pytestmark = pytest.mark.integration


@pytest.fixture
def editor(request, real_db, tenant):
    client, actor = request.getfixturevalue("browser")
    with real_db.transaction() as conn:
        conn.execute(
            "INSERT INTO tenant_grants(tenant_id,principal_id,role) VALUES(%s,%s,'edit')",
            (tenant, actor),
        )
    return client, tenant


def definition(**extra):
    return {
        "name": "Night " + uuid4().hex[:8],
        "cel": "true",
        "sort": [{"field": "timestamp", "direction": "desc"}],
        "columns": ["severity", "message"],
        **extra,
    }


def test_noisy_saves_revisions_history_and_old_retries(editor, real_db):
    """Requirements: REV-58; preset operations 3, 8, 10 and 16."""
    client, tenant = editor
    root = f"/api/tenants/{tenant}/presets"
    body = {"command_id": str(uuid4()), **definition()}  # a client from before REV-58
    created = client.post(root, json=body)
    assert created.status_code == 201, created.text
    preset = created.json()["data"]
    assert preset["noisy"] is False
    # The same command with an explicit false retries the committed result.
    retried = client.post(root, json={**body, "noisy": False})
    assert retried.status_code == 201 and retried.json()["data"]["id"] == preset["id"]
    assert client.post(root, json={**body, "noisy": True}).status_code == 409

    replace = {
        **{key: body[key] for key in ("name", "cel", "sort", "columns")},
        "command_id": str(uuid4()),
        "expected_revision": "1",
        "noisy": True,
    }
    updated = client.put(f"{root}/{preset['id']}", json=replace)
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["noisy"] is True
    assert updated.json()["meta"]["revision"] == "2"
    same = client.put(
        f"{root}/{preset['id']}",
        json={**replace, "command_id": str(uuid4()), "expected_revision": "2"},
    )
    assert same.status_code == 200 and same.json()["meta"]["revision"] == "2"
    assert client.get(f"{root}/{preset['id']}").json()["data"]["noisy"] is True
    listed = client.get(root).json()["data"]
    assert next(row for row in listed if row["id"] == preset["id"])["noisy"] is True
    with real_db.transaction() as conn:
        snapshot = conn.execute(
            "SELECT snapshot FROM preset_revisions WHERE id=%s AND revision=2", (preset["id"],)
        ).fetchone()["snapshot"]
        changed = conn.execute(
            "SELECT changed_fields FROM configuration_activity WHERE resource_id=%s AND revision=2",
            (preset["id"],),
        ).fetchone()["changed_fields"]
    assert snapshot["noisy"] is True and changed == ["noisy"]

    # A replace that omits the flag turns the alarm off, like any complete replacement.
    quiet = {**replace, "command_id": str(uuid4()), "expected_revision": "2"}
    del quiet["noisy"]
    off = client.put(f"{root}/{preset['id']}", json=quiet)
    assert off.status_code == 200 and off.json()["data"]["noisy"] is False
    assert off.json()["meta"]["revision"] == "3"
    for value in ("true", 1, None):
        invalid = {**replace, "command_id": str(uuid4()), "expected_revision": "3", "noisy": value}
        assert client.put(f"{root}/{preset['id']}", json=invalid).status_code == 422


def test_presets_saved_before_the_flag_read_as_not_noisy(editor, real_db):
    """Requirements: REV-58; preset operations 3; migration 0061."""
    client, tenant = editor
    root = f"/api/tenants/{tenant}/presets"
    preset = client.post(root, json={"command_id": str(uuid4()), **definition()}).json()["data"]
    with real_db.transaction() as conn:
        column = conn.execute(
            "SELECT is_nullable,column_default FROM information_schema.columns "
            "WHERE table_name='presets' AND column_name='noisy'"
        ).fetchone()
    assert column == {"is_nullable": "NO", "column_default": "false"}
    assert client.get(f"{root}/{preset['id']}").json()["data"]["noisy"] is False
