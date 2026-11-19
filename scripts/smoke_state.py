"""Shared smoke state: app repo checkouts and their content fingerprints.

``scripts/smoke.py`` records which code a smoke run covered, and
``scripts/smoke_gate.py`` (the finish gate) decides whether a session's changes
are covered. Both use this module, so they always agree on what changed.

* An *app repo checkout* is a git checkout of any workspace repository except
  ``keep-design``: a main child repository or one of its git worktrees.
* An *app change* is a content change outside the excluded paths below. Docs,
  CI files and agent files don't need a smoke run.
* The *fingerprint* is a SHA-256 over ``path -> blob hash`` of the working tree
  (tracked and untracked, not ignored). It depends on content only, so a commit
  of already-tested content keeps the fingerprint. ``ALGORITHM_VERSION`` changes
  whenever the algorithm or the excluded paths change, which invalidates older
  reports.

Standard library only: the gate runs from Claude Code hooks with any Python 3.11+.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess

ALGORITHM_VERSION = 1
NON_APP_REPOS = frozenset({"keep-design"})
_EXCLUDED_EVERYWHERE = ("docs/", ".github/", ".claude/")
_EXCLUDED_IN_DELIVERY = ("workspace/", "status/", "qualification/")
_GIT_TIMEOUT = 60


@dataclass(frozen=True)
class Checkout:
    """One app repo checkout."""

    path: Path
    repo: str
    main: bool

    @property
    def key(self) -> str:
        return path_key(self.path)


def path_key(path: Path | str) -> str:
    """A stable, case-normalized key for a directory on this machine."""
    return os.path.normcase(os.path.abspath(str(path)))


def is_app_path(repo: str, path: str) -> bool:
    """True when a change to ``path`` (relative, ``/``-separated) needs a smoke run."""
    path = path.replace("\\", "/")
    if path.endswith(".md"):
        return False
    if path.startswith(_EXCLUDED_EVERYWHERE) or "/.claude/" in path:
        return False
    if repo == "keep-delivery" and path.startswith(_EXCLUDED_IN_DELIVERY):
        return False
    return True


def git(root: Path, *args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        timeout=_GIT_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args[:2])} failed in {root}: {result.stderr.strip()[:300]}"
        )
    return result.stdout


def checkout_root(path: Path) -> Path | None:
    """The git checkout that contains ``path``, or None."""
    current = Path(os.path.abspath(str(path)))
    if not current.is_dir():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def describe(root: Path) -> Checkout | None:
    """The app repo checkout at ``root``, or None when it is not one.

    The repository is named by its main checkout (the parent of the git common
    directory), so a worktree such as ``.wt/task/keep-api`` is ``keep-api``.
    """
    try:
        common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None
    main = Path(common.strip()).parent
    repo = main.name
    if not repo.startswith("keep-") or repo in NON_APP_REPOS:
        return None
    return Checkout(Path(os.path.abspath(str(root))), repo, path_key(main) == path_key(root))


def workspace_children(workspace: Path) -> list[Checkout]:
    """App checkouts directly under a workspace, main or workspace-worktree alike."""
    found = []
    for child in sorted(Path(workspace).iterdir()):
        if child.is_dir() and child.name.startswith("keep-") and (child / ".git").exists():
            checkout = describe(child)
            if checkout is not None:
                found.append(checkout)
    return found


def inside(path: Path, directory: Path) -> bool:
    child, parent = path_key(path), path_key(directory)
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


def main_checkouts(workspace: Path) -> list[Checkout]:
    """The workspace's main child repositories that are app repos."""
    found = []
    for child in sorted(Path(workspace).iterdir()):
        if (
            child.is_dir()
            and child.name.startswith("keep-")
            and child.name not in NON_APP_REPOS
            and (child / ".git").is_dir()
        ):
            found.append(Checkout(Path(os.path.abspath(str(child))), child.name, True))
    return found


def all_checkouts(workspace: Path) -> list[Checkout]:
    """Main app checkouts plus every git worktree of them."""
    found: dict[str, Checkout] = {}
    for checkout in main_checkouts(workspace):
        found[checkout.key] = checkout
        try:
            listing = git(checkout.path, "worktree", "list", "--porcelain")
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            continue
        for line in listing.splitlines():
            if not line.startswith("worktree "):
                continue
            path = Path(line[len("worktree ") :])
            if path.is_dir() and path_key(path) not in found:
                found[path_key(path)] = Checkout(
                    Path(os.path.abspath(str(path))), checkout.repo, False
                )
    return list(found.values())


