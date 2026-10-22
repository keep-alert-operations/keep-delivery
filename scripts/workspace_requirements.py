"""Pin runtime requirements that editable local sources add beyond delivery's lock.

Run with the target environment's Python (it needs that environment's metadata
and `packaging`): `python workspace_requirements.py '<json request>'`. The request
maps each distribution to check onto the lock of the source that declares it, and
lists every sibling lock. Prints `{"pins": [...], "unresolved": [...]}`.
"""
import json
import re
import sys
import tomllib

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.version import Version


def key(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_entry(package, requirement):
    """A main-group lock entry for this platform whose version the requirement accepts."""
    if key(package["name"]) != key(requirement.name):
        return False
    if "main" not in package.get("groups", ["main"]):
        return False
    marker = package.get("markers")
    if isinstance(marker, dict):
        marker = marker.get("main")
    if marker and not Marker(marker).evaluate():
        return False
    return requirement.specifier.contains(Version(package["version"]), prereleases=True)


def resolve(distributions, locks, requires, installed):
    """Choose a locked pin for every unmet requirement of `distributions`.

    `distributions` maps a distribution name to its own lock path (or None);
    `locks` maps lock paths to their package lists, in fallback order. The
    declaring source's lock is preferred; another sibling lock is used only if
    its entry also satisfies the requirement.
    """
    pins, unresolved = {}, []
    for name, own in distributions.items():
        for text in requires(name):
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            if key(requirement.name) in installed:
                continue
            chosen = pins.get(key(requirement.name))
            if chosen:
                if not requirement.specifier.contains(Version(chosen["version"]), prereleases=True):
                    unresolved.append(f"{requirement} (required by {name}; {chosen['version']} chosen)")
                continue
            order = ([own] if own in locks else []) + [path for path in locks if path != own]
            found = next(
                ((path, package) for path in order for package in locks[path]
                 if runtime_entry(package, requirement)),
                None,
            )
            if found is None:
                unresolved.append(f"{requirement} (required by {name})")
                continue
            path, package = found
            hashes = " ".join(f"--hash={item['hash']}" for item in package.get("files", ()))
            pins[key(requirement.name)] = {
                "name": package["name"],
                "version": package["version"],
                "lock": path,
                "line": f"{package['name']}=={package['version']} {hashes}",
            }
    return {"pins": list(pins.values()), "unresolved": unresolved}


def main():
    from importlib import metadata

    request = json.loads(sys.argv[1])
    locks = {}
    for path in request["locks"]:
        with open(path, "rb") as file:
            locks[path] = tomllib.load(file).get("package", [])
    installed = {key(dist.metadata["Name"]) for dist in metadata.distributions()}
    result = resolve(
        request["distributions"], locks, lambda name: metadata.requires(name) or (), installed
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
