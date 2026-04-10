from __future__ import annotations

import asyncio

from agent_framework import Workflow

from fleet_rlm.agent_host.app import stream_hosted_workspace_task
from fleet_rlm.agent_host.workflow import build_workspace_host_workflow
from fleet_rlm.orchestration_app.sessions import OrchestrationSessionContext
from fleet_rlm.worker import WorkspaceEvent, WorkspaceTaskRequest


class _AgentStub:
    def set_execution_mode(self, execution_mode: str) -> None:
        _ = execution_mode

    async def aiter_chat_turn_stream(self, *args, **kwargs):
        _ = (args, kwargs)
        return None


def test_build_workspace_host_workflow_returns_agent_framework_workflow() -> None:
    assert isinstance(build_workspace_host_workflow(), Workflow)


def test_stream_hosted_workspace_task_preserves_orchestration_and_worker_boundary(
    monkeypatch,
) -> None:
    request = WorkspaceTaskRequest(agent=_AgentStub(), message="approve this")
    session_record = {"manifest": {"metadata": {}}}
    session = OrchestrationSessionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        session_id="session-1",
        session_record=session_record,
    )
    calls: list[WorkspaceTaskRequest] = []

    async def _fake_stream_workspace_task(stream_request: WorkspaceTaskRequest):
        calls.append(stream_request)
        yield WorkspaceEvent(
            kind="hitl_request",
            text="Approve deployment?",
            payload={
                "question": "Approve deployment?",
                "actions": [{"label": "Approve"}, {"label": "Reject"}],
            },
        )
        yield WorkspaceEvent(kind="final", text="done", payload={}, terminal=True)

    monkeypatch.setattr(
        "fleet_rlm.orchestration_app.coordinator.worker_boundary.stream_workspace_task",
        _fake_stream_workspace_task,
    )

    async def _collect() -> list[WorkspaceEvent]:
        return [
            event
            async for event in stream_hosted_workspace_task(
                request=request,
                session=session,
            )
        ]

    events = asyncio.run(_collect())

    assert calls == [request]
    assert events[0].kind == "hitl_request"
    assert events[1].kind == "final"
    assert isinstance(events[0].payload["message_id"], str)
    assert (
        session_record["orchestration"]["workflow_stage"] == "awaiting_hitl_resolution"
    )
