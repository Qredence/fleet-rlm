"""Non-streaming runner entrypoint for the workspace-task worker boundary."""

from __future__ import annotations

from .adapters import status_from_terminal_kind
from .contracts import WorkspaceEvent, WorkspaceTaskRequest, WorkspaceTaskResult
from .streaming import stream_workspace_task


async def run_workspace_task(request: WorkspaceTaskRequest) -> WorkspaceTaskResult:
    """Execute one workspace task and collect a terminal result."""

    events: list[WorkspaceEvent] = []
    terminal_event: WorkspaceEvent | None = None

    async for event in stream_workspace_task(request):
        events.append(event)
        if event.terminal:
            terminal_event = event
            break

    if terminal_event is None:
        raise RuntimeError("Workspace task stream ended without a terminal event")

    return WorkspaceTaskResult(
        status=status_from_terminal_kind(terminal_event.kind),
        terminal_event=terminal_event,
        output_text=terminal_event.text,
        payload=dict(terminal_event.payload or {}),
        events=events,
    )
