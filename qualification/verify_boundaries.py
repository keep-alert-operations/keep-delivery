"""Verify installed service imports cannot accidentally use a sibling service."""
import importlib
import importlib.abc
import json
from pathlib import Path
import subprocess
import sys

WORKSPACE = Path(__file__).resolve().parents[2]
ENTRYPOINTS = {
    "keep-api": "keep.main",
    "keep-ingestion": "keep.ingestion.main",
    "keep-processor": "keep.processor.main",
    "keep-correlation": "keep.correlation.main",
    "keep-indexer": "keep.search.main",
}


def ownership():
    owners = {}
    for repository in WORKSPACE.glob("keep-*"):
        source = repository / "src"
        for path in source.rglob("*.py"):
            module = ".".join(path.relative_to(source).with_suffix("").parts)
            module = module.removesuffix(".__init__")
            if module in owners:
                raise AssertionError(f"Duplicate module owner: {module}")
            owners[module] = repository.name
    return owners


class Boundary(importlib.abc.MetaPathFinder):
    def __init__(self, selected, owners):
        self.selected, self.owners = selected, owners

    def find_spec(self, fullname, path=None, target=None):
        owner = self.owners.get(fullname)
        if owner and owner not in {self.selected, "keep-contracts", "keep-domain"}:
            raise ImportError(f"{self.selected} attempted import from {owner}: {fullname}")
        if self.selected == "keep-ingestion" and fullname.split(".")[0] in {
            "psycopg", "psycopg_pool", "elasticsearch", "redis", "celpy", "authlib", "ldap3"
        }:
            raise ImportError(f"Stateless ingestion imported an unrelated adapter: {fullname}")
        return None


def main():
    owners = ownership()
    if len(sys.argv) == 2:
        selected = sys.argv[1]
        sys.meta_path.insert(0, Boundary(selected, owners))
        module = importlib.import_module(ENTRYPOINTS[selected])
        if hasattr(module, "app"):
            module.app.openapi()
        print(selected, "independent imports passed")
    else:
        for selected in ENTRYPOINTS:
            subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), selected], check=True)
        print(json.dumps({"owned_modules": len(owners), "independent_services": len(ENTRYPOINTS)}))


if __name__ == "__main__":
    main()
