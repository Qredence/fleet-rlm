"""Characterization tests locking the native streaming StreamEvent contract.

These tests pin the exact ``StreamEvent`` sequence the websocket layer emits
today through :meth:`AgentRuntime.aiter_chat_turn_stream` when the agent exposes
a streamable ReAct program (``FleetAgent``). They form the Phase 0 guardrail for
the dspy.ReAct migration: the migration must keep these sequences intact.

The planner step is scripted so the assertions are deterministic and require no
live LM. The goal is to capture observable behaviour, not to test internals.
"""

from __future__ import annotations

from typing import Any

import dspy
import pytest


def _disable_runtime_tool_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.runtime.agent import runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "discover_tools", lambda: [])


def _script_planner(react_program: Any, steps: list[dspy.Prediction]) -> None:
    """Replace ``async_planner_step`` with a scripted, deterministic sequence."""

    iterator = iter(steps)

    async def _scripted(trajectory: dict[str, Any], **_input: Any) -> dspy.Prediction:
        _ = trajectory
        return next(iterator)

    react_program.async_planner_step = _scripted  # type: ignore[assignment]


def _pred(**kwargs: Any) -> dspy.Prediction:
    pred = dspy.Prediction(**kwargs)
    for key, value in kwargs.items():
        object.__setattr__(pred, key, value)
    return pred


@pytest.mark.asyncio
async def test_tool_using_turn_emits_canonical_event_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_runtime_tool_discovery(monkeypatch)
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    rt = AgentRuntime(use_escalation=False)
    react_program = rt.agent

    def echo(value: str) -> str:
        return f"echoed: {value}"

    react_program.tools["echo_tool"] = echo  # type: ignore[index]

    _script_planner(
        react_program,
        [
            _pred(
                next_thought="I should echo the value first.",
                next_tool_name="echo_tool",
                next_tool_args={"value": "hi"},
            ),
            _pred(
                next_thought="Here is your answer.",
                next_tool_name="finish",
                next_tool_args={},
            ),
        ],
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
async def test_finish_first_turn_skips_tool_and_extract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_runtime_tool_discovery(monkeypatch)
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    rt = AgentRuntime(use_escalation=False)
    react_program = rt.agent

    _script_planner(
        react_program,
        [
            _pred(
                next_thought="Direct answer, no tools needed.",
                next_tool_name="finish",
                next_tool_args={},
            ),
        ],
    )

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
