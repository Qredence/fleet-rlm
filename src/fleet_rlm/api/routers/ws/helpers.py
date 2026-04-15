"""Low-level websocket helpers shared across the chat transport."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ...auth import AuthError
from ...dependencies import ServerState, build_unauthenticated_identity

logger = logging.getLogger(__name__)

_WEBSOCKET_CLOSED_ERROR_FRAGMENTS = (
    "after sending 'websocket.close'",
    "response already completed",
    "once a close message has been sent",
)


def _sanitize_for_log(value: object) -> str:
    """Normalize untrusted values to a single log line."""
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


def _is_closed_websocket_runtime_error(exc: RuntimeError) -> bool:
    """Return True when a runtime error indicates the websocket is already closed."""
    message = str(exc)
    if "websocket.send" not in message and "websocket.close" not in message:
        return False
    return any(fragment in message for fragment in _WEBSOCKET_CLOSED_ERROR_FRAGMENTS)


async def _try_send_json(websocket: WebSocket, payload: Any) -> bool:
    """Send JSON when possible, returning False if the websocket already closed."""
    try:
        await websocket.send_json(payload)
        return True
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        if _is_closed_websocket_runtime_error(exc):
            return False
        raise


async def _close_websocket_safely(
    websocket: WebSocket,
    *,
    code: int = 1000,
) -> None:
    """Close a websocket, ignoring races where it already closed."""
    try:
        await websocket.close(code=code)
    except WebSocketDisconnect:
        return
    except RuntimeError as exc:
        if _is_closed_websocket_runtime_error(exc):
            return
        raise


def _error_envelope(
    *, code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": "error", "code": code, "message": message}
    if details:
        payload["details"] = details
    return payload


async def _authenticate_websocket(
    websocket: WebSocket,
    state: ServerState,
):
    cfg = state.config
    provider = state.auth_provider
    if provider is None:
        if cfg.auth_required:
            await websocket.accept()
            if await _try_send_json(
                websocket,
                _error_envelope(
                    code="auth_provider_missing", message="Auth provider missing"
                ),
            ):
                await _close_websocket_safely(websocket, code=1011)
            return None
        return build_unauthenticated_identity(cfg)

    try:
        return await provider.authenticate_websocket(websocket)
    except AuthError as exc:
        if cfg.auth_required:
            await websocket.accept()
            if await _try_send_json(
                websocket,
                _error_envelope(code="auth_failed", message=exc.message),
            ):
                await _close_websocket_safely(websocket, code=1008)
            return None
        logger.debug("WS auth optional; continuing without auth: %s", exc.message)
        return build_unauthenticated_identity(cfg)
