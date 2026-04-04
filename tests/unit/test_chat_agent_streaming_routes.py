"""Direct tests for chat-agent streaming route selection."""

from __future__ import annotations

import pytest

from fleet_rlm.runtime.agent import RLMReActChatAgent
from fleet_rlm.runtime.models.streaming import StreamEvent
from tests.unit.fixtures_react import FakeInterpreter

pytestmark = pytest.mark.usefixtures("react_records")


def test_iter_chat_turn_stream_rlm_only_emits_forced_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(interpreter=FakeInterpreter(), execution_mode="rlm_only")
    captured: dict[str, str] = {}

    def _fake_tool(*, query: str, context: str) -> dict[str, object]:
        captured["query"] = query
        captured["context"] = context
        return {"answer": "forced response", "trajectory": {}}

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.get_tool_by_name",
        lambda _agent, name: _fake_tool if name == "rlm_query" else None,
    )

    events = list(
        agent.iter_chat_turn_stream(
            message="deep task",
            trace=False,
        )
    )

    assert [event.kind for event in events] == [
        "status",
        "rlm_executing",
        "tool_result",
        "final",
    ]
    assert events[-1].text == "forced response"
    assert events[0].payload["forced"] is True
    assert events[1].payload["tool_name"] == "rlm_query"
    assert captured["query"] == "deep task"
    assert "Core memory:" in captured["context"]
    assert len(agent.history.messages) == 1


@pytest.mark.asyncio
async def test_aiter_chat_turn_stream_rlm_only_uses_forced_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(interpreter=FakeInterpreter(), execution_mode="rlm_only")

    async def _fake_spawn_delegate_sub_agent_async(
        _agent: object,
        *,
        prompt: str,
        context: str,
        stream_event_callback: object,
    ) -> dict[str, object]:
        assert prompt == "deep task"
        assert "Core memory:" in context
        assert callable(stream_event_callback)
        await stream_event_callback(
            StreamEvent(kind="reasoning_step", text="delegating to recursive runtime")
        )
        return {"answer": "forced async response", "trajectory": {}}

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.forced_routing.spawn_delegate_sub_agent_async",
        _fake_spawn_delegate_sub_agent_async,
    )

    events = [
        event
        async for event in agent.aiter_chat_turn_stream(
            message="deep task",
            trace=False,
        )
    ]

    kinds = [event.kind for event in events]
    assert kinds[:2] == ["status", "rlm_executing"]
    assert "reasoning_step" in kinds
    assert kinds[-2:] == ["tool_result", "final"]
    assert events[-1].text == "forced async response"
    assert len(agent.history.messages) == 1
