"""Process-owned cleanup for Turn work that cannot be cancelled synchronously."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable

logger = logging.getLogger(__name__)


class TurnCleanupUnavailable(RuntimeError):
    """The bounded detached-cleanup inventory cannot accept more work."""


class TurnCleanupSupervisor:
    """Retain strong ownership of detached cleanup until it really finishes."""

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
            raise TurnCleanupUnavailable("Turn cleanup capacity is unavailable")

    def submit(self, cleanup: Awaitable[None]) -> None:
        self.require_capacity()
        task = asyncio.create_task(self._run(cleanup), name="fleet-turn-cleanup")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, cleanup: Awaitable[None]) -> None:
        try:
            await cleanup
        except Exception:  # noqa: BLE001 - cleanup diagnostics must stay private
            logger.exception("detached Turn cleanup failed")

    async def shutdown(self, *, drain_seconds: float = 30.0) -> None:
        self._accepting = False
        if not self._tasks:
            return
        _, pending = await asyncio.wait(tuple(self._tasks), timeout=max(0.0, drain_seconds))
        if pending:
            logger.warning("Turn cleanup drain expired with %d owned job(s)", len(pending))
