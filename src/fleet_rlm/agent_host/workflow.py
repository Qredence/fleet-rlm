"""Agent Framework workflow that wraps the existing orchestration_app stream."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from uuid import uuid4

from agent_framework import (
    Executor,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowRunResult,
    handler,
)
from typing_extensions import Never

from fleet_rlm.orchestration_app import (
    OrchestrationSessionContext,
    stream_orchestrated_workspace_task,
)
from fleet_rlm.worker import WorkspaceEvent, WorkspaceTaskRequest

_WORKSPACE_HOST_EXECUTOR_ID = "orchestration_app_worker_path"
_HOSTED_TASK_REGISTRY: dict[str, "HostedWorkspaceTaskState"] = {}
_HOSTED_TASK_REGISTRY_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class HostedWorkspaceTaskInput:
    """Serializable workflow input for the outer host."""

    task_id: str


@dataclass(slots=True)
class HostedWorkspaceTaskState:
    """Process-local request state kept outside Agent Framework event copying."""

    request: WorkspaceTaskRequest
    session: OrchestrationSessionContext | None = None
    output_queue: asyncio.Queue[WorkspaceEvent | None] | None = None


def resolve_hosted_workspace_task(task_id: str) -> HostedWorkspaceTaskState:
    """Resolve the process-local task state for one hosted workflow execution."""

    with _HOSTED_TASK_REGISTRY_LOCK:
        task_state = _HOSTED_TASK_REGISTRY.get(task_id)
    if task_state is None:
        raise KeyError(f"Unknown hosted workspace task: {task_id}")
    return task_state


@contextmanager
def register_hosted_workspace_task(
    *,
    request: WorkspaceTaskRequest,
    session: OrchestrationSessionContext | None = None,
    output_queue: asyncio.Queue[WorkspaceEvent | None] | None = None,
):
    """Register non-copyable request state for one Agent Framework workflow run."""

    task_id = f"workspace-task-{uuid4()}"
    with _HOSTED_TASK_REGISTRY_LOCK:
        _HOSTED_TASK_REGISTRY[task_id] = HostedWorkspaceTaskState(
            request=request,
            session=session,
            output_queue=output_queue,
        )
    try:
        yield HostedWorkspaceTaskInput(task_id=task_id)
    finally:
        with _HOSTED_TASK_REGISTRY_LOCK:
            _HOSTED_TASK_REGISTRY.pop(task_id, None)


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
        task_state = resolve_hosted_workspace_task(host_input.task_id)
        async for event in stream_orchestrated_workspace_task(
            request=task_state.request,
            session=task_state.session,
        ):
            if task_state.output_queue is not None:
                await task_state.output_queue.put(event)
            await ctx.yield_output(event)


def build_workspace_host_workflow() -> Workflow:
    """Build the canonical outer workflow host for the websocket execution path."""

    # This builder intentionally creates a fresh Workflow per call instead of
    # caching a module-level instance: Agent Framework runner state is safer
    # when kept request-local, and TestClient/event-loop shutdown was flaky when
    # a shared workflow object crossed loop boundaries.
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


async def run_workspace_host(
    *,
    host_input: HostedWorkspaceTaskInput,
) -> WorkflowRunResult:
    """Run the hosted workflow stream for one websocket execution turn."""

    return await build_workspace_host_workflow().run(
        host_input,
    )
