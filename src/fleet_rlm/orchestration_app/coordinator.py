"""Reduced compatibility coordinator around the one-task worker boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator

import fleet_rlm.worker as worker_boundary

from fleet_rlm.agent_host.hitl_flow import (
    HitlResolution,
    resolve_hitl_continuation as resolve_agent_host_hitl_continuation,
)
from fleet_rlm.agent_host.sessions import OrchestrationSessionContext


class WorkspaceOrchestrationCoordinator:
    """Preserve the worker seam while migrated policy moves into agent_host."""

    async def stream_workspace_task(
        self,
        *,
        request: worker_boundary.WorkspaceTaskRequest,
        session: OrchestrationSessionContext | None = None,
    ) -> AsyncIterator[worker_boundary.WorkspaceEvent]:
        async for worker_event in worker_boundary.stream_workspace_task(request):
            yield worker_event

    def resolve_hitl_continuation(
        self,
        *,
        command: str,
        args: dict[str, object],
        session: OrchestrationSessionContext | None = None,
    ) -> HitlResolution | None:
        return resolve_agent_host_hitl_continuation(
            command=command,
            args=args,
            session=session,
        )


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
