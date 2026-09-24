"""Validate required configuration/evidence references without network calls or secrets."""

import json
from pathlib import Path
import sys


def missing_values(expected, actual, prefix=""):
    missing = []
    for key, value in expected.items():
        path = f"{prefix}.{key}" if prefix else key
        present = actual.get(key) if isinstance(actual, dict) else None
        if isinstance(value, dict):
            missing.extend(missing_values(value, present, path))
        elif value is None and (not isinstance(present, str) or not present.strip()):
            missing.append(path)
    return missing


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python qualification/check_environment_manifest.py <manifest.json>"
        )
        return 2
    template = json.loads(
        Path(__file__).with_name("environment-manifest.example.json").read_text()
    )
    try:
        data = json.loads(Path(sys.argv[1]).read_text())
    except (OSError, ValueError):
        print("Manifest cannot be read as JSON.")
        return 2
    missing = missing_values(template, data)
    if missing:
        print("Missing configuration or evidence references:\n" + "\n".join(missing))
        return 1
    print(
        "Required references present. Service compatibility, guarantees and release qualification remain unverified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
