"""Image dashboard panels through the real Product API and PostgreSQL (REV-62)."""
# ruff: noqa: F811

import struct
import zlib
from uuid import uuid4

import pytest

from test_product import browser  # noqa: F401
from test_configuration_deletion import configuration, create, headers  # noqa: F401

pytestmark = pytest.mark.integration


def png(width, height, salt=b""):
    """A small valid PNG header; ``salt`` makes the bytes, and so the image, unique."""
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk = b"IHDR" + header
    text = b"tEXt" + b"c\x00" + salt
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13) + chunk + struct.pack(">I", zlib.crc32(chunk))
        + struct.pack(">I", len(text) - 4) + text + struct.pack(">I", zlib.crc32(text))
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def upload(configuration, data, media="image/png", command=None):
    client, base, *_ = configuration
    return client.post(
        base + "/dashboard-images",
        content=data,
        headers={"Content-Type": media, "Idempotency-Key": command or str(uuid4())},
    )


def image_panel(x=0, **fields):
    return {"id": str(uuid4()), "type": "image", "title": None, "x": x, "y": 0, "w": 3, "h": 2,
            "image_id": None, "url": None, "fit": "contain", "alt": None, "link": None, **fields}


def save(configuration, panels, *, dashboard=None, revision=None):
    client, base, *_ = configuration
    body = {"command_id": str(uuid4()), "name": str(uuid4()), "panels": panels}
    if dashboard:
        body.update(name=dashboard["name"], expected_revision=revision)
        return client.put(base + "/dashboards/" + dashboard["id"], json=body)
    return client.post(base + "/dashboards", json=body)


def test_upload_stores_once_and_serves_safely(configuration):
    """Requirements: REV-62; dashboard operations 13 and 14."""
    client, base, *_ = configuration
    data = png(1600, 900, uuid4().bytes)
    command = str(uuid4())
    first = upload(configuration, data, command=command)
    assert first.status_code == 201, first.text
    image = first.json()["data"]
    assert {k: image[k] for k in ("media_type", "width", "height", "byte_size")} == {
        # Integers travel as strings, as everywhere in the Product API.
        "media_type": "image/png", "width": "1600", "height": "900", "byte_size": str(len(data))}
    assert upload(configuration, data, command=command).json() == first.json()
    again = upload(configuration, data, media="image/webp")
    assert again.status_code == 200, again.text
    assert again.json()["data"]["id"] == image["id"]
    served = client.get(base + "/dashboard-images/" + image["id"])
    assert served.status_code == 200 and served.content == data
    assert served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert served.headers["cache-control"] == "private, no-cache"
    cached = client.get(base + "/dashboard-images/" + image["id"],
                        headers={"If-None-Match": served.headers["etag"]})
    assert cached.status_code == 304 and cached.content == b""
    reused = upload(configuration, png(10, 10, uuid4().bytes), command=command)
    assert reused.status_code == 409, reused.text


@pytest.mark.parametrize(
    ("data", "media", "status", "code"),
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml", 415, "unsupported_media_type"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/png", 422, "invalid_image"),
        (b"<html><script>alert(1)</script></html>", "image/gif", 422, "invalid_image"),
        (png(8001, 10), "image/png", 422, "invalid_image"),
        (b"x" * (5 * 1024 * 1024 + 1), "image/png", 413, "body_too_large"),
    ],
    ids=["svg-type", "svg-bytes", "html-bytes", "too-wide", "too-large"],
)
def test_upload_rejects_other_files(configuration, data, media, status, code):
    """Requirements: REV-62 limits and file checks."""
    response = upload(configuration, data, media)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def test_upload_needs_a_command_and_edit_access(configuration):
    """Requirements: REV-62; shared commands 7."""
    client, base, *_ = configuration
    missing = client.post(base + "/dashboard-images", content=png(4, 4), headers={"Content-Type": "image/png"})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "invalid_command"
    anonymous = client.post(
        base + "/dashboard-images",
        content=png(4, 4),
        headers={"Content-Type": "image/png", "Idempotency-Key": str(uuid4()), "X-CSRF-Token": "wrong"},
    )
    assert anonymous.status_code in (401, 403), anonymous.text


