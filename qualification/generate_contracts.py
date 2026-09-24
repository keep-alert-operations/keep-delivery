import json
from pathlib import Path
from appchi.main import app
from appchi.event_models import EVENT_MODELS

root = Path(__file__).resolve().parents[1]
(root / "contracts/openapi.json").write_text(
    json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8"
)
for name, model in EVENT_MODELS.items():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **model.model_json_schema(),
    }
    (root / "contracts" / f"{name}.v1.schema.json").write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8"
    )
