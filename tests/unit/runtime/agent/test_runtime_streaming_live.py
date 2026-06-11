"""Integration-style tests for live unified streaming."""

from __future__ import annotations

import asyncio
from typing import Any

import dspy
import pytest

from fleet_rlm.runtime.agent.runtime_streaming import (
    _await_turn_with_live_progress,
    _TurnComplete,
    aiter_chat_turn_stream,
)
from fleet_rlm.runtime.agent.turn_progress_relay import TurnProgressRelay
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind


class _SlowAgent:
    async def aforward(self, **kwargs: Any) -> dspy.Prediction:
        await asyncio.sleep(0.2)
        return dspy.Prediction(response="done")


class _RuntimeStub:
    agent = _SlowAgent()
    execution_mode = "auto"
    history = dspy.History(messages=[])
    history_max_turns = 10
    _use_escalation = False

    def _escalation_call_args(self, message: str) -> dict[str, Any]:
        return {"user_request": message}

    def history_turns(self) -> int:
        return 0

    def _runtime_observability_payload(self) -> dict[str, Any]:
        return {}

    def _runtime_event_context(self) -> dict[str, Any]:
        return {}


@pytest.mark.asyncio
async def test_await_turn_with_live_progress_emits_heartbeat_while_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.runtime_streaming._turn_heartbeat_seconds",
        lambda: 0.05,
    )
    runtime = _RuntimeStub()
    events = [
        item
        async for item in _await_turn_with_live_progress(runtime, message="hello", cancel_check=None)
        if not isinstance(item, _TurnComplete)
    ]
    assert any(
        event.kind == RuntimeEventKind.STATUS and event.payload.get("phase") == "rlm_progress" for event in events
    )


@pytest.mark.asyncio
async def test_unified_stream_drains_relay_events_before_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _RuntimeStub()
    loop = asyncio.get_running_loop()
    relay = TurnProgressRelay(loop=loop)
    runtime._turn_progress_relay = relay

    async def _fast_turn(**kwargs: Any) -> dspy.Prediction:
        await relay.emit(RuntimeEvent.reasoning("live step during turn"))
        return dspy.Prediction(
            response="done",
            trajectory={"steps": [{"index": 0, "thought": "live step during turn"}]},
        )

    runtime.agent.aforward = _fast_turn  # type: ignore[method-assign]

    events = [event async for event in aiter_chat_turn_stream(runtime, message="hello", cancel_check=None)]
    reasoning_texts = [event.text for event in events if event.kind == RuntimeEventKind.REASONING]
    assert "live step during turn" in reasoning_texts
    assert reasoning_texts.count("live step during turn") == 1
