"""WebSocket lifecycle helpers.

Consolidates startup status, task control, loop exit, worker requests,
execution-event support, and failure classification into one module.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.integrations.database import RunStatus, RunStepType
from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log

from ...dependencies import ConfigDeps, DiagnosticsDeps
from ...events import (
    ExecutionEvent,
    ExecutionEventEmitter,
    ExecutionEventType,
    ExecutionStep,
)
from ...runtime_services.chat_persistence import ExecutionLifecycleManager
from .transport import _error_envelope, _try_send_json
from .turn_setup import PreparedStreamingTurn
from .types import (
    ChatAgentProtocol,
    LocalPersistFn,
    WorkspaceEvent,
    WorkspaceTaskRequest,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


class PersistenceRequiredError(RuntimeError):
    """Raised when durable writes fail in strict-persistence mode."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def classify_stream_failure(exc: Exception) -> str:
    """Map runtime failures to stable websocket-facing error codes."""
    if isinstance(exc, PersistenceRequiredError):
        return exc.code

    lowered = str(exc).lower()
    if "planner lm not configured" in lowered:
        return "planner_missing"
    if "llm call timed out" in lowered or "timed out" in lowered and "llm" in lowered:
        return "llm_timeout"
    if "rate limit" in lowered or "429" in lowered:
        return "llm_rate_limited"
    if "sandbox" in lowered or "daytona" in lowered:
        return "sandbox_unavailable"
    return "internal_error"


def chat_startup_error_payload(exc: Exception) -> dict[str, object]:
    """Build a stable websocket error envelope for startup failures."""
    error_code = classify_stream_failure(exc)
    message = f"Server error: {str(exc)}"
    return _error_envelope(
        code=error_code,
        message=message,
        details={"error_type": type(exc).__name__},
    )


# ---------------------------------------------------------------------------
# Execution-event support
# ---------------------------------------------------------------------------

EXECUTION_TO_RUN_STEP_TYPE: dict[str, RunStepType] = {
    "llm": RunStepType.LLM_CALL,
    "tool": RunStepType.TOOL_CALL,
    "repl": RunStepType.REPL_EXEC,
    "memory": RunStepType.MEMORY,
    "output": RunStepType.OUTPUT,
}


def build_execution_event(
    *,
    event_type: ExecutionEventType,
    run_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    step: ExecutionStep | None = None,
    summary: dict[str, Any] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        type=event_type,
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        step=step,
        summary=summary,
    )


def get_execution_emitter(diagnostics: DiagnosticsDeps) -> ExecutionEventEmitter:
    emitter = diagnostics.events_event_emitter
    if emitter is not None:
        return emitter
    return emitter


def get_execution_emitter_with_config(
    diagnostics: DiagnosticsDeps, config_deps: ConfigDeps
) -> ExecutionEventEmitter:
    emitter = diagnostics.events_event_emitter
    if emitter is not None:
        return emitter

    cfg = config_deps.config
    emitter = ExecutionEventEmitter(
        max_queue=cfg.ws_execution_max_queue,
        drop_policy=cfg.ws_execution_drop_policy,
    )
    diagnostics.events_event_emitter = emitter
    return emitter


def map_execution_step_type(step_type: str) -> RunStepType:
    return EXECUTION_TO_RUN_STEP_TYPE.get(step_type, RunStepType.STATUS)


# ---------------------------------------------------------------------------
# Startup status
# ---------------------------------------------------------------------------

EmitStartupEvent = Callable[[WorkspaceEvent], Awaitable[None]]


def build_startup_status_event() -> WorkspaceEvent:
    """Return the canonical delayed startup status event."""
    return WorkspaceEvent(
        kind="status",
        text="Preparing Daytona workspace...",
        payload={
            "phase": "startup",
            "runtime": {"runtime_mode": "daytona_pilot"},
        },
        timestamp=datetime.now(timezone.utc),
    )


async def emit_delayed_startup_status(
    *,
    delay_seconds: float,
    emit_event: EmitStartupEvent,
) -> None:
    """Emit the startup-status event after the configured first-frame delay."""
    await asyncio.sleep(delay_seconds)
    await emit_event(build_startup_status_event())


async def cancel_startup_status_task(task: asyncio.Task[None] | None) -> None:
    """Cancel the delayed startup task when startup completes first."""
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        _ = await task


# ---------------------------------------------------------------------------
# Task control
# ---------------------------------------------------------------------------


