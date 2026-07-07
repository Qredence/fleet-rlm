"""WebSocket streaming loop — thin wiring module.

Execution logic lives in :mod:`.turn_runner`.
Connection loop lives in :mod:`.connection_loop`.
"""

from __future__ import annotations

from fastapi import WebSocket

from ...dependencies import DiagnosticsDeps, SessionCacheDeps
from ...runtime_services.chat_runtime import (
    ChatAgentProtocol,
    LocalPersistFn,
)
from ...runtime_services.chat_runtime import (
    ChatSessionState as _ChatSessionState,
)
from ...runtime_services.chat_runtime import (
    PreparedChatRuntime as _PreparedChatRuntime,
)
from ...schemas import WSMessage
from .connection_loop import _ExecutionConnectionLoop
from .turn_runner import handle_stream_error, handle_terminal_stream_event, run_streaming_turn


async def _chat_message_loop(
    *,
    websocket: WebSocket,
    session_cache: SessionCacheDeps,
    diagnostics_deps: DiagnosticsDeps,
    runtime: _PreparedChatRuntime,
    agent: ChatAgentProtocol,
    interpreter: object | None,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
    initial_message: WSMessage | None = None,
    identity: object | None = None,
) -> None:
    await _ExecutionConnectionLoop(
        websocket=websocket,
        session_cache=session_cache,
        diagnostics_deps=diagnostics_deps,
        runtime=runtime,
        agent=agent,
        interpreter=interpreter,
        session=session,
        local_persist=local_persist,
        initial_message=initial_message,
        identity=identity,
    ).run()


__all__ = [
    "_chat_message_loop",
    "handle_terminal_stream_event",
    "handle_stream_error",
    "run_streaming_turn",
]
