"""Minimal outer coordinator that wraps the one-task worker boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator

import fleet_rlm.worker as worker_boundary

from .hitl_flow import (
    HitlResolution,
    checkpoint_hitl_request,
    finalize_hitl_state_for_terminal_event,
    resolve_hitl_command,
)
from .sessions import OrchestrationSessionContext


class WorkspaceOrchestrationCoordinator:
    """Own narrowly-scoped continuation policy around worker execution."""

    async def stream_workspace_task(
        self,
        *,
        request: worker_boundary.WorkspaceTaskRequest,
        session: OrchestrationSessionContext | None = None,
    ) -> AsyncIterator[worker_boundary.WorkspaceEvent]:
        async for worker_event in worker_boundary.stream_workspace_task(request):
            event = checkpoint_hitl_request(event=worker_event, session=session)
            finalize_hitl_state_for_terminal_event(event=event, session=session)
            yield event

    def resolve_hitl_continuation(
        self,
        *,
        command: str,
        args: dict[str, object],
        session: OrchestrationSessionContext | None = None,
    ) -> HitlResolution | None:
        return resolve_hitl_command(command=command, args=args, session=session)


_COORDINATOR = WorkspaceOrchestrationCoordinator()


async def stream_orchestrated_workspace_task(
    *,
    request: worker_boundary.WorkspaceTaskRequest,
    session: OrchestrationSessionContext | None = None,
) -> AsyncIterator[worker_boundary.WorkspaceEvent]:
    async for event in _COORDINATOR.stream_workspace_task(
        request=request, session=session
    ):
        yield event


def resolve_hitl_continuation(
    *,
    command: str,
    args: dict[str, object],
    session: OrchestrationSessionContext | None = None,
) -> HitlResolution | None:
    return _COORDINATOR.resolve_hitl_continuation(
        command=command,
        args=args,
        session=session,
    )
