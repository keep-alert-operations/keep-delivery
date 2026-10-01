import json
from pathlib import Path

from appchi.event_models import EVENT_MODELS
from generate_http_contracts import documents

root = Path(__file__).resolve().parents[2] / "appchi-contracts/schemas"
for name, document in documents().items():
    (root / name).write_text(json.dumps(document, indent=2)+"\n", encoding="utf-8")
for model in EVENT_MODELS.values():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **model.model_json_schema(),
    }
    version = model.model_fields["schema_version"].annotation.__args__[0]
    kind = model.model_fields["kind"].annotation.__args__[0]
    (root / f"{kind}.v{version}.schema.json").write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8"
    )
