"""VAL-RLM streaming contract tests.

Covers:
- VAL-RLM-014: Streaming emits recursive delegation lifecycle events
- VAL-RLM-016: Runtime streaming emits exactly one terminal event
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import dspy
import pytest

from fleet_rlm.runtime.agent.runtime import AgentRuntime
from fleet_rlm.runtime.execution.streaming_events import (
    TERMINAL_STREAM_EVENT_KINDS,
    is_terminal_stream_event_kind,
)
from fleet_rlm.runtime.schemas import StreamEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stub_agent(prediction: dspy.Prediction) -> Any:
    """Create a plain callable agent stub that returns *prediction*.

    The stub intentionally has no ``react``, ``planner``, or ``extract``
    attributes so ``_get_streamable_react_program`` returns None and the
    posthoc streaming path is exercised without dspy.Module overhead.
    """

    class _StubAgent:
        """Minimal callable that mimics FleetAgent without dspy overhead."""

        def __call__(self, **kwargs: Any) -> dspy.Prediction:
            return prediction

    return _StubAgent()


def _make_failing_agent(exc: Exception) -> Any:
    """Create a stub agent that raises the given exception when called."""

    class _FailingAgent:
        def __call__(self, **kwargs: Any) -> dspy.Prediction:
            raise exc

    return _FailingAgent()


async def _collect_stream(
    runtime: AgentRuntime,
    message: str,
    cancel_check: Any = None,
) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    async for event in runtime.aiter_chat_turn_stream(
        message,
        cancel_check=cancel_check,
    ):
        events.append(event)
    return events


def _terminal_events(events: list[StreamEvent]) -> list[StreamEvent]:
    return [e for e in events if is_terminal_stream_event_kind(e.kind)]


def _events_after_terminal(events: list[StreamEvent]) -> list[StreamEvent]:
    """Return events that appear AFTER the first terminal event."""
    first_terminal_idx = None
    for i, e in enumerate(events):
        if is_terminal_stream_event_kind(e.kind):
            first_terminal_idx = i
            break
    if first_terminal_idx is None:
        return []
    return [
        e
        for e in events[first_terminal_idx + 1 :]
        if e.kind not in ("status",)  # protocol-level status frames are allowed
    ]


def _make_fake_react():
    """Return a fake dspy.ReAct class that records construction arguments."""

    class _FakeReAct:
        def __init__(self, *, signature, tools, max_iters, **kwargs):
            self.signature = signature
            self._tools = list(tools)
            self._max_iters = max_iters

        def __call__(self, **kwargs):
            return dspy.Prediction(response="fake_response")

    return _FakeReAct


def _make_runtime(monkeypatch: pytest.MonkeyPatch) -> AgentRuntime:
    """Create an AgentRuntime with fake dspy.ReAct and no discovered tools."""
    FakeReAct = _make_fake_react()
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])
    return AgentRuntime()


# ---------------------------------------------------------------------------
# VAL-RLM-016: Exactly one terminal event
# ---------------------------------------------------------------------------


class TestExactlyOneTerminalEvent:
    """VAL-RLM-016: Runtime streaming emits exactly one terminal event."""

    @pytest.mark.asyncio
    async def test_success_path_posthoc_emits_exactly_one_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Success (posthoc path): exactly one 'done' event at the end."""
        rt = _make_runtime(monkeypatch)
        rt.agent = _make_stub_agent(dspy.Prediction(response="hello", trajectory={}))

        events = await _collect_stream(rt, "hello world")

        terminals = _terminal_events(events)
        assert len(terminals) == 1, f"Expected exactly one terminal event, got: {[e.kind for e in terminals]}"
        assert terminals[0].kind == "done"
        assert not _events_after_terminal(events), "No semantic events after terminal"

    @pytest.mark.asyncio
    async def test_error_path_posthoc_emits_exactly_one_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Error (posthoc path): exactly one 'error' terminal event."""
        rt = _make_runtime(monkeypatch)
        rt.agent = _make_failing_agent(RuntimeError("agent explosion"))

        events = await _collect_stream(rt, "explode")

        terminals = _terminal_events(events)
        assert len(terminals) == 1, f"Expected exactly one terminal event, got: {[e.kind for e in terminals]}"
        assert terminals[0].kind == "error"
        assert "agent explosion" in terminals[0].text
        assert not _events_after_terminal(events)

    @pytest.mark.asyncio
    async def test_cancel_before_start_emits_exactly_one_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Early cancel: exactly one 'done' terminal event with cancelled=True."""
        rt = _make_runtime(monkeypatch)
        events = await _collect_stream(rt, "cancel me", cancel_check=lambda: True)

        terminals = _terminal_events(events)
        assert len(terminals) == 1
        assert terminals[0].kind == "done"
        assert terminals[0].payload.get("cancelled") is True
        assert not _events_after_terminal(events)

    @pytest.mark.asyncio
    async def test_native_streaming_path_emits_exactly_one_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Native streaming path (streamable react): exactly one terminal 'done' event."""
        from types import SimpleNamespace

        rt = _make_runtime(monkeypatch)

        # Build a fake streamable react program so the native path is used
        planner_predictions = [
            dspy.Prediction(next_thought="thinking...", next_tool_name="", next_tool_args={}),
        ]

        class _FakeStreamableReact:
            max_iters = 1

            def __init__(self) -> None:
                self.planner = object()
                self.extract = SimpleNamespace(predict=MagicMock(return_value=dspy.Prediction(response="answer")))
                self.tools: dict[str, Any] = {}
                self._preds = list(planner_predictions)

            def _format_trajectory(self, traj: dict) -> str:
                return str(traj)

            async def async_planner_step(self, traj: dict, **kwargs: Any) -> dspy.Prediction:
                if not self._preds:
                    raise ValueError("done")
                return self._preds.pop(0)

        fake_react = _FakeStreamableReact()
        rt.agent.react = fake_react  # type: ignore[attr-defined]

        with patch("fleet_rlm.runtime.agent.runtime.dspy.streamify") as mock_streamify:

            async def _fake_stream_extract(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
                yield dspy.Prediction(response="native answer")

            mock_streamify.return_value = _fake_stream_extract
            events = await _collect_stream(rt, "native path")

        terminals = _terminal_events(events)
        assert len(terminals) == 1, f"Expected exactly 1 terminal, got: {[e.kind for e in terminals]}"
        assert terminals[0].kind == "done"
        assert not _events_after_terminal(events)

    @pytest.mark.asyncio
    async def test_degraded_child_posthoc_emits_exactly_one_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Degraded recursive child result (posthoc path): exactly one 'done' terminal event."""
        rt = _make_runtime(monkeypatch)

        # Simulate agent returning trajectory with a degraded delegate_to_rlm observation
        degraded_result = {
            "status": "needs_human_review",
            "answer": "partial answer",
            "degraded": True,
            "reason": "broker_unavailable",
        }

        rt.agent = _make_stub_agent(
            dspy.Prediction(
                response="partial",
                trajectory={
                    "thought_0": "delegating",
                    "tool_name_0": "delegate_to_rlm",
                    "tool_args_0": {},
                    "observation_0": str(degraded_result),
                },
            )
        )

        events = await _collect_stream(rt, "delegate something")

        terminals = _terminal_events(events)
        assert len(terminals) == 1, (
            f"Expected exactly one terminal event for degraded child, got: {[e.kind for e in terminals]}"
        )
        assert terminals[0].kind == "done"
        assert not _events_after_terminal(events)

    def test_recursive_child_review_payload_detects_degraded_observation(self) -> None:
        """_recursive_child_review_payload returns review metadata for degraded results.

        VAL-RLM-016: The native streaming path adds human_review to the 'done' payload
        for degraded recursive child results. This test verifies the detection logic.
        """
        from fleet_rlm.runtime.agent.runtime import _recursive_child_review_payload

        degraded_observation = (
            '{"status": "needs_human_review", "answer": "partial", "degraded": true, "reason": "broker_unavailable"}'
        )
        result = _recursive_child_review_payload("delegate_to_rlm", degraded_observation)

        assert result is not None, "degraded observation must produce review payload"
        assert result.get("required") is True
        assert result.get("repair_mode") == "needs_human_review"
        assert "reason" in result

    def test_recursive_child_review_payload_none_for_success(self) -> None:
        """_recursive_child_review_payload returns None for successful child results."""
        from fleet_rlm.runtime.agent.runtime import _recursive_child_review_payload

        success_observation = '{"status": "ok", "answer": "good answer"}'
        result = _recursive_child_review_payload("delegate_to_rlm", success_observation)
        assert result is None, "Successful observations must not produce review payload"


