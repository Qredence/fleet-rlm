"""Characterization tests locking the unified streaming RuntimeEvent contract.

These tests pin the exact ``RuntimeEvent`` sequence the websocket layer emits
through :meth:`AgentRuntime.aiter_chat_turn_stream`. The unified path runs the
turn (via ``dspy.streamify`` for real DSPy modules, plain ``aforward`` for
everything else), then replays the final prediction's trajectory as reasoning
and tool events before the terminal ``done`` event.

The agent forward pass is scripted so the assertions are deterministic and
require no live LM. The goal is to capture observable behaviour, not to test
internals.
"""

from __future__ import annotations

from typing import Any

import dspy
import pytest


def _disable_runtime_tool_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.runtime.agent import runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "discover_tools", lambda **_kwargs: [])


class _ScriptedAgent:
    """Fake cognition module returning a fixed prediction with a trajectory."""

    def __init__(self, prediction: dspy.Prediction) -> None:
        self._prediction = prediction
        self.calls: list[dict[str, Any]] = []

    async def aforward(self, **kwargs: Any) -> dspy.Prediction:
        self.calls.append(kwargs)
        return self._prediction


@pytest.mark.asyncio
async def test_tool_using_turn_emits_canonical_event_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_runtime_tool_discovery(monkeypatch)
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    rt = AgentRuntime(use_escalation=False)
    rt.agent = _ScriptedAgent(
        dspy.Prediction(
            response="Here is your answer.",
            trajectory={
                "thought_0": "I should echo the value first.",
                "tool_name_0": "echo_tool",
                "tool_args_0": {"value": "hi"},
                "observation_0": "echoed: hi",
                "thought_1": "Here is your answer.",
                "tool_name_1": "finish",
                "tool_args_1": {},
                "observation_1": "Completed.",
            },
        )
    )

    events = [event async for event in rt.aiter_chat_turn_stream("hello")]
    kinds = [event.kind for event in events]

    # Canonical ordering: status -> reasoning -> tool_call -> tool_result -> text -> done
    assert kinds[0] == "status"
    assert events[0].text == "Starting turn..."
    assert "reasoning" in kinds
    assert kinds.index("tool_call") < kinds.index("tool_result")
    assert kinds.index("tool_result") < kinds.index("text")
    assert kinds[-1] == "done"

    tool_call = next(e for e in events if e.kind == "tool_call")
    tool_result = next(e for e in events if e.kind == "tool_result")
    assert tool_call.payload["tool_name"] == "echo_tool"
    assert tool_result.payload["tool_name"] == "echo_tool"

    text = next(e for e in events if e.kind == "text")
    assert text.text == "Here is your answer."

    done = events[-1]
    assert done.text == "Here is your answer."
    assert "trajectory" in done.payload
    assert "history_turns" in done.payload


@pytest.mark.asyncio
async def test_direct_turn_emits_text_and_done_without_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_runtime_tool_discovery(monkeypatch)
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    rt = AgentRuntime(use_escalation=False)
    rt.agent = _ScriptedAgent(dspy.Prediction(response="Direct answer, no tools needed."))

    events = [event async for event in rt.aiter_chat_turn_stream("hi")]
    kinds = [event.kind for event in events]

    assert kinds[0] == "status"
    assert "tool_call" not in kinds
    assert "tool_result" not in kinds
    text = next(e for e in events if e.kind == "text")
    assert text.text == "Direct answer, no tools needed."
    assert kinds[-1] == "done"
    assert events[-1].text == "Direct answer, no tools needed."


@pytest.mark.asyncio
async def test_direct_turn_records_agent_execution_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_runtime_tool_discovery(monkeypatch)
    from fleet_rlm.integrations.observability import mlflow_context
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    captured: list[dict[str, Any]] = []

    class _FakeSpan:
        def __init__(self, name: str, span_type: str | None = None, attributes: dict[str, Any] | None = None):
            self.record = {
                "name": name,
                "span_type": span_type,
                "attributes": attributes or {},
                "outputs": {},
            }
            captured.append(self.record)

        def __enter__(self) -> "_FakeSpan":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    monkeypatch.setattr(
        mlflow_context,
        "mlflow_child_span",
        lambda name, span_type="CHAIN", attributes=None, inputs=None: _FakeSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "set_mlflow_span_outputs",
        lambda span, outputs: span.record.__setitem__("outputs", outputs or {}) if span is not None else None,
    )

    rt = AgentRuntime(use_escalation=False)
    rt.agent = _ScriptedAgent(dspy.Prediction(response="Direct answer."))

    events = [event async for event in rt.aiter_chat_turn_stream("hi")]

    assert events[-1].kind == "done"
    span = next(record for record in captured if record["name"] == "fleet_rlm.agent_turn_execute")
    assert span["span_type"] == "CHAIN"
    assert span["attributes"]["fleet_rlm.agent_class"] == "_ScriptedAgent"
    assert span["outputs"]["status"] == "ok"
    assert span["outputs"]["stream_listener_count"] == 0
    assert span["outputs"]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_cancel_before_turn_emits_cancelled_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_runtime_tool_discovery(monkeypatch)
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    rt = AgentRuntime(use_escalation=False)

    events = [event async for event in rt.aiter_chat_turn_stream("hi", cancel_check=lambda: True)]

    assert len(events) == 1
    assert events[0].kind == "done"
    assert events[0].payload.get("cancelled") is True
