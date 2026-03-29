from __future__ import annotations

import dspy
import pytest

from fleet_rlm.runtime.agent import RLMReActChatAgent
from fleet_rlm.runtime.agent.forced_routing import (
    ForcedFinalPayloadInput,
    arun_forced_rlm_turn,
    forced_stream_final_payload,
    run_forced_rlm_turn,
)
from fleet_rlm.runtime.execution.streaming_context import StreamingContext
from tests.unit.fixtures_react import FakeInterpreter

pytestmark = pytest.mark.usefixtures("react_records")


def test_run_forced_rlm_turn_invokes_explicit_rlm_query_handoff(monkeypatch) -> None:
    agent = RLMReActChatAgent(interpreter=FakeInterpreter(), execution_mode="rlm_only")
    captured: dict[str, str] = {}

    def _fake_tool(*, query: str, context: str) -> dict[str, object]:
        captured["query"] = query
        captured["context"] = context
        return {"answer": "forced response", "trajectory": {}}

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.forced_routing.get_tool_by_name",
        lambda _agent, name: _fake_tool if name == "rlm_query" else None,
    )

    prediction = run_forced_rlm_turn(agent, message="deep task")

    assert isinstance(prediction, dspy.Prediction)
    assert prediction.assistant_response == "forced response"
    assert captured["query"] == "deep task"
    assert "Core memory:" in captured["context"]


@pytest.mark.asyncio
async def test_arun_forced_rlm_turn_invokes_recursive_runtime_handoff(
    monkeypatch,
) -> None:
    agent = RLMReActChatAgent(interpreter=FakeInterpreter(), execution_mode="rlm_only")
    captured: dict[str, object] = {}

    async def _fake_spawn(agent_obj, *, prompt, context, stream_event_callback):
        captured["agent"] = agent_obj
        captured["prompt"] = prompt
        captured["context"] = context
        captured["stream_event_callback"] = stream_event_callback
        return {"answer": "forced async response", "trajectory": {}}

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.forced_routing.spawn_delegate_sub_agent_async",
        _fake_spawn,
    )

    prediction = await arun_forced_rlm_turn(agent, message="deep async task")

    assert isinstance(prediction, dspy.Prediction)
    assert prediction.assistant_response == "forced async response"
    assert captured["agent"] is agent
    assert captured["prompt"] == "deep async task"
    assert captured["stream_event_callback"] is None


def test_forced_stream_final_payload_reuses_canonical_turn_metadata() -> None:
    agent = RLMReActChatAgent(interpreter=FakeInterpreter(), execution_mode="rlm_only")
    agent.history = dspy.History(
        messages=[{"user_request": "hi", "assistant_response": "forced"}]
    )
    agent.prepare_routed_turn(effective_max_iters=9)

    payload = forced_stream_final_payload(
        agent,
        payload_input=ForcedFinalPayloadInput(
            trajectory={"tool_name_0": "rlm_query"},
            guardrail_warnings=["check"],
            final_reasoning="delegate done",
        ),
        ctx=StreamingContext.from_agent(agent, effective_max_iters=9),
    )

    assert payload["history_turns"] == 1
    assert payload["guardrail_warnings"] == ["check"]
    assert payload["final_reasoning"] == "delegate done"
    assert payload["effective_max_iters"] == 9
    assert payload["runtime"]["effective_max_iters"] == 9
