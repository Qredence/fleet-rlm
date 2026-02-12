"""Server middleware configuration."""

from __future__ import annotations

import uuid

from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response


def add_middlewares(app) -> None:
    """Register all middleware on the given FastAPI app."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_request_id(request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
