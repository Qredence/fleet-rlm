"""Auth provider interface and shared errors."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request, WebSocket

from .types import NormalizedIdentity


class AuthError(Exception):
    """Authentication/authorization error surfaced to routes."""

    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AuthProvider(Protocol):
    """Auth provider contract used by HTTP and WebSocket surfaces."""

    async def authenticate_http(self, request: Request) -> NormalizedIdentity: ...

    async def authenticate_websocket(
        self, websocket: WebSocket
    ) -> NormalizedIdentity: ...
