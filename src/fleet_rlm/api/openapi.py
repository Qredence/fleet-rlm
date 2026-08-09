"""OpenAPI projection for closed errors and the AI SDK UI SSE protocol."""

from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from fleet_rlm.api.errors import ErrorResponse

# The AI SDK UI chunk contract is owned by typed transport models in
# `api/ui_stream.py`; this hook derives the OpenAPI variants from that same
# source. Generated TUI validator tables consume the OpenAPI schema, while the
# golden-stream fixture locks runtime bytes against this documented contract.


def _inline_fleet_ui_schema() -> dict[str, Any]:
    """Inline one Model-derived schema without hand-maintained variant tables."""
    from copy import deepcopy

    from fleet_rlm.api.ui_stream import fleet_ui_message_chunk_json_schema

    schema = fleet_ui_message_chunk_json_schema()
    definitions = cast(dict[str, Any], schema.pop("$defs", {}))

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = str(node["$ref"]).removeprefix("#/$defs/")
            if name not in definitions:
                raise RuntimeError(f"unresolved Fleet UI schema reference: {node['$ref']!r}")
            return resolve(deepcopy(definitions[name]))
        return {key: resolve(value) for key, value in node.items()}

    resolved = cast(dict[str, Any], resolve(schema))
    # Pydantic emits discriminator mappings for external `$defs`; after the
    # OpenAPI hook inlines every variant those refs are stale and redundant.
    resolved.pop("discriminator", None)

    def strip_model_defaults(node: Any) -> Any:
        if isinstance(node, list):
            return [strip_model_defaults(item) for item in node]
        if not isinstance(node, dict):
            return node
        return {key: strip_model_defaults(value) for key, value in node.items() if key != "default"}

    resolved = strip_model_defaults(resolved)
    assert isinstance(resolved, dict)
    for variant in resolved.get("oneOf", []):
        required = list(variant.get("required", []))
        if "type" not in required:
            required.insert(0, "type")
        variant["required"] = required
        part_id = variant.get("properties", {}).get("id")
        if isinstance(part_id, dict) and part_id.get("anyOf"):
            # Optional transport part IDs were legacy plain strings, not
            # explicit nulls; preserve that established generated TS shape.
            nullable_string = part_id["anyOf"]
            if len(nullable_string) == 2 and {"type": "null"} in nullable_string:
                base = next(item for item in nullable_string if item != {"type": "null"})
                if base.get("type") == "string":
                    variant["properties"]["id"] = {
                        **base,
                        **({"title": part_id["title"]} if "title" in part_id else {}),
                    }
    resolved["title"] = "FleetUIMessageChunk"
    return resolved


def install_openapi_contract(app: FastAPI) -> None:
    """Install one deterministic schema hook shared by generation and runtime docs."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        components["ErrorResponse"] = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
        components["FleetUIMessageChunk"] = _inline_fleet_ui_schema()

        for path, path_item in schema.get("paths", {}).items():
            for operation in path_item.values():
                if not isinstance(operation, dict) or "responses" not in operation:
                    continue
                for status, response in operation["responses"].items():
                    if str(status).startswith(("4", "5")):
                        response["content"] = {
                            "application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}
                        }
            if path == "/api/sessions/{session_id}/turns":
                media = path_item["post"]["responses"]["200"]["content"]["text/event-stream"]
                media["x-event-data-schema"] = {"$ref": "#/components/schemas/FleetUIMessageChunk"}
        app.openapi_schema = schema
        return schema

    app.__dict__["openapi"] = custom_openapi
