"""OpenAPI projection for closed errors and the AI SDK UI SSE protocol."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from fleet_rlm.api.errors import ErrorResponse
from fleet_rlm.api.sse import FLEET_UI_CHUNK_TYPES

_CHUNK_FIELD_TYPES: dict[str, dict[str, Any]] = {
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

_CHUNK_FIELD_SPECS: dict[str, tuple[tuple[str, ...], list[str]]] = {
    "start": (("messageId", "messageMetadata"), ["type", "messageId", "messageMetadata"]),
    "reasoning-start": (("id",), ["type", "id"]),
    "reasoning-end": (("id",), ["type", "id"]),
    "reasoning-delta": (("id", "delta"), ["type", "id", "delta"]),
    "text-start": (("id",), ["type", "id"]),
    "text-end": (("id",), ["type", "id"]),
    "text-delta": (("id", "delta"), ["type", "id", "delta"]),
    "tool-input-available": (
        ("toolCallId", "toolName", "input", "dynamic", "providerExecuted"),
        ["type", "toolCallId", "toolName", "input"],
    ),
    "tool-output-available": (
        ("toolCallId", "output", "dynamic", "providerExecuted"),
        ["type", "toolCallId", "output"],
    ),
    "tool-output-error": (
        ("toolCallId", "errorText", "dynamic", "providerExecuted"),
        ["type", "toolCallId", "errorText"],
    ),
    "finish": (("finishReason", "messageMetadata"), ["type", "finishReason"]),
    "abort": (("reason",), ["type", "reason"]),
    "error": (("errorText",), ["type", "errorText"]),
}


def _chunk_field_spec(chunk_type: str) -> tuple[tuple[str, ...], list[str]]:
    if chunk_type in _CHUNK_FIELD_SPECS:
        return _CHUNK_FIELD_SPECS[chunk_type]
    if chunk_type.startswith("data-"):
        return (("id", "data", "transient"), ["type", "data"])
    return ((), ["type"])


def _chunk_schema(chunk_type: str) -> dict[str, Any]:
    fields, required = _chunk_field_spec(chunk_type)
    properties: dict[str, Any] = {"type": {"type": "string", "const": chunk_type}}
    properties.update({field: _CHUNK_FIELD_TYPES[field] for field in fields})
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

    app.__dict__["openapi"] = custom_openapi
