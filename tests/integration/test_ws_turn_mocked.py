from __future__ import annotations

from typing import Any

import dspy
import pytest

from fleet_rlm.runtime.agent.runtime import AgentRuntime
from fleet_rlm.runtime.schemas import StreamEvent


class _StreamingAgent:
    """Fake cognition module exercised through the unified streaming path."""

    async def aforward(self, **_: Any) -> dspy.Prediction:
        return dspy.Prediction(response="final", trajectory={})


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_runtime_unified_stream_emits_done_event() -> None:
    runtime = AgentRuntime(interpreter=None, use_escalation=False, extra_tools=[])
    runtime.agent = _StreamingAgent()

    events: list[StreamEvent] = [
        event
        async for event in runtime.aiter_chat_turn_stream(
            message="integration hello",
            trace=False,
        )
    ]

    kinds = [event.kind for event in events]
    assert "status" in kinds
    assert kinds[-1] == "done"
    assert events[-1].payload.get("history_turns", 0) >= 1
