"""Public entrypoints for the workspace orchestration host."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
from contextlib import suppress

from fleet_rlm.worker import WorkspaceEvent, WorkspaceTaskRequest
import fleet_rlm.worker as worker_boundary

from .hitl_flow import checkpoint_hitl_request
from .repl_bridge import ReplHookBridge
from .sessions import OrchestrationSessionContext

logger = logging.getLogger(__name__)


def _is_terminal_host_event(event: WorkspaceEvent) -> bool:
    return bool(getattr(event, "terminal", False)) or event.kind in {
        "final",
        "cancelled",
        "error",
    }


async def _iter_queue(
    queue: asyncio.Queue[WorkspaceEvent | None],
) -> AsyncIterator[WorkspaceEvent]:
    """Yield events from a queue until the None sentinel arrives."""
    while True:
        event = await queue.get()
        if event is None:
            return
        yield event


async def _run_execution_stream(
    *,
    request: WorkspaceTaskRequest,
    session: OrchestrationSessionContext | None,
    output_queue: asyncio.Queue[WorkspaceEvent | None],
) -> None:
    """Stream worker events with HITL checkpointing into the output queue."""
    hitl_seen = False
    success = True
    try:
        async for event in worker_boundary.stream_workspace_task(request):
            event = checkpoint_hitl_request(event=event, session=session)
            if event.kind == "hitl_request":
                hitl_seen = True
            await output_queue.put(event)
    except Exception:
        success = False
        raise
    finally:
        completion = WorkspaceEvent(
            kind="execution_completed",
            text="",
            payload={"success": success, "hitl_seen": hitl_seen},
        )
        await output_queue.put(completion)
        await output_queue.put(None)


async def stream_hosted_workspace_task(
    *,
    request: WorkspaceTaskRequest,
    session: OrchestrationSessionContext | None = None,
    hosted_repl_bridge: ReplHookBridge | None = None,
) -> AsyncIterator[WorkspaceEvent]:
    """Stream the websocket execution path with HITL checkpointing."""

    output_queue: asyncio.Queue[WorkspaceEvent | None] = asyncio.Queue()

    bridge_started = False
    try:
        if hosted_repl_bridge is not None:
            try:
                await hosted_repl_bridge.start()
            except Exception:
                try:
                    await hosted_repl_bridge.stop()
                except Exception:
                    logger.debug("hosted_repl_bridge_cleanup_failed", exc_info=True)
                raise
            bridge_started = True

        host_task = asyncio.create_task(
            _run_execution_stream(
                request=request,
                session=session,
                output_queue=output_queue,
            )
        )
        try:
            async for event in _iter_queue(output_queue):
                yield event
                if _is_terminal_host_event(event):
                    break
        finally:
            if not host_task.done():
                host_task.cancel()
            with suppress(asyncio.CancelledError):
                await host_task
    finally:
        if hosted_repl_bridge is not None and bridge_started:
            try:
                await hosted_repl_bridge.stop()
            except Exception:
                logger.debug("hosted_repl_bridge_cleanup_failed", exc_info=True)
