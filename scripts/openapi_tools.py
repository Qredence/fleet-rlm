#!/usr/bin/env python3
"""Unified CLI for OpenAPI operations (generate, validate)."""

import argparse
import sys
from pathlib import Path
import yaml

# Add src to python path to ensure imports work for generation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def do_generate(args: argparse.Namespace) -> int:
    from fleet_rlm.server.main import app

    openapi_schema = app.openapi()
    openapi_schema["openapi"] = "3.1.0"

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

    paths = openapi_schema.get("paths", {})
    route_count = sum(len(methods) for methods in paths.values())
    print(f"✅ Extracted {route_count} routes")
    return 0


def do_validate(args: argparse.Namespace) -> int:
    with open("openapi.yaml", "r", encoding="utf-8") as f:
        schema = yaml.safe_load(f)

    paths = schema.get("paths", {})
    components = schema.get("components", {}).get("schemas", {})
    violations = []

    for path, methods in paths.items():
        for method, operation in methods.items():
            op_id = operation.get("operationId", f"{method} {path}")

            if not operation.get("summary") and not operation.get("description"):
                violations.append(
                    f"Route '{op_id}' is missing a docstring (summary/description)"
                )

            responses = operation.get("responses", {})
            has_success = False
            has_error = False
            for status, resp in responses.items():
                if status.startswith("2") and "content" in resp:
                    has_success = True
                elif status.startswith("4") or status.startswith("5"):
                    has_error = True

            if not has_success:
                violations.append(
                    f"Route '{op_id}' is missing a success response model (2xx with content)"
                )
            if not has_error:
                violations.append(
                    f"Route '{op_id}' is missing an explicit error response model (4xx/5xx)"
                )

            for param in operation.get("parameters", []):
                if "$ref" not in param and not param.get("description"):
                    violations.append(
                        f"Route '{op_id}' parameter '{param.get('name')}' is missing a description"
                    )

            if "operationId" not in operation:
                violations.append(
                    f"Route '{method} {path}' is missing an explicit operationId"
                )

    for model_name, model in components.items():
        properties = model.get("properties", {})
        for prop_name, prop in properties.items():
            if not prop.get("description") and "$ref" not in prop:
                violations.append(
                    f"Schema '{model_name}' property '{prop_name}' is missing a description (use Pydantic Field(description=...))"
                )

    if violations:
        print(f"❌ Found {len(violations)} Anthropic best practice violations:")
        for v in violations[:15]:
            print(f"  - {v}")
        if len(violations) > 15:
            print(f"  ... and {len(violations) - 15} more.")
        return 1
    else:
        print("✅ OpenAPI schema fully complies with Anthropic best practices.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fleet RLM OpenAPI Tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_gen = subparsers.add_parser(
        "generate", help="Generate openapi.yaml from FastAPI app"
    )
    parser_gen.set_defaults(func=do_generate)

    parser_val = subparsers.add_parser(
        "validate", help="Validate openapi.yaml against Anthropic best practices"
    )
    parser_val.set_defaults(func=do_validate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
