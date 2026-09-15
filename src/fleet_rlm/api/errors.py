"""Closed public HTTP error responses for the coordinated API.

Handlers are registered against Starlette's base ``HTTPException`` so that one
normalizer owns the whole HTTP error surface. Fleet's own ``http_error()``
raises FastAPI's subclass, while routing raises the base class directly for
unknown routes (404) and unsupported methods (405); FastAPI installs its
default handler on the base class too, so registering on the subclass alone
would leave those routing errors on the framework's ``{"detail": ...}`` shape.

Unexpected server errors -- any non-``HTTPException`` escaping a route -- are
deliberately not normalized here. They report a Fleet defect rather than a
domain outcome, so they keep Starlette's ``ServerErrorMiddleware`` behaviour: a
plain 500 with the traceback logged server-side, outside the closed
``{code, message}`` contract. Clients already tolerate a non-JSON error body,
and folding faults into the domain envelope would hide them from operators.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.utils import is_body_allowed_for_status_code
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


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


def http_error(
    status: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    """Build a public closed-contract HTTP error (code/message dict detail).

    Routes must raise the dict form so the global handler never has to guess a
    code from a free-form string.
    """
    return HTTPException(status_code=status, detail={"code": code, "message": message}, headers=headers)


def _error(status_code: int, detail: Any) -> ErrorResponse:
    if isinstance(detail, Mapping):
        code = detail.get("code")
        message = detail.get("message")
        if isinstance(code, str) and isinstance(message, str):
            return ErrorResponse(code=code, message=message)
    return _STATUS_DEFAULTS.get(
        status_code,
        ErrorResponse(code="request_failed", message="Request failed"),
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> Response:
        # Statuses that must not carry a body (1xx, 204, 205, 304) keep the
        # framework's bodyless response instead of the JSON envelope. Reuse the
        # framework predicate so the rule cannot drift from FastAPI's own.
        if not is_body_allowed_for_status_code(exc.status_code):
            return Response(status_code=exc.status_code, headers=exc.headers)
        error = _error(exc.status_code, exc.detail)
        # Framework headers stay authoritative: the 405 ``Allow`` header and
        # any ``Retry-After`` set by a raising route must survive normalization.
        return JSONResponse(status_code=exc.status_code, content=error.model_dump(), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # Selection validation happens while FastAPI binds the body, before the
        # route can translate authorization failures. Keep every malformed
        # selection on the same non-leaking public contract.
        if request.url.path.endswith("/turns") and any(
            "skill_selections" in error.get("loc", ()) or "skill_selections" in str(error.get("msg", ""))
            for error in exc.errors()
        ):
            error = ErrorResponse(code="invalid_skill_selection", message="Invalid Skill selection")
            return JSONResponse(status_code=422, content=error.model_dump())
        error = ErrorResponse(code="invalid_request", message="Invalid request")
        return JSONResponse(status_code=422, content=error.model_dump())
