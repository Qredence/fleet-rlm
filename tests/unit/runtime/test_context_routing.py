"""Tests for large-context routing helpers."""

from __future__ import annotations

from pathlib import Path

import dspy

from fleet_rlm.runtime.agent.turn_context import TurnContext
from fleet_rlm.runtime.modules.context_routing import (
    build_turn_context,
    estimate_turn_context_chars,
    should_auto_route_large_context,
)
from fleet_rlm.runtime.modules.variable_mode import VARIABLE_MODE_THRESHOLD


def test_estimate_turn_context_includes_message_and_history() -> None:
    history = dspy.History(messages=[{"user_message": "x" * 1000, "response": "y" * 500}])
    total, sources = estimate_turn_context_chars(user_request="hello", history=history)
    assert total >= 1505
    assert any(source.startswith("history:") for source in sources)


def test_should_auto_route_when_over_threshold(tmp_path: Path) -> None:
    doc = tmp_path / "large.txt"
    doc.write_text("a" * (VARIABLE_MODE_THRESHOLD + 100))
    turn_context = build_turn_context(user_request="analyze", docs_path=str(doc))
    assert turn_context.estimated_chars >= VARIABLE_MODE_THRESHOLD
    assert should_auto_route_large_context(execution_mode="auto", turn_context=turn_context)


def test_should_not_auto_route_small_context() -> None:
    turn_context = TurnContext(estimated_chars=100, threshold_chars=VARIABLE_MODE_THRESHOLD)
    assert not should_auto_route_large_context(execution_mode="auto", turn_context=turn_context)
