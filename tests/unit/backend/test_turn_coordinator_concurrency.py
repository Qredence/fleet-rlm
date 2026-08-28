"""Cross-Session TurnRuntime concurrency contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest


@pytest.mark.asyncio
async def test_two_sessions_execute_concurrently_with_disjoint_stream_identities() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.preparation import PreparedRun, _PreparedRunResources
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_runtime import TurnRuntime
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.events import EventRecorder, RunStarted
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    store = InMemoryRunStateStore()
    catalog = InMemorySessionCatalog(store)
    access = TurnAccess(uuid4(), uuid4())
    sessions = [
        await catalog.create(user_id=access.user_id, workspace_id=access.workspace_id, title=f"session-{index}")
        for index in range(2)
    ]
    preparation_calls: list[UUID] = []
    active_preparations = 0
    peak_preparations = 0

    class Preparation:
        async def prepare(self, run, *, deadline):
            nonlocal active_preparations, peak_preparations
            del deadline
            preparation_calls.append(run.session_id)
            active_preparations += 1
            peak_preparations = max(peak_preparations, active_preparations)
            await asyncio.sleep(0.02)
            active_preparations -= 1
            return PreparedRun(
                execution=SimpleNamespace(run_id=run.run_id, session_id=run.session_id),
                artifact_sink=None,
                _resources=_PreparedRunResources(()),
            )

    class Stream:
        def __init__(self, run_id: UUID, session_id: UUID) -> None:
            self.outcome = RLMOutcome(
                "completed",
                PredictionResult("done", {"answer": str(run_id)}, "fleet.default", "1"),
            )
            self._events = iter((EventRecorder(run_id, session_id).record(RunStarted("live")),))

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self):
            return None

    class Runner:
        def stream(self, execution):
            return Stream(execution.run_id, execution.session_id)

    cleanup = RunCleanupSupervisor()
    coordinator = TurnRuntime(
        lifecycle=RunLifecycleService(store, max_artifact_bytes=1024),
        preparation=Preparation(),
        runner=Runner(),
        cleanup=cleanup,
        turn_timeout_seconds=5,
    )

    async def open_turn(session_id: UUID, key: str):
        return await coordinator.open(OpenTurnCommand(access, session_id, TurnInput("hello"), key, uuid4()))

    async def drain(stream):
        return [event async for event in stream]

    streams = await asyncio.wait_for(
        asyncio.gather(
            open_turn(sessions[0].id, "session-one"),
            open_turn(sessions[1].id, "session-two"),
        ),
        timeout=2,
    )
    events_one, events_two = await asyncio.wait_for(
        asyncio.gather(*(drain(stream) for stream in streams)),
        timeout=2,
    )
    await cleanup.shutdown(drain_seconds=1)

    assert set(preparation_calls) == {sessions[0].id, sessions[1].id}
    assert peak_preparations == 2
    assert {event.run_id for event in events_one}.isdisjoint({event.run_id for event in events_two})
    assert [event.run_id for event in events_one] == [events_one[0].run_id] * len(events_one)
    assert [event.run_id for event in events_two] == [events_two[0].run_id] * len(events_two)


@pytest.mark.asyncio
async def test_concurrent_sessions_hold_distinct_resident_interpreters() -> None:
    """P52.7(d): no interpreter is shared across concurrent Sessions."""
    from fleet_rlm.rlm.session_runtime import SessionKey, SessionRLMRegistry, SessionRLMState

    class _Interpreter:
        def __init__(self) -> None:
            self.namespace: dict[str, object] = {}

    built: list[SessionRLMState] = []
    entered = asyncio.Event()
    proceed = asyncio.Event()

    async def factory(key: SessionKey, fingerprint: str) -> SessionRLMState:
        state = SessionRLMState(key, fingerprint, object(), _Interpreter())
        built.append(state)
        if len(built) == 2:
            entered.set()
        await proceed.wait()
        return state

    registry = SessionRLMRegistry(factory)
    first_key = SessionKey("workspace", "session-a")
    second_key = SessionKey("workspace", "session-b")

    first_task = asyncio.create_task(registry.acquire(first_key, "fp"))
    second_task = asyncio.create_task(registry.acquire(second_key, "fp"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    proceed.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert first is not second
    assert first.interpreter is not second.interpreter
    assert len(built) == 2
