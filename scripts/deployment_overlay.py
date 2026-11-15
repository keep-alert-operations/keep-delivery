"""Package/apply an explicit isolated-test overlay with hashes and conflict checks.

No commits, remote calls, dependencies, Secrets or ignored local state are copied.
Application is opt-in, requires matching Git bases and preflights every file.
"""

import argparse
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[2]
BUILD_REPOS = tuple(f"keep-{name}" for name in (
    "api", "ingestion", "processor", "correlation", "indexer", "migrations", "frontend"))
FILES = tuple(f"{repo}/{path}" for repo in BUILD_REPOS
              for path in ("Dockerfile", "scripts/git-askpass.sh")) + tuple(
    f"{repo}/{path}" for repo in BUILD_REPOS if repo != "keep-frontend"
    for path in ("pyproject.toml", "poetry.toml", "poetry.lock")
) + (
    "keep-ingestion/kong/Dockerfile",
    # The load-test mock ADAPI runs this file from a ConfigMap (REV-73).
    "keep-delivery/scripts/local_adapi.py",
    "keep-api/src/keep/operators/proof.py", "keep-api/docs/operator-proof.md",
    "keep-api/docs/operator-proof.example.json", "keep-api/tests/unit/test_operator_proof.py",
    "keep-api/tests/integration/test_operator_proof_postgres.py",
    "keep-indexer/src/keep/search/bootstrap.py",
    "keep-indexer/src/keep/search/bootstrap_probe.py",
    "keep-indexer/tests/unit/test_search_bootstrap.py",
    "keep-indexer/tests/integration/test_search_bootstrap_integration.py",
    "keep-delivery/scripts/prepare_internal_build.py",
    "keep-delivery/scripts/deployment_overlay.py",
    "keep-delivery/docs/runbooks/openshift-load-testing.md",
    "keep-delivery/docs/runbooks/cluster-initialization.md",
    "keep-delivery/docs/runbooks/private-test-deployment.md",
    "keep-delivery/qualification/openshift-load-test-handoff.md",
    "keep-delivery/qualification/cluster-initialization-2026-10-05.md",
    "keep-delivery/qualification/load-test/.dockerignore",
    "keep-delivery/tests/unit/test_openshift_load_test_bundle.py",
    "keep-delivery/tests/unit/test_load_identity_prepare.py",
    "keep-delivery/tests/unit/test_load_test_harness.py",
    "keep-delivery/tests/unit/test_prepare_internal_build.py",
    "keep-delivery/tests/unit/test_deployment_overlay.py",
    "keep-delivery/qualification/private-test-deployment-2026-10-07.md",
)
DIRECTORIES = (
    "keep-delivery/deploy/openshift/load-test",
    "keep-delivery/deploy/images/postgres", "keep-delivery/deploy/images/redis",
    "keep-delivery/qualification/load-test", "keep-delivery/qualification/load-test-identity",
)
REPOS = set(BUILD_REPOS) | {"keep-delivery"}


def digest(data):
    return sha256(data).hexdigest()


def allowed_name(name):
    path = PurePosixPath(name)
    return (path.as_posix() == name and not path.is_absolute()
            and not any((part.startswith(".") and not (part == ".dockerignore" and name in FILES))
                        or part == "__pycache__" for part in path.parts)
            and path.suffix not in {".key", ".pem", ".crt", ".pyc", ".log", ".env"}
            and (name in FILES or any(name.startswith(base + "/") for base in DIRECTORIES)))


