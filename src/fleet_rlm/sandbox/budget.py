"""Turn budget, deadline, watchdog, and execution-fence mixin for DaytonaSandboxManager."""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

from fleet_rlm.sandbox import repl

if TYPE_CHECKING:
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

# Distinguishes "the caller supplied no Turn policy" from an explicit `None` (deadline-free).
_UNSET = object()


class SandboxBudgetMixin:
    """Mixin owning Turn deadlines, generation-bound watchdogs, execution fences, and shared budgets."""

    __slots__ = ()

    @property
    def root(self: DaytonaSandboxManager) -> DaytonaSandboxManager:
        """Return the top-level manager that owns the Turn budget and deadline."""
        return self.parent.root if self.parent is not None else self

    @property
    def depth(self: DaytonaSandboxManager) -> int:
        """Return how many parents separate this agent from the root."""
        return self.parent.depth + 1 if self.parent is not None else 0

    @property
    def turn_active(self: DaytonaSandboxManager) -> bool:
        """True while a Turn owns this manager, including a deadline-free Turn."""
        return self.root._turn_active

    def deadline_provenance(self: DaytonaSandboxManager) -> dict[str, Any]:
        """Return the Turn's resolved source and effective duration; `None` is kept explicit."""
        root = self.root
        return {
            "deadline_source": root._deadline_source,
            "effective_seconds": root._effective_deadline_seconds,
        }

    def check_turn(self: DaytonaSandboxManager, *, minimum_seconds: float = 0.0) -> None:
        """Raise if the Turn was cancelled or its deadline is within `minimum_seconds`."""
        from fleet_rlm.sandbox import manager as _mgr

        root = self.root
        if root._cancelled.is_set():
            raise RuntimeError("Turn cancelled")
        if root._deadline is not None and _mgr.time.monotonic() + minimum_seconds >= root._deadline:
            self._expire_turn()

    def _expire_turn(self: DaytonaSandboxManager) -> None:
        """Mark the Turn cancelled and stop publication, then raise the deadline error."""
        root = self.root
        root._cancelled.set()
        root._fence_executions()
        raise TimeoutError("Turn deadline exceeded")

    def admit_execution(self: DaytonaSandboxManager) -> float | None:
        """Sample the remaining deadline once and admit one execution, or expire the Turn."""
        root = self.root
        if root._cancelled.is_set():
            raise RuntimeError("Turn cancelled")
        remaining = self.remaining_turn_seconds()
        if root._deadline is not None and remaining is not None and remaining < 1.0:
            self._expire_turn()
        return remaining

    def _open_execution_fence(self: DaytonaSandboxManager) -> repl.ExecutionFence:
        """Return this execution's publication token, already closed if the Turn is cancelled."""
        root = self.root
        with root._lock:
            fence = repl.ExecutionFence(root.event_callback)
            if root._cancelled.is_set():
                fence.close()
            root._execution_fences.add(fence)
        return fence

    def _report_fence(self: DaytonaSandboxManager, fence: repl.ExecutionFence, ns_id: str) -> None:
        """Deliver a refused publication through the emitter its execution opened with."""
        from fleet_rlm.sandbox import manager as _mgr

        emitter = fence.emitter
        if emitter is None:
            return
        try:
            emitter(
                {
                    "event": "fenced",
                    "agent_id": self.agent_id,
                    "parent_agent_id": self.parent.agent_id if self.parent else None,
                    "sandbox_id": getattr(self._sandbox, "id", None),
                    "namespace": ns_id,
                }
            )
        except Exception as exc:
            _mgr._LOGGER.warning(
                "Fenced observation could not be delivered for Session %s (%s)",
                self.session_id,
                type(exc).__name__,
            )

    def _release_execution_fence(self: DaytonaSandboxManager, fence: repl.ExecutionFence) -> None:
        """End this execution's publication window and stop tracking its token."""
        fence.close()
        root = self.root
        with root._lock:
            root._execution_fences.discard(fence)

    def _fence_executions(self: DaytonaSandboxManager) -> None:
        """Close every live execution token so no successful reply can publish afterwards."""
        root = self.root
        with root._lock:
            fences = tuple(root._execution_fences)
        for fence in fences:
            fence.close()

    def remaining_turn_seconds(self: DaytonaSandboxManager) -> float | None:
        """Seconds left in this Turn's deadline; None when the Turn has no Fleet deadline."""
        from fleet_rlm.sandbox import manager as _mgr

        root = self.root
        if root._deadline is not None:
            return root._deadline - _mgr.time.monotonic()
        return None

    def reset_turn_budget(
        self: DaytonaSandboxManager,
        *,
        deadline_seconds: int | object | None = _UNSET,
        deadline_source: str | None = None,
    ) -> None:
        """Start a fresh Turn: zero the counters, clear receipts, and apply the deadline policy."""
        from fleet_rlm.sandbox import manager as _mgr

        root = self.root
        if deadline_seconds is _UNSET:
            seconds: int | None = root.turn_timeout_seconds
            source = deadline_source or "configuration"
        else:
            seconds = cast("int | None", deadline_seconds)
            source = deadline_source or "request"
        effective = seconds if isinstance(seconds, int) and seconds > 0 else None
        # Serialize the generation check and cancellation with Turn end/reset. Timer.cancel()
        # alone cannot revoke a callback that already started on its timer thread.
        with root._turn_lock:
            root.end_turn()
            # A new Turn owns a new teardown; the previous Turn's outcome and owner must never be reused.
            with root._teardown_lock:
                root._cleanup_outcome = None
                root._cancellation_claimed = False
                root._teardown_done.clear()
            with root._budget_lock:
                root._llm_calls_used = 0
                root._sandbox_errors = 0
                root._submit_rejections = 0
                root._budget_breakdown = dict.fromkeys(root._budget_breakdown, 0)
                root._allocations = 0
                root._child_receipts.clear()
                root._cancelled.clear()
                root._turn_generation += 1
                root._turn_active = True
                root._deadline_source = source
                root._effective_deadline_seconds = effective
                if effective is None:
                    # A deadline-free Turn has no Fleet deadline, so there is no timer to arm.
                    root._deadline = None
                    root._watchdog = None
                else:
                    root._deadline = _mgr.time.monotonic() + effective
                    generation = root._turn_generation
                    root._watchdog = threading.Timer(effective, lambda: root._expire_generation(generation))
                    root._watchdog.daemon = True
                    root._watchdog.start()

    def _expire_generation(self: DaytonaSandboxManager, generation: int) -> None:
        """A watchdog callback only acts for the Turn that armed it; a stale one is a no-op."""
        root = self.root
        with root._turn_lock:
            if not root._turn_active or root._turn_generation != generation:
                return
            root.request_cancellation()

    def end_turn(self: DaytonaSandboxManager) -> None:
        """Cancel the watchdog and clear the Turn deadline without touching provenance."""
        root = self.root
        with root._turn_lock:
            if root._watchdog is not None:
                root._watchdog.cancel()
                root._watchdog = None
            root._deadline = None
            root._turn_active = False

    @contextmanager
    def turn_scope(
        self: DaytonaSandboxManager,
        *,
        deadline_seconds: int | object | None = _UNSET,
        deadline_source: str | None = None,
    ) -> Generator[None, None, None]:
        """Own a Turn only when nobody owns one already, and end only the Turn this scope started."""
        owns = not self.turn_active
        if owns:
            self.reset_turn_budget(deadline_seconds=deadline_seconds, deadline_source=deadline_source)
        try:
            yield
        finally:
            if owns:
                self.end_turn()

    def consume_budget(self: DaytonaSandboxManager, count: int = 1, *, category: str = "generation_query") -> None:
        """Charge shared budget units, categorized without changing admission or completion counts."""
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("budget consumption must be a positive integer")
        if category not in self.root._budget_breakdown:
            raise ValueError("Unknown budget category")
        self.check_turn()
        root = self.root
        with root._budget_lock:
            if root._llm_calls_used + count > root.max_llm_calls:
                raise RuntimeError(f"Shared RLM budget exhausted ({root.max_llm_calls} calls)")
            root._llm_calls_used += count
            root._budget_breakdown[category] += count
            usage = self._usage_unlocked()
        self.emit(
            "budget_update",
            **usage,
            max_llm_calls=root.max_llm_calls,
            tokens=None,
            estimated_cost=None,
        )

    def record_sandbox_error(self: DaytonaSandboxManager) -> None:
        """Count one failed sandbox execution toward the Turn's usage."""
        with self.root._budget_lock:
            self.root._sandbox_errors += 1

    def record_submit_rejection(self: DaytonaSandboxManager) -> None:
        """Count a blocking host rejection once, independently of remote Python failures."""
        with self.root._budget_lock:
            self.root._submit_rejections += 1

    def _usage_unlocked(self: DaytonaSandboxManager) -> dict[str, Any]:
        root = self.root
        return {
            "lm_calls": root._llm_calls_used,
            "sandbox_errors": root._sandbox_errors,
            "submit_rejections": root._submit_rejections,
            "budget_breakdown": dict(root._budget_breakdown),
        }

    def turn_usage(self: DaytonaSandboxManager) -> dict[str, Any]:
        """Host-owned shared budget and recovery counters across the whole agent tree."""
        root = self.root
        with root._budget_lock:
            return self._usage_unlocked()
