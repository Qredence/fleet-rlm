from __future__ import annotations

from typing import Any

import pytest

from fleet_rlm.runtime.agent.runtime import AgentRuntime
from fleet_rlm.runtime.events import RuntimeEventKind


class _SyncOnlyAgent:
    """Simulates EscalatingFleetModule without aforward — must use asyncio.to_thread."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class _Result:
            response = "sync answer"
            trajectory = []

        return _Result()


@pytest.mark.asyncio
async def test_posthoc_stream_uses_worker_thread_for_sync_agent() -> None:
    runtime = AgentRuntime(interpreter=None, use_escalation=False, extra_tools=[])
    runtime.agent = _SyncOnlyAgent()

    events = [
        event
        async for event in runtime._aiter_chat_turn_stream_posthoc(
            message="hello",
            cancel_check=None,
        )
    ]

    assert isinstance(runtime.agent, _SyncOnlyAgent)
    assert runtime.agent.calls
    assert any(event.kind == RuntimeEventKind.DONE for event in events)
    assert events[-1].text == "sync answer"


@pytest.mark.asyncio
async def test_achat_turn_offloads_sync_chat_turn() -> None:
    runtime = AgentRuntime(interpreter=None, use_escalation=False, extra_tools=[])
    runtime.agent = _SyncOnlyAgent()

    await runtime.achat_turn("ping")

    assert runtime.agent.calls
