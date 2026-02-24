import yaml
import sys


def validate_openapi():
    with open("openapi.yaml", "r", encoding="utf-8") as f:
        schema = yaml.safe_load(f)

    paths = schema.get("paths", {})
    components = schema.get("components", {}).get("schemas", {})

    violations = []

    # 1. Docstrings (summary or description)
    # 2. Response Models (2xx, 4xx/5xx)
    # 3. Parameters (descriptions)
    # 5. Operation IDs

    for path, methods in paths.items():
        for method, operation in methods.items():
            op_id = operation.get("operationId", f"{method} {path}")

            # Rule 1: Docstrings
            if not operation.get("summary") and not operation.get("description"):
                violations.append(
                    f"Route '{op_id}' is missing a docstring (summary/description)"
                )

            # Rule 2: Response Models
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
                # We'll make this a warning or just report it
                violations.append(
                    f"Route '{op_id}' is missing an explicit error response model (4xx/5xx)"
                )

            # Rule 3: Parameters
            for param in operation.get("parameters", []):
                # if it's a ref we might skip full check, but let's assume inline
                if "$ref" not in param and not param.get("description"):
                    violations.append(
                        f"Route '{op_id}' parameter '{param.get('name')}' is missing a description"
                    )

            # Rule 5: Operation IDs
            if "operationId" not in operation:
                violations.append(
                    f"Route '{method} {path}' is missing an explicit operationId"
                )

    # Rule 4: Metadata (Pydantic models using Field with description)
    for model_name, model in components.items():
        properties = model.get("properties", {})
        for prop_name, prop in properties.items():
            if not prop.get("description") and "$ref" not in prop:
                # We won't log every single one to avoid spam, but just report a few
                violations.append(
                    f"Schema '{model_name}' property '{prop_name}' is missing a description (use Pydantic Field(description=...))"
                )

    if violations:
        print(f"❌ Found {len(violations)} Anthropic best practice violations:")
        for v in violations[:15]:
            print(f"  - {v}")
        if len(violations) > 15:
            print(f"  ... and {len(violations) - 15} more.")
        sys.exit(1)
    else:
        print("✅ OpenAPI schema fully complies with Anthropic best practices.")
        sys.exit(0)


if __name__ == "__main__":
    validate_openapi()
