"""Offline release inventory and bundle-integrity checks; never qualify or deploy."""

import argparse
import ast
import json
import re
from hashlib import sha256
from pathlib import Path, PurePosixPath

import tomllib

INVENTORY_VERSION = "keep-release-inventory-v1"
MANIFEST_VERSION = "keep-release-manifest-v1"
EVIDENCE = (
    "service_manifest",
    "capacity_profile",
    "configuration_diff",
    "correctness_results",
    "qualification_results",
    "security_review",
    "rollout_rehearsal",
    "rollback_rehearsal",
    "support_handoff",
)
INPUTS = {
    "migrations": ("migrations/[0-9]*.sql",),
    "contracts": ("contracts/*.json", "frontend/src/lib/generated.ts"),
    "dependencies": (
        "backend/pyproject.toml",
        "backend/uv.lock",
        "frontend/package.json",
        "frontend/package-lock.json",
    ),
    "profiles": (
        "backend/keep/cel/compiler.py",
        "backend/keep/cel/catalog.py",
        "backend/keep/maintenance/timezones.py",
        "backend/keep/operators/policy.py",
    ),
    "deployment": (
        "deploy/backend.Dockerfile",
        "frontend/Dockerfile",
        "deploy/openshift/*.json",
        "deploy/openshift/*.yaml",
    ),
    "runbooks": ("docs/runbooks/*.md", "docs/wrapping-key-rotation.md"),
}


