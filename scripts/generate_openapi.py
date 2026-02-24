import sys
from pathlib import Path

import yaml

# Add src to python path to ensure imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fleet_rlm.server.main import app


def main():
    # Explicitly generate schema and enforce OpenAPI 3.1.0
    # FastAPI's default might be 3.0.2 or 3.1.0 depending on the version
    openapi_schema = app.openapi()
    openapi_schema["openapi"] = "3.1.0"

    # Optional: ensure operationIds are consistent, or we can leave that to the app validation

    output_path = Path("openapi.yaml")

    class CustomDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(
            openapi_schema,
            f,
            Dumper=CustomDumper,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )

    print(f"✅ Generated OpenAPI schema to {output_path.absolute()}")
    print(f"✅ OpenAPI version: {openapi_schema['openapi']}")

    # We can also count routes for the report
    paths = openapi_schema.get("paths", {})
    route_count = sum(len(methods) for methods in paths.values())
    print(f"✅ Extracted {route_count} routes")


if __name__ == "__main__":
    main()
