"""Low-level websocket transport utilities.

Consolidates send/close helpers, message parsing, error envelopes,
and stream error handling into a single module.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log

from ...auth import AuthError
from ...dependencies import AuthDeps, ConfigDeps, build_unauthenticated_identity
from ...events import ExecutionStepBuilder
from ...runtime_services.chat_persistence import ExecutionLifecycleManager
from ...schemas import WSMessage
from .completion import build_execution_completion_summary
from .types import WorkspaceEvent

logger = logging.getLogger(__name__)

_WEBSOCKET_CLOSED_ERROR_FRAGMENTS = (
    "after sending 'websocket.close'",
    "response already completed",
    "once a close message has been sent",
)


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
    config_deps: ConfigDeps,
    auth_deps: AuthDeps,
):
    cfg = config_deps.config
    provider = auth_deps.auth_provider
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


async def parse_ws_message_or_send_error(
    *,
    websocket: WebSocket,
    raw_payload: object,
) -> WSMessage | None:
    """Parse a websocket payload into WSMessage, sending error envelopes on failure."""
    payload: dict[str, Any]
    if isinstance(raw_payload, dict):
        payload = {
            str(key): value
            for key, value in raw_payload.items()
            if isinstance(key, str)
        }
    else:
        payload = {}
    try:
        return WSMessage.model_validate(payload)
    except ValidationError as exc:
        raw_type = str(payload.get("type", "")).strip()
        if raw_type and raw_type not in {"message", "cancel", "command"}:
            await _try_send_json(
                websocket,
                {
                    "type": "error",
                    "message": f"Unknown message type: {raw_type}",
                },
            )
            return None
        errors = exc.errors()
        error_types = {str(error.get("type", "")) for error in errors}
        if "daytona_repo_ref_requires_repo" in error_types:
            await _try_send_json(
                websocket,
                _error_envelope(
                    code="daytona_repo_ref_requires_repo",
                    message="Daytona repo_ref requires repo_url.",
                ),
            )
            return None
        if "daytona_max_depth_removed" in error_types:
            await _try_send_json(
                websocket,
                _error_envelope(
                    code="daytona_max_depth_removed",
                    message=(
                        "Daytona websocket requests no longer accept max_depth; "
                        "use the server-configured recursion depth."
                    ),
                ),
            )
            return None
        if "unsupported_identity_fields" in error_types:
            await _try_send_json(
                websocket,
                _error_envelope(
                    code="unsupported_identity_fields",
                    message=(
                        "WebSocket identity is derived from auth. Remove "
                        "workspace_id/user_id and use session_id only."
                    ),
                ),
            )
            return None
        message = "; ".join(
            error.get("msg", "Invalid websocket payload") for error in errors
        )
        await _try_send_json(
            websocket,
            {"type": "error", "message": f"Invalid payload: {message}"},
        )
        return None


def resolve_session_identity(
    *,
    msg: WSMessage,
    workspace_id: str,
    user_id: str,
) -> tuple[str, str, str]:
    """Resolve canonical workspace/user and message session id."""
    sess_id = msg.session_id or str(uuid.uuid4())
    return workspace_id, user_id, sess_id


async def handle_stream_error(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    exc: Exception,
    request_message: str,
) -> None:
    """Log, emit, and persist a failed websocket streaming turn."""
    # Local import to avoid circular dependency with lifecycle.py
    from .lifecycle import classify_stream_failure

    error_code = classify_stream_failure(exc)
    logger.error(
        "Streaming error: %s",
        _sanitize_for_log(exc),
        exc_info=True,
        extra={
            "error_type": type(exc).__name__,
            "error_code": error_code,
        },
    )
    await _try_send_json(
        websocket,
        _error_envelope(
            code=error_code,
            message=f"Streaming error: {exc}",
            details={"error_type": type(exc).__name__},
        ),
    )
    if lifecycle.run_completed:
        return

    error_text = f"Streaming error: {exc}"
    error_payload = {
        "error_type": type(exc).__name__,
        "error_code": error_code,
    }
    error_step = step_builder.from_stream_event(
        kind="error",
        text=error_text,
        payload=error_payload,
        timestamp=time.time(),
    )
    if error_step is not None:
        await lifecycle.emit_step(error_step)
    await lifecycle.complete_run(
        RunStatus.FAILED,
        step=error_step,
        error_json={
            "error": str(exc),
            "error_type": type(exc).__name__,
            "code": error_code,
        },
        summary=build_execution_completion_summary(
            event=WorkspaceEvent(
                kind="error",
                text=error_text,
                payload=error_payload,
                terminal=True,
            ),
            request_message=request_message,
            run_id=lifecycle.run_id,
        ),
    )