def _changed_paths(root: Path) -> list[str]:
    output = git(
        root,
        "--no-optional-locks",
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--no-renames",
    )
    return [entry[3:] for entry in output.split("\0") if len(entry) > 3]


def fingerprint(checkout: Checkout) -> str:
    """Content fingerprint of the checkout's app files (see the module docstring)."""
    root = checkout.path
    blobs: dict[str, str] = {}
    for entry in git(root, "ls-files", "-s", "-z").split("\0"):
        if "\t" not in entry:
            continue
        meta, path = entry.split("\t", 1)
        blobs[path] = meta.split()[1]
    changed = _changed_paths(root)
    present = [path for path in changed if (root / path).is_file()]
    for path in changed:
        if path not in present:
            blobs.pop(path, None)
    if present:
        hashes = git(root, "hash-object", "--stdin-paths", stdin="\n".join(present) + "\n")
        for path, blob in zip(present, hashes.split(), strict=True):
            blobs[path] = blob
    digest = hashlib.sha256()
    for path in sorted(blobs):
        if is_app_path(checkout.repo, path):
            digest.update(f"{path}\0{blobs[path]}\n".encode("utf-8", "surrogateescape"))
    return f"v{ALGORITHM_VERSION}:{digest.hexdigest()}"


def work_in_progress(checkout: Checkout) -> dict:
    """App changes that are not on origin/main: dirty files and unpushed commits."""
    root = checkout.path
    dirty = [path for path in _changed_paths(root) if is_app_path(checkout.repo, path)]
    newest = 0.0
    for path in dirty:
        try:
            newest = max(newest, (root / path).stat().st_mtime)
        except OSError:
            pass
    try:
        ahead: int | None = int(git(root, "rev-list", "--count", "origin/main..HEAD").strip())
    except (RuntimeError, ValueError):
        ahead = None
    if ahead:
        newest = max(newest, float(git(root, "log", "-1", "--format=%ct").strip() or 0))
    return {
        "dirty": len(dirty),
        "ahead": ahead,
        "newest_change": newest or None,
        "active": bool(dirty) or bool(ahead),
    }


def head(checkout: Checkout) -> dict:
    root = checkout.path
    try:
        branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        commit = git(root, "rev-parse", "HEAD").strip()
    except RuntimeError:
        return {"branch": None, "commit": None}
    return {"branch": branch, "commit": commit}


def smoke_command(workspace: Path, stack_delivery: Path) -> str:
    """The exact PowerShell command the smoke-runner runs.

    The finish gate's message and the bootstrap permission rule both use this
    string, so a rule that allows it matches the command character for character.
    """
    python = Path(stack_delivery) / ".venv/Scripts/python.exe"
    if os.name != "nt":
        python = Path(stack_delivery) / ".venv/bin/python"
    script = Path(stack_delivery) / "scripts/smoke.py"
    return f'& "{python}" "{script}" run --workspace "{Path(workspace)}"'


def read_json(path: Path) -> dict | list | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_object(path: Path) -> dict | None:
    """A JSON file holding an object, or None."""
    data = read_json(path)
    return data if isinstance(data, dict) else None


def pid_alive(pid) -> bool:
    """True when a process with this ID is running."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# A holder older than the longest possible run is stale even if its PID was reused.
LOCK_STALE_SECONDS = 660
# A run creates the lock file first and writes its holder right after; until then
# the file is empty, and it is held unless it stays empty this long.
LOCK_WRITE_GRACE_SECONDS = 10


def lock_holder(path: Path, now: float, alive=None) -> dict | None:
    """The live holder recorded in a smoke lock file, or None when free or stale."""
    holder = read_json(path)
    if not isinstance(holder, dict):
        try:
            created = Path(path).stat().st_mtime
        except OSError:
            return None
        if now - created < LOCK_WRITE_GRACE_SECONDS:
            return {"run_id": "starting", "pid": None, "started_at": created}
        return None
    started = holder.get("started_at")
    if not isinstance(started, (int, float)) or now - started > LOCK_STALE_SECONDS:
        return None
    return holder if (alive or pid_alive)(holder.get("pid")) else None