def should_reload_docs_path(last_docs_path: str | None, docs_path: str | None) -> bool:
    """Return True when a docs path is provided and differs from the last loaded path."""
    candidate = (docs_path or "").strip()
    if not candidate:
        return False
    return candidate != (last_docs_path or "")


def enqueue_latest_nonblocking(
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


def cancelled_event_payload(message: str = "Request cancelled.") -> dict[str, Any]:
    """Build the websocket event payload for cancellation notifications."""
    return {
        "type": "event",
        "data": {
            "kind": "done",
            "text": message,
            "payload": {"cancelled": True},
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "version": 2,
            "event_id": str(uuid.uuid4()),
        },
    }


async def cancel_task(task: asyncio.Task[object] | None) -> None:
    """Cancel an in-flight task and swallow expected shutdown exceptions."""
    if task is None or task.done():
        return

    task.cancel()
    outcomes = await asyncio.gather(task, return_exceptions=True)
    if not outcomes:
        return

    outcome = outcomes[0]
    if isinstance(outcome, (asyncio.CancelledError, WebSocketDisconnect)):
        return
    if isinstance(outcome, BaseException):
        raise outcome


# ---------------------------------------------------------------------------
# Loop exit
# ---------------------------------------------------------------------------


async def handle_chat_disconnect(
    *,
    pending_receive_task: asyncio.Task[object] | None,
    stream_task: asyncio.Task[str | None] | None,
    cancel_flag: dict[str, bool],
    local_persist: LocalPersistFn,
    lifecycle: ExecutionLifecycleManager | None,
) -> None:
    """Cleanly stop the active websocket loop after a client disconnect."""
    cancel_flag["cancelled"] = True
    await cancel_task(pending_receive_task)
    await cancel_task(stream_task)
    try:
        await local_persist(include_volume_save=True)
    except PersistenceRequiredError as exc:
        logger.warning(
            "Session persistence failed during disconnect: %s",
            _sanitize_for_log(exc),
        )
        if lifecycle is not None and not lifecycle.run_completed:
            await lifecycle.complete_run(
                RunStatus.FAILED,
                error_json={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "code": exc.code,
                },
            )
        return

    if lifecycle is not None:
        await lifecycle.complete_run(RunStatus.CANCELLED)


async def handle_chat_loop_exception(
    *,
    websocket: WebSocket,
    exc: Exception,
    pending_receive_task: asyncio.Task[object] | None,
    stream_task: asyncio.Task[str | None] | None,
    local_persist: LocalPersistFn,
    lifecycle: ExecutionLifecycleManager | None,
) -> None:
    """Handle an unexpected outer-loop failure without losing client notification."""
    await cancel_task(pending_receive_task)
    await cancel_task(stream_task)
    error_code = classify_stream_failure(exc)
    await _try_send_json(
        websocket,
        _error_envelope(
            code=error_code,
            message=f"Server error: {str(exc)}",
            details={"error_type": type(exc).__name__},
        ),
    )
    try:
        await local_persist(include_volume_save=True)
    except PersistenceRequiredError as persist_exc:
        logger.warning(
            "Session persistence failed after stream error: %s",
            _sanitize_for_log(persist_exc),
        )

    if lifecycle is not None:
        await lifecycle.complete_run(
            RunStatus.FAILED,
            error_json={
                "error": str(exc),
                "error_type": type(exc).__name__,
                "code": error_code,
            },
        )


# ---------------------------------------------------------------------------
# Worker request
# ---------------------------------------------------------------------------


def build_workspace_task_request(
    *,
    agent: ChatAgentProtocol,
    prepared_turn: PreparedStreamingTurn,
    cancel_check: Callable[[], bool],
) -> WorkspaceTaskRequest:
    """Build the worker request for one websocket message turn."""
    return WorkspaceTaskRequest(
        agent=agent,
        message=prepared_turn.message,
        execution_mode=prepared_turn.execution_mode,
        trace=prepared_turn.trace,
        docs_path=prepared_turn.docs_path,
        repo_url=prepared_turn.repo_url,
        repo_ref=prepared_turn.repo_ref,
        context_paths=(
            list(prepared_turn.context_paths)
            if prepared_turn.context_paths is not None
            else None
        ),
        batch_concurrency=prepared_turn.batch_concurrency,
        workspace_id=prepared_turn.workspace_id,
        cancel_check=cancel_check,
        prepare=prepared_turn.prepare_worker,
    )
