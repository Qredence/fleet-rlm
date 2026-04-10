"""Public entrypoints for the Agent Framework orchestration host."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fleet_rlm.worker import WorkspaceEvent, WorkspaceTaskRequest

from .adapters import iter_workspace_host_outputs
from .sessions import OrchestrationSessionContext
from .workflow import run_workspace_host


async def stream_hosted_workspace_task(
    *,
    request: WorkspaceTaskRequest,
    session: OrchestrationSessionContext | None = None,
) -> AsyncIterator[WorkspaceEvent]:
    """Stream the websocket execution path through the Agent Framework host."""

    host_stream = run_workspace_host(request=request, session=session)
    async for event in iter_workspace_host_outputs(host_stream):
        yield event
    await host_stream.get_final_response()
