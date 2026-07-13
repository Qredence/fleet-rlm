"""Deterministic serialization for canonical Volume/logical path writes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from uuid import UUID


class VolumeWriteCoordinator:
    """Per-session write locks so concurrent runs do not clobber shared roots.

    Unique run paths under sessions/{sid}/runs/{rid}/ remain preferred; this
    coordinator serializes writes that target the same session key.
    """

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}

    def _key(self, session_id: UUID | str, *, resource: str = "default") -> str:
        return f"{session_id}:{resource}"

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    @asynccontextmanager
    async def hold(
        self,
        session_id: UUID | str,
        *,
        resource: str = "default",
    ) -> AsyncIterator[None]:
        lock = await self._lock_for(self._key(session_id, resource=resource))
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()

    @staticmethod
    def run_scoped_path(session_id: UUID | str, run_id: UUID | str, *parts: str) -> str:
        """Canonical unique path fragment under a session (posix)."""
        base = PurePosixPath("sessions") / str(session_id) / "runs" / str(run_id)
        for part in parts:
            base = base / part
        return base.as_posix()


_COORD = VolumeWriteCoordinator()


def get_volume_write_coordinator() -> VolumeWriteCoordinator:
    return _COORD


def set_volume_write_coordinator(coord: VolumeWriteCoordinator) -> None:
    global _COORD
    _COORD = coord
