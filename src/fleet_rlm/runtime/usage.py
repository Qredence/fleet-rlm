"""Provider-neutral usage telemetry shape shared by persistence and RLM."""

from __future__ import annotations

from typing import TypedDict

from fleet_rlm.json_types import JsonValue


class RLMUsage(TypedDict):
    """Closed public and durable usage observed for one RLM Turn."""

    iterations: int
    observed_lm_usage: dict[str, dict[str, JsonValue]]
    duration_ms: int


def empty_rlm_usage() -> RLMUsage:
    """Return canonical empty usage for outcomes without a Prediction."""
    return RLMUsage(iterations=0, observed_lm_usage={}, duration_ms=0)


__all__ = ["RLMUsage", "empty_rlm_usage"]
