"""OpenAPI projection for closed errors and the AI SDK UI SSE protocol."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from fleet_rlm.api.errors import ErrorResponse
from fleet_rlm.api.sse import FLEET_UI_CHUNK_TYPES


def _chunk_schema(chunk_type: str) -> dict[str, Any]:
    properties: dict[str, Any] = {"type": {"type": "string", "const": chunk_type}}
    required = ["type"]
    field_types: dict[str, dict[str, Any]] = {
        "id": {"type": "string"},
        "messageId": {"type": "string", "format": "uuid"},
        "messageMetadata": {"type": "object", "additionalProperties": True},
        "delta": {"type": "string"},
        "toolCallId": {"type": "string"},
        "toolName": {"type": "string"},
        "input": {},
        "output": {},
        "errorText": {"type": "string"},
        "reason": {"type": "string"},
        "finishReason": {"type": "string", "enum": ["stop", "error"]},
        "data": {},
        "dynamic": {"type": "boolean"},
        "providerExecuted": {"type": "boolean"},
        "transient": {"type": "boolean"},
    }
    fields: tuple[str, ...]
    if chunk_type == "start":
        fields, required = ("messageId", "messageMetadata"), ["type", "messageId", "messageMetadata"]
    elif chunk_type in {"reasoning-start", "reasoning-end", "text-start", "text-end"}:
        fields, required = ("id",), ["type", "id"]
    elif chunk_type in {"reasoning-delta", "text-delta"}:
        fields, required = ("id", "delta"), ["type", "id", "delta"]
    elif chunk_type.startswith("data-"):
        fields, required = ("id", "data", "transient"), ["type", "data"]
    elif chunk_type == "tool-input-available":
        fields, required = (
            (
                "toolCallId",
                "toolName",
                "input",
                "dynamic",
                "providerExecuted",
            ),
            ["type", "toolCallId", "toolName", "input"],
        )
    elif chunk_type == "tool-output-available":
        fields, required = (
            ("toolCallId", "output", "dynamic", "providerExecuted"),
            [
                "type",
                "toolCallId",
                "output",
            ],
        )
    elif chunk_type == "tool-output-error":
        fields, required = (
            ("toolCallId", "errorText", "dynamic", "providerExecuted"),
            [
                "type",
                "toolCallId",
                "errorText",
            ],
        )
    elif chunk_type == "finish":
        fields, required = ("finishReason", "messageMetadata"), ["type", "finishReason"]
    elif chunk_type == "abort":
        fields, required = ("reason",), ["type", "reason"]
    elif chunk_type == "error":
        fields, required = ("errorText",), ["type", "errorText"]
    else:
        fields = ()
    properties.update({field: field_types[field] for field in fields})
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


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
        components["FleetUIMessageChunk"] = {
            "title": "FleetUIMessageChunk",
            "oneOf": [_chunk_schema(chunk_type) for chunk_type in FLEET_UI_CHUNK_TYPES],
        }

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

    setattr(app, "openapi", custom_openapi)
