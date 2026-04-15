"""Agent Framework workflow that owns hosted orchestration around the worker seam."""

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

from .hitl_flow import checkpoint_hitl_request
from .sessions import OrchestrationSessionContext
import fleet_rlm.worker as worker_boundary

_WORKSPACE_HOST_EXECUTOR_ID = "orchestration_app_worker_path"
_WORKSPACE_HOST_MONITORING_EXECUTOR_ID = "orchestration_app_monitoring"
_HOSTED_TASK_REGISTRY: dict[str, "HostedWorkspaceTaskState"] = {}
_HOSTED_TASK_REGISTRY_LOCK = Lock()

# State key used to communicate whether a HITL event was seen during execution.
_STATE_KEY_HITL_SEEN = "hitl_seen"


@dataclass(frozen=True, slots=True)
class HostedWorkspaceTaskInput:
    """Serializable workflow input for the outer host."""

    task_id: str


@dataclass(slots=True)
class HostedWorkspaceTaskState:
    """Process-local request state kept outside Agent Framework event copying."""

    request: worker_boundary.WorkspaceTaskRequest
    session: OrchestrationSessionContext | None = None
    output_queue: asyncio.Queue[worker_boundary.WorkspaceEvent | None] | None = None


@dataclass(frozen=True, slots=True)
class ExecutionCompletedSignal:
    """Internal signal sent from ExecutionExecutor to MonitoringExecutor on completion."""

    success: bool


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
    request: worker_boundary.WorkspaceTaskRequest,
    session: OrchestrationSessionContext | None = None,
    output_queue: asyncio.Queue[worker_boundary.WorkspaceEvent | None] | None = None,
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


class ExecutionExecutor(Executor):
    """Agent Framework executor that applies hosted HITL policy around worker flow.

    This executor owns the core execution path: it streams worker events, applies
    HITL checkpointing, forwards events to the output queue, and yields them via the
    Agent Framework context.  After the stream finishes it records whether a HITL
    event was seen in shared workflow state and forwards an ``ExecutionCompletedSignal``
    to ``MonitoringExecutor``.
    """

    @handler(
        input=HostedWorkspaceTaskInput,
        output=ExecutionCompletedSignal,
        workflow_output=worker_boundary.WorkspaceEvent,
    )
    async def run(
        self,
        host_input: HostedWorkspaceTaskInput,
        ctx: WorkflowContext[ExecutionCompletedSignal, worker_boundary.WorkspaceEvent],
    ) -> None:
        task_state = resolve_hosted_workspace_task(host_input.task_id)
        hitl_seen = False
        success = True
        try:
            async for event in worker_boundary.stream_workspace_task(
                task_state.request,
            ):
                event = checkpoint_hitl_request(event=event, session=task_state.session)
                if event.kind == "hitl_request":
                    hitl_seen = True
                if task_state.output_queue is not None:
                    await task_state.output_queue.put(event)
                await ctx.yield_output(event)
        except Exception:
            success = False
            raise
        finally:
            ctx.set_state(_STATE_KEY_HITL_SEEN, hitl_seen)
            await ctx.send_message(ExecutionCompletedSignal(success=success))


class MonitoringExecutor(Executor):
    """Agent Framework executor that emits structured monitoring bookend events.

    Receives an ``ExecutionCompletedSignal`` from ``ExecutionExecutor`` and emits
    an ``execution_completed`` ``WorkspaceEvent`` into the Agent Framework output
    stream.  The ``hitl_seen`` flag is read from shared workflow state so downstream
    consumers can inspect it via the AF run result.
    """

    @handler(
        input=ExecutionCompletedSignal,
        workflow_output=worker_boundary.WorkspaceEvent,
    )
    async def on_completed(
        self,
        signal: ExecutionCompletedSignal,
        ctx: WorkflowContext[Never, worker_boundary.WorkspaceEvent],
    ) -> None:
        hitl_seen: bool = ctx.get_state(_STATE_KEY_HITL_SEEN, False)
        completed_event = worker_boundary.WorkspaceEvent(
            kind="execution_completed",
            text="",
            payload={
                "success": signal.success,
                "hitl_seen": hitl_seen,
            },
        )
        await ctx.yield_output(completed_event)


def build_workspace_host_workflow() -> Workflow:
    """Build the canonical outer workflow host for the websocket execution path."""

    # This builder intentionally creates a fresh Workflow per call instead of
    # caching a module-level instance: Agent Framework runner state is safer
    # when kept request-local, and TestClient/event-loop shutdown was flaky when
    # a shared workflow object crossed loop boundaries.
    execution_executor = ExecutionExecutor(id=_WORKSPACE_HOST_EXECUTOR_ID)
    monitoring_executor = MonitoringExecutor(id=_WORKSPACE_HOST_MONITORING_EXECUTOR_ID)
    return (
        WorkflowBuilder(
            name="workspace-orchestration-host",
            description=(
                "Microsoft Agent Framework host around the "
                "fleet_rlm.worker execution seam, including hosted HITL policy."
            ),
            start_executor=execution_executor,
            output_executors=[execution_executor, monitoring_executor],
        )
        .add_edge(execution_executor, monitoring_executor)
        .build()
    )


async def run_workspace_host(
    *,
    host_input: HostedWorkspaceTaskInput,
) -> WorkflowRunResult:
    """Run the hosted workflow stream for one websocket execution turn."""

    return await build_workspace_host_workflow().run(
        host_input,
    )