# ---------------------------------------------------------------------------
# VAL-RLM-014: Streaming emits recursive delegation lifecycle events
# ---------------------------------------------------------------------------


class TestDelegationLifecycleEvents:
    """VAL-RLM-014: Stream must include tool_call and tool_result events for delegation."""

    @pytest.mark.asyncio
    async def test_delegation_emits_tool_call_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """delegate_to_rlm tool usage appears as tool_call event in stream."""
        rt = _make_runtime(monkeypatch)

        rt.agent = _make_stub_agent(
            dspy.Prediction(
                response="child answer",
                trajectory={
                    "thought_0": "need to delegate",
                    "tool_name_0": "delegate_to_rlm",
                    "tool_args_0": {"query": "sub-task"},
                    "observation_0": '{"status": "ok", "answer": "child answer"}',
                },
            )
        )

        events = await _collect_stream(rt, "delegate")

        tool_calls = [e for e in events if e.kind == "tool_call"]
        assert len(tool_calls) >= 1, "Expected at least one tool_call event"
        delegate_calls = [e for e in tool_calls if "delegate_to_rlm" in (e.payload or {}).get("tool_name", "")]
        assert len(delegate_calls) >= 1, (
            f"Expected delegate_to_rlm in tool_call events, got: {[e.payload for e in tool_calls]}"
        )

    @pytest.mark.asyncio
    async def test_delegation_emits_tool_result_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """delegate_to_rlm produces a tool_result event containing the child outcome."""
        rt = _make_runtime(monkeypatch)

        rt.agent = _make_stub_agent(
            dspy.Prediction(
                response="final answer",
                trajectory={
                    "thought_0": "delegating",
                    "tool_name_0": "delegate_to_rlm",
                    "tool_args_0": {"query": "child task"},
                    "observation_0": '{"status": "ok", "answer": "child result"}',
                },
            )
        )

        events = await _collect_stream(rt, "delegate task")

        tool_results = [e for e in events if e.kind == "tool_result"]
        assert len(tool_results) >= 1, "Expected at least one tool_result event"

    @pytest.mark.asyncio
    async def test_delegation_events_precede_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Delegation lifecycle events must appear before the terminal 'done' event."""
        rt = _make_runtime(monkeypatch)

        rt.agent = _make_stub_agent(
            dspy.Prediction(
                response="final",
                trajectory={
                    "thought_0": "delegating",
                    "tool_name_0": "delegate_to_rlm",
                    "tool_args_0": {},
                    "observation_0": '{"status": "ok", "answer": "ok"}',
                },
            )
        )

        events = await _collect_stream(rt, "delegate")

        kinds = [e.kind for e in events]
        assert "done" in kinds, "Expected 'done' terminal event"
        done_idx = kinds.index("done")
        pre_terminal = kinds[:done_idx]
        assert "tool_call" in pre_terminal or "tool_result" in pre_terminal, (
            f"Expected delegation events before 'done', got: {pre_terminal}"
        )

    @pytest.mark.asyncio
    async def test_done_payload_includes_trajectory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'done' event payload carries trajectory with delegation step metadata."""
        rt = _make_runtime(monkeypatch)

        rt.agent = _make_stub_agent(
            dspy.Prediction(
                response="result",
                trajectory={
                    "thought_0": "planning",
                    "tool_name_0": "delegate_to_rlm",
                    "tool_args_0": {},
                    "observation_0": '{"status": "ok", "answer": "child"}',
                },
            )
        )

        events = await _collect_stream(rt, "task")

        done_events = [e for e in events if e.kind == "done"]
        assert len(done_events) == 1
        done = done_events[0]
        assert done.payload is not None
        assert "trajectory" in done.payload, "done payload must include trajectory"
        trajectory = done.payload["trajectory"]
        assert "steps" in trajectory


