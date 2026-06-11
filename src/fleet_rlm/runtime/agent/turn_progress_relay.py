"""Thread-safe progress relay for live chat events during blocking RLM turns."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator

from fleet_rlm.runtime.events import RuntimeEvent

logger = logging.getLogger(__name__)

_DEFAULT_QUEUE_MAX = 128


class TurnProgressRelay:
    """Queue runtime events from worker threads into the unified stream loop."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        maxsize: int = _DEFAULT_QUEUE_MAX,
    ) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[RuntimeEvent | None] = asyncio.Queue(maxsize=maxsize)
        self.seen_keys: set[str] = set()

    def fingerprint(self, event: RuntimeEvent) -> str:
        """Stable dedupe key for live events vs the final trajectory replay."""
        payload = event.payload if isinstance(event.payload, dict) else {}
        kind = event.kind.value
        step_index = payload.get("step_index", payload.get("trajectory_index"))
        tool_name = payload.get("tool_name") or (event.tool.tool_name if event.tool else "")
        phase = payload.get("phase", "")
        text_hash = hash(event.text[:256]) if event.text else 0
        return f"{kind}:{step_index}:{tool_name}:{phase}:{text_hash}"

    def mark_seen(self, event: RuntimeEvent) -> None:
        self.seen_keys.add(self.fingerprint(event))

    def was_seen(self, event: RuntimeEvent) -> bool:
        return self.fingerprint(event) in self.seen_keys

    async def emit(self, event: RuntimeEvent) -> None:
        self._enqueue_event(event)

    def _enqueue_event(self, event: RuntimeEvent) -> None:
        self.mark_seen(event)
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                _ = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                # Queue was drained by another consumer between QueueFull and get_nowait().
                # Safe to continue and retry put_nowait below.
                pass
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("Dropped turn progress event due to queue contention")

    def emit_threadsafe(self, event: RuntimeEvent) -> None:
        self._loop.call_soon_threadsafe(lambda: self._enqueue_event(event))

    def drain_nonblocking(self) -> list[RuntimeEvent]:
        drained: list[RuntimeEvent] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:
                continue
            drained.append(item)
        return drained

    async def wait_for_event(self, timeout: float) -> RuntimeEvent | None:
        try:
            item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        if item is None:
            return None
        return item

    def iter_drained(self, events: Iterator[RuntimeEvent]) -> list[RuntimeEvent]:
        return list(events)


__all__ = ["TurnProgressRelay"]
