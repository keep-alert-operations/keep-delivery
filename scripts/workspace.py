"""Bootstrap and verify the sibling repositories without a parent Git repository."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

DELIVERY = Path(__file__).resolve().parents[1]
MANIFEST = DELIVERY / "workspace/manifest.json"
# Runs with the environment's Python, which has its metadata and `packaging`.
REQUIREMENTS = Path(__file__).resolve().with_name("workspace_requirements.py")


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
        if not name.startswith("keep-") or Path(name).name != name:
            raise ValueError("Invalid repository directory")
        if item["url"] != f"https://github.com/{manifest['organization']}/{name}.git":
            raise ValueError("Repository URL does not match the selected organization")
        revision = item.get("revision")
        if name != "keep-delivery" and (not isinstance(revision, str) or len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision)):
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


def entrypoint_text(path):
    """Entrypoint bytes without line-ending differences that Git checkouts may introduce."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def bootstrap(root, manifest):
    root.mkdir(parents=True, exist_ok=True)
    for name, item in manifest["repositories"].items():
        path = root / name
        if name == "keep-delivery":
            if path.resolve() != DELIVERY:
                raise ValueError("Run bootstrap from keep-delivery inside the target workspace")
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
        differs = installed.exists() and entrypoint_text(installed) != entrypoint_text(canonical)
        if differs and backup.exists():
            raise ValueError(f"Root {name} differs; reconcile it with delivery before bootstrap")
        instructions.append((canonical, installed, backup, differs))
    for canonical, installed, backup, differs in instructions:
        if differs:
            shutil.copyfile(installed, backup)
        shutil.copyfile(canonical, installed)
    for line in install_agent_setup(root):
        print(line)
    print("Workspace prepared; existing branches and local changes were preserved.")


SMOKE_GATE = "smoke_gate.py"
INSTALLED_RECORD = ".claude/keep-bootstrap.json"


def smoke_command(root):
    """The smoke-runner's command, from the module the finish gate uses."""
    name = "_keep_smoke_state"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().with_name("smoke_state.py"))
        module = importlib.util.module_from_spec(spec)
        # Dataclasses resolve string annotations through sys.modules.
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module.smoke_command(root, root / "keep-delivery")


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _backup_once(path):
    backup = path.with_name(path.name + ".before-bootstrap")
    if path.exists() and not backup.exists():
        shutil.copyfile(path, backup)


def merge_hooks(settings, hooks):
    """Settings with the smoke gate's hook entries replaced and everything else kept."""
    merged = json.loads(json.dumps(settings))
    events = merged.setdefault("hooks", {})
    for event, entries in hooks.items():
        kept = [entry for entry in events.get(event, [])
                if not any(SMOKE_GATE in str(hook.get("command", "")) for hook in entry.get("hooks", []))]
        events[event] = kept + entries
    return merged


