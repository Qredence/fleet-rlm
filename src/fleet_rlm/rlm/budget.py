"""Thread-safe, Turn-owned admission accounting, shared with recursive children."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

DEFAULT_PARSE_RETRIES = 2
DEFAULT_FINALIZATION_ATTEMPTS = 2

# The monotonic deadline of the sandbox action whose host tool call is being
# served. The broker sets it on the thread that dispatches host tools, so a
# tool that waits on other work (recursive children) can finish in time for
# its result to be delivered.
_host_action_deadline: ContextVar[float | None] = ContextVar("fleet_host_action_deadline", default=None)


@contextmanager
def host_action_deadline(deadline: float) -> Iterator[None]:
    """Expose the calling sandbox action's deadline to host tools it dispatches."""
    token = _host_action_deadline.set(deadline)
    try:
        yield
    finally:
        _host_action_deadline.reset(token)


def current_host_action_deadline() -> float | None:
    """Return the deadline of the sandbox action being served, if any."""
    return _host_action_deadline.get()


class BudgetDimension(StrEnum):
    DEADLINE = "deadline"
    TOOL_CALLS = "tool_calls"
    RECURSIVE_CHILDREN = "recursive_children"
    EXECUTION_OUTPUT_BYTES = "execution_output_bytes"
    FINALIZATION_ATTEMPTS = "finalization_attempts"
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


