from __future__ import annotations

from fleet_rlm.agent_host.hitl_flow import (
    checkpoint_hitl_request,
    resolve_hitl_continuation,
)
from fleet_rlm.agent_host.sessions import OrchestrationSessionContext
from fleet_rlm.worker import WorkspaceEvent


def test_checkpoint_hitl_request_updates_session_state_and_payload() -> None:
    session_record = {"manifest": {"metadata": {}}}
    session = OrchestrationSessionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        session_id="session-1",
        session_record=session_record,
    )

    event = checkpoint_hitl_request(
        event=WorkspaceEvent(
            kind="hitl_request",
            text="Approve deployment?",
            payload={
                "question": "Approve deployment?",
                "actions": [{"label": "Approve"}, {"label": "Reject"}],
            },
        ),
        session=session,
    )

    assert event.kind == "hitl_request"
    assert isinstance(event.payload["message_id"], str)
    assert session_record["orchestration"]["workflow_stage"] == "awaiting_hitl_resolution"
    assert session_record["orchestration"]["pending_approval"]["message_id"] == (
        event.payload["message_id"]
    )


def test_resolve_hitl_continuation_updates_checkpoint_state() -> None:
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
    ] == "continued"
    assert session.workflow_stage == "continued"
    assert session.continuation_token == "token-123"
    assert session.continuation is not None
    assert session.continuation.resolution == "Approve"
