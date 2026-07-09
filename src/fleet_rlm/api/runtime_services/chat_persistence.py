"""WebSocket chat run/session persistence orchestration for runtime services.

Owns: startup-status events, task-control helpers, loop-exit handler,
      and worker-request builder.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

from fastapi import WebSocketDisconnect

from fleet_rlm.api.runtime_services.run_lifecycle import ExecutionLifecycleManager
from fleet_rlm.api.runtime_services.stream_failures import PersistenceRequiredError
from fleet_rlm.files.schemas import AttachedFiles
from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventContext, RuntimeEventKind
from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Startup status
# ---------------------------------------------------------------------------

EmitStartupEvent = Callable[[Any], Awaitable[None]]


def build_startup_status_event() -> RuntimeEvent:
    """Return the canonical delayed startup status event."""
    return RuntimeEvent(
        kind=RuntimeEventKind.STATUS,
        text="Preparing Daytona workspace...",
        payload={"phase": "startup"},
        context=RuntimeEventContext(runtime_mode="daytona_pilot"),
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
    local_persist: Callable[..., Awaitable[None]],
    lifecycle: ExecutionLifecycleManager | None,
    cancel_active_run: bool = True,
    persist_on_disconnect: bool = True,
) -> None:
    """Cleanly stop the active websocket loop after a client disconnect."""
    if cancel_active_run:
        cancel_flag["cancelled"] = True
    await cancel_task(pending_receive_task)
    if cancel_active_run:
        await cancel_task(stream_task)
    elif stream_task is not None:
        stream_task.add_done_callback(_log_background_disconnect_task_result)
    if not persist_on_disconnect:
        return
    try:
        await local_persist(
            include_volume_save=True,
            allow_volume_session_create=False,
            release_idle_session=True,
        )
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


def _log_background_disconnect_task_result(task: asyncio.Task[Any]) -> None:
    """Consume detached execution task failures after the command socket closes."""
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.warning("Background execution failed after websocket disconnect", exc_info=True)


# ---------------------------------------------------------------------------
# Worker request
# ---------------------------------------------------------------------------


def build_workspace_task_request(
    *,
    agent: Any,
    prepared_turn: Any,
    cancel_check: Callable[[], bool],
    prepared_runtime: Any | None = None,
    identity: Any | None = None,
    session_id: str | None = None,
    canonical_workspace_id: str | None = None,
    canonical_user_id: str | None = None,
    owner_tenant_claim: str | None = None,
    owner_user_claim: str | None = None,
    cancel_flag: dict[str, bool] | None = None,
    selected_skill_ids: list[str] | None = None,
    trace_mode: str | None = None,
    attached_files: AttachedFiles | None = None,
) -> Any:
    """Build the worker request for one websocket message turn.

    When *prepared_runtime* is provided, the worker request includes
    transport-neutral context fields so ``stream_agent_turn`` can build a
    ``ChatExecutionContext`` and delegate to ``stream_turn()``.
    """
    return SimpleNamespace(
        agent=agent,
        message=prepared_turn.message,
        execution_mode=prepared_turn.execution_mode,
        trace=prepared_turn.trace,
        docs_path=prepared_turn.docs_path,
        repo_url=prepared_turn.repo_url,
        repo_ref=prepared_turn.repo_ref,
        context_paths=(list(prepared_turn.context_paths) if prepared_turn.context_paths is not None else None),
        batch_concurrency=prepared_turn.batch_concurrency,
        workspace_id=prepared_turn.workspace_id,
        cancel_check=cancel_check,
        prepare=prepared_turn.prepare_worker,
        # Transport-neutral context (set when refactored path is active)
        prepared_runtime=prepared_runtime,
        identity=identity,
        session_id=session_id,
        canonical_workspace_id=canonical_workspace_id,
        canonical_user_id=canonical_user_id,
        owner_tenant_claim=owner_tenant_claim,
        owner_user_claim=owner_user_claim,
        cancel_flag=cancel_flag,
        selected_skill_ids=selected_skill_ids,
        trace_mode=trace_mode,
        attached_files=attached_files,
    )


__all__ = [
    "build_startup_status_event",
    "emit_delayed_startup_status",
    "cancel_startup_status_task",
    "should_reload_docs_path",
    "enqueue_latest_nonblocking",
    "cancel_task",
    "handle_chat_disconnect",
    "build_workspace_task_request",
]
