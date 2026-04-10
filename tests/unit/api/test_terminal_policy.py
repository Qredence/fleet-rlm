from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fleet_rlm.api.orchestration.terminal_policy import apply_terminal_event_policy
from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.worker import WorkspaceEvent


class _LifecycleStub:
    def __init__(self) -> None:
        self.run_id = "test-run"
        self.completed_with: dict[str, Any] | None = None

    async def complete_run(
        self,
        status: RunStatus,
        *,
        step: Any = None,
        error_json: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        self.completed_with = {
            "status": status,
            "step": step,
            "error_json": error_json,
            "summary": summary,
        }


def _ts(epoch: float = 1_234_567_890.0) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def test_terminal_policy_shim_preserves_legacy_call_shape() -> None:
    async def scenario() -> None:
        lifecycle = _LifecycleStub()
        persist_calls: list[bool] = []
        sent_calls: list[str] = []

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            persist_calls.append(include_volume_save)

        async def send_terminal_event() -> bool:
            sent_calls.append("sent")
            return True

        sent = await apply_terminal_event_policy(
            lifecycle=lifecycle,  # type: ignore[arg-type]
            event=WorkspaceEvent(
                kind="final",
                text="done",
                timestamp=_ts(),
                terminal=True,
            ),
            step=None,
            persist_session_state=persist_session_state,  # type: ignore[arg-type]
            request_message="hello",
            send_terminal_event=send_terminal_event,
        )

        assert sent is True
        assert persist_calls == [True]
        assert sent_calls == ["sent"]
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"] is RunStatus.COMPLETED

    asyncio.run(scenario())
