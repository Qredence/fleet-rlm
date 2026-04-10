"""Streaming entrypoint for the workspace-task worker boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator

from .adapters import build_agent_stream_kwargs, to_workspace_event
from .contracts import WorkspaceEvent, WorkspaceTaskRequest


async def stream_workspace_task(
    request: WorkspaceTaskRequest,
) -> AsyncIterator[WorkspaceEvent]:
    """Stream one workspace task through the canonical runtime agent."""

    if request.execution_mode is not None:
        request.agent.set_execution_mode(request.execution_mode)
    if request.prepare is not None:
        await request.prepare()

    async for runtime_event in request.agent.aiter_chat_turn_stream(
        **build_agent_stream_kwargs(request)
    ):
        yield to_workspace_event(runtime_event)
