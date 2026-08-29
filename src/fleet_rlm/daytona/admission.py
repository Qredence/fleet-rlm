"""Process-local Daytona admission ownership.

Bounded concurrent Interpreter/Sandbox leases; each admitted unit of work
holds one idempotently releasable permit. Confirmed provider ownership lives
in ``sandbox_lease.py`` / ``lifecycle.py``; this module owns only capacity.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from threading import Lock

__all__ = [
    "DaytonaAdmission",
    "DaytonaAdmissionPermit",
    "DaytonaAdmissionTimeoutError",
]


class DaytonaAdmissionTimeoutError(RuntimeError):
    """The Turn deadline elapsed before Daytona capacity became available."""


@dataclass(slots=True)
class DaytonaAdmissionPermit:
    """One idempotently releasable slot in Daytona admission."""

    _semaphore: asyncio.BoundedSemaphore
    _loop: asyncio.AbstractEventLoop | None = None
    _released: bool = field(default=False, init=False)
    _release_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def release(self) -> None:
        """Release on the semaphore's owning loop, safely from worker threads."""
        with self._release_lock:
            if self._released:
                return
            self._released = True
        loop = self._loop or getattr(self._semaphore, "_loop", None)
        if loop is None or loop.is_closed() or not loop.is_running():
            # No waiter can be serviced by a closed/stopped loop.  This path
            # preserves teardown accounting for direct test/worker owners.
            self._semaphore.release()
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            self._semaphore.release()
            return
        try:
            loop.call_soon_threadsafe(self._semaphore.release)
        except RuntimeError:
            # The loop may close between the liveness check and posting.
            self._semaphore.release()


class DaytonaAdmission:
    """Bound acquiring plus active Interpreter Leases for one process."""

    def __init__(self, *, max_active_leases: int = 8) -> None:
        if max_active_leases <= 0:
            raise ValueError("max_active_leases must be positive")
        if max_active_leases > 8:
            raise ValueError("max_active_leases must be at most 8")
        self._semaphore = asyncio.BoundedSemaphore(max_active_leases)

    async def acquire(self, *, deadline: float) -> DaytonaAdmissionPermit:
        try:
            async with asyncio.timeout_at(deadline):
                await self._semaphore.acquire()
        except TimeoutError:
            raise DaytonaAdmissionTimeoutError("Daytona admission unavailable") from None
        return DaytonaAdmissionPermit(self._semaphore, asyncio.get_running_loop())
