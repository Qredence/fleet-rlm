"""Public entrypoints for the Agent Framework orchestration host."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
from contextlib import suppress

from fleet_rlm.worker import WorkspaceEvent, WorkspaceTaskRequest

from .adapters import iter_workspace_host_queue
from .repl_bridge import ReplHookBridge
from .sessions import OrchestrationSessionContext
from .workflow import register_hosted_workspace_task, run_workspace_host

logger = logging.getLogger(__name__)


def _is_terminal_host_event(event: WorkspaceEvent) -> bool:
    return bool(getattr(event, "terminal", False)) or event.kind in {
        "final",
        "cancelled",
        "error",
    }


async def stream_hosted_workspace_task(
    *,
    request: WorkspaceTaskRequest,
    session: OrchestrationSessionContext | None = None,
    hosted_repl_bridge: ReplHookBridge | None = None,
) -> AsyncIterator[WorkspaceEvent]:
    """Stream the websocket execution path through the Agent Framework host."""

    output_queue: asyncio.Queue[WorkspaceEvent | None] = asyncio.Queue()

    async def _run_host(host_input) -> None:
        try:
            await run_workspace_host(host_input=host_input)
        finally:
            await output_queue.put(None)

    bridge_started = False
    try:
        if hosted_repl_bridge is not None:
            try:
                await hosted_repl_bridge.start()
            except Exception:
                try:
                    await hosted_repl_bridge.stop()
                except Exception:  # pragma: no cover - defensive cleanup
                    logger.debug("hosted_repl_bridge_cleanup_failed", exc_info=True)
                raise
            bridge_started = True
        with register_hosted_workspace_task(
            request=request,
            session=session,
            output_queue=output_queue,
        ) as host_input:
            host_task = asyncio.create_task(_run_host(host_input))
            try:
                async for event in iter_workspace_host_queue(output_queue):
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
            except Exception:  # pragma: no cover - defensive cleanup
                logger.debug("hosted_repl_bridge_cleanup_failed", exc_info=True)
