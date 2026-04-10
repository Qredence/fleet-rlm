"""Adapters between Agent Framework workflow events and worker-native events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fleet_rlm.worker import WorkspaceEvent


async def iter_workspace_host_queue(
    queue: asyncio.Queue[WorkspaceEvent | None],
) -> AsyncIterator[WorkspaceEvent]:
    """Yield worker-native events from the hosted queue until the sentinel arrives."""

    while True:
        event = await queue.get()
        if event is None:
            return
        yield event
