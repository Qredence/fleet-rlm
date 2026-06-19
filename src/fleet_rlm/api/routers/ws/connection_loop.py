"""WebSocket connection loop: message receive/interleave and background execution.

Owns: ``_ExecutionConnectionLoop``, message-type dispatch,
receive/stream interleaving, and the background execution task.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.runtime.events import RuntimeEvent

from ...dependencies import DiagnosticsDeps, SessionCacheDeps
from ...events import ExecutionEventEmitter, ExecutionSubscription
from ...runtime_services.chat_persistence import (
    build_startup_status_event,
    handle_chat_disconnect,
)
from ...runtime_services.chat_runtime import (
    ChatAgentProtocol,
    LocalPersistFn,
    SessionContext,
    set_interpreter_default_profile,
)
from ...runtime_services.chat_runtime import (
    ChatSessionState as _ChatSessionState,
)
from ...runtime_services.chat_runtime import (
    PreparedChatRuntime as _PreparedChatRuntime,
)
from ...runtime_services.session_persistence import build_local_persist_fn
from ...schemas import WSMessage
from .commands import handle_command_with_persist
from .session import switch_session_if_needed
from .stream_events import build_stream_event_dict
from .transport import (
    _error_envelope,
    _try_send_json,
    handle_chat_loop_exception,
    parse_ws_message_or_send_error,
    resolve_session_identity,
)
from .turn_runner import run_streaming_turn
from .turn_setup import prepare_chat_message_turn

logger = logging.getLogger(__name__)


def _agent_turn_count(agent: ChatAgentProtocol) -> int:
    turn_count = getattr(agent, "turn_count", None)
    if isinstance(turn_count, int):
        return turn_count
    agent_module = getattr(agent, "agent", None)
    module_turn_count = getattr(agent_module, "_turn_count", None)
    if isinstance(module_turn_count, int):
        return module_turn_count
    return 0


def _build_routing_preview_event(agent: ChatAgentProtocol, msg: WSMessage) -> RuntimeEvent | None:
    preview_routing = getattr(agent, "preview_routing", None)
    if not callable(preview_routing):
        return None
    from fleet_rlm.runtime.agent.runtime_helpers import routing_status_text
    from fleet_rlm.runtime.modules.context_routing import build_turn_context_for_agent
    from fleet_rlm.runtime.modules.skill_selection import preview_skills_for_turn

    turn_context = build_turn_context_for_agent(
        agent,
        user_request=msg.content,
        docs_path=msg.docs_path,
        context_paths=list(msg.context_paths or []) if msg.context_paths is not None else None,
    )
    execution_mode = msg.execution_mode or "auto"
    payload = preview_routing(
        user_request=msg.content,
        execution_mode=execution_mode,
        turn_context=turn_context,
    )
    if not isinstance(payload, dict) or not payload.get("routing_decision"):
        return None
    preview_skills = preview_skills_for_turn(
        msg.content,
        execution_mode=execution_mode,
        routing_decision=str(payload.get("routing_decision") or "") or None,
        is_first_turn=_agent_turn_count(agent) == 0,
    )
    if preview_skills:
        payload = {**payload, "selected_skills": preview_skills}
    return RuntimeEvent.status(
        routing_status_text(payload),
        payload={**payload, "phase": "routing"},
    )


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
) -> tuple[WSMessage | None, asyncio.Task[str | None] | None, asyncio.Task[object] | None]:
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
            persistence=runtime.persistence,
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
                "A run is already in progress. Cancel it or wait for completion before sending another message."
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
        await _try_send_json(
            websocket,
            _error_envelope(
                code="no_active_run",
                message="No active websocket run is available to cancel.",
            ),
        )
        return True

    if msg.type == "command":
        await handle_command_with_persist(
            websocket=websocket,
            agent=agent,
            payload=msg.model_dump(),
            session_record=session.session_record,
            persistence=runtime.persistence,
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


async def _process_chat_message(
    *,
    websocket: WebSocket | None,
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

    orchestration_session = session.orchestration_session or SessionContext(
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=sess_id,
        session_record=session.session_record,
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
        execution_emitter=execution_emitter,
    )


async def _background_execution_task(
    *,
    msg: WSMessage,
    session_cache: SessionCacheDeps,
    runtime: _PreparedChatRuntime,
    session: _ChatSessionState,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    execution_emitter: ExecutionEventEmitter,
) -> str | None:
    """Run execution in the background with its own agent context."""
    from ...runtime_services.chat_runtime import build_chat_agent_context

    try:
        agent_context = await build_chat_agent_context(runtime)
        async with agent_context as agent:
            interpreter = getattr(agent, "interpreter", None)
            set_interpreter_default_profile(interpreter, runtime.cfg)

            async def _noop_persist(
                *,
                include_volume_save: bool = True,
                latest_user_message: str = "",
            ) -> None:
                _ = include_volume_save, latest_user_message

            (
                session.active_key,
                session.active_manifest_path,
                session.session_record,
                session.last_loaded_docs_path,
                session.orchestration_session,
            ) = await switch_session_if_needed(
                session_cache=session_cache,
                agent=agent,
                interpreter=interpreter,
                workspace_id=workspace_id,
                user_id=user_id,
                sess_id=sess_id,
                owner_tenant_claim=session.owner_tenant_claim,
                owner_user_claim=session.owner_user_claim,
                active_key=None,
                session_record=session.session_record,
                last_loaded_docs_path=session.last_loaded_docs_path,
                local_persist=_noop_persist,
                persistence=runtime.persistence,
                identity_rows=runtime.identity_rows,
            )

            agent._db_session_id = (session.session_record or {}).get("db_session_id")
            agent._identity_rows = runtime.identity_rows
            if agent.interpreter is not None:
                agent.interpreter._host_repository = runtime.persistence
                agent.interpreter._host_identity = runtime.identity_rows
                agent.interpreter._host_run_id = None
            local_persist = build_local_persist_fn(
                session_cache=session_cache,
                runtime=runtime,
                agent=agent,
                interpreter=interpreter,
                session=session,
            )

            return await _process_chat_message(
                websocket=None,
                msg=msg,
                agent=agent,
                interpreter=interpreter,
                session=session,
                local_persist=local_persist,
                runtime=runtime,
                workspace_id=workspace_id,
                user_id=user_id,
                sess_id=sess_id,
                execution_emitter=execution_emitter,
            )
    except Exception:
        logger.exception("Background websocket execution task failed")
        raise


class _ExecutionConnectionLoop:
    """Connection-scoped websocket message loop for one execution socket."""

    def __init__(
        self,
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
    ) -> None:
        self.websocket = websocket
        self.session_cache = session_cache
        self.diagnostics_deps = diagnostics_deps
        self.runtime = runtime
        self.agent = agent
        self.interpreter = interpreter
        self.session = session
        self.local_persist = local_persist
        self.execution_emitter = diagnostics_deps.events_event_emitter
        self.stream_task: asyncio.Task[str | None] | asyncio.Task[None] | None = None
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

                if await _handle_idle_non_turn_message(
                    websocket=self.websocket,
                    msg=msg,
                    agent=self.agent,
                    runtime=self.runtime,
                    session=self.session,
                    local_persist=self.local_persist,
                ):
                    continue

                if not str(msg.content or "").strip():
                    await _try_send_json(
                        self.websocket,
                        {"type": "error", "message": "Message content cannot be empty"},
                    )
                    continue

                workspace_id, user_id, sess_id = resolve_session_identity(
                    msg=msg,
                    workspace_id=self.session.canonical_workspace_id,
                    user_id=self.session.canonical_user_id,
                )
                await self.execution_emitter.update_subscription(
                    self.websocket,
                    ExecutionSubscription(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        session_id=sess_id,
                    ),
                )
                startup_event = build_startup_status_event()
                await _try_send_json(
                    self.websocket,
                    {
                        "type": "event",
                        "data": build_stream_event_dict(
                            event=startup_event,
                            payload=startup_event.payload,
                        ),
                    },
                )
                routing_preview_event = _build_routing_preview_event(self.agent, msg)
                if routing_preview_event is not None:
                    await _try_send_json(
                        self.websocket,
                        {
                            "type": "event",
                            "data": build_stream_event_dict(
                                event=routing_preview_event,
                                payload=routing_preview_event.payload,
                            ),
                        },
                    )
                self.stream_task = asyncio.create_task(
                    _background_execution_task(
                        msg=msg,
                        session_cache=self.session_cache,
                        runtime=self.runtime,
                        session=self.session,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        sess_id=sess_id,
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
                cancel_active_run=False,
                persist_on_disconnect=False,
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


__all__ = ["_ExecutionConnectionLoop"]
