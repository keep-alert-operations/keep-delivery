"""Retained source and deleted-note discovery through the real Product API."""
# ruff: noqa: F811

from uuid import uuid4

import pytest

from test_search_pipeline import real_db  # noqa: F401
from test_product import browser, admit_alert  # noqa: F401

pytestmark = pytest.mark.integration


def test_deleted_notes_are_discoverable_without_current_text(browser, real_db, cipher, profile, v1):
    client, principal = browser
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    path = f"/api/tenants/{tenant}/alerts/{alert}"
    created = client.post(
        path + "/notes",
        json={
            "command_id": str(uuid4()),
            "expected_revision": "1",
            "text": "Original retained text",
        },
    )
    assert created.status_code == 201
    note = created.json()["data"]["id"]
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE principals SET display_name='Renamed editor' WHERE id=%s", (principal,)
        )
    edited = client.patch(
        path + "/notes/" + note,
        json={
            "command_id": str(uuid4()),
            "expected_revision": "2",
            "text": "Updated retained text",
        },
    )
    assert edited.status_code == 200
    deleted = client.delete(
        path + "/notes/" + note, headers={"Idempotency-Key": str(uuid4()), "If-Match": '"3"'}
    )
    assert deleted.status_code == 200
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE principals SET display_name='Current directory name' WHERE id=%s", (principal,)
        )
    assert client.get(path + "/notes").json()["data"] == []
    discovered = client.get(path + "/notes?include_deleted=true")
    assert discovered.status_code == 200
    assert discovered.json()["data"][0]["id"] == note
    assert discovered.json()["data"][0]["text"] is None
    assert discovered.json()["data"][0]["actor_display_name"] == "Synthetic user"
    history = client.get(path + "/notes/" + note + "/history?limit=1")
    assert history.status_code == 200 and history.json()["data"][0]["deleted"]
    assert history.json()["data"][0]["actor_display_name"] == "Renamed editor"
    assert history.json()["next_cursor"]
    next_page = client.get(
        path + "/notes/" + note + "/history", params={"cursor": history.json()["next_cursor"]}
    )
    assert [row["text"] for row in next_page.json()["data"]] == [
        "Updated retained text",
        "Original retained text",
    ]
    assert [row["actor_display_name"] for row in next_page.json()["data"]] == [
        "Renamed editor",
        "Synthetic user",
    ]
    activity = client.get(path + "/history?direction=asc").json()["data"]
    assert [(row["kind"], row["actor_display_name"]) for row in activity] == [
        ("source", None),
        ("note-create", "Synthetic user"),
        ("note-edit", "Renamed editor"),
        ("note-delete", "Renamed editor"),
    ]
    assert all(row["actor_id"] == str(principal) for row in activity if row["kind"] != "source")


def test_note_filter_binds_cursor_and_source_detail_preserves_received_json(
    browser, real_db, cipher, profile, v1
):
    client, _ = browser
    v1["message"] = "<script>window.sourceExecuted=true</script>"
    tenant, alert = admit_alert(real_db, cipher, profile, v1)
    path = f"/api/tenants/{tenant}/alerts/{alert}"
    for revision in (1, 2):
        assert (
            client.post(
                path + "/notes",
                json={
                    "command_id": str(uuid4()),
                    "expected_revision": str(revision),
                    "text": "Note " + str(revision),
                },
            ).status_code
            == 201
        )
    page = client.get(path + "/notes?limit=1").json()
    assert page["next_cursor"]
    changed_scope = client.get(
        path + "/notes", params={"cursor": page["next_cursor"], "include_deleted": "true"}
    )
    assert changed_scope.status_code == 409
    assert changed_scope.json()["error"]["code"] == "invalid_cursor"
    assert client.get(path + "/notes?include_deleted=1").status_code == 422
    assert client.get(path + "/source-events?include_deleted=true").status_code == 422
    event = client.get(path + "/source-events?direction=asc").json()["data"][0]
    assert "raw_json" not in event
    source = client.get(path + "/source-events/" + event["id"])
    assert (
        source.status_code == 200 and source.json()["data"]["payload"]["message"] == v1["message"]
    )
    assert v1["message"] in source.json()["data"]["raw_json"]
