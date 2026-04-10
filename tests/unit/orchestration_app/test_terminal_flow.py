from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.orchestration_app.sessions import OrchestrationSessionContext
from fleet_rlm.orchestration_app.terminal_flow import apply_terminal_event_policy
from fleet_rlm.worker import WorkspaceEvent


class _LifecycleStub:
    def __init__(self, operations: list[str]) -> None:
        self.run_id = "test-run"
        self.operations = operations
        self.completed_with: dict[str, Any] | None = None

    async def complete_run(
        self,
        status: RunStatus,
        *,
        step: Any = None,
        error_json: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        self.operations.append("complete")
        self.completed_with = {
            "status": status,
            "step": step,
            "error_json": error_json,
            "summary": summary,
        }


def _ts(epoch: float = 1_234_567_890.0) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _session(session_record: dict[str, Any]) -> OrchestrationSessionContext:
    session = OrchestrationSessionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        session_id="session-1",
        session_record=session_record,
    )
    session.refresh_from_session_record()
    return session


def test_apply_terminal_event_policy_final_marks_completed_before_send() -> None:
    async def scenario() -> None:
        operations: list[str] = []
        lifecycle = _LifecycleStub(operations)
        session_record = {
            "orchestration": {
                "workflow_stage": "continued",
                "continuation": {
                    "continuation_token": "token-123",
                    "message_id": "hitl-123",
                    "requested_at": "2026-04-10T15:00:00Z",
                    "updated_at": "2026-04-10T15:00:00Z",
                },
            },
            "manifest": {"metadata": {}},
        }
        session = _session(session_record)
        event = WorkspaceEvent(
            kind="final", text="done", timestamp=_ts(), terminal=True
        )

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            operations.append(f"persist:{include_volume_save}")

        async def send_terminal_event() -> bool:
            operations.append("send")
            return True

        sent = await apply_terminal_event_policy(
            lifecycle=lifecycle,  # type: ignore[arg-type]
            event=event,
            step=None,
            session=session,
            persist_session_state=persist_session_state,  # type: ignore[arg-type]
            request_message="hello",
            send_terminal_event=send_terminal_event,
        )

        assert sent is True
        assert operations == ["persist:True", "complete", "send"]
        assert session.workflow_stage == "completed"
        assert session.continuation is not None
        assert session.continuation.continuation_token == "token-123"
        assert session_record["orchestration"]["workflow_stage"] == "completed"
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"] is RunStatus.COMPLETED
        assert lifecycle.completed_with["summary"]["status"] == "completed"

    asyncio.run(scenario())


def test_apply_terminal_event_policy_error_preserves_pending_continuation() -> None:
    async def scenario() -> None:
        operations: list[str] = []
        lifecycle = _LifecycleStub(operations)
        session_record = {
            "orchestration": {
                "workflow_stage": "awaiting_hitl_resolution",
                "continuation": {
                    "continuation_token": "token-123",
                    "message_id": "hitl-123",
                    "requested_at": "2026-04-10T15:00:00Z",
                    "updated_at": "2026-04-10T15:00:00Z",
                },
                "pending_approval": {
                    "message_id": "hitl-123",
                    "continuation_token": "token-123",
                    "workflow_stage": "awaiting_hitl_resolution",
                    "question": "Approve deployment?",
                    "requested_at": "2026-04-10T15:00:00Z",
                },
            },
            "manifest": {"metadata": {}},
        }
        session = _session(session_record)
        event = WorkspaceEvent(
            kind="error", text="boom", timestamp=_ts(), terminal=True
        )

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            operations.append(f"persist:{include_volume_save}")

        async def send_terminal_event() -> bool:
            operations.append("send")
            return True

        sent = await apply_terminal_event_policy(
            lifecycle=lifecycle,  # type: ignore[arg-type]
            event=event,
            step=None,
            session=session,
            persist_session_state=persist_session_state,  # type: ignore[arg-type]
            request_message="hello",
            send_terminal_event=send_terminal_event,
        )

        assert sent is True
        assert operations == ["send", "persist:True", "complete"]
        assert session.workflow_stage == "awaiting_hitl_resolution"
        assert session.pending_approval is not None
        assert session.pending_approval.continuation_token == "token-123"
        assert session.continuation is not None
        assert session.continuation.continuation_token == "token-123"
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"] is RunStatus.FAILED
        assert lifecycle.completed_with["error_json"] == {
            "error": "boom",
            "kind": "error",
        }
        assert lifecycle.completed_with["summary"]["status"] == "error"

    asyncio.run(scenario())


def test_apply_terminal_event_policy_cancelled_marks_run_cancelled() -> None:
    async def scenario() -> None:
        operations: list[str] = []
        lifecycle = _LifecycleStub(operations)
        session = _session({"manifest": {"metadata": {}}})
        event = WorkspaceEvent(
            kind="cancelled",
            text="stopped",
            timestamp=_ts(),
            terminal=True,
        )

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            operations.append(f"persist:{include_volume_save}")

        async def send_terminal_event() -> bool:
            operations.append("send")
            return True

        sent = await apply_terminal_event_policy(
            lifecycle=lifecycle,  # type: ignore[arg-type]
            event=event,
            step=None,
            session=session,
            persist_session_state=persist_session_state,  # type: ignore[arg-type]
            request_message="hello",
            send_terminal_event=send_terminal_event,
        )

        assert sent is True
        assert operations == ["send", "persist:True", "complete"]
        assert session.workflow_stage == "completed"
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"] is RunStatus.CANCELLED
        assert lifecycle.completed_with["summary"]["status"] == "cancelled"

    asyncio.run(scenario())
