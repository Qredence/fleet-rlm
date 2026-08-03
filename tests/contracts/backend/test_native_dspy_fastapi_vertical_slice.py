"""Provider-free vertical contract from Runtime Events to FastAPI SSE frames."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from fleet_rlm.rlm.events import EventRecorder, RLMReasoning, RunCompleted, RunStarted, RuntimeEvent


class _OpenedStream:
    def __init__(self, events: tuple[RuntimeEvent, ...]) -> None:
        self.run_id = events[0].run_id
        self._events = events
        self.closed = False

    def __aiter__(self) -> AsyncIterator[RuntimeEvent]:
        return self._generate()

    async def _generate(self) -> AsyncIterator[RuntimeEvent]:
        for event in self._events:
            yield event

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_native_runtime_deltas_reach_fastapi_sse_before_done() -> None:
    from fleet_rlm.api.routes.turns import create_turn

    recorder = EventRecorder(uuid4(), uuid4())
    opened = _OpenedStream(
        (
            recorder.record(RunStarted("live")),
            recorder.record(RLMReasoning("first", 1, "stream-1", True, False)),
            recorder.record(RLMReasoning("last", 1, "stream-1", True, True)),
            recorder.record(RunCompleted(checkpoint_version=1, delivery="live")),
        )
    )

    frames = [frame async for frame in create_turn(opened)]

    assert [frame.data["type"] for frame in frames[:-1]] == [
        "start",
        "reasoning-start",
        "reasoning-delta",
        "reasoning-delta",
        "reasoning-end",
        "finish",
    ]
    assert frames[-1].raw_data == "[DONE]"
    assert opened.closed
