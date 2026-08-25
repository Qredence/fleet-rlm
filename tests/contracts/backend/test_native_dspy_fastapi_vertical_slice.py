"""Provider-free vertical contract from Runtime Events to FastAPI SSE frames."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.rlm.events import EventRecorder, RLMReasoning, RunCompleted, RunStarted, RuntimeEvent


class _OpenedStream:
    def __init__(self, events: tuple[RuntimeEvent, ...]) -> None:
        self.run_id = events[0].run_id
        self._events = events
        self._index = 0
        self.closed = False

    def __aiter__(self) -> _OpenedStream:
        return self

    async def __anext__(self) -> RuntimeEvent:
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_native_runtime_deltas_reach_fastapi_sse_before_done() -> None:
    from fleet_rlm.api.routes.turns import create_turn
    from fleet_rlm.api.schemas import CreateTurnRequest

    recorder = EventRecorder(uuid4(), uuid4())
    opened = _OpenedStream(
        (
            recorder.record(RunStarted("live")),
            recorder.record(RLMReasoning("first", 1, "stream-1", True, False)),
            recorder.record(RLMReasoning("last", 1, "stream-1", True, True)),
            recorder.record(RunCompleted(checkpoint_version=1, delivery="live")),
        )
    )

    class _Coordinator:
        def open_owned(self, _command: object):
            from fleet_rlm.chat.turn_coordinator import OpenedTurnStream

            return OpenedTurnStream(opened.run_id, opened)

    frames = [
        frame
        async for frame in create_turn(
            uuid4(),
            CreateTurnRequest(text="hello"),
            SimpleNamespace(headers={}),
            SimpleNamespace(user_id=uuid4(), workspace_id=uuid4()),
            _Coordinator(),
            SimpleNamespace(run_heartbeat_seconds=10),
            "vertical-slice",
            None,
        )
    ]

    assert [frame.data["type"] for frame in frames[:-1]] == [
        "data-status",
        "start",
        "reasoning-start",
        "reasoning-delta",
        "reasoning-delta",
        "reasoning-end",
        "finish",
    ]
    assert frames[0].data == {
        "type": "data-status",
        "data": {"phase": "preparation", "status": "running", "message": None},
        "transient": True,
    }
    assert frames[-1].raw_data == "[DONE]"
    assert opened.closed
