"""OpenAPI customization helpers for the FastAPI app."""

from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI

_VALIDATION_ERROR_PROPERTY_DESCRIPTIONS: dict[str, str] = {
    "detail": "Structured list of request validation issues returned by FastAPI.",
    "loc": "Location path identifying where the validation error occurred.",
    "msg": "Human-readable validation failure message.",
    "type": "Pydantic validation error type identifier.",
    "input": "Input value that failed validation, when available.",
    "ctx": "Optional structured validation context for templated error messages.",
}


def annotate_validation_error_schemas(app: FastAPI) -> None:
    """Fill FastAPI-generated validation schemas with property descriptions.

    Results are cached via ``app.openapi_schema`` so the schema walk runs exactly
    once per app lifetime, matching the FastAPI docs guidance at
    https://fastapi.tiangolo.com/how-to/extending-openapi/#cache-the-generated-schema.
    """
    original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = original_openapi()
        components = schema.get("components", {}).get("schemas", {})

        for schema_name in ("HTTPValidationError", "ValidationError"):
            properties = components.get(schema_name, {}).get("properties", {})
            for property_name, description in _VALIDATION_ERROR_PROPERTY_DESCRIPTIONS.items():
                if property_name in properties and not properties[property_name].get("description"):
                    properties[property_name]["description"] = description

        app.openapi_schema = schema
        return schema

    app.openapi = cast(Any, custom_openapi)


__all__ = ["annotate_validation_error_schemas"]
