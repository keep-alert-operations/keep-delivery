"""Bootstrap and verify the sibling repositories without a parent Git repository."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

DELIVERY = Path(__file__).resolve().parents[1]
MANIFEST = DELIVERY / "workspace/manifest.json"
# Runs inside the environment: unmet runtime requirements of the named distributions.
UNMET = """
import json, sys
from importlib import metadata
from packaging.requirements import Requirement
unmet = set()
for name in sys.argv[1:]:
    for text in metadata.requires(name) or ():
        requirement = Requirement(text)
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        try:
            metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            unmet.add(requirement.name)
print(json.dumps(sorted(unmet)))
"""


def run(args, cwd, *, capture=False):
    env = dict(os.environ)
    # npm canonicalizes GitHub Git dependencies to SSH; use the configured HTTPS
    # credential helper without changing the developer's global Git settings.
    if Path(args[0]).name in {"npm", "npm.cmd"}:
        index = int(env.get("GIT_CONFIG_COUNT", "0"))
        env.update({"GIT_CONFIG_COUNT": str(index + 1),
                    f"GIT_CONFIG_KEY_{index}": "url.https://github.com/.insteadOf",
                    f"GIT_CONFIG_VALUE_{index}": "ssh://git@github.com/"})
    result = subprocess.run(args, cwd=cwd, env=env, text=True, check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def configuration():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for name, item in manifest["repositories"].items():
        if not name.startswith("appchi-") or Path(name).name != name:
            raise ValueError("Invalid repository directory")
        if item["url"] != f"https://github.com/{manifest['organization']}/{name}.git":
            raise ValueError("Repository URL does not match the selected organization")
        revision = item.get("revision")
        if name != "appchi-delivery" and (not isinstance(revision, str) or len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision)):
            raise ValueError(f"Missing immutable revision for {name}")
    return manifest


def repository(root, name):
    path = (root / name).resolve()
    if path.parent != root.resolve():
        raise ValueError("Repository path escapes workspace")
    actual = Path(run(["git", "rev-parse", "--show-toplevel"], path, capture=True)).resolve()
    if actual != path:
        raise ValueError(f"{name} is not an independent Git checkout")
    return path


def bootstrap(root, manifest):
    root.mkdir(parents=True, exist_ok=True)
    for name, item in manifest["repositories"].items():
        path = root / name
        if name == "appchi-delivery":
            if path.resolve() != DELIVERY:
                raise ValueError("Run bootstrap from appchi-delivery inside the target workspace")
            repository(root, name)
            continue
        if path.exists():
            repository(root, name)
            print(f"Preserving existing checkout: {name}")
            continue
        run(["git", "clone", "--no-checkout", item["url"], str(path)], root)
        run(["git", "checkout", "--detach", item["revision"]], path)
    instructions = []
    for name in ("AGENTS.md", "CLAUDE.md"):
        canonical = DELIVERY / "workspace" / name
        installed = root / name
        backup = root / f"{name}.before-bootstrap"
        differs = installed.exists() and installed.read_bytes() != canonical.read_bytes()
        if differs and backup.exists():
            raise ValueError(f"Root {name} differs; reconcile it with delivery before bootstrap")
        instructions.append((canonical, installed, backup, differs))
    for canonical, installed, backup, differs in instructions:
        if differs:
            shutil.copyfile(installed, backup)
        shutil.copyfile(canonical, installed)
    print("Workspace prepared; existing branches and local changes were preserved.")


def inventory(root, manifest):
    result = {"schema_version": 1, "repositories": {}, "migrations": {}}
    for name, item in manifest["repositories"].items():
        path = repository(root, name)
        revision = run(["git", "rev-parse", "HEAD"], path, capture=True)
        dirty = bool(run(["git", "status", "--porcelain=v1"], path, capture=True))
        result["repositories"][name] = {
            "revision": revision, "dirty": dirty,
            "matches_manifest": name == "appchi-delivery" or revision == item["revision"],
        }
    for path in sorted((root / "appchi-migrations/src/appchi/migration_sql").glob("*.sql")):
        result["migrations"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    result["design_revision"] = result["repositories"]["appchi-design"]["revision"]
    result["manifest_sha256"] = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    return result


def canonical_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_requirement(locks, name):
    """Exact version and hashes from the first sibling lock that pins the package."""
    for lock in locks:
        for package in lock.get("package", ()):
            if canonical_name(package["name"]) == canonical_name(name):
                hashes = " ".join(f"--hash={item['hash']}" for item in package.get("files", ()))
                return f"{package['name']}=={package['version']} {hashes}"
    raise ValueError(f"{name} is required by a local source but pinned by no sibling lock")


def install_unmet(python, sources):
    """Install runtime requirements that local sources added since delivery's lock.

    Editable overrides use --no-deps, so a requirement a sibling added after the
    delivery repin is otherwise missing. Pins come from that sibling's own lock.
    """
    names = [tomllib.loads((path / "pyproject.toml").read_text())["project"]["name"]
             for path in sources]
    locks = [tomllib.loads((path / "poetry.lock").read_text())
             for path in sources if (path / "poetry.lock").exists()]
    for _ in range(5):
        unmet = json.loads(run([str(python), "-c", UNMET, *names], DELIVERY, capture=True))
        if not unmet:
            return
        with tempfile.TemporaryDirectory() as directory:
            pins = Path(directory) / "requirements.txt"
            pins.write_text("\n".join(locked_requirement(locks, name) for name in unmet) + "\n")
            run([str(python), "-m", "pip", "install", "--no-deps", "--require-hashes",
                 "-r", str(pins)], DELIVERY)
        print("Installed locked requirements of local sources: " + ", ".join(unmet))
        names = unmet
    raise ValueError("Local source requirements did not converge")


def install(root, manifest, editable):
    run(["poetry", "check", "--lock"], DELIVERY)
    run(["poetry", "sync", "--all-extras"], DELIVERY)
    # Poetry owns the environment location (in-project `.venv` by default).
    environment = Path(run(["poetry", "env", "info", "--path"], DELIVERY, capture=True))
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if editable:
        # Environments created before the Poetry migration may lack pip.
        if subprocess.run([str(python), "-m", "pip", "--version"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            run([str(python), "-m", "ensurepip", "--default-pip"], DELIVERY)
        sources = [repository(root, name) for name in manifest["repositories"]]
        sources = [path for path in sources if (path / "pyproject.toml").exists()]
        args = [str(python), "-m", "pip", "install", "--no-deps"]
        for path in sources:
            args += ["--editable", str(path)]
        run(args, DELIVERY)
        install_unmet(python, sources)
        print("Local editable sources installed; release locks are unchanged.")
    run(["npm.cmd" if os.name == "nt" else "npm", "ci"], root / "appchi-frontend")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bootstrap", "inventory", "install", "test"))
    parser.add_argument("--root", type=Path, default=DELIVERY.parent)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--editable", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = configuration()
    if args.action == "bootstrap":
        bootstrap(root, manifest)
    elif args.action == "install":
        install(root, manifest, args.editable)
    elif args.action == "inventory":
        encoded = json.dumps(inventory(root, manifest), indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded)
        else:
            print(encoded)
    else:
        for name in manifest["repositories"]:
            path = repository(root, name)
            if (path / "tests/unit").is_dir():
                run([sys.executable, "-m", "pytest", "tests/unit", "-q"], path)


if __name__ == "__main__":
    main()
