from __future__ import annotations

import asyncio

from fleet_rlm.orchestration_app.coordinator import (
    resolve_hitl_continuation,
    stream_orchestrated_workspace_task,
)
from fleet_rlm.orchestration_app.sessions import OrchestrationSessionContext
from fleet_rlm.worker import WorkspaceEvent, WorkspaceTaskRequest


class _AgentStub:
    def set_execution_mode(self, execution_mode: str) -> None:
        _ = execution_mode

    async def aiter_chat_turn_stream(self, *args, **kwargs):
        _ = (args, kwargs)
        return None


def test_coordinator_checkpoints_hitl_events(monkeypatch) -> None:
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
            async for event in stream_orchestrated_workspace_task(
                request=request,
                session=session,
            )
        ]

    events = asyncio.run(_collect())

    assert calls == [request]
    assert events[0].kind == "hitl_request"
    assert isinstance(events[0].payload["message_id"], str)
    assert (
        session_record["orchestration"]["workflow_stage"] == "awaiting_hitl_resolution"
    )
    assert (
        session_record["orchestration"]["continuation"]["continuation_token"]
        == session_record["manifest"]["metadata"]["orchestration"]["continuation"][
            "continuation_token"
        ]
    )
    assert (
        session_record["manifest"]["metadata"]["orchestration"]["pending_approval"][
            "message_id"
        ]
        == events[0].payload["message_id"]
    )


def test_resolve_hitl_updates_checkpoint_state() -> None:
    session_record = {
        "orchestration": {
            "workflow_stage": "awaiting_hitl_resolution",
            "pending_approval": {
                "message_id": "hitl-123",
                "continuation_token": "token-123",
                "workflow_stage": "awaiting_hitl_resolution",
                "question": "Approve deployment?",
                "source": "clarification_questions",
                "action_labels": ["Approve", "Reject"],
                "requested_at": "2026-04-10T15:00:00Z",
            },
        },
        "manifest": {"metadata": {}},
    }
    session = OrchestrationSessionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        session_id="session-1",
        session_record=session_record,
    )

    resolution = resolve_hitl_continuation(
        command="resolve_hitl",
        args={"message_id": "hitl-123", "action_label": "Approve"},
        session=session,
    )

    assert resolution is not None
    assert resolution.event_payload is not None
    assert resolution.event_payload["kind"] == "hitl_resolved"
    assert resolution.command_result == {
        "status": "ok",
        "message_id": "hitl-123",
        "resolution": "Approve",
    }
    assert session_record["orchestration"]["workflow_stage"] == "continued"
    assert session_record["orchestration"]["continuation"]["continuation_token"] == (
        "token-123"
    )
    assert session_record["orchestration"]["continuation"]["resolution"] == "Approve"
    assert session_record["manifest"]["metadata"]["orchestration"][
        "workflow_stage"
    ] == ("continued")
    assert session.workflow_stage == "continued"
    assert session.continuation_token == "token-123"
    assert session.continuation is not None
    assert session.continuation.resolution == "Approve"
