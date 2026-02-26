"""Inner streaming loop and REPL hook management for WebSocket chat."""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket

from fleet_rlm.analytics.trace_context import runtime_telemetry_enabled_context
from fleet_rlm.db.models import RunStatus
from fleet_rlm.react.agent import RLMReActChatAgent

from ..execution_events import ExecutionStep, ExecutionStepBuilder
from .ws_helpers import _error_envelope, _sanitize_for_log
from .ws_lifecycle import ExecutionLifecycleManager, _classify_stream_failure

logger = logging.getLogger(__name__)

_REPL_HOOK_STEP_QUEUE_MAX = 128


async def run_streaming_turn(
    *,
    websocket: WebSocket,
    agent: RLMReActChatAgent,
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
) -> str | None:
    """Execute one streaming turn, emitting events and persisting lifecycle steps."""

    await lifecycle.emit_started()
    ws_loop = asyncio.get_running_loop()

    previous_execution_hook = None
    repl_step_queue: asyncio.Queue[ExecutionStep | None] | None = None
    repl_step_worker_task: asyncio.Task[None] | None = None

    async def _emit_and_persist_repl_step(step_data: ExecutionStep) -> None:
        if lifecycle.run_completed:
            return
        try:
            await lifecycle.emit_step(step_data)
            await lifecycle.persist_step(step_data)
        except Exception as exc:  # pragma: no cover - defensive path
            logger.warning(
                "Failed to emit/persist REPL execution step: %s",
                _sanitize_for_log(exc),
            )
            lifecycle._persistence_error = exc

    async def _repl_step_worker() -> None:
        assert repl_step_queue is not None
        while True:
            step_data = await repl_step_queue.get()
            if step_data is None:
                break
            await _emit_and_persist_repl_step(step_data)

    def _queue_repl_step(step_data: ExecutionStep) -> None:
        if repl_step_queue is None or lifecycle.run_completed:
            return
        if not _enqueue_latest_nonblocking(repl_step_queue, step_data):
            logger.debug("Dropped REPL execution step due to queue contention")

    def _interpreter_hook(payload: dict[str, Any]) -> None:
        if lifecycle.run_completed:
            return
        repl_step = step_builder.from_interpreter_hook(payload)
        if repl_step is None:
            return
        ws_loop.call_soon_threadsafe(
            lambda step_data=repl_step: _queue_repl_step(step_data)
        )

    repl_step_queue = asyncio.Queue(maxsize=_REPL_HOOK_STEP_QUEUE_MAX)
    repl_step_worker_task = asyncio.create_task(_repl_step_worker())

    if interpreter is not None:
        previous_execution_hook = getattr(interpreter, "execution_event_callback", None)
        interpreter.execution_event_callback = _interpreter_hook

    if _should_reload_docs_path(last_loaded_docs_path, docs_path):
        agent.load_document(str(docs_path))
        last_loaded_docs_path = str(docs_path).strip()

    try:
        with runtime_telemetry_enabled_context(analytics_enabled):
            async for event in agent.aiter_chat_turn_stream(
                message=message,
                trace=trace,
                cancel_check=cancel_check,
            ):
                lifecycle.raise_if_persistence_error()
                event_dict = {
                    "kind": event.kind,
                    "text": event.text,
                    "payload": event.payload,
                    "timestamp": event.timestamp.isoformat(),
                    "version": 2,
                    "event_id": str(uuid.uuid4()),
                }
                is_terminal_event = event.kind in {
                    "final",
                    "cancelled",
                    "error",
                }
                if not is_terminal_event:
                    await websocket.send_json({"type": "event", "data": event_dict})

                step = step_builder.from_stream_event(
                    kind=event.kind,
                    text=event.text,
                    payload=event.payload,
                    timestamp=event.timestamp.timestamp(),
                )
                if step is not None:
                    await lifecycle.emit_step(step)
                    await lifecycle.persist_step(step)
                    lifecycle.raise_if_persistence_error()

                if event.kind == "final":
                    await persist_session_state(include_volume_save=True)
                    await lifecycle.complete_run(RunStatus.COMPLETED, step=step)
                    await websocket.send_json({"type": "event", "data": event_dict})
                elif event.kind in {"cancelled", "error"}:
                    status = (
                        RunStatus.CANCELLED
                        if event.kind == "cancelled"
                        else RunStatus.FAILED
                    )
                    error_json = (
                        {"error": event.text, "kind": event.kind}
                        if event.kind == "error"
                        else None
                    )
                    await lifecycle.complete_run(
                        status, step=step, error_json=error_json
                    )
                    await websocket.send_json({"type": "event", "data": event_dict})

        if not lifecycle.run_completed:
            lifecycle.raise_if_persistence_error()
            await lifecycle.complete_run(RunStatus.COMPLETED)

    except Exception as exc:
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
        await websocket.send_json(
            _error_envelope(
                code=error_code,
                message=f"Streaming error: {exc}",
                details={"error_type": type(exc).__name__},
            )
        )
        if not lifecycle.run_completed:
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
    finally:
        if interpreter is not None:
            interpreter.execution_event_callback = previous_execution_hook
        if repl_step_queue is not None:
            await repl_step_queue.put(None)
        if repl_step_worker_task is not None:
            try:
                await repl_step_worker_task
            except asyncio.CancelledError:
                pass

    return last_loaded_docs_path


def _should_reload_docs_path(last_docs_path: str | None, docs_path: str | None) -> bool:
    """Return True when a docs path is provided and differs from the last loaded path."""
    candidate = (docs_path or "").strip()
    if not candidate:
        return False
    return candidate != (last_docs_path or "")


def _enqueue_latest_nonblocking(queue: asyncio.Queue, item: object) -> bool:
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