# ---------------------------------------------------------------------------
# VAL-RLM-016: TERMINAL_STREAM_EVENT_KINDS constant is consistent
# ---------------------------------------------------------------------------


class TestTerminalEventKindsConstant:
    """VAL-RLM-016: TERMINAL_STREAM_EVENT_KINDS must be consistent with runtime usage."""

    def test_terminal_kinds_include_done_and_error(self) -> None:
        """'done' and 'error' must both be terminal."""
        assert "done" in TERMINAL_STREAM_EVENT_KINDS
        assert "error" in TERMINAL_STREAM_EVENT_KINDS

    def test_is_terminal_helper_matches_set(self) -> None:
        """is_terminal_stream_event_kind must agree with TERMINAL_STREAM_EVENT_KINDS."""
        for kind in TERMINAL_STREAM_EVENT_KINDS:
            assert is_terminal_stream_event_kind(kind), f"{kind} should be terminal"
        for kind in ("status", "text", "reasoning", "tool_call", "tool_result", "clarification"):
            assert not is_terminal_stream_event_kind(kind), f"{kind} should NOT be terminal"

    def test_non_terminal_kinds_not_in_set(self) -> None:
        """Legacy/removed event kinds must not reappear in the terminal set."""
        legacy_kinds = {"final", "cancelled", "token"}
        overlap = legacy_kinds & TERMINAL_STREAM_EVENT_KINDS
        assert not overlap, f"Legacy event kinds must not be in TERMINAL_STREAM_EVENT_KINDS: {overlap}"
