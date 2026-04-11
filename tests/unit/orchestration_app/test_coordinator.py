from __future__ import annotations

import asyncio

from fleet_rlm.agent_host.sessions import OrchestrationSessionContext
from fleet_rlm.orchestration_app.coordinator import (
    resolve_hitl_continuation,
    stream_orchestrated_workspace_task,
)
from fleet_rlm.worker import WorkspaceEvent, WorkspaceTaskRequest


class _AgentStub:
    def set_execution_mode(self, execution_mode: str) -> None:
        _ = execution_mode

    async def aiter_chat_turn_stream(self, *args, **kwargs):
        _ = (args, kwargs)
        return None


def test_coordinator_streams_worker_events_without_hitl_ownership(monkeypatch) -> None:
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
    assert "message_id" not in events[0].payload
    assert events[1].kind == "final"
    assert "orchestration" not in session_record


def test_resolve_hitl_continuation_delegates_to_agent_host(monkeypatch) -> None:
    from fleet_rlm.agent_host.hitl_flow import HitlResolution

    captured: dict[str, object] = {}

    def _fake_resolve_hitl_continuation(*, command, args, session):
        captured["command"] = command
        captured["args"] = args
        captured["session"] = session
        return HitlResolution(
            event_payload={"kind": "hitl_resolved"},
            command_result={"status": "ok"},
        )

    monkeypatch.setattr(
        "fleet_rlm.orchestration_app.coordinator.resolve_agent_host_hitl_continuation",
        _fake_resolve_hitl_continuation,
    )

    resolution = resolve_hitl_continuation(
        command="resolve_hitl",
        args={"message_id": "hitl-123", "action_label": "Approve"},
        session=None,
    )

    assert resolution is not None
    assert resolution.event_payload == {"kind": "hitl_resolved"}
    assert resolution.command_result == {"status": "ok"}
    assert captured == {
        "command": "resolve_hitl",
        "args": {"message_id": "hitl-123", "action_label": "Approve"},
        "session": None,
    }
