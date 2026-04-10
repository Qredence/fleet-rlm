"""Agent Framework workflow that wraps the existing orchestration_app stream."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Never

from agent_framework import (
    Executor,
    ResponseStream,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowEvent,
    WorkflowRunResult,
    handler,
)

from fleet_rlm.orchestration_app import (
    OrchestrationSessionContext,
    stream_orchestrated_workspace_task,
)
from fleet_rlm.worker import WorkspaceEvent, WorkspaceTaskRequest

_WORKSPACE_HOST_EXECUTOR_ID = "orchestration_app_worker_path"


@dataclass(frozen=True, slots=True)
class HostedWorkspaceTaskInput:
    """Host input preserving the existing worker request and session context."""

    request: WorkspaceTaskRequest
    session: OrchestrationSessionContext | None = None


class OrchestrationAppWorkflowExecutor(Executor):
    """Thin Agent Framework executor that delegates to orchestration_app."""

    # TODO(agent-framework): move more continuation policy from
    # orchestration_app into Agent Framework after this outer-host seam is proven.

    @handler
    async def run(
        self,
        host_input: HostedWorkspaceTaskInput,
        ctx: WorkflowContext[Never, WorkspaceEvent],
    ) -> None:
        async for event in stream_orchestrated_workspace_task(
            request=host_input.request,
            session=host_input.session,
        ):
            await ctx.yield_output(event)


@lru_cache(maxsize=1)
def build_workspace_host_workflow() -> Workflow:
    """Build the canonical outer workflow host for the websocket execution path."""

    executor = OrchestrationAppWorkflowExecutor(id=_WORKSPACE_HOST_EXECUTOR_ID)
    return WorkflowBuilder(
        name="workspace-orchestration-host",
        description=(
            "Thin Microsoft Agent Framework host around orchestration_app and the "
            "fleet_rlm.worker execution seam."
        ),
        start_executor=executor,
        output_executors=[executor],
    ).build()


def run_workspace_host(
    *,
    request: WorkspaceTaskRequest,
    session: OrchestrationSessionContext | None = None,
) -> ResponseStream[WorkflowEvent, WorkflowRunResult]:
    """Run the hosted workflow stream for one websocket execution turn."""

    return build_workspace_host_workflow().run(
        HostedWorkspaceTaskInput(request=request, session=session),
        stream=True,
    )
