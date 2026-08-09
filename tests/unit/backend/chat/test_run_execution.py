"""Focused coverage for the private post-preparation Run driver."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _turn():
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

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
        from fleet_rlm.chat.run_lifecycle import FailedRunReceipt

        return FailedRunReceipt(uuid4(), "failed", "execution_failed", "Turn failed", True)

    async def settle(self, turn, failure):
        del turn, failure
        self.settle_calls += 1
        from fleet_rlm.chat.run_lifecycle import FailedRunReceipt

        return FailedRunReceipt(uuid4(), "cancelled", "cancelled", "Turn cancelled", True)

    async def revoke_claim(self, turn, failure):
        del turn, failure
        self.revoke_calls += 1
        from fleet_rlm.chat.run_lifecycle import FailedRunReceipt

        return FailedRunReceipt(uuid4(), "failed", "stale_claim", "Turn failed", True)

    async def complete_settling(self, turn):
        del turn
        self.complete_calls += 1


def _driver(lifecycle, runner, cleanup):
    from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
    from fleet_rlm.chat.run_execution import RunExecutionDriver

    return RunExecutionDriver(
        lifecycle=lifecycle,
        runner=runner,
        projector=CommittedTurnEventProjector(),
        cleanup=cleanup,
        claim_loss_fence=None,
        turn_timeout_seconds=10,
        revoke_claim=lifecycle.revoke_claim,
    )


@pytest.mark.asyncio
async def test_finalization_wins_simultaneous_claim_loss() -> None:
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_execution import _ClaimHeartbeat
    from fleet_rlm.rlm.events import RunFailed
    from fleet_rlm.rlm.outcome import RLMOutcome

    release_finish = asyncio.Event()
    lifecycle = _CleanupLifecycle(
        outcome=RLMOutcome("failed", public_error_message="Turn failed"),
        release_finish=release_finish,
    )
    stream = _Stream(outcome=lifecycle.outcome, blocking=False)
    heartbeat_task = asyncio.create_task(asyncio.Event().wait())
    heartbeat = _ClaimHeartbeat(heartbeat_task, asyncio.Event())
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
async def test_disconnect_cancels_provider_wait_and_orders_detached_cleanup() -> None:
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_execution import _ClaimHeartbeat
    from fleet_rlm.rlm.outcome import RLMOutcome

    order: list[str] = []
    lifecycle = _CleanupLifecycle(outcome=RLMOutcome("failed", public_error_message="Turn failed"))
    stream = _Stream(outcome=lifecycle.outcome, order=order)
    heartbeat_task = asyncio.create_task(asyncio.Event().wait())
    heartbeat = _ClaimHeartbeat(heartbeat_task, asyncio.Event())
    cleanup = RunCleanupSupervisor()

    class Runner:
        def stream(self, _execution):
            return stream

    turn = _turn()
    prepared = _Prepared(deadline=asyncio.get_running_loop().time() + 10)

    async def collect_driver():
        async for _event in _driver(lifecycle, Runner(), cleanup).stream(
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


async def _collect(driver, turn, prepared, heartbeat):
    return [
        event
        async for event in driver.stream(
            turn,
            prepared,
            heartbeat,
            trace_id=None,
        )
    ]
