"""Adapters between Agent Framework workflow events and worker-native events."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_framework import ResponseStream, WorkflowEvent, WorkflowRunResult

from fleet_rlm.worker import WorkspaceEvent


async def iter_workspace_host_outputs(
    stream: ResponseStream[WorkflowEvent, WorkflowRunResult],
) -> AsyncIterator[WorkspaceEvent]:
    """Yield only worker-native workspace events from the hosted workflow stream."""

    async for event in stream:
        if event.type != "output":
            continue
        output = event.data
        if not isinstance(output, WorkspaceEvent):
            raise TypeError(
                "Agent Framework workspace host emitted a non-WorkspaceEvent output"
            )
        yield output
