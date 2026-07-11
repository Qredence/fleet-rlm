"""Per-session asyncio locks to serialize mutations for one session_id."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID


class SessionLockRegistry:
    """Process-local session mutation locks (not distributed)."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def _lock_for(self, session_id: UUID) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_id] = lock
            return lock

    @asynccontextmanager
    async def hold(self, session_id: UUID) -> AsyncIterator[None]:
        """Hold the exclusive mutation lock for ``session_id`` until the block exits."""
        lock = await self._lock_for(session_id)
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
