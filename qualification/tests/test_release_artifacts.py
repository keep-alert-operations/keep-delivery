"""Synthetic offline bundle checks: no service calls, image builds or qualification."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "release_artifacts.py"
spec = importlib.util.spec_from_file_location("release_artifacts", SOURCE)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def bundle(tmp_path):
    for path in (
        "migrations/0001_test.sql",
        "contracts/alerts.changed.v1.schema.json",
        "contracts/openapi.json",
        "frontend/src/lib/generated.ts",
        "backend/pyproject.toml",
        "backend/uv.lock",
        "frontend/package.json",
        "frontend/package-lock.json",
        "backend/appchi/cel/compiler.py",
        "backend/appchi/cel/catalog.py",
        "backend/appchi/maintenance/timezones.py",
        "backend/appchi/operators/policy.py",
        "deploy/backend.Dockerfile",
        "frontend/Dockerfile",
        "deploy/openshift/test.json",
        "deploy/openshift/test.yaml",
        "docs/runbooks/test.md",
        "docs/wrapping-key-rotation.md",
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture\n", encoding="utf-8")
    bodies = {
        "backend/pyproject.toml": '[project]\nversion="0.1.0"\n',
        "backend/uv.lock": '[[package]]\nname="tzdata"\nversion="fixture-tz"\n[[package]]\nname="cel-expr-python"\nversion="fixture-cel"\n',
        "frontend/package.json": '{"version":"0.1.0"}',
        "contracts/openapi.json": '{"info":{"version":"0.1.0"}}',
        "contracts/alerts.changed.v1.schema.json": '{"properties":{"schema_version":{"const":1}}}',
        "backend/appchi/cel/compiler.py": 'PROFILE="fixture-profile"\nCOST_MODEL="fixture-cost"\n',
        "backend/appchi/maintenance/timezones.py": 'PINNED_TZDATA_VERSION="fixture-tz"\n',
        "backend/appchi/operators/policy.py": 'if unicodedata.unidata_version != "15.1.0":\n    pass\n',
    }
    for path, body in bodies.items():
        (tmp_path / path).write_text(body, encoding="utf-8")
    value = release.inventory(tmp_path, "a" * 40)
    (tmp_path / "inventory.json").write_text(json.dumps(value), encoding="utf-8")
    # Deliberately synthetic evidence; validation proves structure, never truth.
    (tmp_path / "evidence.json").write_text('{"fixture":true}', encoding="utf-8")
    reference = release.reference(tmp_path, "evidence.json")
    image = {
        "reference": "fixture.invalid/app@sha256:" + "b" * 64,
        **{key: reference for key in ("sbom", "security_scan", "build_provenance")},
    }
    manifest = {
        "schema": release.MANIFEST_VERSION,
        "release_id": "synthetic-test",
        "source_revision": "a" * 40,
        "inventory": release.reference(tmp_path, "inventory.json"),
        "images": {"backend": deepcopy(image), "frontend": deepcopy(image)},
        "evidence": {name: deepcopy(reference) for name in release.EVIDENCE},
        "compatibility": {
            "schema_min": "0001_test.sql",
            "schema_max": "0001_test.sql",
            "event_versions": value["versions"]["events"],
            "cel_profiles": ["fixture-profile"],
            "rollback_images": {
                "backend": image["reference"],
                "frontend": image["reference"],
            },
            "evidence": reference,
        },
    }
    return tmp_path, value, manifest


def test_complete_synthetic_bundle_validates_without_qualification_claim(bundle):
    root, value, manifest = bundle
    release.verify_inventory(root, value)
    release.verify_manifest(root, manifest)
    assert "qualified" not in value and "qualified" not in manifest


@pytest.mark.parametrize(
    "change",
    [
        "migration_body",
        "added_migration",
        "missing_contract",
        "removed_inventory_entry",
        "version",
        "duplicate_migration",
    ],
)
def test_inventory_rejects_source_drift_and_missing_entries(bundle, change):
    root, value, _ = bundle
    if change == "migration_body":
        (root / "migrations/0001_test.sql").write_text("changed")
    elif change == "added_migration":
        (root / "migrations/0002_test.sql").write_text("new")
    elif change == "duplicate_migration":
        (root / "migrations/0001_other.sql").write_text("conflict")
    elif change == "missing_contract":
        (root / "contracts/openapi.json").unlink()
    elif change == "removed_inventory_entry":
        value["files"]["migrations"] = []
    else:
        value["versions"]["tzdata"] = "made-up"
    with pytest.raises(release.InvalidBundle):
        release.verify_inventory(root, value)


@pytest.mark.parametrize(
    "path", ["../outside", "/absolute", "C:/outside", "a/../file", "a\\file", "./file"]
)
def test_bundle_paths_cannot_escape_or_use_ambiguous_syntax(bundle, path):
    root, _, _ = bundle
    with pytest.raises(release.InvalidBundle):
        release.bundle_file(root, path)


@pytest.mark.parametrize(
    "change",
    [
        "missing_evidence",
        "empty_evidence",
        "tampered_digest",
        "mutable_image",
        "revision",
        "schema_range",
        "event_version",
        "cel_profile",
        "rollback_image",
        "extra_qualified_flag",
    ],
)
def test_release_manifest_rejects_incomplete_or_inconsistent_bundle(bundle, change):
    root, _, manifest = bundle
    if change == "missing_evidence":
        del manifest["evidence"]["service_manifest"]
    elif change == "empty_evidence":
        (root / "evidence.json").write_text("")
    elif change == "tampered_digest":
        manifest["inventory"]["sha256"] = "0" * 64
    elif change == "mutable_image":
        manifest["images"]["backend"]["reference"] = "fixture/app:latest"
    elif change == "revision":
        manifest["source_revision"] = "c" * 40
    elif change == "schema_range":
        manifest["compatibility"]["schema_max"] = "0000_missing.sql"
    elif change == "event_version":
        manifest["compatibility"]["event_versions"] = {}
    elif change == "cel_profile":
        manifest["compatibility"]["cel_profiles"] = []
    elif change == "rollback_image":
        manifest["compatibility"]["rollback_images"]["frontend"] = None
    else:
        manifest["qualified"] = True
    with pytest.raises(release.InvalidBundle):
        release.verify_manifest(root, manifest)


def test_null_template_is_intentionally_not_a_complete_release(bundle):
    template = release.read_json(SOURCE.with_name("release-manifest.example.json"))
    with pytest.raises(release.InvalidBundle):
        release.verify_manifest(bundle[0], template)


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema":"one","schema":"two"}')
    with pytest.raises(release.InvalidBundle):
        release.read_json(path)


def test_symlink_cannot_read_artifact_outside_bundle(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private fixture")
    link = root / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Host does not permit unprivileged symlink creation")
    with pytest.raises(release.InvalidBundle):
        release.bundle_file(root, "escape.txt")
