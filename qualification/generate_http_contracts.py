"""Generate separate producer schemas and the frontend aggregate from installed apps."""
import json
from pathlib import Path
from keep.main import app as product
from keep.ingestion.main import app as ingestion


def documents():
    api = product.openapi()
    admission = ingestion.openapi()
    combined = json.loads(json.dumps(api))
    for path, definition in admission["paths"].items():
        if path in combined["paths"] and combined["paths"][path] != definition:
            raise ValueError(f"HTTP producers disagree on {path}")
        combined["paths"][path] = definition
    for category, definitions in admission.get("components", {}).items():
        target = combined.setdefault("components", {}).setdefault(category, {})
        for name, definition in definitions.items():
            if name in target and target[name] != definition:
                raise ValueError(f"HTTP components disagree on {name}")
            target[name] = definition
    return {"product.openapi.json": api, "ingestion.openapi.json": admission, "openapi.json": combined}


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[2] / "keep-contracts/schemas"
    for name, content in documents().items():
        (target / name).write_text(json.dumps(content, indent=2)+"\n", encoding="utf-8")
