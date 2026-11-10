"""Verify generated OpenAPI and native strict-input fixtures without touching services."""

import json
from pathlib import Path

from keep.event_models import EVENT_MODELS
from generate_http_contracts import documents

root = Path(__file__).resolve().parents[2] / "keep-contracts/schemas"
for name, document in documents().items():
    expected = json.loads((root / name).read_text(encoding="utf-8"))
    if document != expected:
        raise SystemExit(f"OpenAPI drift: {name}; run qualification/generate_contracts.py")
print("OpenAPI contract matches application")
for name, model in EVENT_MODELS.items():
    expected = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **model.model_json_schema(),
    }
    version = model.model_fields["schema_version"].annotation.__args__[0]
    kind = model.model_fields["kind"].annotation.__args__[0]
    actual = json.loads(
        (root / f"{kind}.v{version}.schema.json").read_text(encoding="utf-8")
    )
    if actual != expected:
        raise SystemExit(f"Event schema drift: {name}")
print("Internal event schemas match strict runtime models")
