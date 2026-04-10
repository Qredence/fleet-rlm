"""Inner streaming loop and REPL hook management for WebSocket chat."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.integrations.observability.mlflow_context import (
    merge_trace_result_metadata as _merge_trace_result_metadata,
)
from fleet_rlm.integrations.observability.trace_context import (
    runtime_telemetry_enabled_context,
)
from fleet_rlm.worker import WorkspaceEvent, stream_workspace_task

from ...orchestration.repl_bridge import ReplHookBridge
from ...dependencies import ServerState
from ...events import ExecutionEventEmitter, ExecutionStepBuilder
from ...schemas import WSMessage
from .commands import handle_command_with_persist
from .execution_support import get_execution_emitter
from .errors import handle_stream_error
from .helpers import _try_send_json
from .lifecycle import ExecutionLifecycleManager
from .loop_exit import handle_chat_disconnect, handle_chat_loop_exception
from .messages import parse_ws_message_or_send_error, resolve_session_identity
from .runtime import _ChatSessionState, _PreparedChatRuntime
from .session import (
    switch_session_if_needed,
)
from .task_control import (
    cancelled_event_payload,
    enqueue_latest_nonblocking,
    should_reload_docs_path,
)
from .terminal import build_stream_event_dict, handle_terminal_stream_event
from .turn_setup import PreparedStreamingTurn, prepare_chat_message_turn
from .types import ChatAgentProtocol, LocalPersistFn, StreamEventLike
from .worker_request import build_workspace_task_request

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ResolvedSessionTarget:
    workspace_id: str
    user_id: str
    sess_id: str


def merge_trace_result_metadata(
    payload: dict[str, Any] | None,
    *,
    response_preview: str | None = None,
    trace_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility shim for MLflow final-event metadata enrichment."""
    return _merge_trace_result_metadata(
        payload,
        response_preview=response_preview,
        trace_metadata=trace_metadata,
    )


def _runtime_trace_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    runtime_payload = payload.get("runtime")
    runtime = runtime_payload if isinstance(runtime_payload, dict) else {}

    metadata: dict[str, Any] = {}
    for key in (
        "runtime_degraded",
        "runtime_failure_category",
        "runtime_failure_phase",
        "runtime_fallback_used",
    ):
        value = payload.get(key, runtime.get(key))
        if value in (None, "", False):
            if key in {"runtime_degraded", "runtime_fallback_used"} and value is False:
                metadata[key] = False
            continue
        metadata[key] = value
    return metadata


def _is_terminal_transport_event(event: StreamEventLike) -> bool:
    """Return websocket-terminal semantics for worker and legacy runtime events."""

    return bool(getattr(event, "terminal", False)) or event.kind in {
        "final",
        "cancelled",
        "error",
    }


async def run_streaming_turn(
    *,
    websocket: WebSocket,
    agent: ChatAgentProtocol,
    prepared_turn: PreparedStreamingTurn,
    cancel_check: Callable[[], bool],
    interpreter: object | None,
    persist_session_state: LocalPersistFn,
) -> str | None:
    """Execute one streaming turn, emitting events and persisting lifecycle steps."""

    lifecycle = prepared_turn.lifecycle
    step_builder = prepared_turn.step_builder
    await lifecycle.emit_started()
    ws_loop = asyncio.get_running_loop()
    repl_hook_bridge = ReplHookBridge(
        ws_loop=ws_loop,
        lifecycle=lifecycle,
        step_builder=step_builder,
        interpreter=interpreter,
        enqueue_nonblocking=enqueue_latest_nonblocking,
    )
    await repl_hook_bridge.start()

    last_loaded_docs_path = prepared_turn.last_loaded_docs_path
    if should_reload_docs_path(last_loaded_docs_path, prepared_turn.docs_path):
        agent.load_document(str(prepared_turn.docs_path))
        last_loaded_docs_path = str(prepared_turn.docs_path).strip()

    try:

        async def _stream_body() -> None:
            await _stream_agent_events(
                websocket=websocket,
                agent=agent,
                prepared_turn=prepared_turn,
                cancel_check=cancel_check,
                lifecycle=lifecycle,
                step_builder=step_builder,
                analytics_enabled=prepared_turn.analytics_enabled,
                persist_session_state=persist_session_state,
            )

        await _run_prepared_stream(
            mlflow_trace_context=prepared_turn.mlflow_trace_context,
            stream_body=_stream_body,
        )
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        await handle_stream_error(
            websocket=websocket,
            lifecycle=lifecycle,
            step_builder=step_builder,
            exc=exc,
            request_message=prepared_turn.message,
        )
    finally:
        await repl_hook_bridge.stop()

    return last_loaded_docs_path


