"""Mirror builds must preserve source authority and fail on dependency drift."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "scripts/prepare_internal_build.py"
spec = importlib.util.spec_from_file_location("prepare_internal_build", SOURCE)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
INDEX = "https://mirror.example.test/pypi/simple"
PROJECT = '[project]\nname = "appchi-api"\nversion = "0.1.0"\nrequires-python = ">=3.13,<3.14"\ndependencies = ["dependency==1.2.3"]\n'
LOCK = '''[[package]]
name = "dependency"
version = "1.2.3"
optional = false
python-versions = "*"
groups = ["main"]
files = [{file = "dependency-1.2.3-py3-none-any.whl", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]
[[package]]
name = "appchi-contracts"
version = "0.1.0"
optional = false
python-versions = ">=3.13,<3.14"
groups = ["main"]
files = []
[package.source]
type = "git"
url = "https://github.com/appchi-alert-operations/appchi-contracts.git"
reference = "123"
resolved_reference = "123"
[metadata]
lock-version = "2.1"
python-versions = ">=3.13,<3.14"
content-hash = "REPLACE_HASH"
'''



def documents():
    original = build.tomllib.loads(LOCK)
    refreshed = copy.deepcopy(original)
    refreshed["package"][0]["source"] = {"type": "legacy", "url": INDEX, "reference": "internal"}
    return original, refreshed


def test_mirror_can_change_download_location_with_identical_artifact():
    assert build.verify_lock(*documents(), INDEX)["registry_packages"] == 1


@pytest.mark.parametrize("change", ["version", "hash", "registry", "git", "edges", "markers", "groups", "extras", "python"])
def test_mirror_refuses_dependency_or_provenance_drift(change):
    before, after = documents()
    package = after["package"][0]
    if change == "version":
        package["version"] = "1.2.4"
    elif change == "hash":
        package["files"][0]["hash"] = "sha256:" + "b" * 64
    elif change == "registry":
        package["source"]["url"] = "https://outside.test/simple"
    elif change == "git":
        after["package"][1]["source"]["resolved_reference"] = "456"
    elif change == "markers":
        package["markers"] = "sys_platform == 'win32'"
    elif change == "groups":
        package["groups"] = ["dev"]
    elif change == "extras":
        after["extras"] = {"unexpected": ["dependency"]}
    elif change == "python":
        after["metadata"]["python-versions"] = ">=3.12"
    else:
        package["dependencies"] = {"unexpected": "*"}
    with pytest.raises(ValueError):
        build.verify_lock(before, after, INDEX)


@pytest.mark.parametrize("value", ["http://mirror.test/simple", "https://user:secret@mirror.test/simple",
                                  "https://REQUIRED_INDEX/simple", "https://mirror.test/?token=secret"])
def test_mirror_url_cannot_embed_credentials_or_placeholder(value):
    with pytest.raises(ValueError):
        build.https_url(value)


def make_source(tmp_path):
    source = tmp_path / "workspace/appchi-api"
    source.mkdir(parents=True)
    (source / "Dockerfile").write_text("FROM python:3.13\n")
    from poetry.packages.locker import Locker
    (source / "pyproject.toml").write_text(PROJECT)
    digest = Locker(source / "poetry.lock", build.tomllib.loads(PROJECT))._get_content_hash()
    (source / "poetry.lock").write_text(LOCK.replace("REPLACE_HASH", digest))
    (source / "src").mkdir()
    (source / "src/main.py").write_text("# application\n")
    (source / ".env").write_text("PRIVATE=never-copy\n")
    return source, tmp_path / "build/appchi-api"


def test_stage_excludes_private_inputs_and_refuses_existing_output(tmp_path):
    source, context = make_source(tmp_path)
    build.stage(source.parent, source.name, context)
    assert not (context / ".env").exists()
    assert (context / "src/main.py").exists()
    assert (context / build.ORIGINAL).read_bytes() == (source / "poetry.lock").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        build.stage(source.parent, source.name, context)


def test_relock_drift_rolls_back_build_copy_and_never_writes_source(tmp_path):
    source, context = make_source(tmp_path)
    build.stage(source.parent, source.name, context)
    source_project = (source / "pyproject.toml").read_bytes()
    source_lock = (source / "poetry.lock").read_bytes()

    def drift(args, cwd, env, check):
        assert args == ["poetry", "check", "--lock"]
        raise RuntimeError("native lock validation failed")

    with pytest.raises(RuntimeError, match="validation"):
        build.relock(context, INDEX, "https://git.example.test/appchi", runner=drift)
    assert (context / "poetry.lock").read_bytes() == source_lock
    assert (source / "poetry.lock").read_bytes() == source_lock
    assert (context / "pyproject.toml").read_bytes() == source_project
    assert (source / "pyproject.toml").read_bytes() == source_project
    assert not json.loads((context / build.RECEIPT).read_text())["mirror_lock_verified"]


def test_relock_records_verified_override_without_source_change(tmp_path):
    source, context = make_source(tmp_path)
    build.stage(source.parent, source.name, context)

    source_lock = (source / "poetry.lock").read_bytes()

    def mirrored(args, cwd, env, check):
        assert args == ["poetry", "check", "--lock"]
        assert env["POETRY_SYSTEM_GIT_CLIENT"] == "true"
        from poetry.packages.locker import Locker
        project = build.tomllib.loads((cwd / "pyproject.toml").read_text())
        assert Locker(cwd / "poetry.lock", project).is_fresh()

    build.relock(context, INDEX, "https://git.example.test/appchi", runner=mirrored)
    assert json.loads((context / build.RECEIPT).read_text())["mirror_lock_verified"]
    assert (source / "poetry.lock").read_bytes() == source_lock
    assert "[[tool.poetry.source]]" in (context / "pyproject.toml").read_text()


def test_native_poetry_accepts_mirror_lock_without_resolution(tmp_path):
    source, context = make_source(tmp_path)
    build.stage(source.parent, source.name, context)
    build.relock(context, INDEX, "https://git.example.test/appchi")


def test_unsupported_poetry_version_is_rejected_before_staged_changes(tmp_path, monkeypatch):
    source, context = make_source(tmp_path)
    build.stage(source.parent, source.name, context)
    project = (context / "pyproject.toml").read_bytes()
    lock = (context / "poetry.lock").read_bytes()
    monkeypatch.setattr(build, "version", lambda name: "3.0.0")
    with pytest.raises(ValueError, match="exactly Poetry 2.3.1"):
        build.relock(context, INDEX, "https://git.example.test/appchi")
    assert (context / "pyproject.toml").read_bytes() == project
    assert (context / "poetry.lock").read_bytes() == lock
