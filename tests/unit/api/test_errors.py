from __future__ import annotations

import importlib
import json

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request


def _build_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})



def test_build_error_response_uses_canonical_envelope():
    errors_module = importlib.import_module("fleet_rlm.api.errors")

    response = errors_module.build_error_response(
        code="forbidden",
        message="Forbidden",
        status_code=403,
        detail={"reason": "missing-role"},
        headers={"X-Trace": "trace-1"},
    )

    payload = json.loads(response.body)
    assert response.status_code == 403
    assert response.headers["x-trace"] == "trace-1"
    assert payload == {
        "code": "forbidden",
        "message": "Forbidden",
        "detail": {"reason": "missing-role"},
    }


@pytest.mark.asyncio
async def test_http_exception_handler_uses_string_detail_as_message():
    errors_module = importlib.import_module("fleet_rlm.api.errors")

    response = await errors_module.http_exception_handler(
        _build_request(),
        HTTPException(status_code=404, detail="Session not found"),
    )

    payload = json.loads(response.body)
    assert response.status_code == 404
    assert payload == {
        "code": "not_found",
        "message": "Session not found",
        "detail": None,
    }


@pytest.mark.asyncio
async def test_validation_exception_handler_sanitizes_non_serializable_ctx():
    errors_module = importlib.import_module("fleet_rlm.api.errors")

    exc = RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body", "field"),
                "msg": "Value error",
                "input": "bad",
                "ctx": {"limit": 5, "error": ValueError("boom")},
            }
        ]
    )

    response = await errors_module.validation_exception_handler(_build_request(), exc)

    payload = json.loads(response.body)
    assert response.status_code == 422
    assert payload["code"] == "validation_error"
    assert payload["message"] == "Request validation failed."
    assert payload["detail"][0]["ctx"] == {"limit": "5"}
