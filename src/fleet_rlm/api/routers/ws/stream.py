"""Inner streaming loop and REPL hook management for WebSocket chat."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.integrations.database.models import RunStatus
from fleet_rlm.integrations.observability.mlflow_context import (
    merge_trace_result_metadata as _merge_trace_result_metadata,
)
from fleet_rlm.integrations.observability.trace_context import (
    runtime_telemetry_enabled_context,
)
from fleet_rlm.runtime.execution.streaming import is_terminal_stream_event_kind
from fleet_rlm.runtime.models import StreamEvent

from ...dependencies import ServerState
from ...execution import ExecutionEventEmitter, ExecutionStep, ExecutionStepBuilder
from ...schemas import WSMessage
from .commands import handle_command_with_persist
from .execution_support import get_execution_emitter
from .errors import handle_stream_error
from .helpers import _sanitize_for_log, _try_send_json
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
from .turn_setup import prepare_chat_message_turn
from .types import ChatAgentProtocol, LocalPersistFn, PreStreamSetupFn

logger = logging.getLogger(__name__)

_REPL_HOOK_STEP_QUEUE_MAX = 128


def merge_trace_result_metadata(
    payload: dict[str, Any] | None,
    *,
    response_preview: str | None = None,
) -> dict[str, Any]:
    """Compatibility shim for MLflow final-event metadata enrichment."""
    return _merge_trace_result_metadata(payload, response_preview=response_preview)


class ReplHookBridge:
    """Queue and forward interpreter REPL hook callbacks to lifecycle handlers."""

    def __init__(
        self,
        *,
        ws_loop: asyncio.AbstractEventLoop,
        lifecycle: ExecutionLifecycleManager,
        step_builder: ExecutionStepBuilder,
        interpreter: Any,
        enqueue_nonblocking: Callable[
            [asyncio.Queue[ExecutionStep | None], ExecutionStep], bool
        ],
    ) -> None:
        self._ws_loop = ws_loop
        self._lifecycle = lifecycle
        self._step_builder = step_builder
        self._interpreter = interpreter
        self._enqueue_nonblocking = enqueue_nonblocking
        self._previous_execution_hook: Any = None
        self._queue: asyncio.Queue[ExecutionStep | None] = asyncio.Queue(
            maxsize=_REPL_HOOK_STEP_QUEUE_MAX
        )
        self._worker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._repl_step_worker())
        if self._interpreter is None:
            return
        self._previous_execution_hook = getattr(
            self._interpreter, "execution_event_callback", None
        )
        self._interpreter.execution_event_callback = self._interpreter_hook

    async def stop(self) -> None:
        if self._interpreter is not None:
            self._interpreter.execution_event_callback = self._previous_execution_hook
        await self._queue.put(None)
        if self._worker_task is not None:
            try:
                await self._worker_task
            except asyncio.CancelledError:
                logger.debug("REPL step worker task was cancelled during shutdown")

    async def _emit_and_persist_repl_step(self, step_data: ExecutionStep) -> None:
        if self._lifecycle.run_completed:
            return
        try:
            await self._lifecycle.emit_step(step_data)
            await self._lifecycle.persist_step(step_data)
        except Exception as exc:  # pragma: no cover - defensive path
            logger.warning(
                "Failed to emit/persist REPL execution step: %s",
                _sanitize_for_log(exc),
            )
            self._lifecycle._persistence_error = exc

    async def _repl_step_worker(self) -> None:
        while True:
            step_data = await self._queue.get()
            if step_data is None:
                break
            await self._emit_and_persist_repl_step(step_data)

    def _queue_repl_step(self, step_data: ExecutionStep) -> None:
        if self._lifecycle.run_completed:
            return
        if not self._enqueue_nonblocking(self._queue, step_data):
            logger.debug("Dropped REPL execution step due to queue contention")

    def _interpreter_hook(self, payload: dict[str, Any]) -> None:
        if self._lifecycle.run_completed:
            return
        repl_step = self._step_builder.from_interpreter_hook(payload)
        if repl_step is None:
            return
        self._ws_loop.call_soon_threadsafe(
            lambda step_data=repl_step: self._queue_repl_step(step_data)
        )


async def run_streaming_turn(
    *,
    websocket: WebSocket,
    agent: ChatAgentProtocol,
    message: str,
    docs_path: str | None,
    trace: bool,
    cancel_check: Callable[[], bool],
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    interpreter: object | None,
    last_loaded_docs_path: str | None,
    analytics_enabled: bool | None,
    persist_session_state: LocalPersistFn,
    mlflow_trace_context: Any | None = None,
    prepare_stream: PreStreamSetupFn | None = None,
) -> str | None:
    """Execute one streaming turn, emitting events and persisting lifecycle steps."""

    await lifecycle.emit_started()
    ws_loop = asyncio.get_running_loop()
    mlflow_request_context = None
    if mlflow_trace_context is not None:
        from fleet_rlm.integrations.observability.mlflow_runtime import (
            mlflow_request_context as _mlflow_request_context,
        )

        mlflow_request_context = _mlflow_request_context
    repl_hook_bridge = ReplHookBridge(
        ws_loop=ws_loop,
        lifecycle=lifecycle,
        step_builder=step_builder,
        interpreter=interpreter,
        enqueue_nonblocking=enqueue_latest_nonblocking,
    )
    await repl_hook_bridge.start()

    if should_reload_docs_path(last_loaded_docs_path, docs_path):
        agent.load_document(str(docs_path))
        last_loaded_docs_path = str(docs_path).strip()

    try:
        if mlflow_trace_context is None:
            if prepare_stream is not None:
                await prepare_stream()
            await _stream_agent_events(
                websocket=websocket,
                agent=agent,
                request_message=message,
                message=message,
                docs_path=docs_path,
                trace=trace,
                cancel_check=cancel_check,
                lifecycle=lifecycle,
                step_builder=step_builder,
                analytics_enabled=analytics_enabled,
                persist_session_state=persist_session_state,
            )
        else:
            assert mlflow_request_context is not None
            with mlflow_request_context(mlflow_trace_context):
                if prepare_stream is not None:
                    await prepare_stream()
                await _stream_agent_events(
                    websocket=websocket,
                    agent=agent,
                    request_message=message,
                    message=message,
                    docs_path=docs_path,
                    trace=trace,
                    cancel_check=cancel_check,
                    lifecycle=lifecycle,
                    step_builder=step_builder,
                    analytics_enabled=analytics_enabled,
                    persist_session_state=persist_session_state,
                )
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        await handle_stream_error(
            websocket=websocket,
            lifecycle=lifecycle,
            step_builder=step_builder,
            exc=exc,
            request_message=message,
        )
    finally:
        await repl_hook_bridge.stop()

    return last_loaded_docs_path


async def _stream_agent_events(
    *,
    websocket: WebSocket,
    agent: ChatAgentProtocol,
    request_message: str,
    message: str,
    docs_path: str | None,
    trace: bool,
    cancel_check: Callable[[], bool],
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    analytics_enabled: bool | None,
    persist_session_state: LocalPersistFn,
) -> None:
    with runtime_telemetry_enabled_context(analytics_enabled):
        event_stream = agent.aiter_chat_turn_stream(
            message=message,
            trace=trace,
            cancel_check=cancel_check,
            docs_path=docs_path,
        )

        async for event in event_stream:
            await _emit_stream_event(
                websocket=websocket,
                lifecycle=lifecycle,
                step_builder=step_builder,
                event=event,
                persist_session_state=persist_session_state,
                request_message=request_message,
            )

    if not lifecycle.run_completed:
        lifecycle.raise_if_persistence_error()
        await lifecycle.complete_run(RunStatus.COMPLETED)


async def _emit_stream_event(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    event: StreamEvent,
    persist_session_state: LocalPersistFn,
    request_message: str,
) -> None:
    lifecycle.raise_if_persistence_error()
    payload = event.payload
    if event.kind == "final":
        payload = merge_trace_result_metadata(
            payload if isinstance(payload, dict) else None,
            response_preview=event.text,
        )
    event_dict = build_stream_event_dict(event=event, payload=payload)
    is_terminal_event = is_terminal_stream_event_kind(event.kind)
    if not is_terminal_event:
        if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
            raise WebSocketDisconnect(code=1001)

    step = step_builder.from_stream_event(
        kind=event.kind,
        text=event.text,
        payload=payload,
        timestamp=event.timestamp.timestamp(),
    )
    if step is not None:
        await lifecycle.emit_step(step)
        await lifecycle.persist_step(step)
        lifecycle.raise_if_persistence_error()

    if event.kind in {"cancelled", "error"}:
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

    if event.kind == "final":
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
        message=prepared_turn.message,
        docs_path=prepared_turn.docs_path,
        trace=prepared_turn.trace,
        cancel_check=cancel_check,
        lifecycle=prepared_turn.lifecycle,
        step_builder=prepared_turn.step_builder,
        interpreter=interpreter,
        last_loaded_docs_path=prepared_turn.last_loaded_docs_path,
        analytics_enabled=prepared_turn.analytics_enabled,
        persist_session_state=local_persist,
        mlflow_trace_context=prepared_turn.mlflow_trace_context,
        prepare_stream=prepared_turn.prepare_stream,
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
    execution_emitter = get_execution_emitter(state)
    stream_task: asyncio.Task[str | None] | None = None
    pending_receive_task: asyncio.Task[object] | None = None
    pending_message = initial_message

    try:
        while True:
            if stream_task is not None:
                if pending_receive_task is None:
                    pending_receive_task = asyncio.create_task(websocket.receive_json())

                done, _pending = await asyncio.wait(
                    {stream_task, pending_receive_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if stream_task in done:
                    session.last_loaded_docs_path = await stream_task
                    stream_task = None
                    continue

                assert pending_receive_task in done
                raw_payload = await pending_receive_task
                pending_receive_task = None
                msg = await parse_ws_message_or_send_error(
                    websocket=websocket,
                    raw_payload=raw_payload,
                )
                if msg is None:
                    continue

                if msg.type == "cancel":
                    session.cancel_flag["cancelled"] = True
                    continue

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
                    continue

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
                continue

            if pending_message is not None:
                msg = pending_message
                pending_message = None
            elif pending_receive_task is not None:
                raw_payload = await pending_receive_task
                pending_receive_task = None
                msg = await parse_ws_message_or_send_error(
                    websocket=websocket,
                    raw_payload=raw_payload,
                )
            else:
                raw_payload = await websocket.receive_json()
                msg = await parse_ws_message_or_send_error(
                    websocket=websocket,
                    raw_payload=raw_payload,
                )
            if msg is None:
                continue

            if msg.type == "cancel":
                session.cancel_flag["cancelled"] = True
                await _try_send_json(websocket, cancelled_event_payload())
                continue

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
                active_key=session.active_key,
                session_record=session.session_record,
                last_loaded_docs_path=session.last_loaded_docs_path,
                local_persist=local_persist,
            )

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
                continue

            if msg.type != "message":
                await _try_send_json(
                    websocket,
                    {"type": "error", "message": f"Unknown message type: {msg.type}"},
                )
                continue

            stream_task = asyncio.create_task(
                _process_chat_message(
                    websocket=websocket,
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
            )
    except WebSocketDisconnect:
        await handle_chat_disconnect(
            pending_receive_task=pending_receive_task,
            stream_task=stream_task,
            cancel_flag=session.cancel_flag,
            local_persist=local_persist,
            lifecycle=session.lifecycle,
        )
    except Exception as exc:
        await handle_chat_loop_exception(
            websocket=websocket,
            exc=exc,
            pending_receive_task=pending_receive_task,
            stream_task=stream_task,
            local_persist=local_persist,
            lifecycle=session.lifecycle,
        )
