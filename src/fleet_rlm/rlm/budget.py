"""Thread-safe, Turn-owned admission accounting, shared with recursive children."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock


class BudgetDimension(StrEnum):
    DEADLINE = "deadline"
    PROVIDER_ATTEMPTS = "provider_attempts"
    TOOL_CALLS = "tool_calls"
    RECURSIVE_CHILDREN = "recursive_children"
    EXECUTION_OUTPUT_BYTES = "execution_output_bytes"
    SETTLED = "settled"


class TurnBudgetExhausted(RuntimeError):  # noqa: N818 - domain exhaustion category
    def __init__(self, dimension: BudgetDimension) -> None:
        self.dimension = dimension
        super().__init__(f"Turn budget exhausted: {dimension.value}")


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """None means observed accounting, not a claimed hard admission limit."""

    provider_attempts: int | None = None
    tool_calls: int | None = None
    recursive_children: int | None = None
    execution_output_bytes: int | None = None
    finalization_attempts: int = 0
    finalization_seconds: float = 0.0

    def __post_init__(self) -> None:
        for value in (
            self.provider_attempts,
            self.tool_calls,
            self.recursive_children,
            self.execution_output_bytes,
            self.finalization_attempts,
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("budget counts must be nonnegative integers")
        if not math.isfinite(self.finalization_seconds) or self.finalization_seconds < 0:
            raise ValueError("finalization_seconds must be finite and nonnegative")
        if self.provider_attempts is not None and self.finalization_attempts > self.provider_attempts:
            raise ValueError("finalization reserve exceeds provider attempt limit")


class TurnBudget:
    """Atomic monotonic reservations; failed work is charged, never refunded.

    Finalization is an explicit caller capability, not a global mutable mode:
    concurrent child exploration cannot borrow the root's reserved capacity.
    Counts describe admitted operations. Provider caching may mean an admitted
    attempt does not reach the network; token counts are deliberately absent.
    """

    def __init__(self, *, deadline: float, limits: BudgetLimits | None = None) -> None:
        if not math.isfinite(deadline):
            raise ValueError("deadline must be finite")
        self.deadline = deadline
        self.limits = limits or BudgetLimits()
        self._lock = Lock()
        self._settled = False
        self._used = {
            BudgetDimension.PROVIDER_ATTEMPTS: 0,
            BudgetDimension.TOOL_CALLS: 0,
            BudgetDimension.RECURSIVE_CHILDREN: 0,
            BudgetDimension.EXECUTION_OUTPUT_BYTES: 0,
        }
        self._exploration_attempts = 0

    def reserve(
        self,
        dimension: BudgetDimension,
        count: int = 1,
        *,
        finalization: bool = False,
    ) -> float:
        """Debit atomically and return remaining time for the admitted operation."""
        if dimension not in self._used:
            raise ValueError("dimension is not a reservable counter")
        if type(count) is not int or count < 0:
            raise ValueError("reservation count must be a nonnegative integer")
        with self._lock:
            remaining = self._remaining(finalization=finalization)
            limit = getattr(self.limits, dimension.value)
            if limit is not None and self._used[dimension] + count > limit:
                raise TurnBudgetExhausted(dimension)
            if dimension == BudgetDimension.PROVIDER_ATTEMPTS and not finalization:
                if limit is not None and (
                    self._exploration_attempts + count > limit - self.limits.finalization_attempts
                ):
                    raise TurnBudgetExhausted(dimension)
                self._exploration_attempts += count
            self._used[dimension] += count
            return remaining

    def _remaining(self, *, finalization: bool) -> float:
        if self._settled:
            raise TurnBudgetExhausted(BudgetDimension.SETTLED)
        remaining = self.deadline - time.monotonic()
        if not finalization:
            remaining -= self.limits.finalization_seconds
        if remaining <= 0:
            raise TurnBudgetExhausted(BudgetDimension.DEADLINE)
        return remaining

    def remaining(self, *, finalization: bool = False) -> float:
        with self._lock:
            return self._remaining(finalization=finalization)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {dimension.value: count for dimension, count in self._used.items()}

    def settle(self) -> None:
        """Close admission. Owning lifecycle must separately cancel in-flight work."""
        with self._lock:
            self._settled = True