class FinalizationExhausted(TimeoutError):  # noqa: N818 - domain exhaustion category
    """Raised when wrap-up finalization capacity is spent without a compliant SUBMIT.

    Deliberately a `TimeoutError` subclass: wrap-up exhaustion already
    participates in Turn deadline reserve accounting and must flow through the
    deadline handling that already exists. The distinct type exists so
    diagnostics can report exhaustion instead of a wall-clock expiry that never
    happened.
    """

    dimension = BudgetDimension.FINALIZATION_ATTEMPTS


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """None means observed accounting, not a claimed hard admission limit."""

    tool_calls: int | None = None
    recursive_children: int | None = None
    execution_output_bytes: int | None = None
    # ``None`` leaves shared Turn admission unbounded; each invocation still
    # applies its own ``AdapterBudget`` allowance.
    finalization_attempts: int | None = None
    finalization_seconds: float = 0.0

    def __post_init__(self) -> None:
        """
        Validate budget limits and finalization configuration.

        Raises:
            ValueError: If a count limit is not a nonnegative integer or finalization
                seconds is not finite and nonnegative.
        """
        for value in (
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


class TurnBudget:
    """Atomic reservations; admitted work is charged, not pre-start refusals.

    Finalization is an explicit caller capability, not a global mutable mode:
    concurrent child exploration cannot borrow the root's reserved capacity.
    Counts describe admitted operations. Token counts are deliberately absent.
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
            BudgetDimension.TOOL_CALLS: 0,
            BudgetDimension.RECURSIVE_CHILDREN: 0,
            BudgetDimension.EXECUTION_OUTPUT_BYTES: 0,
            BudgetDimension.FINALIZATION_ATTEMPTS: 0,
        }

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
            finalization (bool): Whether this reservation may use the finalization deadline reserve.

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
            self._used[dimension] += count
            return remaining

    def release_unstarted_recursive_child(self) -> None:
        """Undo one child reservation when capacity refused it before start."""
        with self._lock:
            if self._used[BudgetDimension.RECURSIVE_CHILDREN] <= 0:
                raise RuntimeError("no unstarted child reservation to release")
            self._used[BudgetDimension.RECURSIVE_CHILDREN] -= 1

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

    def reclassify_finalization(self, count: int = 1, *, shared: bool = True) -> None:
        """
        Check Turn admission and optionally consume shared root finalization capacity.

        Parameters:
                count (int): Number of finalization attempts to consume.
                shared (bool): Whether to charge the root allowance or only check child admission.

        Raises:
                ValueError: If count is not a nonnegative integer.
                TurnBudgetExhausted: If the deadline or finalization-attempt limit is exhausted.
        """
        if type(count) is not int or count < 0:
            raise ValueError("reclassification count must be a nonnegative integer")
        if shared:
            self.reserve(BudgetDimension.FINALIZATION_ATTEMPTS, count, finalization=True)
        else:
            # Child-local capacity never spends the root allowance, but cannot
            # outlive the shared Turn's admission authority or deadline.
            with self._lock:
                self._remaining(finalization=True)

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
    """Invocation-local repair and finalization policy for one RLM invocation.

    Finalization slots are counted locally, so a late exploration response can
    consume a slot without a second provider call. DSPy owns provider retries.
    """

    def __init__(
        self,
        *,
        max_parse_retries: int = DEFAULT_PARSE_RETRIES,
        max_finalization_attempts: int = DEFAULT_FINALIZATION_ATTEMPTS,
        turn: TurnBudget | None = None,
    ) -> None:
        """
        Initialize invocation-local repair and finalization policies.

        Parameters:
            max_parse_retries (int): Maximum number of parse-repair retries.
            max_finalization_attempts (int): Maximum number of finalization attempts.
            turn (TurnBudget | None): Shared turn ledger, or an unbounded one when omitted.

        Raises:
            ValueError: If a limit is not a nonnegative integer.
        """
        for value in (max_parse_retries, max_finalization_attempts):
            if type(value) is not int or value < 0:
                raise ValueError("repair attempt limits must be nonnegative integers")
        self.turn = turn or TurnBudget(deadline=None)
        self.max_parse_retries = max_parse_retries
        self.max_finalization_attempts = max_finalization_attempts
        self._finalization_used = 0
        self._parse_repairs_used = 0
        self._wrap_up_entered = False
        self._wrap_up_rejection_reason: str | None = None
        self._lock = Lock()

    def can_repair(self, retries: int) -> bool:
        """Determine whether another parse-repair attempt is allowed.

        Parameters:
                retries (int): Number of parse-repair attempts already used.

        Returns:
                bool: `true` if another repair attempt is allowed, `false` otherwise.
        """
        return retries < self.max_parse_retries

    @property
    def parse_repairs_used(self) -> int:
        """Report the number of corrective parse re-asks issued by this invocation.

        A parse re-ask is a full additional provider call. Counting it makes
        cap-saturated actions legible: an action whose response hits the output
        ceiling cannot close its JSON, so it pays for a second call to recover.

        Returns:
                int: The number of parse-repair re-asks consumed.
        """
        with self._lock:
            return self._parse_repairs_used

    def note_parse_repair(self) -> None:
        """Admit and record one corrective parse re-ask against this invocation."""
        with self._lock:
            # A returned malformed response cannot start new Fleet correction
            # work after settlement. This does not intercept DSPy retries or
            # impose a deadline on an already-running LM request.
            self.turn.remaining(finalization=True)
            self._parse_repairs_used += 1

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
        """Raise `FinalizationExhausted` when the local finalization-attempt limit is exhausted."""
        if self._finalization_used >= self.max_finalization_attempts:
            raise FinalizationExhausted("wrap-up finalization attempts exhausted before a compliant SUBMIT")

    def reclassify_late_response(self, *, can_finalize: bool = True) -> None:
        """Reclassify an already-returned response as a finalization attempt.

        No provider-attempt counter is charged. Root corrections consume both
        local allowance and shared root capacity; child corrections consume
        only local allowance after checking shared Turn admission.
        """
        with self._lock:
            self._check_finalization()
            self.turn.reclassify_finalization(shared=can_finalize)
            self._finalization_used += 1

    def enter_wrap_up(self, *, rejection_reason: str | None = None) -> None:
        """Record the first wrap-up transition and any bounded rejection reason."""
        with self._lock:
            self._wrap_up_entered = True
            if rejection_reason is not None:
                self._wrap_up_rejection_reason = rejection_reason

    def set_wrap_up_rejection(self, reason: str) -> None:
        """Update the wrap-up rejection reason after an already-entered reserve."""
        with self._lock:
            self._wrap_up_rejection_reason = reason

    def wrap_up_summary(self) -> dict[str, object]:
        """Return bounded engineering metadata for the current wrap-up reserve."""
        with self._lock:
            return {
                "wrap_up_entered": self._wrap_up_entered,
                "wrap_up_attempts": self._finalization_used,
                "wrap_up_rejection_reason": self._wrap_up_rejection_reason,
            }
