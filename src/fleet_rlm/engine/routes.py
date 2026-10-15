"""The one execution-route vocabulary shared by triage, the engine, events, scorers, and gold labels."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class Route(StrEnum):
    """How a Turn is executed. Values are the wire, trace, and label names."""

    DIRECT = "direct"  # ChainOfThought, no code execution
    FLEX = "flex"  # dspy.Flex pipeline with Daytona Python
    RLM = "rlm"  # dspy.RLM with Daytona Python and child agents


# Names written by releases before the single vocabulary; read-side only, never emitted.
_LEGACY = {
    "direct_cot": Route.DIRECT,
    "single_rlm": Route.FLEX,
    "flex_pipeline": Route.FLEX,
    "delegated_rlm": Route.RLM,
    "deep_rlm": Route.RLM,
}


def parse_route(value: Any) -> Route | None:
    """Read a route from a decision, stored turn, trace, or gold label; None when absent or unknown."""
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    text = str(raw).strip()
    try:
        return Route(text)
    except ValueError:
        return _LEGACY.get(text)
