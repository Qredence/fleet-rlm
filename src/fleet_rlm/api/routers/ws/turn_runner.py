"""Streaming turn execution: agent events → lifecycle steps → persistence.

Owns the hot path from ``aiter_chat_turn_stream`` to ``lifecycle.complete_run``.
Pure execution functions; no connection/receive logic.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.integrations.observability.trace_context import (
    runtime_telemetry_enabled_context,
)
from fleet_rlm.runtime.events import RuntimeEvent
from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log

from ...events import (
    ExecutionEventEmitter,
    ExecutionStep,
    ExecutionStepBuilder,
)
from ...runtime_services.chat_persistence import (
    enqueue_latest_nonblocking,
    should_reload_docs_path,
)
from ...runtime_services.chat_runtime import (
    ChatAgentProtocol,
    LocalPersistFn,
    SessionContext,
    StreamEventLike,
)
from ...runtime_services.run_lifecycle import ExecutionLifecycleManager
from ...runtime_services.stream_failures import classify_stream_failure
from .repl_bridge import ReplHookBridge
from .stream_events import (
    WorkspaceEvent,
    _is_terminal_transport_event,
    build_stream_event_dict,
    stream_agent_turn,
)
from .stream_summary import (
    _runtime_trace_metadata,
    build_execution_completion_summary,
    enrich_terminal_stream_payload,
    final_event_failed,
    merge_trace_result_metadata,
)
from .transport import _error_envelope, _try_send_json
from .turn_setup import PreparedStreamingTurn

logger = logging.getLogger(__name__)


def _terminal_run_status(event: StreamEventLike) -> RunStatus:
    """Return the authoritative terminal run status for one event."""
    if event.kind == "done" and (isinstance(event.payload, dict) and event.payload.get("cancelled")):
        return RunStatus.CANCELLED
    if event.kind == "done":
        payload = event.payload if isinstance(event.payload, dict) else {}
        return RunStatus.FAILED if final_event_failed(payload) else RunStatus.COMPLETED
    return RunStatus.FAILED


async def handle_terminal_stream_event(
    *,
    websocket: WebSocket | None,
    lifecycle: ExecutionLifecycleManager,
    event: StreamEventLike,
    event_dict: dict[str, Any],
    step: ExecutionStep | None,
    persist_session_state: LocalPersistFn,
    request_message: str,
    orchestration_session: SessionContext | None = None,
) -> None:
    """Handle terminal websocket events: persist, complete lifecycle, send."""
    summary = build_execution_completion_summary(
        event=event,
        request_message=request_message,
        run_id=lifecycle.run_id,
    )

    if event.kind == "done":
        try:
            await persist_session_state(include_volume_save=True, release_idle_session=True)
        except Exception:
            logger.debug(
                "Failed to persist session state before final event; continuing",
                exc_info=True,
            )
        await lifecycle.complete_run(
            _terminal_run_status(event),
            step=step,
            summary=summary,
        )
        return

    try:
        await persist_session_state(include_volume_save=True, release_idle_session=True)
    except Exception:
        logger.debug(
            "Failed to persist session state after %s event; completing run anyway",
            event.kind,
            exc_info=True,
        )

    error_json: dict[str, Any] | None = {"error": event.text, "kind": event.kind} if event.kind == "error" else None
    await lifecycle.complete_run(
        _terminal_run_status(event),
        step=step,
        error_json=error_json,
        summary=summary,
    )


async def handle_stream_error(
    *,
    websocket: WebSocket | None,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    exc: Exception,
    request_message: str,
) -> None:
    """Log, emit, and persist a failed websocket streaming turn."""
    import time

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
    if websocket is not None:
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


async def _emit_stream_event(
    *,
    websocket: WebSocket | None,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    event: WorkspaceEvent | StreamEventLike,
    orchestration_session: SessionContext | None = None,
    persist_session_state: LocalPersistFn,
    request_message: str,
    execution_emitter: ExecutionEventEmitter,
) -> None:
    lifecycle.raise_if_persistence_error()
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.kind in {"done", "error"}:
        payload = merge_trace_result_metadata(
            payload,
            response_preview=event.text,
            trace_metadata=_runtime_trace_metadata(payload),
        )
        payload = enrich_terminal_stream_payload(
            event=event,
            payload=payload,
            request_message=request_message,
            run_id=lifecycle.run_id,
        )
    event_dict = build_stream_event_dict(
        event=event,
        payload=payload,
        sequence=step_builder._sequence,
        run_id=lifecycle.run_id,
    )
    is_terminal_event = _is_terminal_transport_event(event)
    if websocket is not None:
        await _try_send_json(websocket, {"type": "event", "data": event_dict})

    if isinstance(event, RuntimeEvent):
        step = step_builder.from_runtime_event(event)
    else:
        step = step_builder.from_stream_event(
            kind=event.kind,
            text=event.text,
            payload=payload,
            timestamp=event.timestamp.timestamp(),
        )

    if step is not None:
        if event.kind == "text":
            await lifecycle.emit_step(step)
        else:
            await asyncio.gather(
                lifecycle.emit_step(step),
                lifecycle.persist_step(step),
            )
        lifecycle.raise_if_persistence_error()

    if is_terminal_event:
        await handle_terminal_stream_event(
            websocket=websocket,
            lifecycle=lifecycle,
            event=event,
            event_dict=event_dict,
            step=step,
            orchestration_session=orchestration_session,
            persist_session_state=persist_session_state,
            request_message=request_message,
        )


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
    websocket: WebSocket | None,
    agent: ChatAgentProtocol,
    prepared_turn: PreparedStreamingTurn,
    orchestration_session: SessionContext | None,
    cancel_check: Callable[[], bool],
    lifecycle: ExecutionLifecycleManager,
    hosted_repl_bridge: ReplHookBridge | None,
    step_builder: ExecutionStepBuilder,
    analytics_enabled: bool | None,
    persist_session_state: LocalPersistFn,
    execution_emitter: ExecutionEventEmitter,
) -> None:
    from ...runtime_services.chat_persistence import build_workspace_task_request

    worker_request = build_workspace_task_request(
        agent=agent,
        prepared_turn=prepared_turn,
        cancel_check=cancel_check,
    )

    bridge_started = False
    try:
        if hosted_repl_bridge is not None:
            hosted_repl_bridge.start()
            bridge_started = True

        with runtime_telemetry_enabled_context(analytics_enabled):
            async for worker_event in stream_agent_turn(worker_request):
                await _emit_stream_event(
                    websocket=websocket,
                    lifecycle=lifecycle,
                    step_builder=step_builder,
                    event=worker_event,
                    orchestration_session=orchestration_session,
                    persist_session_state=persist_session_state,
                    request_message=prepared_turn.message,
                    execution_emitter=execution_emitter,
                )
    finally:
        if hosted_repl_bridge is not None and bridge_started:
            try:
                await hosted_repl_bridge.stop()
            except Exception:
                logger.warning("Failed to stop hosted REPL bridge during stream cleanup.", exc_info=True)

    if not lifecycle.run_completed:
        lifecycle.raise_if_persistence_error()
        await lifecycle.complete_run(RunStatus.COMPLETED)


async def run_streaming_turn(
    *,
    websocket: WebSocket | None,
    agent: ChatAgentProtocol,
    prepared_turn: PreparedStreamingTurn,
    orchestration_session: SessionContext | None,
    cancel_check: Callable[[], bool],
    interpreter: object | None,
    persist_session_state: LocalPersistFn,
    execution_emitter: ExecutionEventEmitter,
) -> str | None:
    """Execute one streaming turn, emitting events and persisting lifecycle steps."""
    lifecycle = prepared_turn.lifecycle
    step_builder = prepared_turn.step_builder
    if interpreter is not None and hasattr(lifecycle, "active_run_db_id"):
        setattr(interpreter, "_host_run_id", lifecycle.active_run_db_id)
    await lifecycle.emit_started()
    ws_loop = asyncio.get_running_loop()
    from fleet_rlm.runtime.agent import runtime_helpers as rh
    from fleet_rlm.runtime.agent.turn_progress_relay import TurnProgressRelay

    progress_relay = TurnProgressRelay(loop=ws_loop)
    previous_progress_relay = getattr(agent, "_turn_progress_relay", None)
    if hasattr(agent, "_turn_progress_relay"):
        setattr(agent, "_turn_progress_relay", progress_relay)

    def _turn_step_callback(payload: dict[str, Any]) -> None:
        phase = str(payload.get("phase", "")).strip().lower()
        source = "interpreter" if phase in {"start", "complete", "progress"} else "rlm"
        rh.emit_turn_progress_from_payload(progress_relay, payload, source=source)

    previous_turn_callback = getattr(interpreter, "_turn_step_callback", None) if interpreter is not None else None
    if interpreter is not None:
        setattr(interpreter, "_turn_step_callback", _turn_step_callback)

    repl_hook_bridge = ReplHookBridge(
        ws_loop=ws_loop,
        lifecycle=lifecycle,
        step_builder=step_builder,
        interpreter=interpreter,
        enqueue_nonblocking=enqueue_latest_nonblocking,
        progress_relay=progress_relay,
    )

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
                orchestration_session=orchestration_session,
                cancel_check=cancel_check,
                lifecycle=lifecycle,
                hosted_repl_bridge=repl_hook_bridge,
                step_builder=step_builder,
                analytics_enabled=prepared_turn.analytics_enabled,
                persist_session_state=persist_session_state,
                execution_emitter=execution_emitter,
            )

        await _run_prepared_stream(
            mlflow_trace_context=prepared_turn.mlflow_trace_context,
            stream_body=_stream_body,
        )
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        try:
            await persist_session_state(
                include_volume_save=True,
                allow_volume_session_create=False,
                release_idle_session=True,
            )
        except Exception:
            logger.debug("Failed to persist session state after stream exception", exc_info=True)
        await handle_stream_error(
            websocket=websocket,
            lifecycle=lifecycle,
            step_builder=step_builder,
            exc=exc,
            request_message=prepared_turn.message,
        )
    finally:
        if interpreter is not None:
            setattr(interpreter, "_turn_step_callback", previous_turn_callback)
        if hasattr(agent, "_turn_progress_relay"):
            setattr(agent, "_turn_progress_relay", previous_progress_relay)

    return last_loaded_docs_path


__all__ = [
    "run_streaming_turn",
    "handle_terminal_stream_event",
    "handle_stream_error",
    "_emit_stream_event",
]