class InvalidBundle(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidBundle(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON property")
        result[key] = value
    return result


def read_json(path):
    require(path.stat().st_size <= 4 * 1024 * 1024, "JSON document exceeds 4 MiB")
    return json.loads(
        path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object
    )


def bundle_file(root, relative):
    require(
        isinstance(relative, str)
        and relative
        and "\\" not in relative
        and ":" not in relative,
        "Artifact path must be relative POSIX syntax",
    )
    path = PurePosixPath(relative)
    require(
        not path.is_absolute()
        and ".." not in path.parts
        and path.as_posix() == relative,
        "Artifact path escapes or is not canonical",
    )
    resolved = (root / relative).resolve()
    require(
        resolved.is_relative_to(root.resolve()) and resolved.is_file(),
        "Artifact missing or escapes bundle",
    )
    return resolved


def digest(path):
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def reference(root, relative):
    return {"path": relative, "sha256": digest(bundle_file(root, relative))}


def verify_reference(root, value):
    require(
        isinstance(value, dict) and set(value) == {"path", "sha256"},
        "Artifact reference requires path and sha256",
    )
    require(
        isinstance(value["sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]),
        "Invalid artifact digest",
    )
    path = bundle_file(root, value["path"])
    require(path.stat().st_size > 0, "Artifact is empty")
    require(
        digest(path) == value["sha256"], "Artifact digest mismatch: " + value["path"]
    )
    return path


def constants(path):
    return {
        node.targets[0].id: node.value.value
        for node in ast.parse(path.read_text(encoding="utf-8-sig")).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }


def versions(root):
    project = tomllib.loads(
        bundle_file(root, "backend/pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    lock = tomllib.loads(
        bundle_file(root, "backend/uv.lock").read_text(encoding="utf-8")
    )
    packages = {item["name"]: item.get("version") for item in lock["package"]}
    cel = constants(bundle_file(root, "backend/keep/cel/compiler.py"))
    tzdata = constants(bundle_file(root, "backend/keep/maintenance/timezones.py"))[
        "PINNED_TZDATA_VERSION"
    ]
    require(packages["tzdata"] == tzdata, "Runtime tzdata profile differs from lock")
    unicode_versions = re.findall(
        r'unidata_version\s*!=\s*"([0-9.]+)"',
        bundle_file(root, "backend/keep/operators/policy.py").read_text(),
    )
    require(len(unicode_versions) == 1, "Expected one explicit Unicode profile")
    events = {
        path.name: read_json(path)["properties"]["schema_version"]["const"]
        for path in sorted((root / "contracts").glob("*.schema.json"))
    }
    return {
        "backend": project["version"],
        "frontend": read_json(bundle_file(root, "frontend/package.json"))["version"],
        "openapi": read_json(bundle_file(root, "contracts/openapi.json"))["info"][
            "version"
        ],
        "events": events,
        "cel_profile": cel["PROFILE"],
        "cel_cost_model": cel["COST_MODEL"],
        "cel_compiler": packages["cel-expr-python"],
        "unicode": unicode_versions[0],
        "tzdata": tzdata,
    }


def revision(value):
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value),
        "Full source commit revision required",
    )


def inventory(root, source_revision):
    revision(source_revision)
    files = {}
    for category, patterns in INPUTS.items():
        paths = set()
        for pattern in patterns:
            matched = list(root.glob(pattern))
            require(matched, "Missing release input: " + pattern)
            paths.update(path.relative_to(root).as_posix() for path in matched)
        files[category] = [reference(root, path) for path in sorted(paths)]
    names = [item["path"] for item in files["migrations"]]
    prefixes = [PurePosixPath(path).name.split("_", 1)[0] for path in names]
    require(len(set(prefixes)) == len(prefixes), "Duplicate migration order prefix")
    return {
        "schema": INVENTORY_VERSION,
        "source_revision": source_revision,
        "files": files,
        "versions": versions(root),
    }


def verify_inventory(root, value):
    require(
        isinstance(value, dict)
        and set(value) == {"schema", "source_revision", "files", "versions"},
        "Invalid inventory shape",
    )
    require(value["schema"] == INVENTORY_VERSION, "Unsupported inventory version")
    require(
        value == inventory(root, value["source_revision"]),
        "Release inventory differs from actual source inputs",
    )


def image_reference(value):
    require(
        isinstance(value, str)
        and "//" not in value
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", value),
        "Immutable image reference required",
    )


def verify_manifest(root, value):
    require(
        isinstance(value, dict)
        and set(value)
        == {
            "schema",
            "release_id",
            "source_revision",
            "inventory",
            "images",
            "evidence",
            "compatibility",
        },
        "Invalid release manifest shape",
    )
    require(value["schema"] == MANIFEST_VERSION, "Unsupported manifest version")
    require(
        isinstance(value["release_id"], str)
        and 1 <= len(value["release_id"].strip()) <= 128,
        "Release ID required",
    )
    revision(value["source_revision"])
    source = read_json(verify_reference(root, value["inventory"]))
    verify_inventory(root, source)
    require(
        source["source_revision"] == value["source_revision"],
        "Source revision mismatch",
    )
    require(
        isinstance(value["images"], dict)
        and set(value["images"]) == {"backend", "frontend"},
        "Both application images required",
    )
    for image in value["images"].values():
        require(
            isinstance(image, dict)
            and set(image)
            == {"reference", "sbom", "security_scan", "build_provenance"},
            "Image evidence incomplete",
        )
        image_reference(image["reference"])
        for field in ("sbom", "security_scan", "build_provenance"):
            verify_reference(root, image[field])
    require(
        isinstance(value["evidence"], dict) and set(value["evidence"]) == set(EVIDENCE),
        "Release evidence inventory incomplete",
    )
    for artifact in value["evidence"].values():
        verify_reference(root, artifact)
    compatibility = value["compatibility"]
    require(
        isinstance(compatibility, dict)
        and set(compatibility)
        == {
            "schema_min",
            "schema_max",
            "event_versions",
            "cel_profiles",
            "rollback_images",
            "evidence",
        },
        "Explicit compatibility range required",
    )
    migrations = [
        PurePosixPath(item["path"]).name for item in source["files"]["migrations"]
    ]
    require(
        compatibility["schema_min"] in migrations
        and compatibility["schema_max"] in migrations
        and compatibility["schema_min"]
        <= migrations[-1]
        <= compatibility["schema_max"],
        "Schema compatibility does not include release schema",
    )
    require(
        compatibility["event_versions"] == source["versions"]["events"],
        "Event compatibility differs from bundled contracts",
    )
    require(
        isinstance(compatibility["cel_profiles"], list)
        and source["versions"]["cel_profile"] in compatibility["cel_profiles"],
        "CEL profile compatibility absent",
    )
    rollback = compatibility["rollback_images"]
    require(
        isinstance(rollback, dict) and set(rollback) == {"backend", "frontend"},
        "Explicit compatible rollback image pair required",
    )
    for image in rollback.values():
        image_reference(image)
    verify_reference(root, compatibility["evidence"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("inventory")
    create.add_argument("--source-revision", required=True)
    create.add_argument("--output", type=Path, required=True)
    for name in ("verify-inventory", "verify-manifest"):
        commands.add_parser(name).add_argument("path", type=Path)
    for command in commands.choices.values():
        command.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            value = inventory(args.root.resolve(), args.source_revision)
            args.output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        elif args.command == "verify-inventory":
            verify_inventory(args.root.resolve(), read_json(args.path))
        else:
            verify_manifest(args.root.resolve(), read_json(args.path))
        print(
            "Artifact integrity/completeness check passed. Qualification, scan findings and rollout approval are not verified."
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError, SyntaxError):
        print(
            "Release artifact check failed: missing, invalid or inconsistent bundle input."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
