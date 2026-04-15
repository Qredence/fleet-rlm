"""Inner streaming loop and REPL hook management for WebSocket chat."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.agent_host import (
    OrchestrationSessionContext,  # noqa: F401 — re-exported for backwards compat
    ReplHookBridge,  # noqa: F401 — re-exported for backwards compat
    build_orchestration_session_context,
)

from ...dependencies import ServerState
from ...events import ExecutionEventEmitter
from ...schemas import WSMessage
from .commands import handle_command_with_persist
from .execution_support import get_execution_emitter
from .helpers import _try_send_json
from .lifecycle import ExecutionLifecycleManager  # noqa: F401 — re-exported for compat
from .loop_exit import handle_chat_disconnect, handle_chat_loop_exception
from .messages import parse_ws_message_or_send_error, resolve_session_identity
from .runtime import _ChatSessionState, _PreparedChatRuntime
from .session import switch_session_if_needed
from .task_control import cancelled_event_payload, enqueue_latest_nonblocking  # noqa: F401
from .turn_persistence import _emit_stream_event  # noqa: F401 — re-exported for backwards compat
from .turn_persistence import _is_terminal_transport_event  # noqa: F401 — re-exported for backwards compat
from .turn_persistence import _runtime_trace_metadata  # noqa: F401 — re-exported for backwards compat
from .turn_persistence import merge_trace_result_metadata  # noqa: F401 — re-exported for backwards compat
from .turn_runner import run_streaming_turn
from .turn_setup import PreparedStreamingTurn, prepare_chat_message_turn  # noqa: F401
from .types import ChatAgentProtocol, LocalPersistFn, StreamEventLike  # noqa: F401

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ResolvedSessionTarget:
    workspace_id: str
    user_id: str
    sess_id: str


def _ensure_pending_receive_task(
    *,
    websocket: WebSocket,
    pending_receive_task: asyncio.Task[object] | None,
) -> asyncio.Task[object]:
    if pending_receive_task is not None:
        return pending_receive_task
    return asyncio.create_task(websocket.receive_json())


async def _await_message_while_streaming(
    *,
    websocket: WebSocket,
    stream_task: asyncio.Task[str | None],
    pending_receive_task: asyncio.Task[object] | None,
    session: _ChatSessionState,
) -> tuple[
    WSMessage | None, asyncio.Task[str | None] | None, asyncio.Task[object] | None
]:
    pending_receive_task = _ensure_pending_receive_task(
        websocket=websocket,
        pending_receive_task=pending_receive_task,
    )
    done, _pending = await asyncio.wait(
        {stream_task, pending_receive_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if stream_task in done:
        session.last_loaded_docs_path = await stream_task
        return None, None, pending_receive_task

    raw_payload = await pending_receive_task
    msg = await parse_ws_message_or_send_error(
        websocket=websocket,
        raw_payload=raw_payload,
    )
    return msg, stream_task, None


async def _handle_message_while_streaming(
    *,
    websocket: WebSocket,
    msg: WSMessage,
    agent: ChatAgentProtocol,
    runtime: _PreparedChatRuntime,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
) -> bool:
    if msg.type == "cancel":
        session.cancel_flag["cancelled"] = True
        return True

    if msg.type == "command":
        await handle_command_with_persist(
            websocket=websocket,
            agent=agent,
            payload=msg.model_dump(),
            session_record=session.session_record,
            repository=runtime.repository,
            identity_rows=runtime.identity_rows,
            persistence_required=runtime.persistence_required,
            local_persist=local_persist,
        )
        return True

    if session.lifecycle is not None and session.lifecycle.run_completed:
        return False

    await _try_send_json(
        websocket,
        {
            "type": "error",
            "message": (
                "A run is already in progress. Cancel it or wait for "
                "completion before sending another message."
            ),
        },
    )
    return True


async def _receive_next_chat_message(
    *,
    websocket: WebSocket,
    pending_message: WSMessage | None,
    pending_receive_task: asyncio.Task[object] | None,
) -> tuple[WSMessage | None, asyncio.Task[object] | None]:
    if pending_message is not None:
        return pending_message, pending_receive_task

    if pending_receive_task is not None:
        raw_payload = await pending_receive_task
        pending_receive_task = None
    else:
        raw_payload = await websocket.receive_json()

    msg = await parse_ws_message_or_send_error(
        websocket=websocket,
        raw_payload=raw_payload,
    )
    return msg, pending_receive_task


async def _handle_idle_non_turn_message(
    *,
    websocket: WebSocket,
    msg: WSMessage,
    agent: ChatAgentProtocol,
    runtime: _PreparedChatRuntime,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
) -> bool:
    if msg.type == "cancel":
        session.cancel_flag["cancelled"] = True
        await _try_send_json(websocket, cancelled_event_payload())
        return True

    if msg.type == "command":
        await handle_command_with_persist(
            websocket=websocket,
            agent=agent,
            payload=msg.model_dump(),
            session_record=session.session_record,
            repository=runtime.repository,
            identity_rows=runtime.identity_rows,
            persistence_required=runtime.persistence_required,
            local_persist=local_persist,
        )
        return True

    if msg.type != "message":
        await _try_send_json(
            websocket,
            {"type": "error", "message": f"Unknown message type: {msg.type}"},
        )
        return True

    return False


async def _resolve_session_target(
    *,
    state: ServerState,
    agent: ChatAgentProtocol,
    interpreter: object | None,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
    msg: WSMessage,
) -> _ResolvedSessionTarget:
    workspace_id, user_id, sess_id = resolve_session_identity(
        msg=msg,
        workspace_id=session.canonical_workspace_id,
        user_id=session.canonical_user_id,
    )
    (
        session.active_key,
        session.active_manifest_path,
        session.session_record,
        session.last_loaded_docs_path,
        session.orchestration_session,
    ) = await switch_session_if_needed(
        state=state,
        agent=agent,
        interpreter=interpreter,
        workspace_id=workspace_id,
        user_id=user_id,
        sess_id=sess_id,
        owner_tenant_claim=session.owner_tenant_claim,
        owner_user_claim=session.owner_user_claim,
        active_key=session.active_key,
        session_record=session.session_record,
        last_loaded_docs_path=session.last_loaded_docs_path,
        local_persist=local_persist,
    )
    setattr(
        agent,
        "_db_session_id",
        (session.session_record or {}).get("db_session_id"),
    )
    return _ResolvedSessionTarget(
        workspace_id=workspace_id,
        user_id=user_id,
        sess_id=sess_id,
    )


async def _process_chat_message(
    *,
    websocket: WebSocket,
    msg: WSMessage,
    agent: ChatAgentProtocol,
    interpreter: object | None,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
    runtime: _PreparedChatRuntime,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    execution_emitter: ExecutionEventEmitter,
) -> str | None:
    """Process one ``message`` payload and return the loaded docs path."""
    prepared_turn = await prepare_chat_message_turn(
        websocket=websocket,
        msg=msg,
        agent=agent,
        session=session,
        local_persist=local_persist,
        runtime=runtime,
        workspace_id=workspace_id,
        user_id=user_id,
        sess_id=sess_id,
        execution_emitter=execution_emitter,
    )
    if prepared_turn is None:
        return session.last_loaded_docs_path

    def cancel_check() -> bool:
        return session.cancel_flag["cancelled"]

    orchestration_session = (
        session.orchestration_session
        or build_orchestration_session_context(
            session_record=session.session_record,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=sess_id,
            key=session.active_key,
            manifest_path=session.active_manifest_path,
        )
    )
    session.orchestration_session = orchestration_session

    return await run_streaming_turn(
        websocket=websocket,
        agent=agent,
        prepared_turn=prepared_turn,
        orchestration_session=orchestration_session,
        cancel_check=cancel_check,
        interpreter=interpreter,
        persist_session_state=local_persist,
    )


class _ExecutionConnectionLoop:
    """Connection-scoped websocket message loop for one execution socket."""

    def __init__(
        self,
        *,
        websocket: WebSocket,
        state: ServerState,
        runtime: _PreparedChatRuntime,
        agent: ChatAgentProtocol,
        interpreter: object | None,
        session: _ChatSessionState,
        local_persist: LocalPersistFn,
        initial_message: WSMessage | None = None,
    ) -> None:
        self.websocket = websocket
        self.state = state
        self.runtime = runtime
        self.agent = agent
        self.interpreter = interpreter
        self.session = session
        self.local_persist = local_persist
        self.execution_emitter = get_execution_emitter(state)
        self.stream_task: asyncio.Task[str | None] | None = None
        self.pending_receive_task: asyncio.Task[object] | None = None
        self.pending_message = initial_message

    async def run(self) -> None:
        try:
            while True:
                if self.stream_task is not None:
                    (
                        msg,
                        self.stream_task,
                        self.pending_receive_task,
                    ) = await _await_message_while_streaming(
                        websocket=self.websocket,
                        stream_task=self.stream_task,
                        pending_receive_task=self.pending_receive_task,
                        session=self.session,
                    )
                    if msg is None:
                        continue
                    if self.stream_task is None:
                        self.pending_message = msg
                        continue

                    if await _handle_message_while_streaming(
                        websocket=self.websocket,
                        msg=msg,
                        agent=self.agent,
                        runtime=self.runtime,
                        session=self.session,
                        local_persist=self.local_persist,
                    ):
                        continue
                    continue

                (
                    self.pending_message,
                    self.pending_receive_task,
                ) = await _receive_next_chat_message(
                    websocket=self.websocket,
                    pending_message=self.pending_message,
                    pending_receive_task=self.pending_receive_task,
                )
                msg = self.pending_message
                self.pending_message = None
                if msg is None:
                    continue

                target = await _resolve_session_target(
                    state=self.state,
                    agent=self.agent,
                    interpreter=self.interpreter,
                    local_persist=self.local_persist,
                    session=self.session,
                    msg=msg,
                )

                if await _handle_idle_non_turn_message(
                    websocket=self.websocket,
                    msg=msg,
                    agent=self.agent,
                    runtime=self.runtime,
                    session=self.session,
                    local_persist=self.local_persist,
                ):
                    continue

                self.stream_task = asyncio.create_task(
                    _process_chat_message(
                        websocket=self.websocket,
                        msg=msg,
                        agent=self.agent,
                        interpreter=self.interpreter,
                        session=self.session,
                        local_persist=self.local_persist,
                        runtime=self.runtime,
                        workspace_id=target.workspace_id,
                        user_id=target.user_id,
                        sess_id=target.sess_id,
                        execution_emitter=self.execution_emitter,
                    )
                )
        except (asyncio.CancelledError, WebSocketDisconnect):
            await handle_chat_disconnect(
                pending_receive_task=self.pending_receive_task,
                stream_task=self.stream_task,
                cancel_flag=self.session.cancel_flag,
                local_persist=self.local_persist,
                lifecycle=self.session.lifecycle,
            )
        except Exception as exc:
            await handle_chat_loop_exception(
                websocket=self.websocket,
                exc=exc,
                pending_receive_task=self.pending_receive_task,
                stream_task=self.stream_task,
                local_persist=self.local_persist,
                lifecycle=self.session.lifecycle,
            )


async def _chat_message_loop(
    *,
    websocket: WebSocket,
    state: ServerState,
    runtime: _PreparedChatRuntime,
    agent: ChatAgentProtocol,
    interpreter: object | None,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
    initial_message: WSMessage | None = None,
) -> None:
    loop = _ExecutionConnectionLoop(
        websocket=websocket,
        state=state,
        runtime=runtime,
        agent=agent,
        interpreter=interpreter,
        session=session,
        local_persist=local_persist,
        initial_message=initial_message,
    )
    await loop.run()
