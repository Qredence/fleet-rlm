from __future__ import annotations

from typing import Any

import pytest

from fleet_rlm.runtime.agent.runtime import AgentRuntime
from fleet_rlm.runtime.schemas import StreamEvent


class _StreamingAgent:
    planner = object()

    async def async_planner_step(self, trajectory: dict[str, Any], **_: Any) -> Any:
        if trajectory:
            raise ValueError("stop")

        class _Prediction:
            next_thought = "final"
            next_tool_name = "finish"
            next_tool_args = {}

        return _Prediction()

    @property
    def tools(self) -> dict[str, Any]:
        return {}

    @property
    def max_iters(self) -> int:
        return 3

    class _Extract:
        @staticmethod
        def predict(**_: Any) -> Any:
            return None

    extract = _Extract()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_runtime_native_stream_emits_done_event() -> None:
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
