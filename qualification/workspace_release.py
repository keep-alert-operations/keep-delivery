"""Capture a clean, pinned multi-repository release with supplied image digests."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

DELIVERY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("workspace", DELIVERY / "scripts/workspace.py")
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)
IMAGES = {"appchi-frontend", "appchi-api", "appchi-ingestion", "appchi-processor",
          "appchi-correlation", "appchi-indexer", "appchi-migrations"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True, help="JSON mapping repository names to image@sha256 references")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = workspace.inventory(DELIVERY.parent, workspace.configuration())
    for name, repository in inventory["repositories"].items():
        if repository["dirty"] or not repository["matches_manifest"]:
            raise SystemExit(f"Release requires a clean pinned checkout: {name}")
    images = json.loads(args.images.read_text(encoding="utf-8"))
    if set(images) != IMAGES or any(not isinstance(v, str) or not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", v) for v in images.values()):
        raise SystemExit("Supply exactly seven immutable image references, one per deployable repository")
    inventory.update({"images": images, "qualification": "unperformed", "format": "appchi-workspace-release-v1"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2)+"\n", encoding="utf-8")
    print("Source/image inventory recorded; attach scans, SBOMs and qualification evidence separately.")


if __name__ == "__main__":
    main()
