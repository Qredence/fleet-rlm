"""Provider-free vertical contract from Runtime Events to FastAPI SSE frames."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from daytona.common.errors import DaytonaFileNotFoundError

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, _SandboxProcessBackend
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
            from fleet_rlm.turns import OpenedTurnStream

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


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_scratch", [True, False])
async def test_run_scratch_cleanup_preserves_completed_sse_boundary(missing_scratch: bool) -> None:
    from fleet_rlm.api.routes.turns import create_turn
    from fleet_rlm.api.schemas import CreateTurnRequest
    from fleet_rlm.turns import OpenedTurnStream

    run_id = uuid4()
    path = f"/tmp/fleet/{run_id}"
    deleted: list[str] = []

    class Filesystem:
        def delete_file(self, target: str, *, recursive: bool) -> None:
            assert recursive
            deleted.append(target)
            if missing_scratch:
                raise DaytonaFileNotFoundError("missing Run scratch", status_code=404)
            raise RuntimeError("Sandbox is unavailable")

    template = DaytonaCodeInterpreter(backend=_SandboxProcessBackend(SimpleNamespace(fs=Filesystem())))
    template.bind_run_scratch(run_id)
    invocation = template.new_invocation()
    assert invocation._backend._run_scratch_path == path

    recorder = EventRecorder(uuid4(), run_id)

    class Opened:
        def __init__(self) -> None:
            self.events = iter((recorder.record(RunStarted("live")), recorder.record(RunCompleted(1, "live"))))

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.events)
            except StopIteration:
                template.cleanup_run_scratch()
                raise StopAsyncIteration from None

        async def aclose(self) -> None:
            return None

    class Coordinator:
        def open_owned(self, _command):
            return OpenedTurnStream(run_id, Opened())

    stream = create_turn(
        uuid4(),
        CreateTurnRequest(text="hello"),
        SimpleNamespace(headers={}),
        SimpleNamespace(user_id=uuid4(), workspace_id=uuid4()),
        Coordinator(),
        SimpleNamespace(run_heartbeat_seconds=10),
        "scratch-cleanup",
        None,
    )
    if missing_scratch:
        frames = [frame async for frame in stream]
        assert [frame.data["type"] for frame in frames[1:-1]] == ["start", "finish"]
        assert frames[-1].raw_data == "[DONE]"
        assert template._backend._run_scratch_path is None
    else:
        with pytest.raises(RuntimeError, match="Sandbox is unavailable"):
            _ = [frame async for frame in stream]
        assert template._backend._run_scratch_path == path
    assert deleted == [path]