async def _run_prepared_stream(
    *,
    mlflow_trace_context: Any | None,
    stream_body: Callable[[], Awaitable[None]],
) -> None:
    if mlflow_trace_context is None:
        await stream_body()
        return

    from fleet_rlm.integrations.observability.mlflow_runtime import (
        mlflow_request_context,
    )

    with mlflow_request_context(mlflow_trace_context):
        await stream_body()


async def _stream_agent_events(
    *,
    websocket: WebSocket,
    agent: ChatAgentProtocol,
    prepared_turn: PreparedStreamingTurn,
    cancel_check: Callable[[], bool],
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    analytics_enabled: bool | None,
    persist_session_state: LocalPersistFn,
) -> None:
    worker_request = build_workspace_task_request(
        agent=agent,
        prepared_turn=prepared_turn,
        cancel_check=cancel_check,
    )

    with runtime_telemetry_enabled_context(analytics_enabled):
        # The worker boundary owns request.prepare execution via
        # stream_workspace_task(...), so websocket transport only builds the
        # request and consumes worker-native events.
        async for worker_event in stream_workspace_task(worker_request):
            await _emit_stream_event(
                websocket=websocket,
                lifecycle=lifecycle,
                step_builder=step_builder,
                event=worker_event,
                persist_session_state=persist_session_state,
                request_message=prepared_turn.message,
            )

    if not lifecycle.run_completed:
        lifecycle.raise_if_persistence_error()
        await lifecycle.complete_run(RunStatus.COMPLETED)


async def _emit_stream_event(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    event: WorkspaceEvent | StreamEventLike,
    persist_session_state: LocalPersistFn,
    request_message: str,
) -> None:
    lifecycle.raise_if_persistence_error()
    payload = event.payload
    if event.kind == "final":
        payload = merge_trace_result_metadata(
            payload if isinstance(payload, dict) else None,
            response_preview=event.text,
            trace_metadata=_runtime_trace_metadata(
                payload if isinstance(payload, dict) else None
            ),
        )
    event_dict = build_stream_event_dict(event=event, payload=payload)
    is_terminal_event = _is_terminal_transport_event(event)
    if not is_terminal_event:
        if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
            raise WebSocketDisconnect(code=1001)

    event_timestamp = event.timestamp.timestamp()
    step = step_builder.from_stream_event(
        kind=event.kind,
        text=event.text,
        payload=payload,
        timestamp=event_timestamp,
    )
    if step is not None:
        await lifecycle.emit_step(step)
        await lifecycle.persist_step(step)
        lifecycle.raise_if_persistence_error()

    if is_terminal_event:
        await handle_terminal_stream_event(
            websocket=websocket,
            lifecycle=lifecycle,
            event=event,
            event_dict=event_dict,
            step=step,
            persist_session_state=persist_session_state,
            request_message=request_message,
        )
        return


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

    return await run_streaming_turn(
        websocket=websocket,
        agent=agent,
        prepared_turn=prepared_turn,
        cancel_check=cancel_check,
        interpreter=interpreter,
        persist_session_state=local_persist,
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
        except WebSocketDisconnect:
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
