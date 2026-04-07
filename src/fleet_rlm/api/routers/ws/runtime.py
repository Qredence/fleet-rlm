"""Compatibility wrapper around chat runtime services for websocket helpers."""

from __future__ import annotations

from fastapi import WebSocket

from ...auth import NormalizedIdentity
from ...dependencies import ServerState
from ...runtime_services.chat_runtime import (
    ChatSessionState as _ChatSessionState,
    PreparedChatRuntime as _PreparedChatRuntime,
    build_chat_agent_context as _build_chat_agent_context,
    new_chat_session_state as _new_chat_session_state,
    prepare_chat_runtime as _prepare_chat_runtime_service,
    set_interpreter_default_profile as _set_interpreter_default_profile,
)
from .helpers import _close_websocket_safely, _error_envelope, _try_send_json


async def _prepare_chat_runtime(
    *,
    websocket: WebSocket,
    state: ServerState,
    identity: NormalizedIdentity,
) -> _PreparedChatRuntime | None:
    async def _send_error(
        target: WebSocket,
        *,
        code: str,
        message: str,
    ) -> bool:
        return await _try_send_json(
            target,
            _error_envelope(code=code, message=message),
        )

    return await _prepare_chat_runtime_service(
        websocket=websocket,
        state=state,
        identity=identity,
        send_error=_send_error,
        close_websocket=_close_websocket_safely,
    )


__all__ = [
    "_ChatSessionState",
    "_PreparedChatRuntime",
    "_build_chat_agent_context",
    "_new_chat_session_state",
    "_prepare_chat_runtime",
    "_set_interpreter_default_profile",
]
