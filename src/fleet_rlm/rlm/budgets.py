"""Immutable Run limits and one execution-local mutable ledger."""

from __future__ import annotations

from dataclasses import dataclass

from fleet_rlm.rlm.errors import RunBudgetError, TurnBudgetExhausted


@dataclass(frozen=True, slots=True)
class RunBudget:
    """Safety limits for one recursive Run.

    ``max_iterations`` maps to the installed DSPy 3.3.0b1 constructor spelling.
    """

    max_iterations: int = 6
    max_llm_calls: int = 16
    max_output_chars: int = 3_000
    max_wall_seconds: int = 900
    max_sub_lm_concurrency: int = 8
    max_tool_calls: int = 32
    max_skill_loads: int = 8

    def __post_init__(self) -> None:
        for name, value in (
            ("max_iterations", self.max_iterations),
            ("max_llm_calls", self.max_llm_calls),
            ("max_output_chars", self.max_output_chars),
            ("max_wall_seconds", self.max_wall_seconds),
            ("max_sub_lm_concurrency", self.max_sub_lm_concurrency),
            ("max_tool_calls", self.max_tool_calls),
            ("max_skill_loads", self.max_skill_loads),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RunBudgetError(f"{name} must be a positive integer, got {value!r}")


class RunBudgetExceeded(TurnBudgetExhausted):
    """Raised when one ledger dimension would exceed its immutable limit."""

    def __init__(self, dimension: str) -> None:
        self.dimension = dimension
        super().__init__(f"Turn budget exhausted: {dimension}")


@dataclass(slots=True)
class RunBudgetLedger:
    """Mutable accounting scoped to one runner invocation."""

    budget: RunBudget = RunBudget()
    iterations: int = 0
    llm_calls: int = 0
    output_chars: int = 0
    tool_calls: int = 0
    skill_loads: int = 0

    @staticmethod
    def _amount(value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("budget consumption must be a positive integer")
        return value

    def _consume(self, dimension: str, limit_name: str, amount: int) -> None:
        amount = self._amount(amount)
        current = int(getattr(self, dimension))
        limit = int(getattr(self.budget, limit_name))
        if current + amount > limit:
            raise RunBudgetExceeded(dimension)
        setattr(self, dimension, current + amount)

    def consume_iterations(self, amount: int = 1) -> None:
        self._consume("iterations", "max_iterations", amount)

    def consume_llm_calls(self, amount: int = 1) -> None:
        self._consume("llm_calls", "max_llm_calls", amount)

    def consume_output_chars(self, amount: int = 1) -> None:
        self._consume("output_chars", "max_output_chars", amount)

    def consume_tool_calls(self, amount: int = 1) -> None:
        self._consume("tool_calls", "max_tool_calls", amount)

    def consume_skill_loads(self, amount: int = 1) -> None:
        self._consume("skill_loads", "max_skill_loads", amount)

    def snapshot(self) -> dict[str, int]:
        """Return measured usage without exposing mutable ledger state."""
        return {
            "iterations": self.iterations,
            "llm_calls": self.llm_calls,
            "output_chars": self.output_chars,
            "tool_calls": self.tool_calls,
            "skill_loads": self.skill_loads,
        }
