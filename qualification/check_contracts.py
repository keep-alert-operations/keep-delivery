"""Verify generated OpenAPI and native strict-input fixtures without touching services."""

import json
from pathlib import Path
from appchi.main import app
from appchi.event_models import EVENT_MODELS

root = Path(__file__).resolve().parents[1]
expected = json.loads((root / "contracts/openapi.json").read_text(encoding="utf-8-sig"))
if app.openapi() != expected:
    raise SystemExit(
        "OpenAPI drift: run python qualification/generate_contracts.py and regenerate frontend client"
    )
print("OpenAPI contract matches application")
for name, model in EVENT_MODELS.items():
    expected = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **model.model_json_schema(),
    }
    actual = json.loads(
        (root / "contracts" / f"{name}.v1.schema.json").read_text(encoding="utf-8")
    )
    if actual != expected:
        raise SystemExit(f"Event schema drift: {name}")
print("Internal event schemas match strict runtime models")
