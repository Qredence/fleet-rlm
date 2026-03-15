from contextlib import contextmanager
from unittest.mock import MagicMock

import dspy
import pytest
from dspy.streaming.messages import StatusMessage

from fleet_rlm.react.agent import RLMReActChatAgent
from fleet_rlm.react.delegate_sub_agent import (
    _build_child_interpreter,
    spawn_delegate_sub_agent_async,
)
from fleet_rlm.models import StreamEvent
from tests.unit.fixtures_state_trajectory import (
    make_modal_interpreter,
    patch_child_module,
)


@pytest.mark.asyncio
async def test_sub_agent_interpreter_sharing(monkeypatch):
    mock_interpreter = MagicMock()
    parent_agent = RLMReActChatAgent(interpreter=mock_interpreter)
    parent_agent._current_depth = 0

    created = patch_child_module(monkeypatch)

    result = await spawn_delegate_sub_agent_async(parent_agent, prompt="Test delegate")

    assert created
    assert created[0]["interpreter"] is parent_agent.interpreter
    assert result["depth"] == 1
    assert result["status"] == "ok"


def test_child_modal_interpreter_shares_parent_llm_budget_counter():
    parent_interpreter = make_modal_interpreter(max_llm_calls=4, used_calls=1)
    parent_agent = RLMReActChatAgent(interpreter=parent_interpreter)

    child_interpreter = _build_child_interpreter(parent_agent, remaining_llm_budget=3)

    assert child_interpreter is not parent_interpreter
    child_interpreter._check_and_increment_llm_calls(2)
    assert parent_interpreter._llm_call_count == 3


def test_live_modal_interpreter_reuses_parent_sandbox_for_delegate_turns():
    parent_interpreter = make_modal_interpreter(
        max_llm_calls=4,
        used_calls=1,
        sandbox_active=True,
    )
    parent_agent = RLMReActChatAgent(interpreter=parent_interpreter)

    child_interpreter = _build_child_interpreter(parent_agent, remaining_llm_budget=3)

    assert child_interpreter is parent_interpreter


@pytest.mark.asyncio
async def test_delegate_budget_cap_returns_bounded_error(monkeypatch):
    mock_interpreter = MagicMock()
    parent_agent = RLMReActChatAgent(
        interpreter=mock_interpreter,
        delegate_max_calls_per_turn=1,
    )
    parent_agent._current_depth = 0

    patch_child_module(monkeypatch)

    first = await spawn_delegate_sub_agent_async(parent_agent, prompt="delegate once")
    second = await spawn_delegate_sub_agent_async(parent_agent, prompt="delegate twice")

    assert first["status"] == "ok"
    assert second["status"] == "error"
    assert "Delegate call budget reached" in second["error"]


@pytest.mark.asyncio
async def test_delegate_result_truncation_updates_metadata(monkeypatch):
    mock_interpreter = MagicMock()
    parent_agent = RLMReActChatAgent(
        interpreter=mock_interpreter,
        delegate_result_truncation_chars=256,
    )
    parent_agent._current_depth = 0

    patch_child_module(monkeypatch, answer="x" * 1024)

    result = await spawn_delegate_sub_agent_async(parent_agent, prompt="truncate")

    assert result["status"] == "ok"
    assert result["delegate_output_truncated"] is True
    assert result["assistant_response"].endswith("[truncated delegate output]")
    assert parent_agent._delegate_result_truncated_count_turn == 1


@pytest.mark.asyncio
async def test_delegate_uses_delegate_lm_context_when_available(monkeypatch):
    mock_interpreter = MagicMock()
    delegate_lm = object()
    parent_agent = RLMReActChatAgent(
        interpreter=mock_interpreter, delegate_lm=delegate_lm
    )
    parent_agent._current_depth = 0

    seen_lms: list[object] = []

    @contextmanager
    def _fake_context(*, lm):
        seen_lms.append(lm)
        yield

    monkeypatch.setattr(
        "fleet_rlm.react.delegate_sub_agent.dspy.context", _fake_context
    )
    patch_child_module(monkeypatch)

    result = await spawn_delegate_sub_agent_async(
        parent_agent, prompt="delegate with lm"
    )

    assert result["status"] == "ok"
    assert result["delegate_lm_fallback"] is False
    assert seen_lms and seen_lms[0] is delegate_lm


