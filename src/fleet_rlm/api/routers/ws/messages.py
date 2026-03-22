"""Inbound websocket message parsing and session identity helpers."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import WebSocket
from pydantic import ValidationError

from ...schemas import WSMessage
from .helpers import _error_envelope, _try_send_json


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
