"""Inner streaming loop and REPL hook management for WebSocket chat."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.analytics import (
    MlflowTraceRequestContext,
    merge_trace_result_metadata,
    mlflow_request_context,
)
from fleet_rlm.analytics.trace_context import runtime_telemetry_enabled_context
from fleet_rlm.db.models import RunStatus
from ...execution import ExecutionStepBuilder
from .helpers import _error_envelope, _sanitize_for_log, _try_send_json
from .lifecycle import ExecutionLifecycleManager, _classify_stream_failure
from .repl_hook import ReplHookBridge

logger = logging.getLogger(__name__)


async def run_streaming_turn(
    *,
    websocket: WebSocket,
    agent: Any,
    message: str,
    docs_path: str | None,
    trace: bool,
    cancel_check: Callable[[], bool],
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    interpreter: Any,
    last_loaded_docs_path: str | None,
    analytics_enabled: bool | None,
    persist_session_state: Callable[..., Awaitable[None]],
    mlflow_trace_context: MlflowTraceRequestContext | None = None,
    agent_stream_kwargs: dict[str, Any] | None = None,
) -> str | None:
    """Execute one streaming turn, emitting events and persisting lifecycle steps."""

    await lifecycle.emit_started()
    ws_loop = asyncio.get_running_loop()
    repl_hook_bridge = ReplHookBridge(
        ws_loop=ws_loop,
        lifecycle=lifecycle,
        step_builder=step_builder,
        interpreter=interpreter,
        enqueue_nonblocking=_enqueue_latest_nonblocking,
    )
    await repl_hook_bridge.start()

    if _should_reload_docs_path(last_loaded_docs_path, docs_path):
        agent.load_document(str(docs_path))
        last_loaded_docs_path = str(docs_path).strip()

    try:
        if mlflow_trace_context is None:
            await _stream_agent_events(
                websocket=websocket,
                agent=agent,
                message=message,
                docs_path=docs_path,
                trace=trace,
                cancel_check=cancel_check,
                lifecycle=lifecycle,
                step_builder=step_builder,
                analytics_enabled=analytics_enabled,
                persist_session_state=persist_session_state,
                agent_stream_kwargs=agent_stream_kwargs,
            )
        else:
            with mlflow_request_context(mlflow_trace_context):
                await _stream_agent_events(
                    websocket=websocket,
                    agent=agent,
                    message=message,
                    docs_path=docs_path,
                    trace=trace,
                    cancel_check=cancel_check,
                    lifecycle=lifecycle,
                    step_builder=step_builder,
                    analytics_enabled=analytics_enabled,
                    persist_session_state=persist_session_state,
                    agent_stream_kwargs=agent_stream_kwargs,
                )
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        await _handle_stream_error(
            websocket=websocket,
            lifecycle=lifecycle,
            step_builder=step_builder,
            exc=exc,
        )
    finally:
        await repl_hook_bridge.stop()

    return last_loaded_docs_path


async def _stream_agent_events(
    *,
    websocket: WebSocket,
    agent: Any,
    message: str,
    docs_path: str | None,
    trace: bool,
    cancel_check: Callable[[], bool],
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    analytics_enabled: bool | None,
    persist_session_state: Callable[..., Awaitable[None]],
    agent_stream_kwargs: dict[str, Any] | None = None,
) -> None:
    with runtime_telemetry_enabled_context(analytics_enabled):
        async for event in agent.aiter_chat_turn_stream(
            message=message,
            trace=trace,
            cancel_check=cancel_check,
            docs_path=docs_path,
            **(agent_stream_kwargs or {}),
        ):
            await _emit_stream_event(
                websocket=websocket,
                lifecycle=lifecycle,
                step_builder=step_builder,
                event=event,
                persist_session_state=persist_session_state,
            )

    if not lifecycle.run_completed:
        lifecycle.raise_if_persistence_error()
        await lifecycle.complete_run(RunStatus.COMPLETED)


async def _emit_stream_event(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    event: Any,
    persist_session_state: Callable[..., Awaitable[None]],
) -> None:
    lifecycle.raise_if_persistence_error()
    payload = event.payload
    if event.kind == "final":
        payload = merge_trace_result_metadata(
            payload if isinstance(payload, dict) else None,
            response_preview=event.text,
        )
    event_dict = {
        "kind": event.kind,
        "text": event.text,
        "payload": payload,
        "timestamp": event.timestamp.isoformat(),
        "version": 2,
        "event_id": str(uuid.uuid4()),
    }
    is_terminal_event = event.kind in {"final", "cancelled", "error"}
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

    if event.kind == "final":
        await persist_session_state(include_volume_save=True)
        await lifecycle.complete_run(RunStatus.COMPLETED, step=step)
        if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
            raise WebSocketDisconnect(code=1001)
        return

    if event.kind in {"cancelled", "error"}:
        # Surface terminal Daytona/chat failures to the client before any
        # slower lifecycle bookkeeping so the UI does not sit in a spinner
        # when persistence or completion hooks stall.
        if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
            raise WebSocketDisconnect(code=1001)
        await persist_session_state(include_volume_save=True)
        status = RunStatus.CANCELLED if event.kind == "cancelled" else RunStatus.FAILED
        error_json = (
            {"error": event.text, "kind": event.kind} if event.kind == "error" else None
        )
        await lifecycle.complete_run(status, step=step, error_json=error_json)


async def _handle_stream_error(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    exc: Exception,
) -> None:
    error_code = _classify_stream_failure(exc)
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

    error_step = step_builder.from_stream_event(
        kind="error",
        text=f"Streaming error: {exc}",
        payload={
            "error_type": type(exc).__name__,
            "error_code": error_code,
        },
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
    )


def _should_reload_docs_path(last_docs_path: str | None, docs_path: str | None) -> bool:
    """Return True when a docs path is provided and differs from the last loaded path."""
    candidate = (docs_path or "").strip()
    if not candidate:
        return False
    return candidate != (last_docs_path or "")


def _enqueue_latest_nonblocking(
    queue: asyncio.Queue[Any],
    item: Any,
) -> bool:
    """Enqueue without blocking, dropping the oldest item when the queue is full."""
    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        pass

    try:
        _ = queue.get_nowait()
    except asyncio.QueueEmpty:
        return False

    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        return False
