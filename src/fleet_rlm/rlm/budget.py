"""Thread-safe, Turn-owned admission accounting, shared with recursive children."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

DEFAULT_PARSE_RETRIES = 2
DEFAULT_FINALIZATION_ATTEMPTS = 2


class BudgetDimension(StrEnum):
    DEADLINE = "deadline"
    PROVIDER_ATTEMPTS = "provider_attempts"
    TOOL_CALLS = "tool_calls"
    RECURSIVE_CHILDREN = "recursive_children"
    EXECUTION_OUTPUT_BYTES = "execution_output_bytes"
    SETTLED = "settled"


class TurnBudgetExhausted(RuntimeError):  # noqa: N818 - domain exhaustion category
    def __init__(self, dimension: BudgetDimension) -> None:
        """
        Initialize an exception identifying the exhausted turn-budget dimension.

        Parameters:
                dimension (BudgetDimension): The budget dimension that has been exhausted.
        """
        self.dimension = dimension
        super().__init__(f"Turn budget exhausted: {dimension.value}")


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """None means observed accounting, not a claimed hard admission limit."""

    provider_attempts: int | None = None
    tool_calls: int | None = None
    recursive_children: int | None = None
    execution_output_bytes: int | None = None
    # ``None`` leaves finalization admission to the invocation-local
    # ``AdapterBudget``. A value, including zero, is an explicit global ceiling.
    finalization_attempts: int | None = None
    finalization_seconds: float = 0.0

    def __post_init__(self) -> None:
        """
        Validate budget limits and finalization configuration.

        Raises:
            ValueError: If a count limit is not a nonnegative integer, finalization
                seconds is not finite and nonnegative, or finalization attempts exceed
                the provider-attempt limit.
        """
        for value in (
            self.provider_attempts,
            self.tool_calls,
            self.recursive_children,
            self.execution_output_bytes,
            self.finalization_attempts,
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("budget counts must be nonnegative integers")
        if (
            not isinstance(self.finalization_seconds, (int, float))
            or isinstance(self.finalization_seconds, bool)
            or not math.isfinite(self.finalization_seconds)
            or self.finalization_seconds < 0
        ):
            raise ValueError("finalization_seconds must be finite and nonnegative")
        if (
            self.provider_attempts is not None
            and self.finalization_attempts is not None
            and self.finalization_attempts > self.provider_attempts
        ):
            raise ValueError("finalization reserve exceeds provider attempt limit")


class TurnBudget:
    """Atomic monotonic reservations; failed work is charged, never refunded.

    Finalization is an explicit caller capability, not a global mutable mode:
    concurrent child exploration cannot borrow the root's reserved capacity.
    Counts describe admitted operations. Provider caching may mean an admitted
    attempt does not reach the network; token counts are deliberately absent.
    """

    def __init__(self, *, deadline: float | None, limits: BudgetLimits | None = None) -> None:
        """
        Initialize a turn budget with an optional deadline and resource limits.

        Parameters:
            deadline (float | None): Finite absolute deadline, or None for no deadline.
            limits (BudgetLimits | None): Resource and finalization limits for the turn.

        Raises:
            ValueError: If deadline is not a finite number or None.
        """
        if deadline is not None and (
            not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or not math.isfinite(deadline)
        ):
            raise ValueError("deadline must be a finite number or None")
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
        self._finalization_attempts = 0

    def reserve(
        self,
        dimension: BudgetDimension,
        count: int = 1,
        *,
        finalization: bool = False,
    ) -> float:
        """
        Atomically admit an operation against a budget dimension and report its remaining time.

        Parameters:
            dimension (BudgetDimension): Resource dimension to charge.
            count (int): Amount to charge.
            finalization (bool): Whether the provider-attempt reservation uses finalization capacity.

        Returns:
            float: Time remaining for the admitted operation.

        Raises:
            ValueError: If the dimension is not reservable or count is not a nonnegative integer.
            TurnBudgetExhausted: If the reservation exceeds a budget limit or available deadline.
        """
        if dimension not in self._used:
            raise ValueError("dimension is not a reservable counter")
        if type(count) is not int or count < 0:
            raise ValueError("reservation count must be a nonnegative integer")
        with self._lock:
            remaining = self._remaining(finalization=finalization)
            limit = getattr(self.limits, dimension.value)
            if limit is not None and self._used[dimension] + count > limit:
                raise TurnBudgetExhausted(dimension)
            if dimension == BudgetDimension.PROVIDER_ATTEMPTS and finalization:
                finalization_limit = self.limits.finalization_attempts
                if finalization_limit is not None and self._finalization_attempts + count > finalization_limit:
                    raise TurnBudgetExhausted(dimension)
                self._finalization_attempts += count
            elif dimension == BudgetDimension.PROVIDER_ATTEMPTS:
                finalization_reserve = self.limits.finalization_attempts or 0
                if limit is not None and (self._exploration_attempts + count > limit - finalization_reserve):
                    raise TurnBudgetExhausted(dimension)
                self._exploration_attempts += count
            self._used[dimension] += count
            return remaining

    def _remaining(self, *, finalization: bool) -> float:
        """
        Calculate the time available for an operation.

        Parameters:
            finalization (bool): Whether the operation is a finalization attempt.

        Returns:
            float: Available time in seconds.

        Raises:
            TurnBudgetExhausted: If the budget is settled or the available time is exhausted.
        """
        if self._settled:
            raise TurnBudgetExhausted(BudgetDimension.SETTLED)
        remaining = math.inf if self.deadline is None else self.deadline - time.monotonic()
        if not finalization:
            remaining -= self.limits.finalization_seconds
        if remaining <= 0:
            raise TurnBudgetExhausted(BudgetDimension.DEADLINE)
        return remaining

    def remaining(self, *, finalization: bool = False) -> float:
        """
        Determine the time available for an operation.

        Parameters:
            finalization (bool): Whether the operation is a finalization attempt.

        Returns:
            float: Available seconds for the operation.
        """
        with self._lock:
            return self._remaining(finalization=finalization)

    def reclassify_finalization(self, count: int = 1) -> None:
        """
        Consume finalization capacity for an already-admitted response.

        Parameters:
                count (int): Number of finalization attempts to consume.

        Raises:
                ValueError: If count is not a nonnegative integer.
                TurnBudgetExhausted: If the deadline or finalization-attempt limit is exhausted.
        """
        if type(count) is not int or count < 0:
            raise ValueError("reclassification count must be a nonnegative integer")
        with self._lock:
            self._remaining(finalization=True)
            limit = self.limits.finalization_attempts
            if limit is not None and self._finalization_attempts + count > limit:
                raise TurnBudgetExhausted(BudgetDimension.PROVIDER_ATTEMPTS)
            self._finalization_attempts += count

    def exploration_exhausted(self) -> bool:
        """
        Determine whether provider exploration has exhausted its available capacity.

        Returns:
            bool: `True` if the provider-attempt limit or exploration capacity before the
                finalization reserve has been reached, `False` otherwise.
        """
        with self._lock:
            limit = self.limits.provider_attempts
            finalization_reserve = self.limits.finalization_attempts or 0
            return limit is not None and (
                self._used[BudgetDimension.PROVIDER_ATTEMPTS] >= limit
                or self._exploration_attempts >= limit - finalization_reserve
            )

    def snapshot(self) -> dict[str, int]:
        """
        Return the current usage counts for each tracked budget dimension.

        Returns:
                dict[str, int]: A mapping from budget dimension names to their consumed counts.
        """
        with self._lock:
            return {dimension.value: count for dimension, count in self._used.items()}

    def settle(self) -> None:
        """Close admission. Owning lifecycle must separately cancel in-flight work."""
        with self._lock:
            self._settled = True


class AdapterBudget:
    """Invocation-local repair policy over one shared Turn admission ledger.

    Finalization slots count physical provider admissions, including transport
    retries and DSPy schema fallback. A late exploration response can consume a
    finalization slot without charging its provider attempt a second time.
    """

    def __init__(
        self,
        *,
        deadline: float | None = None,
        reserve_seconds: float = 0.0,
        max_parse_retries: int = DEFAULT_PARSE_RETRIES,
        max_finalization_attempts: int = DEFAULT_FINALIZATION_ATTEMPTS,
        turn: TurnBudget | None = None,
    ) -> None:
        # Existing preparation-only seams use +inf for an unbounded invocation.
        """
        Initialize invocation-local budget and finalization policies.

        Parameters:
            deadline (float | None): Absolute deadline for the invocation; positive infinity means unbounded.
            reserve_seconds (float): Time reserved for finalization.
            max_parse_retries (int): Maximum number of parse-repair retries.
            max_finalization_attempts (int): Maximum number of finalization attempts.
            turn (TurnBudget | None): Shared turn budget, or a new budget when omitted.

        Raises:
            ValueError: If a numeric limit is invalid, negative, non-finite where
                prohibited, or not an integer where required.
        """
        if deadline == math.inf:
            deadline = None
        if deadline is not None and (
            not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or not math.isfinite(deadline)
        ):
            raise ValueError("deadline must be finite or None")
        if (
            not isinstance(reserve_seconds, (int, float))
            or isinstance(reserve_seconds, bool)
            or not math.isfinite(reserve_seconds)
            or reserve_seconds < 0
        ):
            raise ValueError("reserve_seconds must be finite and nonnegative")
        for value in (max_parse_retries, max_finalization_attempts):
            if type(value) is not int or value < 0:
                raise ValueError("repair attempt limits must be nonnegative integers")
        # Without an explicitly supplied Turn ledger, finalization remains
        # governed by this invocation-local adapter cap. A shared production
        # ledger may opt into a global finalization ceiling via its limits.
        self.turn = turn or TurnBudget(deadline=deadline)
        self.deadline = deadline
        self.reserve_seconds = reserve_seconds
        self.max_parse_retries = max_parse_retries
        self.max_finalization_attempts = max_finalization_attempts
        self._finalization_used = 0
        self._lock = Lock()

    def remaining(self) -> float | None:
        """
        Determine the time remaining for the current invocation.

        Returns:
            float | None: Available time in seconds, or `None` when the deadline is unbounded.

        Raises:
            TimeoutError: If the turn or invocation deadline has expired.
        """
        try:
            remaining = self.turn.remaining(finalization=True)
        except TurnBudgetExhausted as exc:
            if exc.dimension != BudgetDimension.DEADLINE:
                raise
            raise TimeoutError("Turn deadline exceeded") from exc
        if self.deadline is not None:
            remaining = min(remaining, self.deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("Turn deadline exceeded")
        return remaining if math.isfinite(remaining) else None

    def can_repair(self, retries: int) -> bool:
        """Determine whether another parse-repair attempt is allowed.

        Parameters:
                retries (int): Number of parse-repair attempts already used.

        Returns:
                bool: `true` if another repair attempt is allowed, `false` otherwise.
        """
        return retries < self.max_parse_retries

    @property
    def finalization_used(self) -> int:
        """Report the number of finalization attempts used by this invocation.

        Returns:
                int: The number of finalization attempts consumed.
        """
        with self._lock:
            return self._finalization_used

    def can_finalize(self) -> bool:
        """Determine whether another finalization attempt is available.

        Returns:
                bool: `true` if the finalization limit has not been reached, `false` otherwise.
        """
        return self.finalization_used < self.max_finalization_attempts

    def _check_finalization(self) -> None:
        """Raise ``TimeoutError`` when the local finalization-attempt limit is exhausted."""
        if self._finalization_used >= self.max_finalization_attempts:
            raise TimeoutError("wrap-up action did not submit before the Turn deadline")

    def reclassify_late_response(self) -> None:
        """Reclassify a previously admitted response as a finalization attempt
        without charging another provider attempt.
        """
        with self._lock:
            self._check_finalization()
            self.turn.reclassify_finalization()
            self._finalization_used += 1

    def reserve_provider(self, *, action: bool, wrap_up: bool, can_finalize: bool) -> float:
        """
        Reserve a provider attempt for an action or finalization call.

        Parameters:
            action (bool): Whether the call performs an action.
            wrap_up (bool): Whether the call is a wrap-up finalization call.
            can_finalize (bool): Whether the call may use finalization capacity.

        Returns:
            float: The time available for the admitted provider call.

        Raises:
            TimeoutError: If the available time is exhausted.
        """
        with self._lock:
            if wrap_up:
                self._check_finalization()
            remaining = self.remaining()
            available = math.inf if remaining is None else remaining
            if action and not wrap_up:
                available -= self.reserve_seconds
            if available <= 0:
                raise TimeoutError("Turn final-answer reserve exhausted")
            remaining_turn = self.turn.reserve(
                BudgetDimension.PROVIDER_ATTEMPTS,
                finalization=can_finalize and (wrap_up or not action),
            )
            if wrap_up:
                self._finalization_used += 1
            return min(available, remaining_turn)


@dataclass(frozen=True, slots=True)
class ProviderAdmission:
    """An explicit call-local capability, never provider kwargs or global mode."""

    budget: AdapterBudget
    action: bool
    wrap_up: bool
    can_finalize: bool

    def reserve(self) -> float:
        """Reserve admission for the provider call.

        Returns:
                float: The remaining time available for the call.
        """
        return self.budget.reserve_provider(action=self.action, wrap_up=self.wrap_up, can_finalize=self.can_finalize)
