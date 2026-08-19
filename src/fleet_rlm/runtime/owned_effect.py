"""Provider-neutral settlement for asynchronous effects already owned by a caller."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


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


__all__ = ["OwnedEffect", "OwnedEffectWait"]
