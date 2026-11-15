"""Applying a transfer overlay must not destroy unrelated local work."""

from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "scripts/deployment_overlay.py"
spec = importlib.util.spec_from_file_location("deployment_overlay", SOURCE)
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)
NAME = "keep-api/Dockerfile"
NEW = "keep-delivery/docs/runbooks/private-test-deployment.md"


def test_python_build_overlay_transfers_poetry_metadata_and_lock():
    for repo in overlay.BUILD_REPOS:
        if repo != "keep-frontend":
            for path in ("pyproject.toml", "poetry.toml", "poetry.lock"):
                assert f"{repo}/{path}" in overlay.FILES


def test_overlay_carries_the_mock_adapi_and_no_keycloak_image():
    """Requirements: REV-73 (Keycloak removed; the load test runs the mock ADAPI)."""
    assert "keep-delivery/scripts/local_adapi.py" in overlay.FILES
    assert not [name for name in overlay.FILES if "keycloak" in name.lower()]


def archive(tmp_path, files=None, extra=None):
    files = files or {NAME: (b"new", b"old"), NEW: (b"guide", None)}
    manifest = {"format": "keep-isolated-test-overlay-v1", "classification": "test",
                "repositories": {repo: "a" * 40 for repo in overlay.REPOS},
                "files": {name: {"sha256": sha256(current).hexdigest(),
                                 "base_sha256": sha256(base).hexdigest() if base else None}
                          for name, (current, base) in files.items()}}
    output = tmp_path / "overlay.tar.gz"
    with tarfile.open(output, "w:gz") as tar:
        entries = {"overlay-manifest.json": json.dumps(manifest).encode(),
                   **{name: current for name, (current, _) in files.items()}, **(extra or {})}
        for name, data in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return output


def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    for repo in overlay.REPOS:
        (root / repo).mkdir(parents=True)

    def git(root, repo, *args, **kwargs):
        if args == ("rev-parse", "--show-toplevel"):
            return str((root / repo).resolve()).encode()
        return ("a" * 40).encode()

    monkeypatch.setattr(overlay, "git", git)
    (root / NAME).write_bytes(b"old")
    return root


def test_check_is_read_only_and_apply_is_idempotent(tmp_path, monkeypatch):
    root = workspace(tmp_path, monkeypatch)
    package = archive(tmp_path)
    assert overlay.apply_overlay(root, package)["pending"] == 2
    assert (root / NAME).read_bytes() == b"old"
    assert not (root / NEW).exists()
    overlay.apply_overlay(root, package, apply=True)
    assert (root / NAME).read_bytes() == b"new"
    assert (root / NEW).read_bytes() == b"guide"
    assert overlay.apply_overlay(root, package, apply=True)["pending"] == 0


def test_conflict_preflights_every_file_before_writing_anything(tmp_path, monkeypatch):
    root = workspace(tmp_path, monkeypatch)
    (root / NEW).parent.mkdir(parents=True)
    (root / NEW).write_bytes(b"someone else's work")
    with pytest.raises(ValueError, match="conflicts"):
        overlay.apply_overlay(root, archive(tmp_path), apply=True)
    assert (root / NAME).read_bytes() == b"old"
    assert (root / NEW).read_bytes() == b"someone else's work"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "keep-api/.git/config",
                                  "keep-api/.env", "keep-api/unassigned.py",
                                  "keep-api/../keep-api/Dockerfile"])
def test_unassigned_and_traversal_targets_refused(tmp_path, name):
    with pytest.raises(ValueError):
        overlay.load_archive(archive(tmp_path, extra={name: b"untrusted"}))


def test_altered_payload_refused(tmp_path):
    path = archive(tmp_path)
    manifest, contents = overlay.load_archive(path)
    contents[NAME] = b"changed"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in {"overlay-manifest.json": json.dumps(manifest).encode(), **contents}.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    with pytest.raises(ValueError, match="hash mismatch"):
        overlay.load_archive(path)
