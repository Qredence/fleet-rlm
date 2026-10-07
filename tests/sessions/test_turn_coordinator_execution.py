"""Coordinator-owned execution, settlement, and Turn-open command contracts.

* ``test_turn_coordinator_execution.py``: Focused coverage for coordinator-owned execution and settlement.
* ``test_open_turn_command.py``: Validated application command for Session-first Turn creation.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tests.support.turn_settlement import TestingRunSettlement


# --- from test_turn_coordinator_execution.py --------------------------
def _turn():
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )

    async def not_cancelled() -> bool:
        return False

    access = TurnAccess(uuid4(), uuid4())
    return ClaimedRun(
        uuid4(),
        uuid4(),
        access,
        TurnInput("driver test"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )


class _Prepared:
    artifact_sink = None
    result_snapshot_sink = None
    post_commit_memory_promotion = None

    def __init__(self, *, deadline: float) -> None:
        self.execution = SimpleNamespace(request="driver test", deadline=deadline)
        self.closed = asyncio.Event()

    async def aclose(self) -> None:
        self.closed.set()


class _Stream:
    def __init__(self, *, outcome, order: list[str] | None = None, blocking: bool = True) -> None:
        self.outcome = outcome
        self._order = order if order is not None else []
        self._blocking = blocking
        self.started = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.started.set()
        if not self._blocking:
            raise StopAsyncIteration
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self._order.append("stream_closed")

    async def wait_owned(self) -> None:
        self._order.append("worker_stopped")


class _CleanupLifecycle:
    heartbeat_seconds = 10
    stale_after_seconds = 60

    def __init__(self, *, outcome, release_finish: asyncio.Event | None = None) -> None:
        self.outcome = outcome
        self.release_finish = release_finish
        self.finish_started = asyncio.Event()
        self.settle_calls = 0
        self.revoke_calls = 0
        self.complete_calls = 0

    async def finish(self, turn, resolution, **kwargs):
        del turn, resolution, kwargs
        self.finish_started.set()
        if self.release_finish is not None:
            await self.release_finish.wait()
        from fleet_rlm.sessions.run_state import FailedRunReceipt

        return FailedRunReceipt(uuid4(), "failed", "execution_failed", "Turn failed", True)

    async def settle(self, turn, failure):
        del turn, failure
        self.settle_calls += 1
        from fleet_rlm.sessions.run_state import FailedRunReceipt

        status = self.outcome.terminal_status
        message = "Turn cancelled" if status == "cancelled" else "Turn timed out"
        return FailedRunReceipt(uuid4(), status, status, message, True)

    async def revoke_claim(self, turn, failure):
        del turn, failure
        self.revoke_calls += 1
        from fleet_rlm.sessions.run_state import FailedRunReceipt

        return FailedRunReceipt(uuid4(), "failed", "stale_claim", "Turn failed", True)

    async def complete_settling(self, turn):
        del turn
        self.complete_calls += 1


def _driver(lifecycle, runner, cleanup):
    from fleet_rlm.sessions.committed_turn import CommittedTurnEventProjector
    from fleet_rlm.turns import TurnRuntime

    return TurnRuntime(
        lifecycle=lifecycle,
        preparation=object(),  # type: ignore[arg-type]
        runner=runner,
        projector=CommittedTurnEventProjector(),
        cleanup=cleanup,
        claim_loss_fence=None,
        turn_timeout_seconds=10,
    )


@pytest.mark.asyncio
async def test_finalization_wins_simultaneous_claim_loss() -> None:
    from fleet_rlm.rlm.events import RunFailed
    from fleet_rlm.rlm.ownership import RunCleanupSupervisor
    from fleet_rlm.rlm.result import RLMOutcome
    from fleet_rlm.turns import ClaimHeartbeat

    release_finish = asyncio.Event()
    lifecycle = _CleanupLifecycle(
        outcome=RLMOutcome("failed", public_error_message="Turn failed"),
        release_finish=release_finish,
    )
    stream = _Stream(outcome=lifecycle.outcome, blocking=False)
    heartbeat_task = asyncio.create_task(asyncio.Event().wait())
    heartbeat = ClaimHeartbeat(heartbeat_task, asyncio.Event())
    cleanup = RunCleanupSupervisor()

    class Runner:
        def stream(self, _execution):
            return stream

    turn = _turn()
    prepared = _Prepared(deadline=asyncio.get_running_loop().time() + 10)
    task = asyncio.create_task(_collect(_driver(lifecycle, Runner(), cleanup), turn, prepared, heartbeat))
    await lifecycle.finish_started.wait()
    heartbeat.lost.set()
    release_finish.set()
    events = await task
    await cleanup.shutdown(drain_seconds=1)

    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "execution_failed"
    assert lifecycle.revoke_calls == 0
    assert lifecycle.complete_calls == 0
    assert prepared.closed.is_set()


@pytest.mark.asyncio
async def test_claim_loss_reconciles_a_commit_that_finishes_after_the_waiter_race() -> None:
    """A claim-loss waiter must not turn a concurrently committed Turn into failure."""
    from fleet_rlm.rlm.events import RunCompleted, RunFailed, TextCompleted, TextDelta
    from fleet_rlm.rlm.ownership import RunCleanupSupervisor
    from fleet_rlm.rlm.result import RLMOutcome, empty_rlm_usage
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.run_state import CommittedTurnReceipt
    from fleet_rlm.turns import ClaimHeartbeat

    release_finish = asyncio.Event()
    committed_turn = CommittedTurn(
        schema_version=1,
        parts=(UsagePart(value=empty_rlm_usage()), TextPart(text="committed")),
    )
    turn = _turn()
    receipt = CommittedTurnReceipt(turn.run_id, 1, committed_turn, ())
    lifecycle = _CleanupLifecycle(outcome=RLMOutcome("failed"), release_finish=release_finish)

    async def finish(_turn, _resolution, **kwargs):
        del kwargs
        lifecycle.finish_started.set()
        await release_finish.wait()
        return receipt

    lifecycle.finish = finish
    stream = _Stream(outcome=RLMOutcome("failed"), blocking=False)
    cleanup = RunCleanupSupervisor()

    class Runner:
        def stream(self, _execution):
            return stream

    driver = _driver(lifecycle, Runner(), cleanup)
    prepared = _Prepared(deadline=asyncio.get_running_loop().time() + 10)
    claim_lost = asyncio.Event()
    heartbeat = ClaimHeartbeat(asyncio.create_task(asyncio.Event().wait()), claim_lost)
    task = asyncio.create_task(_collect(driver, turn, prepared, heartbeat))
    await lifecycle.finish_started.wait()
    claim_lost.set()
    await asyncio.sleep(0)
    release_finish.set()
    events = await task
    await cleanup.shutdown(drain_seconds=1)

    details = [event.detail for event in events]
    assert any(isinstance(detail, TextDelta) for detail in details)
    assert any(isinstance(detail, TextCompleted) for detail in details)
    assert sum(isinstance(detail, RunCompleted) for detail in details) == 1
    assert not any(isinstance(detail, RunFailed) for detail in details)


@pytest.mark.asyncio
async def test_disconnect_cancels_provider_wait_and_orders_detached_cleanup() -> None:
    from fleet_rlm.rlm.ownership import RunCleanupSupervisor
    from fleet_rlm.rlm.result import RLMOutcome
    from fleet_rlm.turns import ClaimHeartbeat

    order: list[str] = []
    lifecycle = _CleanupLifecycle(outcome=RLMOutcome("failed", public_error_message="Turn failed"))
    stream = _Stream(outcome=lifecycle.outcome, order=order)
    heartbeat_task = asyncio.create_task(asyncio.Event().wait())
    heartbeat = ClaimHeartbeat(heartbeat_task, asyncio.Event())
    cleanup = RunCleanupSupervisor()

    class Runner:
        def stream(self, _execution):
            return stream

    turn = _turn()
    prepared = _Prepared(deadline=asyncio.get_running_loop().time() + 10)

    async def collect_driver():
        async for _event in _driver(lifecycle, Runner(), cleanup)._execute_claimed(
            turn,
            prepared,
            heartbeat,
            trace_id=None,
        ):
            pass

    task = asyncio.create_task(collect_driver())
    await stream.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await cleanup.shutdown(drain_seconds=1)

    assert lifecycle.settle_calls == 1
    assert order == ["stream_closed", "worker_stopped"]
    assert prepared.closed.is_set()
    assert lifecycle.complete_calls == 1


@pytest.mark.asyncio
async def test_finalization_failure_after_claim_loss_routes_to_claim_loss_cleanup() -> None:
    from fleet_rlm.rlm.ownership import RunCleanupSupervisor
    from fleet_rlm.turns.models import _ClaimLost

    lifecycle = _CleanupLifecycle(outcome=None)
    driver = _driver(lifecycle, object(), RunCleanupSupervisor())
    claim_lost = asyncio.Event()
    claim_lost.set()
    claim_waiter = asyncio.create_task(claim_lost.wait())

    async def fail_finalization() -> None:
        raise RuntimeError("authority revoked")

    finalization = asyncio.create_task(fail_finalization())
    await asyncio.sleep(0)
    result = await driver._wait_for_finalization(
        finalization,
        claim_waiter,
        remaining=1,
        is_authority_revoked=lambda: True,
    )
    claim_waiter.cancel()
    await asyncio.gather(claim_waiter, return_exceptions=True)

    assert isinstance(result, _ClaimLost)


@pytest.mark.asyncio
async def test_normal_failure_waits_for_recursive_ownership_before_prepared_close() -> None:
    from fleet_rlm.rlm.ownership import RunCleanupSupervisor
    from fleet_rlm.rlm.result import RLMOutcome

    lifecycle = _CleanupLifecycle(outcome=RLMOutcome("failed", public_error_message="Turn failed"))
    release = asyncio.Event()
    ownership_started = asyncio.Event()

    class BlockingOwnedStream(_Stream):
        async def wait_owned(self) -> None:
            ownership_started.set()
            await release.wait()

    stream = BlockingOwnedStream(outcome=lifecycle.outcome, blocking=False)
    prepared = _Prepared(deadline=asyncio.get_running_loop().time() + 10)
    runner = type("Runner", (), {"stream": lambda _self, _execution: stream})()
    cleanup = RunCleanupSupervisor()
    turn = _turn()
    task = asyncio.create_task(_collect(_driver(lifecycle, runner, cleanup), turn, prepared, None))

    await ownership_started.wait()
    assert not task.done()
    assert not prepared.closed.is_set()

    release.set()
    await task
    await cleanup.shutdown(drain_seconds=1)
    assert prepared.closed.is_set()


@pytest.mark.asyncio
async def test_cleanup_capacity_fallback_settles_after_owned_stream_drains() -> None:
    from fleet_rlm.rlm.ownership import RunCleanupSupervisor
    from fleet_rlm.rlm.result import RLMOutcome

    lifecycle = _CleanupLifecycle(outcome=RLMOutcome("timeout", public_error_message="Turn timed out"))
    stream = _Stream(outcome=lifecycle.outcome, blocking=False)
    cleanup = RunCleanupSupervisor(max_jobs=1)
    release_blocker = asyncio.Event()

    async def blocker() -> None:
        await release_blocker.wait()

    cleanup.submit(blocker())
    turn = _turn()
    prepared = _Prepared(deadline=asyncio.get_running_loop().time() + 10)
    runner = type("Runner", (), {"stream": lambda _self, _execution: stream})()
    events = await _collect(_driver(lifecycle, runner, cleanup), turn, prepared, None)

    assert events[-1].detail.__class__.__name__ == "RunTimedOut"
    assert turn.authority.revoked
    assert prepared.closed.is_set()
    assert lifecycle.settle_calls == 1
    assert lifecycle.complete_calls == 1

    release_blocker.set()
    await cleanup.shutdown(drain_seconds=1)


async def _collect(driver, turn, prepared, heartbeat):
    return [
        event
        async for event in driver._execute_claimed(
            turn,
            prepared,
            heartbeat,
            trace_id=None,
        )
    ]


async def _deadline_in_loop(driver, prepared) -> float:
    return driver._execution_deadline(prepared)


def test_execution_deadline_reads_the_deep_execution_context() -> None:
    """B1 regression: finalization waits against the shared Turn deadline,
    not a fresh fallback window (P25)."""
    from types import SimpleNamespace

    from fleet_rlm.turns import TurnRuntime

    driver = TurnRuntime.__new__(TurnRuntime)
    driver._turn_timeout_seconds = 99.0
    deep = SimpleNamespace(execution=SimpleNamespace(deadline=1234.5))
    assert driver._execution_deadline(SimpleNamespace(execution=deep)) == 1234.5
    legacy = SimpleNamespace()
    loop = asyncio.new_event_loop()
    try:
        fallback = loop.run_until_complete(_deadline_in_loop(driver, SimpleNamespace(execution=legacy)))
    finally:
        loop.close()
    assert isinstance(fallback, float)


def test_trace_request_reads_the_session_view() -> None:
    """B2 regression: MLflow turn traces record the public request text."""
    from types import SimpleNamespace

    from fleet_rlm.turns import TurnRuntime

    prepared = SimpleNamespace(execution=SimpleNamespace(session=SimpleNamespace(request="show me")))
    assert TurnRuntime._trace_request(prepared) == "show me"
    legacy = SimpleNamespace(execution=SimpleNamespace())
    assert TurnRuntime._trace_request(legacy) == ""


# --- from test_open_turn_command.py -----------------------------------
def test_open_turn_command_contains_only_claimed_canonical_values() -> None:
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.turns import OpenTurnCommand

    command = OpenTurnCommand(
        access=TurnAccess(user_id=uuid4(), workspace_id=uuid4()),
        session_id=uuid4(),
        input=TurnInput(text="inspect"),
        idempotency_key="request-1",
        proposed_run_id=uuid4(),
    )

    assert command.idempotency_key == "request-1"
    assert command.input.text == "inspect"


@pytest.mark.parametrize(
    "key",
    ["", "   ", "line\nbreak"],
    ids=["empty", "whitespace", "newline"],
)
def test_open_turn_command_rejects_invalid_idempotency_keys(key: str) -> None:
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.turns import OpenTurnCommand

    with pytest.raises(ValueError):
        OpenTurnCommand(
            access=TurnAccess(user_id=uuid4(), workspace_id=uuid4()),
            session_id=uuid4(),
            input=TurnInput(text="inspect"),
            idempotency_key=key,
            proposed_run_id=uuid4(),
        )


# --- from test_turn_coordinator_stream.py -----------------------------
@dataclass
class _OpenedStream:
    values: list[str]
    outcome: object | None = None
    run_id: object | None = None
    close_count: int = 0
    next_count: int = 0

    def __aiter__(self) -> _OpenedStream:
        return self

    async def __anext__(self) -> str:
        self.next_count += 1
        if not self.values:
            raise StopAsyncIteration
        return self.values.pop(0)

    async def aclose(self) -> None:
        self.close_count += 1


@pytest.mark.asyncio
async def test_wait_open_timeout_does_not_cancel_coordinator_open_task() -> None:
    from fleet_rlm.turns import OpenedTurnStream

    gate = asyncio.Event()
    stream = _OpenedStream(["event"])

    async def open_stream() -> _OpenedStream:
        await gate.wait()
        return stream

    owner = OpenedTurnStream(None, open_task=asyncio.create_task(open_stream()))
    assert await owner.wait_open(timeout=0.001) is None
    assert owner._open_task is not None
    assert not owner._open_task.done()
    gate.set()
    assert await owner.wait_open() is owner
    await owner.aclose()
    assert stream.next_count == 1
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_close_before_first_iteration_primes_and_closes_async_generator() -> None:
    from fleet_rlm.turns import OpenedTurnStream

    closed = asyncio.Event()

    async def events():
        try:
            yield "event"
        finally:
            closed.set()

    owner = OpenedTurnStream(None, events())
    await owner.aclose()
    await owner.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_close_failure_is_replayed_by_idempotent_close_task() -> None:
    from fleet_rlm.turns import OpenedTurnStream

    class Broken(_OpenedStream):
        async def aclose(self) -> None:
            self.close_count += 1
            raise RuntimeError("worker cleanup failed")

    owner = OpenedTurnStream(None, Broken([]))
    with pytest.raises(RuntimeError, match="worker cleanup failed"):
        await owner.aclose()
    with pytest.raises(RuntimeError, match="worker cleanup failed"):
        await owner.aclose()


@pytest.mark.asyncio
async def test_close_continues_after_first_iteration_failure() -> None:
    from fleet_rlm.turns import OpenedTurnStream

    class BrokenFirst(_OpenedStream):
        async def __anext__(self) -> str:
            self.next_count += 1
            raise RuntimeError("first iteration failed")

    stream = BrokenFirst([])
    owner = OpenedTurnStream(None, stream)
    with pytest.raises(RuntimeError, match="first iteration failed"):
        await owner.aclose()
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_nested_opened_stream_transfers_iteration_and_cleanup_ownership() -> None:
    from fleet_rlm.turns import OpenedTurnStream

    stream = _OpenedStream(["event"])
    marker = object()
    stream.outcome = marker
    inner = OpenedTurnStream(None, stream)

    async def open_stream() -> OpenedTurnStream:
        return inner

    outer = OpenedTurnStream(None, open_task=asyncio.create_task(open_stream()))
    assert await outer.wait_open() is outer
    assert outer._opened_owner is inner
    assert outer.outcome is marker

    assert await outer.__anext__() == "event"
    await outer.aclose()
    await outer.aclose()

    assert stream.next_count == 1
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_nested_opened_stream_wait_open_includes_inner_pending_open() -> None:
    from fleet_rlm.turns import OpenedTurnStream

    gate = asyncio.Event()
    inner_run_id = uuid4()
    stream = _OpenedStream(["event"], run_id=inner_run_id)

    async def open_inner() -> _OpenedStream:
        await gate.wait()
        return stream

    inner = OpenedTurnStream(inner_run_id, open_task=asyncio.create_task(open_inner()))

    async def open_outer() -> OpenedTurnStream:
        return inner

    outer = OpenedTurnStream(None, open_task=asyncio.create_task(open_outer()))
    assert await outer.wait_open(timeout=0.001) is None
    assert inner._open_task is not None
    assert not inner._open_task.done()

    gate.set()
    assert await outer.wait_open() is outer
    assert outer.run_id == inner_run_id
    assert await outer.__anext__() == "event"
    await outer.aclose()
    assert stream.close_count == 1


def test_canonical_turn_runtime_exposes_execution_deadline_behavior() -> None:
    from types import SimpleNamespace

    from fleet_rlm.turns import TurnRuntime

    runtime = TurnRuntime.__new__(TurnRuntime)
    runtime._turn_timeout_seconds = 7.0
    prepared = SimpleNamespace(execution=SimpleNamespace(execution=SimpleNamespace(deadline=42.5)))

    assert runtime._execution_deadline(prepared) == 42.5


@pytest.mark.asyncio
async def test_successful_native_context_cleanup_precedes_durable_finish() -> None:
    from types import SimpleNamespace

    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turns import TurnRuntime

    order: list[str] = []

    class Lifecycle:
        async def finish(self, *_args, **_kwargs):
            order.append("finish")
            return SimpleNamespace()

    async def not_cancelled() -> bool:
        return False

    run = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("answer"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    async def close_before_commit() -> None:
        order.append("native-context")

    prepared = SimpleNamespace(
        artifact_sink=None,
        result_snapshot_sink=None,
        post_commit_memory_promotion=None,
        aclose_before_commit=close_before_commit,
    )
    outcome = RLMOutcome(
        "completed",
        prediction=PredictionResult("answer", {"answer": "done"}, "fleet.default", "1"),
    )
    runtime = TurnRuntime(lifecycle=Lifecycle(), preparation=object(), runner=object())  # type: ignore[arg-type]

    await runtime._finish_with_trace(run, outcome, prepared)
    assert order == ["native-context", "finish"]


@pytest.mark.asyncio
async def test_native_context_cleanup_failure_blocks_durable_success() -> None:
    from types import SimpleNamespace

    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )
    from fleet_rlm.turns import TurnRuntime

    finished = False

    class Lifecycle:
        async def finish(self, *_args, **_kwargs):
            nonlocal finished
            finished = True

    async def not_cancelled() -> bool:
        return False

    run = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("answer"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    async def failed_cleanup() -> None:
        raise RuntimeError("native context still active")

    prepared = SimpleNamespace(
        artifact_sink=None,
        result_snapshot_sink=None,
        post_commit_memory_promotion=None,
        aclose_before_commit=failed_cleanup,
    )
    outcome = RLMOutcome(
        "completed",
        prediction=PredictionResult("answer", {"answer": "done"}, "fleet.default", "1"),
    )
    runtime = TurnRuntime(lifecycle=Lifecycle(), preparation=object(), runner=object())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="still active"):
        await runtime._finish_with_trace(run, outcome, prepared)
    assert finished is False


# --- Turn Coordinator Failure & Terminal State Contracts ---


def test_terminal_maps_turn_output_too_large_public_message() -> None:
    from uuid import uuid4

    from fleet_rlm.rlm.events import EventRecorder, RunFailed
    from fleet_rlm.sessions.run_state import FailedRunReceipt
    from fleet_rlm.turns import terminal

    event = terminal(
        EventRecorder(uuid4(), uuid4()),
        FailedRunReceipt(
            run_id=uuid4(),
            terminal_status="failed",
            failure_code="execution_failed",
            public_message="Turn output is too large",
            durable=False,
        ),
    )
    assert isinstance(event.detail, RunFailed)
    assert event.detail.message == "Turn output is too large"


def test_terminal_preserves_provider_endpoint_not_found_message_from_durable_failure() -> None:
    from fleet_rlm.rlm.events import PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE, EventRecorder, RunFailed
    from fleet_rlm.sessions.run_state import FailedRunReceipt
    from fleet_rlm.turns import terminal

    event = terminal(
        EventRecorder(uuid4(), uuid4()),
        FailedRunReceipt(
            run_id=uuid4(),
            terminal_status="failed",
            failure_code="execution_failed",
            public_message=PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE,
            durable=True,
        ),
    )
    assert isinstance(event.detail, RunFailed)
    assert event.detail.message == PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "terminal_type"),
    (("cancelled", "RunCancelled"), ("timeout", "RunTimedOut")),
)
async def test_open_non_success_has_one_last_terminal_and_never_promotes(
    status: str,
    terminal_type: str,
) -> None:
    from hashlib import sha256

    from fleet_rlm.rlm.events import (
        TERMINAL_DETAIL_TYPES,
        EventRecorder,
        RunCancelled,
        RunStarted,
        RunTimedOut,
        Status,
    )
    from fleet_rlm.rlm.ownership import RunCleanupSupervisor
    from fleet_rlm.rlm.result import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime
    from fleet_rlm.workspace.artifacts import ArtifactCandidate
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    expected_terminal = {"RunCancelled": RunCancelled, "RunTimedOut": RunTimedOut}[terminal_type]
    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title=f"{status} coordinator",
    )
    run_id, data = uuid4(), b"private candidate"
    candidate = ArtifactCandidate(
        uuid4(),
        access.user_id,
        access.workspace_id,
        session.id,
        run_id,
        "text",
        None,
        "text/plain",
        len(data),
        sha256(data).hexdigest(),
        f"/staging/{status}.txt",
        f"/artifacts/{status}.txt",
    )
    sink_operations: list[str] = []
    closes = 0
    fenced = asyncio.Event()

    async def fence(session_id):
        assert session_id == session.id
        fenced.set()

    class Sink:
        async def read(self, location, *, max_bytes):
            sink_operations.append(f"read:{location}:{max_bytes}")
            return data

        async def write(self, location, value):
            sink_operations.append(f"write:{location}:{len(value)}")

        async def remove(self, location):
            sink_operations.append(f"remove:{location}")

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = Sink()
        result_snapshot_sink = None
        post_commit_memory_promotion = None

        async def aclose(self):
            nonlocal closes
            closes += 1

    class Preparation:
        async def prepare(self, turn, *, deadline):
            del turn, deadline
            return Prepared()

    class Stream:
        def __init__(self):
            recorder = EventRecorder(run_id, session.id)
            self._events = iter(
                (
                    recorder.record(RunStarted(delivery="live")),
                    recorder.record(Status("execution", "running")),
                )
            )
            self.outcome = RLMOutcome(
                status,  # type: ignore[arg-type]
                artifact_candidates=(candidate,),
                public_error_message="Turn cancelled" if status == "cancelled" else "Turn timed out",
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self):
            return None

        async def wait_owned(self):
            assert fenced.is_set(), "remote stop must precede blocked-worker draining"

    class Runner:
        def stream(self, _execution):
            return Stream()

    cleanup = RunCleanupSupervisor()
    coordinator = TurnRuntime(
        lifecycle=TestingRunSettlement(store, max_artifact_bytes=100),
        preparation=Preparation(),
        runner=Runner(),
        cleanup=cleanup,
        claim_loss_fence=fence,
    )
    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput(status), status, run_id)
        )
    ]
    await cleanup.shutdown(drain_seconds=1)

    assert fenced.is_set()
    assert isinstance(events[0].detail, RunStarted)
    assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events[:-1])
    assert sum(isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events) == 1
    assert isinstance(events[-1].detail, expected_terminal)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert sink_operations == []
    assert closes == 1
    run = store._runs[run_id]
    assert (run.status, run.failure_code) == (status, status)
    assert cleanup.active_jobs == 0
    if status == "cancelled":
        # D2: a cancelled Run persists a bounded tombstone pair; other terminal
        # failures still leave the listing untouched.
        records = await store.turn_records(session.id, access)
        assert [type(record).__name__ for record in records] == ["UserTurnRecord", "AssistantTurnRecord"]
        assert records[0].input.text == status
        assert records[1].committed.text == "Turn cancelled"
        assert [part.type for part in records[1].committed.parts] == ["status", "usage", "text"]
    else:
        assert await store.turn_records(session.id, access) == ()


@pytest.mark.asyncio
async def test_open_preparation_failure_is_durable_before_stream_and_releases_claim() -> None:
    from fleet_rlm.rlm.result import empty_rlm_usage
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        RunClaim,
        RunFailure,
    )
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime
    from fleet_rlm.turns.preparation import RunPreparationUnavailableError
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    lifecycle = TestingRunSettlement(store, max_artifact_bytes=100)
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="preparation failure",
    )
    runner_calls = 0

    class Preparation:
        async def prepare(self, turn, *, deadline):
            del turn, deadline
            raise RunPreparationUnavailableError("provider detail must not escape")

    class Runner:
        def stream(self, _execution):
            nonlocal runner_calls
            runner_calls += 1
            raise AssertionError("runner must not start")

    coordinator = TurnRuntime(lifecycle=lifecycle, preparation=Preparation(), runner=Runner())
    with pytest.raises(RunPreparationUnavailableError):
        await coordinator.open(OpenTurnCommand(access, session.id, TurnInput("prepare"), "prepare-failure", uuid4()))

    assert runner_calls == 0
    assert await store.turn_records(session.id, access) == ()
    followup = await lifecycle.begin(RunClaim(access, session.id, TurnInput("followup"), "followup", uuid4()))
    await lifecycle.finish(
        followup,
        RunFailure("failed", "execution_failed", "Turn failed", empty_rlm_usage()),
    )


@pytest.mark.asyncio
async def test_open_preparation_timeout_finishes_as_typed_timeout_before_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime
    from fleet_rlm.turns.preparation import RunPreparationTimeoutError
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    class Span:
        request_id = "tr-preparation-timeout"

        def set_inputs(self, _payload):
            return None

        def set_outputs(self, _payload):
            return None

        def set_status(self, _status):
            return None

    span = Span()

    @contextmanager
    def start_span(**_kwargs: Any) -> Iterator[Span]:
        yield span

    mlflow = ModuleType("mlflow")
    mlflow.start_span = start_span  # type: ignore[attr-defined]
    mlflow.update_current_trace = lambda **_kwargs: None  # type: ignore[attr-defined]
    mlflow.get_last_active_trace_id = lambda: span.request_id  # type: ignore[attr-defined]
    mlflow.get_current_active_span = lambda: span  # type: ignore[attr-defined]
    entities = ModuleType("mlflow.entities")
    entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    authoritative = TestingRunSettlement(store, max_artifact_bytes=100)
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="preparation timeout",
    )
    finishes = []

    class Lifecycle:
        heartbeat_seconds = authoritative.heartbeat_seconds
        stale_after_seconds = authoritative.stale_after_seconds

        async def begin(self, request):
            return await authoritative.begin(request)

        async def heartbeat(self, turn):
            return await authoritative.heartbeat(turn)

        async def settle(self, turn, failure):
            return await authoritative.settle(turn, failure)

        async def revoke_claim(self, turn, failure):
            return await authoritative.revoke_claim(turn, failure)

        async def complete_settling(self, turn):
            return await authoritative.complete_settling(turn)

        async def finish(self, turn, resolution, **kwargs):
            finishes.append(resolution)
            return await authoritative.finish(turn, resolution, **kwargs)

    class Preparation:
        async def prepare(self, turn, *, deadline):
            del turn, deadline
            raise RunPreparationTimeoutError("private provider timeout")

    class Runner:
        def stream(self, _execution):
            raise AssertionError("runner must not start")

    with pytest.raises(RunPreparationTimeoutError):
        await TurnRuntime(
            lifecycle=Lifecycle(),
            preparation=Preparation(),
            runner=Runner(),
            mlflow_tracing_enabled=True,
        ).open(OpenTurnCommand(access, session.id, TurnInput("prepare"), "prepare-timeout", uuid4()))

    assert len(finishes) == 1
    assert finishes[0].terminal_status == "timeout"
    assert finishes[0].failure_code == "timeout"
    assert finishes[0].public_message == "Turn preparation timed out"


@pytest.mark.asyncio
async def test_open_midstream_execution_failure_keeps_sequence_and_terminal_order() -> None:

    from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES, EventRecorder, RunFailed, RunStarted, Status
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="execution failure",
    )
    run_id = uuid4()
    closes = 0

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = None
        result_snapshot_sink = None
        post_commit_memory_promotion = None

        async def aclose(self):
            nonlocal closes
            closes += 1

    class Preparation:
        async def prepare(self, turn, *, deadline):
            del turn, deadline
            return Prepared()

    class Stream:
        def __init__(self):
            recorder = EventRecorder(run_id, session.id)
            self._events = iter(
                (
                    recorder.record(RunStarted(delivery="live")),
                    recorder.record(Status("execution", "running")),
                )
            )
            self.outcome = None

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration:
                raise RuntimeError("provider detail must not escape") from None

        async def aclose(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    coordinator = TurnRuntime(
        lifecycle=TestingRunSettlement(store, max_artifact_bytes=100),
        preparation=Preparation(),
        runner=Runner(),
    )
    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("fail"), "execution-failure", run_id)
        )
    ]

    assert isinstance(events[0].detail, RunStarted)
    assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events[:-1])
    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "execution_failed"
    assert [event.sequence for event in events] == [1, 2, 3]
    assert closes == 1


@pytest.mark.asyncio
async def test_open_commit_failure_projects_commit_failure_terminal(monkeypatch: pytest.MonkeyPatch) -> None:

    from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES, EventRecorder, RunFailed, RunStarted
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    updates: list[dict[str, object]] = []

    class Span:
        request_id = "tr-commit-failed"

        def set_inputs(self, _payload):
            return None

        def set_outputs(self, _payload):
            return None

        def set_attributes(self, _payload):
            return None

        def set_status(self, _status):
            return None

    span = Span()

    @contextmanager
    def start_span(**_kwargs: Any) -> Iterator[Span]:
        yield span

    def update_current_trace(**kwargs: object) -> None:
        updates.append(kwargs)

    mlflow = ModuleType("mlflow")
    mlflow.start_span = start_span  # type: ignore[attr-defined]
    mlflow.update_current_trace = update_current_trace  # type: ignore[attr-defined]
    mlflow.get_last_active_trace_id = lambda: span.request_id  # type: ignore[attr-defined]
    mlflow.get_current_active_span = lambda: span  # type: ignore[attr-defined]
    entities = ModuleType("mlflow.entities")
    entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)

    access = TurnAccess(uuid4(), uuid4())
    authoritative = InMemoryRunStateStore()
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="commit failure",
    )
    run_id = uuid4()
    closes = 0

    class CommitFailingStore:
        begin = authoritative.begin
        transition_claim = authoritative.transition_claim
        request_cancel = authoritative.request_cancel

        async def commit(self, turn, committed, artifacts):
            del turn, committed, artifacts
            raise RuntimeError("database detail must not escape")

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = None
        result_snapshot_sink = None
        post_commit_memory_promotion = None

        async def aclose(self):
            nonlocal closes
            closes += 1

    class Preparation:
        async def prepare(self, turn, *, deadline):
            del turn, deadline
            return Prepared()

    class Stream:
        def __init__(self):
            self._event = EventRecorder(run_id, session.id).record(RunStarted(delivery="live"))
            self.outcome = RLMOutcome(
                "completed",
                PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._event is None:
                raise StopAsyncIteration
            event, self._event = self._event, None
            return event

        async def aclose(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    coordinator = TurnRuntime(
        lifecycle=TestingRunSettlement(CommitFailingStore(), max_artifact_bytes=100),
        preparation=Preparation(),
        runner=Runner(),
        mlflow_tracing_enabled=True,
    )
    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("commit"), "commit-failure", run_id)
        )
    ]

    assert isinstance(events[0].detail, RunStarted)
    assert all(not isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events[:-1])
    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "commit_failed"
    assert events[-1].detail.message == "Turn could not be committed"
    assert updates[-1] == {"state": "ERROR"}
    assert closes == 1
    assert await authoritative.turn_records(session.id, access) == ()


@pytest.mark.asyncio
async def test_failed_turn_emits_settlement_claim_and_cleanup_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.observability import tracing as turn_tracing
    from fleet_rlm.rlm.events import EventRecorder, RunFailed, RunStarted, Status
    from fleet_rlm.rlm.ownership import RunCleanupSupervisor
    from fleet_rlm.rlm.result import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    token = turn_tracing._fleet_trace_active.set(True)
    try:
        names: list[str] = []

        class Span:
            request_id = "tr-failed-spans"

            def set_inputs(self, _payload):
                return None

            def set_outputs(self, _payload):
                return None

            def set_status(self, _status):
                return None

        span = Span()

        @contextmanager
        def start_span(*, name: str = "span", **_kwargs: Any) -> Iterator[Span]:
            names.append(name)
            yield span

        mlflow = ModuleType("mlflow")
        mlflow.start_span = start_span  # type: ignore[attr-defined]
        mlflow.update_current_trace = lambda **_kwargs: None  # type: ignore[attr-defined]
        mlflow.get_last_active_trace_id = lambda: span.request_id  # type: ignore[attr-defined]
        mlflow.get_current_active_span = lambda: span  # type: ignore[attr-defined]
        entities = ModuleType("mlflow.entities")
        entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "mlflow", mlflow)
        monkeypatch.setitem(sys.modules, "mlflow.entities", entities)

        access = TurnAccess(uuid4(), uuid4())
        store = InMemoryRunStateStore()
        session = await InMemorySessionCatalog(store).create(
            user_id=access.user_id, workspace_id=access.workspace_id, title="failed spans"
        )
        run_id = uuid4()

        class Sink:
            async def remove(self, location):
                del location
                return None

        class Prepared:
            execution = SimpleNamespace(run_id=run_id, session_id=session.id, request="fail")
            artifact_sink = Sink()
            result_snapshot_sink = None
            post_commit_memory_promotion = None

            async def aclose(self):
                return None

        class Preparation:
            async def prepare(self, turn, *, deadline):
                del turn, deadline
                return Prepared()

        class Stream:
            def __init__(self):
                recorder = EventRecorder(run_id, session.id)
                self._events = iter(
                    (
                        recorder.record(RunStarted(delivery="live")),
                        recorder.record(Status("execution", "running")),
                    )
                )
                self.outcome = RLMOutcome("failed", public_error_message="Turn failed")

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self._events)
                except StopIteration:
                    raise StopAsyncIteration from None

            async def aclose(self):
                return None

            async def wait_owned(self):
                return None

        class Runner:
            def stream(self, _execution):
                return Stream()

        cleanup = RunCleanupSupervisor()
        coordinator = TurnRuntime(
            lifecycle=TestingRunSettlement(store, max_artifact_bytes=100),
            preparation=Preparation(),
            runner=Runner(),
            cleanup=cleanup,
        )
        events = [
            event
            async for event in await coordinator.open(
                OpenTurnCommand(access, session.id, TurnInput("fail"), "fail", run_id)
            )
        ]
        await cleanup.shutdown(drain_seconds=1)

        assert isinstance(events[-1].detail, RunFailed)
        assert [name for name in names if name.startswith("Turn.") and not name.startswith("Turn.progress.")] == [
            "Turn.prepare",
            "Turn.settlement",
            "Turn.claim_transition",
            "Turn.cleanup",
        ]
        # Runtime Events are no longer echoed into MLflow: only real phase
        # spans (and standard DSPy autolog spans, not active here) exist.
        assert [name for name in names if name.startswith("Turn.progress.")] == []
    finally:
        turn_tracing._fleet_trace_active.reset(token)
