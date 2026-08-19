"""Process-local Daytona admission ownership.

Bounded concurrent Interpreter/Sandbox leases; each admitted unit of work
holds one idempotently releasable permit. Confirmed provider ownership lives
in ``sandbox_lease.py`` / ``lifecycle.py``; this module owns only capacity.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

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
    _released: bool = field(default=False, init=False)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
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
        return DaytonaAdmissionPermit(self._semaphore)
