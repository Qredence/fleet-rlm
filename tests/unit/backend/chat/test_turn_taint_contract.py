"""Coordinator-level taint contract for resident Session RLM runtimes (P52.3 b/c/d).

The production seam is ``chat/turn_runtime.py::_mark_stream_runtime``: the
coordinator duck-types ``mark_tainted``/``mark_committed`` on the runner's
event stream, and the production stream forwards the decision to the Session
runtime lease it holds (``rlm/runtime.py::RunEventStream``). Coordinator test
doubles that lack both methods silently no-op the seam, so the doubles below
implement BOTH methods, record every call, and forward to a real
``SessionRLMRegistry`` lease. Rotation is then driven and observed through the
registry itself: ``DefaultRunPreparer`` retires a tainted resident via
``close_unhealthy(SessionKey)`` before acquiring the environment
(``chat/preparation.py:321-329``), and the next stream acquires a fresh
generation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import NamedTuple
from uuid import uuid4

import pytest

from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunLifecycleService, RunStateError
from fleet_rlm.chat.turn_runtime import TurnRuntime
from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
from fleet_rlm.rlm.events import EventRecorder, RunCancelled, RunCompleted, RunFailed, RunStarted, RuntimeEvent
from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
from fleet_rlm.rlm.session_runtime import SessionKey, SessionRLMRegistry, SessionRLMState, SessionRuntimeLease
from fleet_rlm.runtime.cleanup import RunCleanupSupervisor
from fleet_rlm.sessions.models import TurnAccess, TurnInput

_FINGERPRINT = "fp-taint-contract"


class _FakeInterpreter:
    """Caller-owned interpreter double with persistent ordinary state."""

    def __init__(self) -> None:
        self.namespace: dict[str, object] = {}
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _make_factory(built: list[SessionRLMState]) -> Callable[[SessionKey, str], Awaitable[SessionRLMState]]:
    async def factory(key: SessionKey, fingerprint: str) -> SessionRLMState:
        state = SessionRLMState(
            session_key=key,
            program_fingerprint=fingerprint,
            # The production field is the native DSPy RLM; the coordinator
            # contract under test never touches it, so a tiny stand-in is fine.
            rlm=object(),
            interpreter=_FakeInterpreter(),
        )
        built.append(state)
        return state

    return factory


class _StreamScript(NamedTuple):
    outcome: RLMOutcome | None
    wait: Callable[[SimpleNamespace], Awaitable[None]] | None = None


async def _block_until_cancelled(execution: SimpleNamespace) -> None:
    """Hold the stream open until the durable cancellation probe flips."""
    while not await execution.cancellation_requested():
        await asyncio.sleep(0.005)


async def _block_forever(_execution: SimpleNamespace) -> None:
    """Hold the stream open until the coordinator cancels the pending read."""
    await asyncio.Event().wait()


def _completed_outcome() -> RLMOutcome:
    return RLMOutcome(
        "completed",
        PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
    )


class _RecordingStream:
    """Runner stream double recording the coordinator's durable-outcome marks.

    The production ``RunEventStream`` forwards ``mark_tainted`` /
    ``mark_committed`` to the Session runtime lease it acquired; this double
    forwards both to a real ``SessionRLMRegistry`` lease acquired lazily on
    first iteration — the same point where the production runner acquires its
    Session lane — so the resident-state effect of every mark stays observable
    through the registry.
    """

    def __init__(
        self,
        *,
        registry: SessionRLMRegistry,
        key: SessionKey,
        execution: SimpleNamespace,
        script: _StreamScript,
    ) -> None:
        self._registry = registry
        self._key = key
        self._execution = execution
        self.outcome = script.outcome
        self._wait = script.wait
        self.lease: SessionRuntimeLease | None = None
        self.marks: list[str] = []
        self.started = asyncio.Event()

    def __aiter__(self) -> _RecordingStream:
        return self

    async def __anext__(self) -> RuntimeEvent:
        if self.lease is None:
            self.lease = await self._registry.acquire_execution(self._key, _FINGERPRINT)
        if not self.started.is_set():
            self.started.set()
            recorder = EventRecorder(self._execution.run_id, self._execution.session_id)
            return recorder.record(RunStarted(delivery="live"))
        if self._wait is not None:
            await self._wait(self._execution)
        raise StopAsyncIteration

    def mark_tainted(self) -> None:
        self.marks.append("tainted")
        if self.lease is not None:
            self.lease.mark_tainted()

    def mark_committed(self) -> None:
        self.marks.append("committed")
        if self.lease is not None:
            self.lease.mark_committed()

    def defer_runtime_release(self) -> None:
        return None

    async def release_runtime(self) -> None:
        if self.lease is not None:
            await self.lease.release()

    async def aclose(self) -> None:
        return None

    async def wait_owned(self) -> None:
        return None


class _Runner:
    """Runner double issuing one scripted recording stream per Turn."""

    def __init__(self, *, registry: SessionRLMRegistry, key: SessionKey, scripts: list[_StreamScript]) -> None:
        self._registry = registry
        self._key = key
        self._scripts = list(scripts)
        self.streams: list[_RecordingStream] = []

    def stream(self, execution: SimpleNamespace) -> _RecordingStream:
        script = self._scripts[len(self.streams)]
        stream = _RecordingStream(registry=self._registry, key=self._key, execution=execution, script=script)
        self.streams.append(stream)
        return stream


class _Prepared:
    """Minimal PreparedTurn double with a clean async close boundary."""

    def __init__(self, execution: SimpleNamespace) -> None:
        self.execution = execution
        self.artifact_sink = None
        self.result_snapshot_sink = None
        self.post_commit_memory_promotion = None
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _RegistryPreparation:
    """Preparation double performing the production unhealthy-rotation hook.

    ``DefaultRunPreparer.prepare`` retires a tainted resident through
    ``close_unhealthy(SessionKey)`` before acquiring the environment
    (``chat/preparation.py:321-329``); this double performs the same registry
    call sequence so each Turn's rotation is driven through the real
    ``SessionRLMRegistry``.
    """

    def __init__(self, registry: SessionRLMRegistry) -> None:
        self._registry = registry
        self.retired: list[bool] = []

    async def prepare(self, run: ClaimedRun, *, deadline: float) -> _Prepared:
        await self._registry.evict_configured_idle(deadline=deadline)
        retired = await self._registry.close_unhealthy(
            SessionKey(workspace_id=str(run.access.workspace_id), session_id=str(run.session_id)),
            deadline=deadline,
        )
        self.retired.append(retired)
        return _Prepared(
            SimpleNamespace(
                run_id=run.run_id,
                session_id=run.session_id,
                cancellation_requested=run.cancellation_requested,
            )
        )


async def _wait_stream_started(runner: _Runner, index: int) -> _RecordingStream:
    while len(runner.streams) <= index:
        await asyncio.sleep(0.005)
    stream = runner.streams[index]
    await stream.started.wait()
    return stream


@pytest.mark.asyncio
async def test_cancelled_turn_taints_resident_runtime_and_next_turn_rotates() -> None:
    store = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="cancelled turn taint",
    )
    key = SessionKey(workspace_id=str(access.workspace_id), session_id=str(session.id))
    built: list[SessionRLMState] = []
    registry = SessionRLMRegistry(_make_factory(built))
    preparation = _RegistryPreparation(registry)
    runner = _Runner(
        registry=registry,
        key=key,
        scripts=[
            _StreamScript(
                outcome=RLMOutcome("cancelled", public_error_message="Turn cancelled"),
                wait=_block_until_cancelled,
            ),
            _StreamScript(outcome=_completed_outcome()),
        ],
    )
    cleanup = RunCleanupSupervisor()
    coordinator = TurnRuntime(
        lifecycle=RunLifecycleService(store, max_artifact_bytes=1024),
        preparation=preparation,
        runner=runner,
        cleanup=cleanup,
    )

    run_id = uuid4()

    async def collect_cancelled_turn() -> list[RuntimeEvent]:
        opened = await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("first"), "taint-cancel-1", run_id)
        )
        return [event async for event in opened]

    task = asyncio.create_task(collect_cancelled_turn())
    stream1 = await _wait_stream_started(runner, 0)
    assert await store.request_cancel(access, run_id) == "requested"
    events1 = await task

    # The cancelled Turn settles as terminal "cancelled" and taints the
    # resident runtime exactly once; no commit mark is ever recorded.
    assert isinstance(events1[-1].detail, RunCancelled)
    assert stream1.marks == ["tainted"]
    assert len(built) == 1
    assert built[0].tainted
    assert preparation.retired == [False]

    events2 = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("second"), "taint-cancel-2", uuid4())
        )
    ]

    # The next Turn's preparation retires the tainted state (close_unhealthy)
    # and the stream runs a fresh resident generation that commits cleanly.
    assert isinstance(events2[-1].detail, RunCompleted)
    stream2 = runner.streams[1]
    assert stream2.marks == ["committed"]
    assert preparation.retired == [False, True]
    assert built[0].closed
    assert built[0].interpreter.close_calls == 1
    assert len(built) == 2
    assert built[1] is not built[0]
    assert built[1].healthy
    assert registry.get(key) is built[1]

    await cleanup.shutdown(drain_seconds=1)
    await registry.shutdown()


@pytest.mark.asyncio
async def test_commit_failure_taints_and_next_turn_rotates() -> None:
    authoritative = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="commit failure taint",
    )

    class CommitFailingStore:
        begin = authoritative.begin
        transition_claim = authoritative.transition_claim
        request_cancel = authoritative.request_cancel

        def __init__(self) -> None:
            self.commit_failures_remaining = 1

        async def commit(self, run, committed, artifacts, memory_intents=()):
            if self.commit_failures_remaining > 0:
                self.commit_failures_remaining -= 1
                raise RuntimeError("database detail must not escape")
            return await authoritative.commit(run, committed, artifacts, memory_intents=memory_intents)

    key = SessionKey(workspace_id=str(access.workspace_id), session_id=str(session.id))
    built: list[SessionRLMState] = []
    registry = SessionRLMRegistry(_make_factory(built))
    preparation = _RegistryPreparation(registry)
    runner = _Runner(
        registry=registry,
        key=key,
        scripts=[_StreamScript(outcome=_completed_outcome()), _StreamScript(outcome=_completed_outcome())],
    )
    cleanup = RunCleanupSupervisor()
    coordinator = TurnRuntime(
        lifecycle=RunLifecycleService(CommitFailingStore(), max_artifact_bytes=1024),
        preparation=preparation,
        runner=runner,
        cleanup=cleanup,
    )

    events1 = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("commit"), "taint-commit-1", uuid4())
        )
    ]

    # The commit failure projects the commit_failed terminal and taints the
    # resident runtime; the durable outcome was never committed.
    assert isinstance(events1[-1].detail, RunFailed)
    assert events1[-1].detail.code == "commit_failed"
    stream1 = runner.streams[0]
    assert stream1.marks == ["tainted"]
    assert len(built) == 1
    assert built[0].tainted
    assert await authoritative.turn_records(session.id, access) == ()
    assert preparation.retired == [False]

    events2 = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("commit again"), "taint-commit-2", uuid4())
        )
    ]

    # The next Turn rotates the tainted state before executing and commits a
    # fresh resident generation.
    assert isinstance(events2[-1].detail, RunCompleted)
    stream2 = runner.streams[1]
    assert stream2.marks == ["committed"]
    assert preparation.retired == [False, True]
    assert built[0].closed
    assert built[0].interpreter.close_calls == 1
    assert len(built) == 2
    assert built[1] is not built[0]
    assert built[1].healthy
    assert registry.get(key) is built[1]
    assert len(await authoritative.turn_records(session.id, access)) == 2

    await cleanup.shutdown(drain_seconds=1)
    await registry.shutdown()


@pytest.mark.asyncio
async def test_claim_loss_taints_resident_runtime() -> None:
    authoritative = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="claim loss taint",
    )
    attack = asyncio.Event()

    class Store:
        begin = authoritative.begin
        commit = authoritative.commit
        request_cancel = authoritative.request_cancel

        async def transition_claim(self, run, command):
            from fleet_rlm.chat.run_claim import HeartbeatClaim

            if isinstance(command, HeartbeatClaim) and attack.is_set():
                raise RunStateError("Turn claim is invalid")
            return await authoritative.transition_claim(run, command)

    key = SessionKey(workspace_id=str(access.workspace_id), session_id=str(session.id))
    built: list[SessionRLMState] = []
    registry = SessionRLMRegistry(_make_factory(built))
    preparation = _RegistryPreparation(registry)
    runner = _Runner(
        registry=registry,
        key=key,
        scripts=[_StreamScript(outcome=None, wait=_block_forever)],
    )
    cleanup = RunCleanupSupervisor()
    coordinator = TurnRuntime(
        lifecycle=RunLifecycleService(
            Store(),
            max_artifact_bytes=1024,
            heartbeat_seconds=0.01,
            stale_after_seconds=0.06,
        ),
        preparation=preparation,
        runner=runner,
        cleanup=cleanup,
    )

    async def collect_claim_lost_turn() -> list[RuntimeEvent]:
        opened = await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("claim loss"), "taint-claim-loss-1", uuid4())
        )
        return [event async for event in opened]

    task = asyncio.create_task(collect_claim_lost_turn())
    stream = await _wait_stream_started(runner, 0)
    # The claim heartbeat starts failing only after the stream is mid-flight,
    # so the definitive claim loss lands on the execution claim-loss branch.
    attack.set()
    events = await task

    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "unavailable"
    assert stream.marks == ["tainted"]
    assert len(built) == 1
    assert built[0].tainted
    assert not built[0].healthy

    await cleanup.shutdown(drain_seconds=1)
    await registry.shutdown()