def install_agent_setup(root, delivery=None, python=None):
    """Installs the workspace's Claude Code smoke-runner agent and smoke finish gate.

    From `workspace/.claude/` in delivery:
    * `agents/*.md` go to the root `.claude/agents/`. A file edited locally since
      bootstrap installed it is kept as `<name>.before-bootstrap` first;
    * the hooks in `settings.hooks.json` merge into the root `.claude/settings.json`:
      earlier smoke-gate entries are replaced, all other keys and hooks are kept;
    * the root `.claude/settings.local.json` gets one permission rule allowing the
      exact smoke command, so a smoke run never waits on a prompt (user decision
      2026-11-19). Nothing else in it changes.
    Each settings file is backed up once before its first change. A repeated run
    changes nothing. Returns what changed.
    """
    delivery = Path(delivery or DELIVERY)
    canonical = delivery / "workspace/.claude"
    if not canonical.is_dir():
        return []
    python = Path(python or sys.executable).as_posix()
    changes = []
    record_path = root / INSTALLED_RECORD
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    for source in sorted((canonical / "agents").glob("*.md")):
        target = root / ".claude/agents" / source.name
        wanted = entrypoint_text(source)
        if target.exists() and entrypoint_text(target) == wanted:
            continue
        name = f"agents/{source.name}"
        if target.exists() and hashlib.sha256(entrypoint_text(target)).hexdigest() != record.get(name):
            backup = target.with_name(target.name + ".before-bootstrap")
            if backup.exists():
                raise ValueError(f"Root .claude/{name} differs; reconcile it with delivery before bootstrap")
            shutil.copyfile(target, backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        record[name] = hashlib.sha256(wanted).hexdigest()
        changes.append(f"Installed .claude/{name}")
    if changes:
        _write_json(record_path, record)

    template = (canonical / "settings.hooks.json").read_text(encoding="utf-8")
    hooks = json.loads(template.replace("{python}", python))["hooks"]
    settings_path = root / ".claude/settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    merged = merge_hooks(settings, hooks)
    if merged != settings:
        _backup_once(settings_path)
        _write_json(settings_path, merged)
        changes.append("Merged the smoke gate hooks into .claude/settings.json")

    local_path = root / ".claude/settings.local.json"
    local = json.loads(local_path.read_text(encoding="utf-8")) if local_path.exists() else {}
    rule = f"PowerShell({smoke_command(root)})"
    allow = local.setdefault("permissions", {}).setdefault("allow", [])
    if rule not in allow:
        _backup_once(local_path)
        allow.append(rule)
        _write_json(local_path, local)
        changes.append("Allowed the smoke command in .claude/settings.local.json")
    return changes


def inventory(root, manifest):
    result = {"schema_version": 1, "repositories": {}, "migrations": {}}
    for name, item in manifest["repositories"].items():
        path = repository(root, name)
        revision = run(["git", "rev-parse", "HEAD"], path, capture=True)
        dirty = bool(run(["git", "status", "--porcelain=v1"], path, capture=True))
        result["repositories"][name] = {
            "revision": revision, "dirty": dirty,
            "matches_manifest": name == "keep-delivery" or revision == item["revision"],
        }
    for path in sorted((root / "keep-migrations/src/keep/migration_sql").glob("*.sql")):
        result["migrations"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    result["design_revision"] = result["repositories"]["keep-design"]["revision"]
    result["manifest_sha256"] = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    return result


def install_unmet(python, sources):
    """Install runtime requirements that local sources added since delivery's lock.

    Editable overrides use --no-deps, so a requirement a sibling added after the
    delivery repin is otherwise missing. Pins come from the declaring sibling's
    own lock (main group, this platform, within the requirement's specifier).
    """
    locks = {path: str(path / "poetry.lock") for path in sources
             if (path / "poetry.lock").exists()}
    distributions = {
        tomllib.loads((path / "pyproject.toml").read_text())["project"]["name"]: locks.get(path)
        for path in sources
    }
    for _ in range(5):
        request = json.dumps({"distributions": distributions, "locks": list(locks.values())})
        result = json.loads(run([str(python), str(REQUIREMENTS), request], DELIVERY, capture=True))
        if result["unresolved"]:
            raise ValueError("No sibling lock pins a compatible runtime version: "
                             + "; ".join(result["unresolved"]))
        if not result["pins"]:
            return
        with tempfile.TemporaryDirectory() as directory:
            pins = Path(directory) / "requirements.txt"
            pins.write_text("\n".join(pin["line"] for pin in result["pins"]) + "\n")
            run([str(python), "-m", "pip", "install", "--no-deps", "--require-hashes",
                 "-r", str(pins)], DELIVERY)
        print("Installed locked requirements of local sources: "
              + ", ".join(pin["name"] + "==" + pin["version"] for pin in result["pins"]))
        # Newly installed packages may add requirements; resolve them from their lock.
        distributions = {pin["name"]: pin["lock"] for pin in result["pins"]}
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
    run(["npm.cmd" if os.name == "nt" else "npm", "ci"], root / "keep-frontend")


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
