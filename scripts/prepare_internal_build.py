"""Stage private-network builds without modifying the source/release lockfiles.

``relock`` remaps a staged Poetry lock without resolving or building packages.
It retains exact package versions, Git sources, dependency graphs and file hashes.
"""

import argparse
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
from urllib.parse import urlsplit


PYTHON_REPOS = {f"keep-{name}" for name in (
    "api", "ingestion", "processor", "correlation", "indexer", "migrations")}
REPOS = PYTHON_REPOS | {"keep-frontend"}
INPUTS = ("Dockerfile", "pyproject.toml", "poetry.toml", "poetry.lock", "src", "scripts",
          "package.json", "package-lock.json", "next.config.ts", "next-env.d.ts",
          "tsconfig.json", "keycloak", ".dockerignore")
ORIGINAL = ".keep-source-poetry.lock"
RECEIPT = ".keep-internal-build.json"


def https_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or "REQUIRED" in value or "<" in value):
        raise ValueError("Supply an HTTPS mirror URL without credentials or placeholders")
    return value.rstrip("/")


def copy_input(source, target):
    if source.is_symlink():
        raise ValueError("Build inputs cannot be symlinks")
    if source.is_dir():
        target.mkdir()
        for child in sorted(source.iterdir()):
            if child.name in {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv"}:
                continue
            copy_input(child, target / child.name)
    else:
        shutil.copyfile(source, target)


def stage(workspace, repository, output):
    source = (workspace / repository).resolve()
    output = output.resolve()
    if source.name != repository or not source.is_dir():
        raise ValueError("Missing source repository")
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Use a build context separate from its source repository")
    if output.exists():
        raise ValueError("Build context already exists; choose a fresh output directory")
    # Preflight all trees before any copy, including link directories.
    files = {}
    for name in INPUTS:
        path = source / name
        if not path.exists():
            continue
        candidates = [path] + (list(path.rglob("*")) if path.is_dir() else [])
        for child in candidates:
            if child.is_symlink():
                raise ValueError("Build inputs cannot be symlinks")
            if child.is_file() and "__pycache__" not in child.parts:
                files[child.relative_to(source).as_posix()] = sha256(child.read_bytes()).hexdigest()
    if "Dockerfile" not in files:
        raise ValueError("Missing service Dockerfile")
    output.mkdir(parents=True)
    for name in INPUTS:
        if (source / name).exists():
            copy_input(source / name, output / name)
    if repository in PYTHON_REPOS:
        shutil.copyfile(output / "poetry.lock", output / ORIGINAL)
    receipt = {"classification": "isolated-test build override; not a release",
               "repository": repository, "source_inputs": files,
               "mirror_lock_verified": repository not in PYTHON_REPOS}
    (output / RECEIPT).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    # These receipts and any accidental private inputs must never enter COPY layers.
    ignore = output / ".dockerignore"
    with ignore.open("a", encoding="utf-8") as stream:
        stream.write("\n.keep-*\n.env*\n*.pem\n*.key\n.netrc\n.npmrc\n")
    return receipt


def packages(document):
    result = {}
    for package in document["package"]:
        key = (package["name"], package.get("version"))
        if key in result:
            raise ValueError("Ambiguous duplicate package identity in lockfile")
        result[key] = package
    return result


def artifact_hashes(package):
    result = {}
    for item in package.get("files", []):
        name, digest = item.get("file", ""), item.get("hash", "")
        if not name or not digest.startswith("sha256:"):
            raise ValueError("Mirror artifact lacks its original filename/SHA-256")
        if name in result and result[name] != digest:
            raise ValueError("Conflicting artifact hashes")
        result[name] = digest
    return result


def verify_lock(original, refreshed, index):
    before, after = packages(original), packages(refreshed)
    if before.keys() != after.keys():
        raise ValueError("Mirror remap changed package versions or package inventory")
    old_meta = dict(original.get("metadata", {}))
    new_meta = dict(refreshed.get("metadata", {}))
    old_meta.pop("content-hash", None)
    new_meta.pop("content-hash", None)
    if old_meta != new_meta:
        raise ValueError("Mirror remap changed Python compatibility or lock metadata")
    for key in original.keys() | refreshed.keys():
        if key not in {"package", "metadata"} and original.get(key) != refreshed.get(key):
            raise ValueError("Mirror remap changed lock graph or extras")
    count = 0
    expected_source = {"type": "legacy", "url": index, "reference": "internal"}
    for key, package in after.items():
        old = before[key]
        original_source = old.get("source")
        if original_source and original_source.get("type") != "legacy":
            if package != old:
                raise ValueError("Mirror remap changed a pinned Git/local dependency")
            continue
        if original_source:
            raise ValueError("Existing registry configuration needs explicit reconciliation")
        if package.get("source") != expected_source:
            raise ValueError("Mirror remap retained an unexpected package registry")
        if {k: v for k, v in package.items() if k != "source"} != old:
            raise ValueError("Mirror remap changed dependency edges, markers or artifact hashes")
        known, actual = artifact_hashes(old), artifact_hashes(package)
        if not actual or known != actual:
            raise ValueError("Mirror artifacts differ from the source lock hashes")
        count += 1
    return {"packages": len(after), "registry_packages": count,
            "versions_git_sources_artifact_hashes": "unchanged"}


def relock(context, index, git_base, runner=subprocess.run):
    context = context.resolve()
    index, git_base = https_url(index), https_url(git_base) + "/"
    receipt_path = context / RECEIPT
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt["repository"] not in PYTHON_REPOS:
        raise ValueError("Frontend uses npm ci with its unchanged integrity lock and mirror registry")
    if receipt["mirror_lock_verified"]:
        raise ValueError("Context is already verified; use a fresh build copy for a different mirror")
    # Execute with the Python interpreter that owns the pinned Poetry tool. Stage
    # remains standard-library-only; remapping uses Poetry's native hash authority.
    try:
        if version("poetry") != "2.3.1":
            raise ValueError("Mirror remapping requires exactly Poetry 2.3.1")
        import tomlkit
        from poetry.packages.locker import Locker
    except ImportError as error:
        raise ValueError("Run relock with the Python interpreter containing Poetry 2.3.1") from error
    project_path = context / "pyproject.toml"
    lock_path = context / "poetry.lock"
    original_text = project_path.read_text(encoding="utf-8")
    project = tomlkit.parse(original_text)
    if project.get("tool", {}).get("poetry", {}).get("source"):
        raise ValueError("Existing index configuration needs explicit reconciliation")
    if not Locker(lock_path, tomllib.loads(original_text)).is_fresh():
        raise ValueError("Source Poetry lock does not match its project")
    before_text = (context / ORIGINAL).read_text(encoding="utf-8")
    before = tomllib.loads(before_text)
    refreshed = tomlkit.parse(before_text)
    sources = tomlkit.aot()
    sources.append({"name": "internal", "url": index, "priority": "primary"})
    project.setdefault("tool", {}).setdefault("poetry", {})["source"] = sources
    for package in refreshed["package"]:
        source = package.get("source")
        if source and source.get("type") == "legacy":
            raise ValueError("Existing registry configuration needs explicit reconciliation")
        if not source:
            package["source"] = {"type": "legacy", "url": index, "reference": "internal"}
    task_env = dict(os.environ)
    task_env["POETRY_VIRTUALENVS_CREATE"] = "false"
    task_env["POETRY_SYSTEM_GIT_CLIENT"] = "true"
    # Git pins retain canonical URLs; system Git rewrites them only in the build.
    # No resolution or package backend executes during this metadata-only remap.
    try:
        project_path.write_text(tomlkit.dumps(project), encoding="utf-8")
        locker = Locker(lock_path, tomllib.loads(project_path.read_text(encoding="utf-8")))
        refreshed["metadata"]["content-hash"] = locker._get_content_hash()
        lock_path.write_text(tomlkit.dumps(refreshed), encoding="utf-8")
        result = verify_lock(before, tomllib.loads(lock_path.read_text(encoding="utf-8")), index)
        runner(["poetry", "check", "--lock"], cwd=context, env=task_env, check=True)
    except Exception:
        project_path.write_text(original_text, encoding="utf-8")
        shutil.copyfile(context / ORIGINAL, lock_path)
        raise
    receipt.update(mirror_lock_verified=True, lock_verification=result,
                   git_base_url=git_base,
                   staged_lock_sha256=sha256(lock_path.read_bytes()).hexdigest())
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    staging = actions.add_parser("stage")
    staging.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[2])
    staging.add_argument("--repository", choices=sorted(REPOS), required=True)
    staging.add_argument("--output", type=Path, required=True)
    locking = actions.add_parser("relock")
    locking.add_argument("--context", type=Path, required=True)
    locking.add_argument("--index", required=True)
    locking.add_argument("--git-base-url", required=True)
    args = parser.parse_args()
    if args.action == "stage":
        result = stage(args.workspace, args.repository, args.output)
    else:
        result = relock(args.context, args.index, args.git_base_url)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
