"""WebSocket shared helpers: auth, sanitization, constants."""

import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.infrastructure.database.models import RunStepType

from ...auth import AuthError
from ...deps import ServerState, build_unauthenticated_identity

logger = logging.getLogger(__name__)

_WEBSOCKET_CLOSED_ERROR_FRAGMENTS = (
    "after sending 'websocket.close'",
    "response already completed",
    "once a close message has been sent",
)

# ── Constants ──────────────────────────────────────────────────────────

EXECUTION_TO_RUN_STEP_TYPE: dict[str, RunStepType] = {
    "llm": RunStepType.LLM_CALL,
    "tool": RunStepType.TOOL_CALL,
    "repl": RunStepType.REPL_EXEC,
    "memory": RunStepType.MEMORY,
    "output": RunStepType.OUTPUT,
}


# ── Sanitization ───────────────────────────────────────────────────────


def _sanitize_for_log(value: object) -> str:
    """Normalize untrusted values to a single log line."""
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


def _sanitize_id(value: str, default_value: str) -> str:
    """Restrict workspace/user IDs to a safe path/key subset."""
    candidate = (value or "").strip()
    if not candidate:
        return default_value
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "-", candidate)
    return cleaned[:128] or default_value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


# ── Error helpers ──────────────────────────────────────────────────────


def _error_envelope(
    *, code: str, message: str, details: dict[str, Any] | None = None
) -> dict:
    payload: dict[str, Any] = {"type": "error", "code": code, "message": message}
    if details:
        payload["details"] = details
    return payload


def _map_execution_step_type(step_type: str) -> RunStepType:
    return EXECUTION_TO_RUN_STEP_TYPE.get(step_type, RunStepType.STATUS)


# ── Execution emitter singleton ────────────────────────────────────────


def _get_execution_emitter(state: ServerState):
    emitter = state.execution_event_emitter
    if emitter is not None:
        return emitter

    from ...execution import ExecutionEventEmitter

    cfg = state.config
    emitter = ExecutionEventEmitter(
        max_queue=cfg.ws_execution_max_queue,
        drop_policy=cfg.ws_execution_drop_policy,
    )
    state.execution_event_emitter = emitter
    return emitter


# ── Authentication ─────────────────────────────────────────────────────


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