def git(root, repository, *args, missing_ok=False):
    path = (root / repository).resolve()
    result = subprocess.run(["git", "-c", f"safe.directory={path.as_posix()}", "-C", str(path),
                             *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        if missing_ok:
            return None
        raise ValueError(f"Git checkout inspection failed: {repository}")
    return result.stdout


def file_paths(root):
    paths = set(FILES)
    for directory in DIRECTORIES:
        base = root / directory
        if not base.is_dir() or base.is_symlink():
            raise ValueError(f"Missing deployment inputs: {directory}")
        for path in base.rglob("*"):
            if path.is_symlink():
                raise ValueError("Overlay source cannot contain symlinks")
            if path.is_file() and not any(part.startswith(".") or part == "__pycache__"
                                          for part in path.relative_to(base).parts):
                paths.add(path.relative_to(root).as_posix())
    for name in paths:
        path = root / name
        if not path.is_file() or path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError(f"Missing or linked deployment input: {name}")
        if not allowed_name(name):
            raise ValueError("Private/local inputs cannot enter the overlay")
    return sorted(paths)


def pack(root, output):
    root = root.resolve()
    if output.exists():
        raise ValueError("Archive already exists; choose a fresh output path")
    revisions = {}
    for repo in sorted(REPOS):
        actual = Path(git(root, repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        if actual != (root / repo).resolve():
            raise ValueError("Each repository must be an independent Git checkout")
        revisions[repo] = git(root, repo, "rev-parse", "HEAD").decode().strip()
    payload, records = {}, {}
    for name in file_paths(root):
        repo, relative = name.split("/", 1)
        current = (root / name).read_bytes()
        base = git(root, repo, "show", f"HEAD:{relative}", missing_ok=True)
        # Tracked unchanged inputs are already in the transferred Git checkout.
        if base == current:
            continue
        payload[name] = current
        records[name] = {"sha256": digest(current), "base_sha256": digest(base) if base is not None else None}
    manifest = {"format": "keep-isolated-test-overlay-v1", "classification": "local source overlay; not a release",
                "repositories": revisions, "files": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for name, data in {"overlay-manifest.json": (json.dumps(manifest, indent=2) + "\n").encode(),
                           **payload}.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(data), 0o644
            archive.addfile(entry, io.BytesIO(data))
    return {"archive_sha256": digest(output.read_bytes()), "files": len(records),
            "classification": manifest["classification"]}


def load_archive(path):
    contents = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if (not member.isfile() or member.name in contents or not parts
                    or PurePosixPath(member.name).is_absolute() or ".." in parts
                    or "\\" in member.name or ":" in member.name
                    or member.size > 8 * 1024 * 1024):
                raise ValueError("Invalid or oversized archive member")
            if member.name != "overlay-manifest.json" and not allowed_name(member.name):
                raise ValueError("Archive member is outside the affected repositories")
            contents[member.name] = archive.extractfile(member).read()
    manifest = json.loads(contents.pop("overlay-manifest.json"))
    if (manifest["format"] != "keep-isolated-test-overlay-v1"
            or set(contents) != set(manifest["files"])
            or set(manifest["repositories"]) != REPOS):
        raise ValueError("Archive manifest mismatch")
    for name, data in contents.items():
        if digest(data) != manifest["files"][name]["sha256"]:
            raise ValueError("Overlay file hash mismatch")
    return manifest, contents


def apply_overlay(root, archive, apply=False):
    root = root.resolve()
    manifest, contents = load_archive(archive)
    for repo, revision in manifest["repositories"].items():
        actual_root = Path(git(root, repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        if actual_root != (root / repo).resolve():
            raise ValueError("Each target must be an independent Git checkout")
        if git(root, repo, "rev-parse", "HEAD").decode().strip() != revision:
            raise ValueError(f"Checkout base revision differs: {repo}")
    pending = []
    for name, data in contents.items():
        target = root / name
        if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
            raise ValueError("Overlay target cannot traverse symlinks")
        actual = digest(target.read_bytes()) if target.is_file() else None
        record = manifest["files"][name]
        if actual == record["sha256"]:
            continue  # Safe/idempotent repeat after an interrupted application.
        if actual != record["base_sha256"]:
            raise ValueError(f"Local file conflicts with overlay: {name}")
        pending.append((target, data))
    # No writes occur until all repository identities, targets and hashes pass.
    if apply:
        for target, data in pending:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                stream.write(data)
                temp = Path(stream.name)
            os.replace(temp, target)
    return {"files": len(contents), "pending": len(pending), "applied": apply,
            "classification": manifest["classification"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pack", "check", "apply"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    result = pack(args.root, args.archive) if args.action == "pack" else apply_overlay(
        args.root, args.archive, apply=args.action == "apply")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
