"""Provider-neutral settlement for asynchronous effects already owned by a caller."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OwnedEffectWait(Generic[T]):
    """Outcome of waiting on an owned effect without changing its ownership."""

    _effect: OwnedEffect[T]
    caller_cancelled: bool
    timed_out: bool

    @property
    def done(self) -> bool:
        """
        Determine whether the owned effect has completed.

        Returns:
                bool: `True` if the effect has completed, `False` otherwise.
        """
        return self._effect.done()

    @property
    def pending(self) -> bool:
        """
        Indicates whether the effect remains incomplete after the wait.

        Returns:
            bool: `true` if the effect is still pending, `false` otherwise.
        """
        return not self.done

    def result(self) -> T:
        """Return the effect result, preserving its original exception."""
        return self._effect.result()


class OwnedEffect(Generic[T]):
    """Own one started async effect while callers wait, cancel, or time out."""

    def __init__(self, task: asyncio.Future[T]) -> None:
        """
        Initialize an owned effect from an existing asynchronous task.

        Parameters:
                task (asyncio.Future[T]): The task or future representing the effect.
        """
        self._task = task
        self._caller_cancelled = False

    @classmethod
    def start(cls, awaitable: Awaitable[T]) -> OwnedEffect[T]:
        """Start one caller-supplied awaitable and retain its task."""
        try:
            task = asyncio.ensure_future(awaitable)
        except BaseException:
            close = getattr(awaitable, "close", None)
            if callable(close):
                with contextlib.suppress(BaseException):
                    close()
            raise
        return cls(task)

    @classmethod
    def from_task(cls, task: asyncio.Future[T]) -> OwnedEffect[T]:
        """Wrap an already-started task without creating another waiter twin."""
        return cls(task)

    @property
    def caller_cancelled(self) -> bool:
        """
        Indicates whether a caller waiting on the effect requested cancellation.

        Returns:
            bool: `true` if a caller requested cancellation, `false` otherwise.
        """
        return self._caller_cancelled

    def done(self) -> bool:
        """Return whether the owned effect is terminal."""
        return self._task.done()

    def result(self) -> T:
        """Return the effect result, preserving its original exception."""
        return self._task.result()

    def consume_exception(self) -> None:
        """Explicitly observe a terminal exception for a domain that owns errors."""
        if self._task.done() and not self._task.cancelled():
            with contextlib.suppress(BaseException):
                self._task.exception()

    async def observe_completion(self) -> None:
        """
        Wait for the owned effect to complete and observe any resulting exception without recording caller cancellation.
        """
        if not self._task.done():
            await asyncio.wait({self._task})
        self.consume_exception()

    async def settle(self, *, timeout: float | None = None) -> OwnedEffectWait[T]:
        """
        Wait for the owned effect without cancelling it.

        Caller cancellation is recorded while waiting, and a timeout leaves an incomplete
        effect pending for later observation. Negative timeout values are treated as zero.

        Parameters:
            timeout (float | None): Maximum time to wait in seconds, or None to wait
                without a deadline.

        Returns:
            OwnedEffectWait[T]: Outcome describing caller cancellation and whether the
                wait expired before the effect completed.
        """
        deadline = None if timeout is None else asyncio.get_running_loop().time() + max(0.0, timeout)
        timed_out = False
        while not self._task.done():
            remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
            if remaining is not None and remaining <= 0:
                timed_out = True
                break
            try:
                if remaining is None:
                    await asyncio.shield(self._task)
                else:
                    await asyncio.wait_for(asyncio.shield(self._task), timeout=remaining)
            except asyncio.CancelledError:
                if self._task.cancelled():
                    raise
                self._caller_cancelled = True
            except TimeoutError:
                if self._task.done():
                    break
                timed_out = True
                break

        if self._task.done():
            # Observe and re-raise the original effect failure. Callers that
            # intentionally own a non-authoritative cleanup error must catch it
            # explicitly at their domain boundary.
            self._task.result()
        return OwnedEffectWait(self, caller_cancelled=self._caller_cancelled, timed_out=timed_out)


class RunCleanupUnavailableError(RuntimeError):
    """The bounded detached-cleanup inventory cannot accept more work."""


class RunCleanupSupervisor:
    """Retain strong ownership of detached cleanup until it finishes."""

    def __init__(self, *, max_jobs: int = 8) -> None:
        if max_jobs <= 0:
            raise ValueError("max_jobs must be positive")
        self._max_jobs = max_jobs
        self._tasks: set[asyncio.Task[None]] = set()
        self._accepting = True

    @property
    def available(self) -> bool:
        return self._accepting and len(self._tasks) < self._max_jobs

    @property
    def active_jobs(self) -> int:
        return len(self._tasks)

    def require_capacity(self) -> None:
        if not self.available:
            raise RunCleanupUnavailableError("Turn cleanup capacity is unavailable")

    def submit(self, cleanup: Awaitable[None]) -> asyncio.Task[None]:
        self.require_capacity()
        task = asyncio.create_task(self._run(cleanup), name="fleet-turn-cleanup")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _run(self, cleanup: Awaitable[None]) -> None:
        try:
            await cleanup
        except BaseException:
            logger.exception("detached Run cleanup failed")

    async def shutdown(self, *, drain_seconds: float = 30.0) -> None:
        self._accepting = False
        if not self._tasks:
            return
        _, pending = await asyncio.wait(tuple(self._tasks), timeout=max(0.0, drain_seconds))
        if pending:
            logger.warning("Run cleanup drain expired with %d owned job(s)", len(pending))


__all__ = ["OwnedEffect", "OwnedEffectWait", "RunCleanupSupervisor", "RunCleanupUnavailableError"]
