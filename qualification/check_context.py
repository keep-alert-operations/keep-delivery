"""Validate the repository-native context layer with the shared CLI implementation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from context import main  # noqa: E402


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    # Preserve top-level --workspace support before inserting the check subcommand.
    arguments = sys.argv[1:]
    workspace = []
    if "--workspace" in arguments:
        index = arguments.index("--workspace")
        workspace = arguments[index:index + 2]
        del arguments[index:index + 2]
    raise SystemExit(main([*workspace, "check", *arguments]))
