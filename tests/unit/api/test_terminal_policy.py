from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, cast

from fleet_rlm.api.orchestration.terminal_policy import apply_terminal_event_policy
from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.worker import WorkspaceEvent

_TEST_EVENT_EPOCH = 1_234_567_890.0


class _MockExecutionLifecycleManager:
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


def _timestamp_from_epoch(epoch: float = _TEST_EVENT_EPOCH) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def test_terminal_policy_shim_preserves_legacy_call_shape() -> None:
    async def scenario() -> None:
        lifecycle = _MockExecutionLifecycleManager()
        persist_calls: list[bool] = []
        sent_count = 0

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            persist_calls.append(include_volume_save)

        async def send_terminal_event() -> bool:
            nonlocal sent_count
            sent_count += 1
            return True

        sent = await apply_terminal_event_policy(
            lifecycle=cast(Any, lifecycle),
            event=WorkspaceEvent(
                kind="final",
                text="done",
                timestamp=_timestamp_from_epoch(),
                terminal=True,
            ),
            step=None,
            persist_session_state=cast(Any, persist_session_state),
            request_message="hello",
            send_terminal_event=send_terminal_event,
        )

        assert sent is True
        assert persist_calls == [True]
        assert sent_count == 1
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["status"] is RunStatus.COMPLETED

    asyncio.run(scenario())
