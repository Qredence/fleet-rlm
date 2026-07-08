"""Canonical HTTP error envelopes for the FastAPI surface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ExceptionHandler

from .schemas.base import ApiErrorResponse

_STATUS_ERROR_CODES: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "auth_required",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_409_CONFLICT: "conflict",
    413: "request_too_large",
    422: "validation_error",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_error",
    status.HTTP_502_BAD_GATEWAY: "upstream_error",
    status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
    status.HTTP_504_GATEWAY_TIMEOUT: "timeout",
}


def build_error_response(
    *,
    code: str,
    message: str,
    status_code: int,
    detail: Any | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the canonical non-secret HTTP error envelope."""
    payload = ApiErrorResponse(code=code, message=message, detail=detail)
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


def _http_error_code(status_code: int) -> str:
    return _STATUS_ERROR_CODES.get(status_code, f"http_{status_code}")


def _http_exception_message(exc: HTTPException | StarletteHTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict) and "message" in detail:
        return str(detail["message"])
    if isinstance(detail, str) and detail.strip():
        return detail
    return "HTTP request failed."


def _json_safe_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Return FastAPI validation details without non-serializable exception objects."""
    safe_errors: list[dict[str, Any]] = []
    for error in errors:
        if not isinstance(error, Mapping):
            safe_errors.append({"message": str(error)})
            continue
        safe_error = dict(error)
        ctx = safe_error.get("ctx")
        if isinstance(ctx, dict):
            safe_error["ctx"] = {
                str(key): str(value)
                for key, value in ctx.items()
                if isinstance(value, str | int | float | bool) or value is None
            }
        safe_errors.append(safe_error)
    return safe_errors


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Convert FastAPI/Starlette HTTPException payloads to the canonical error envelope."""
    _ = request
    if not isinstance(exc, HTTPException | StarletteHTTPException):
        return build_error_response(
            code="internal_error",
            message="HTTP request failed.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    headers = exc.headers if isinstance(exc, HTTPException | StarletteHTTPException) else None
    code = _http_error_code(exc.status_code)
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        code = str(exc.detail["code"])
    return build_error_response(
        code=code,
        message=_http_exception_message(exc),
        status_code=exc.status_code,
        detail=exc.detail if not isinstance(exc.detail, str) and not isinstance(exc.detail, dict) else None,
        headers=headers,
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Convert request validation errors to the canonical error envelope."""
    _ = request
    if not isinstance(exc, RequestValidationError):
        return build_error_response(
            code="validation_error",
            message="Request validation failed.",
            status_code=422,
        )
    return build_error_response(
        code="validation_error",
        message="Request validation failed.",
        status_code=422,
        detail=_json_safe_validation_errors(exc.errors()),
    )


def add_exception_handlers(app: FastAPI) -> None:
    """Register canonical HTTP exception handlers on the app."""
    app.add_exception_handler(HTTPException, cast(ExceptionHandler, http_exception_handler))
    app.add_exception_handler(StarletteHTTPException, cast(ExceptionHandler, http_exception_handler))
    app.add_exception_handler(RequestValidationError, cast(ExceptionHandler, validation_exception_handler))


__all__ = [
    "add_exception_handlers",
    "build_error_response",
    "http_exception_handler",
    "validation_exception_handler",
]
