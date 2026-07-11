"""Finite RLM execution budgets. Invalid values fail closed before construction."""

from __future__ import annotations

from dataclasses import dataclass

from fleet_rlm_clean.rlm.errors import RLMBudgetError


@dataclass(frozen=True, slots=True)
class RLMBudget:
    """Safety limits for one recursive turn.

    ``max_iterations`` maps to installed ``dspy.RLM(..., max_iterations=...)``
    (not the docs-only name ``max_iters``).
    """

    max_iterations: int = 20
    max_llm_calls: int = 50
    max_output_chars: int = 10_000
    max_wall_seconds: int = 300
    max_sub_lm_concurrency: int = 8
    max_tool_calls: int = 32
    max_skill_loads: int = 8
    max_artifact_bytes: int = 10 * 1024 * 1024

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Raise RLMBudgetError when any required limit is non-positive."""
        required_positive = {
            "max_iterations": self.max_iterations,
            "max_llm_calls": self.max_llm_calls,
            "max_output_chars": self.max_output_chars,
            "max_wall_seconds": self.max_wall_seconds,
            "max_sub_lm_concurrency": self.max_sub_lm_concurrency,
            "max_tool_calls": self.max_tool_calls,
            "max_skill_loads": self.max_skill_loads,
            "max_artifact_bytes": self.max_artifact_bytes,
        }
        for name, value in required_positive.items():
            if not isinstance(value, int) or value <= 0:
                raise RLMBudgetError(f"{name} must be a positive integer, got {value!r}")
