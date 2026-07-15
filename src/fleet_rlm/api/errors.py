"""Closed public HTTP error responses for the coordinated API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    code: str
    message: str


_STATUS_DEFAULTS = {
    400: ErrorResponse(code="invalid_request", message="Invalid request"),
    404: ErrorResponse(code="not_found", message="Resource not found"),
    409: ErrorResponse(code="turn_in_progress", message="Turn conflict"),
    422: ErrorResponse(code="invalid_request", message="Invalid request"),
    503: ErrorResponse(code="turn_unavailable", message="Service unavailable"),
    504: ErrorResponse(code="turn_preparation_timeout", message="Turn preparation timed out"),
}

_DETAIL_CODES = {
    "session not found": "session_not_found",
    "run not found": "run_not_found",
    "attachment not found": "attachment_not_found",
    "artifact not found": "artifact_not_found",
    "skill not found": "skill_not_found",
}


def _error(status_code: int, detail: Any) -> ErrorResponse:
    if isinstance(detail, Mapping):
        code = detail.get("code")
        message = detail.get("message")
        if isinstance(code, str) and isinstance(message, str):
            return ErrorResponse(code=code, message=message)
    if isinstance(detail, str):
        normalized = detail.strip()
        code = _DETAIL_CODES.get(normalized.lower())
        if code is not None:
            return ErrorResponse(code=code, message=normalized[:1].upper() + normalized[1:])
    return _STATUS_DEFAULTS.get(
        status_code,
        ErrorResponse(code="request_failed", message="Request failed"),
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        error = _error(exc.status_code, exc.detail)
        return JSONResponse(status_code=exc.status_code, content=error.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        error = ErrorResponse(code="invalid_request", message="Invalid request")
        return JSONResponse(status_code=422, content=error.model_dump())