def test_image_panels_store_read_and_keep_history(configuration):
    """Requirements: REV-62; dashboard operations 2, 13, 14 and 15."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    image = upload(configuration, png(800, 450, uuid4().bytes)).json()["data"]
    uploaded = image_panel(0, image_id=image["id"], alt="Checkout service map", title="Map")
    linked = image_panel(3, url="https://wiki.example.com/map.png?v=2", fit="cover")
    counted = {"id": str(uuid4()), "preset_id": preset["id"], "title": None, "x": 6, "y": 0,
               "w": 3, "h": 2, "thresholds": [], "link": None}
    response = save(configuration, [uploaded, linked, counted])
    assert response.status_code == 201, response.text
    dashboard = response.json()["data"]
    panels = {panel["id"]: panel for panel in dashboard["panels"]}
    assert panels[uploaded["id"]] == uploaded
    assert panels[linked["id"]] == linked
    read = client.get(base + "/dashboards/" + dashboard["id"])
    assert read.json()["data"]["panels"] == dashboard["panels"]
    references = client.get(base + "/presets/" + preset["id"] + "/references")
    assert [item["dashboard_id"] for item in references.json()["data"]] == [dashboard["id"]]
    same = save(configuration, [uploaded, linked, counted], dashboard=dashboard, revision="1")
    assert same.status_code == 200 and same.json()["meta"]["revision"] == "1"
    moved = save(configuration, [linked], dashboard=dashboard, revision="1")
    assert moved.status_code == 200, moved.text
    history = client.get(base + "/dashboards/" + dashboard["id"] + "/history/1")
    assert history.json()["data"]["definition"]["panels"] == dashboard["panels"]
    # The image a past revision shows keeps serving after its panel is removed.
    assert client.get(base + "/dashboard-images/" + image["id"]).status_code == 200


def test_unknown_or_foreign_images_are_unavailable(configuration, real_db):
    """Requirements: REV-62; dashboard operations 13 and 14."""
    client, base, _, tenant, actor = configuration
    other = uuid4()
    foreign = uuid4()
    with real_db.transaction() as conn:
        conn.execute("INSERT INTO tenants(id,kind,name,name_key) VALUES(%s,'specific',%s,%s)",
                     (other, str(other), str(other)))
        conn.execute(
            "INSERT INTO dashboard_images(id,tenant_id,sha256,media_type,width,height,byte_size,bytes,created_at,created_by) "
            "VALUES(%s,%s,%s,'image/png',4,4,4,'PNG!',now(),%s)",
            (foreign, other, uuid4().bytes * 2, actor),
        )
    for image_id in (str(uuid4()), str(foreign)):
        saved = save(configuration, [image_panel(image_id=image_id)])
        assert saved.status_code == 422 and saved.json()["error"]["code"] == "image_unavailable"
        checked = client.post(base + "/dashboards/validate",
                              json={"name": "Check", "panels": [image_panel(image_id=image_id)]})
        assert checked.status_code == 422 and checked.json()["error"]["code"] == "image_unavailable"
        assert client.get(base + "/dashboard-images/" + image_id).status_code == 404
    other_base = f"/api/tenants/{other}"
    assert client.get(other_base + "/dashboard-images/" + str(foreign)).status_code == 404


def test_invalid_addresses_and_sources_are_rejected(configuration):
    """Requirements: REV-62; dashboard operations 14."""
    for fields in (
        {"url": "http://example.com/a.png"},
        {"url": "https://user:pw@example.com/a.png"},
        {},
    ):
        response = save(configuration, [image_panel(**fields)])
        assert response.status_code == 422, response.text


def test_never_used_uploads_are_cleaned_up_by_later_uploads(configuration, real_db):
    """Requirements: REV-62; dashboard operations 13; worker scope REV-34."""
    client, base, _, tenant, _ = configuration
    stale = upload(configuration, png(4, 4, uuid4().bytes)).json()["data"]["id"]
    used = upload(configuration, png(4, 4, uuid4().bytes)).json()["data"]["id"]
    assert save(configuration, [image_panel(image_id=used)]).status_code == 201
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE dashboard_images SET created_at=created_at-interval '25 hours' WHERE id = ANY(%s)",
            ([stale, used],),
        )
    assert upload(configuration, png(4, 4, uuid4().bytes)).status_code == 201
    assert client.get(base + "/dashboard-images/" + stale).status_code == 404
    assert client.get(base + "/dashboard-images/" + used).status_code == 200
    late = save(configuration, [image_panel(image_id=stale)])
    assert late.status_code == 422 and late.json()["error"]["code"] == "image_unavailable"


def test_quota_limits_stored_bytes_per_tenant(configuration, real_db, monkeypatch):
    """Requirements: REV-62 limits."""
    import appchi.configuration.image_routes as routes

    client, base, _, tenant, _ = configuration
    with real_db.transaction() as conn:
        stored = conn.execute(
            "SELECT coalesce(sum(byte_size),0) AS total FROM dashboard_images WHERE tenant_id=%s", (tenant,)
        ).fetchone()["total"]
    data = png(4, 4, uuid4().bytes)
    monkeypatch.setattr(routes, "TENANT_QUOTA", stored + len(data) - 1)
    full = upload(configuration, data)
    assert full.status_code == 409 and full.json()["error"]["code"] == "image_quota_exceeded"
    monkeypatch.setattr(routes, "TENANT_QUOTA", stored + len(data))
    assert upload(configuration, data).status_code == 201


def test_image_panels_do_not_reference_or_block_presets(configuration):
    """Requirements: REV-62; dashboard operations 2 and 13."""
    client, base, *_ = configuration
    preset, _ = create(configuration)
    created = save(configuration, [image_panel(url="https://example.com/a.png")])
    assert created.status_code == 201, created.text
    assert client.get(base + "/presets/" + preset["id"] + "/references").json()["data"] == []
    assert client.delete(base + "/presets/" + preset["id"], headers=headers()).status_code == 200