@pytest.mark.asyncio
async def test_delegate_fallback_count_increments_when_delegate_lm_missing(monkeypatch):
    mock_interpreter = MagicMock()
    parent_agent = RLMReActChatAgent(interpreter=mock_interpreter, delegate_lm=None)
    parent_agent._current_depth = 0

    patch_child_module(monkeypatch)

    result = await spawn_delegate_sub_agent_async(parent_agent, prompt="no delegate lm")

    assert result["status"] == "ok"
    assert result["delegate_lm_fallback"] is True
    assert parent_agent._delegate_fallback_count_turn == 1


@pytest.mark.asyncio
async def test_delegate_rejects_when_parent_llm_budget_is_exhausted(monkeypatch):
    parent_interpreter = make_modal_interpreter(max_llm_calls=2, used_calls=2)
    parent_agent = RLMReActChatAgent(interpreter=parent_interpreter)
    parent_agent._current_depth = 0

    build_child = MagicMock()
    monkeypatch.setattr(
        "fleet_rlm.react.delegate_sub_agent.build_recursive_subquery_rlm",
        build_child,
    )

    result = await spawn_delegate_sub_agent_async(parent_agent, prompt="no budget left")

    assert result["status"] == "error"
    assert "LLM call limit already exhausted" in result["error"]
    build_child.assert_not_called()


@pytest.mark.asyncio
async def test_async_sub_agent_interpreter_sharing(monkeypatch):
    mock_interpreter = MagicMock()
    parent_agent = RLMReActChatAgent(interpreter=mock_interpreter)
    parent_agent._current_depth = 0

    created = patch_child_module(monkeypatch, answer="async sub-agent mock response")

    result = await spawn_delegate_sub_agent_async(
        parent_agent, prompt="Test async delegate"
    )

    assert created
    assert created[0]["interpreter"] is parent_agent.interpreter
    assert result["depth"] == 1
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_async_delegate_fallback_count_increments_when_delegate_lm_missing(
    monkeypatch,
):
    mock_interpreter = MagicMock()
    parent_agent = RLMReActChatAgent(interpreter=mock_interpreter, delegate_lm=None)
    parent_agent._current_depth = 0

    patch_child_module(monkeypatch)

    result = await spawn_delegate_sub_agent_async(
        parent_agent,
        prompt="no delegate lm configured (async)",
    )

    assert result["status"] == "ok"
    assert result["delegate_lm_fallback"] is True
    assert parent_agent._delegate_fallback_count_turn == 1


@pytest.mark.asyncio
async def test_delegate_stream_event_callback_forwards_coarse_child_events(
    monkeypatch,
):
    mock_interpreter = MagicMock()
    parent_agent = RLMReActChatAgent(interpreter=mock_interpreter)
    parent_agent._current_depth = 0

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str):
            _ = (prompt, context)
            return dspy.Prediction(
                answer="done",
                trajectory=[{"reasoning": "inspect file", "output": "ok"}],
                final_reasoning="done",
            )

    monkeypatch.setattr(
        "fleet_rlm.react.delegate_sub_agent.build_recursive_subquery_rlm",
        lambda **kwargs: _FakeChildModule(),
    )

    def _fake_streamify(*args, **kwargs):
        _ = (args, kwargs)

        async def _stream_with_prediction(**stream_kwargs):
            _ = stream_kwargs
            yield StatusMessage(message="Calling tool: read_file_slice(path='a.py')")
            yield StatusMessage(message="Tool finished.")
            yield dspy.Prediction(
                answer="done",
                trajectory=[{"reasoning": "inspect file", "output": "ok"}],
                final_reasoning="done",
            )

        return _stream_with_prediction

    monkeypatch.setattr(
        "fleet_rlm.react.delegate_sub_agent.dspy.streamify", _fake_streamify
    )

    events: list[StreamEvent] = []
    result = await spawn_delegate_sub_agent_async(
        parent_agent,
        prompt="inspect file",
        context="ctx",
        stream_event_callback=events.append,
    )

    assert result["status"] == "ok"
    kinds = [event.kind for event in events]
    assert "status" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert "trajectory_step" in kinds
