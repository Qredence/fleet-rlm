"""Turn coordinator cancellation during commit."""

from __future__ import annotations

import asyncio
from typing import ClassVar
from uuid import uuid4

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_succeeds", [False])
async def test_coordinator_settles_commit_after_cancellation(commit_succeeds: bool) -> None:

    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        CommittedTurnReceipt,
        FailedRunReceipt,
        RunLifecycleService,
        _RunClaimToken,
    )
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("hi"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    commit_started, release_commit = asyncio.Event(), asyncio.Event()

    class Store:
        failures = 0

        async def begin(self, request):
            del request
            return turn

        async def commit(self, claimed, committed, artifacts):
            del claimed
            commit_started.set()
            await release_commit.wait()
            if not commit_succeeds:
                raise RuntimeError("commit failed")
            return CommittedTurnReceipt(run_id, 1, committed, artifacts)

        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.result import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            self.failures += 1
            return FailedRunReceipt(
                claimed.run_id,
                failure.terminal_status,
                failure.failure_code,
                failure.public_message,
                True,
            )

        async def heartbeat(self, claimed):
            del claimed
            return None

    class Snapshot:
        path = f"/sessions/{session_id}/runs/{run_id}/result.json"
        values: ClassVar[dict[str, bytes]] = {}

        def result_path(self, requested_session_id, requested_run_id):
            del requested_session_id, requested_run_id
            return self.path

        async def write(self, location, value):
            self.values[location] = value

        async def remove(self, location):
            self.values.pop(location, None)

    snapshot = Snapshot()

    class Prepared:
        execution = object()
        artifact_sink = None
        result_snapshot_sink = snapshot
        post_commit_memory_promotion = None

        async def aclose(self):
            return None

    class Preparation:
        async def prepare(self, claimed, *, deadline):
            del claimed, deadline
            return Prepared()

    class Stream:
        outcome = RLMOutcome(
            "completed",
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
        )

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self):
            return None

    class Runner:
        def stream(self, execution):
            del execution
            return Stream()

    store = Store()
    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(store, max_artifact_bytes=1024),
        preparation=Preparation(),
        runner=Runner(),
    )

    async def collect():
        opened = await coordinator.open(OpenTurnCommand(access, session_id, TurnInput("hi"), "key", run_id))
        return [event async for event in opened]

    task = asyncio.create_task(collect())
    await commit_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_commit.set()

    if commit_succeeds:
        events = await task
        assert events[-1].kind == "run.completed"
        assert snapshot.values.keys() == {snapshot.path}
        assert store.failures == 0
    else:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert snapshot.values == {}
        assert store.failures == 1


@pytest.mark.asyncio
async def test_coordinator_cancellation_during_preparation_cancels_late_prepare_and_revokes_authority() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, FailedRunReceipt, RunLifecycleService, _RunClaimToken
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("prepare"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Store:
        failures = 0

        async def begin(self, request):
            del request
            return turn

        async def transition_claim(self, claimed, command):
            del command
            self.failures += 1
            return FailedRunReceipt(claimed.run_id, "cancelled", "cancelled", "Turn cancelled", True)

        async def heartbeat(self, claimed):
            del claimed

    class Preparation:
        async def prepare(self, claimed, *, deadline):
            del claimed, deadline
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(Store(), max_artifact_bytes=1024),
        preparation=Preparation(),
        runner=object(),  # type: ignore[arg-type]
    )

    task = asyncio.create_task(
        coordinator.open(OpenTurnCommand(access, session_id, TurnInput("prepare"), "key", run_id))
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled.is_set()
    assert turn.authority.revoked


@pytest.mark.asyncio
async def test_cancellation_resistant_preparation_completes_settling_after_late_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, FailedRunReceipt, RunLifecycleService, _RunClaimToken
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("prepare"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()

    class Store:
        failures = 0

        async def begin(self, request):
            del request
            return turn

        async def transition_claim(self, claimed, command):
            del command
            self.failures += 1
            return FailedRunReceipt(claimed.run_id, "cancelled", "cancelled", "Turn cancelled", True)

        async def heartbeat(self, claimed):
            del claimed

    class Prepared:
        async def aclose(self):
            closed.set()

    class Preparation:
        async def prepare(self, claimed, *, deadline):
            del claimed, deadline
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return Prepared()

    monkeypatch.setattr("fleet_rlm.chat.turn_coordinator._PREPARATION_CLEANUP_TIMEOUT_S", 0.01)
    store = Store()
    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(store, max_artifact_bytes=1024),
        preparation=Preparation(),
        runner=object(),  # type: ignore[arg-type]
    )
    task = asyncio.create_task(
        coordinator.open(OpenTurnCommand(access, session_id, TurnInput("prepare"), "key", run_id))
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert turn.authority.revoked
    assert store.failures == 1
    release.set()
    for _ in range(100):
        if closed.is_set() and store.failures == 2:
            break
        await asyncio.sleep(0.01)
    assert closed.is_set()
    assert store.failures == 2


@pytest.mark.asyncio
async def test_late_preparation_close_failure_blocks_settlement_release(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, FailedRunReceipt, RunLifecycleService, _RunClaimToken
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("prepare"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()

    class Store:
        failures = 0

        async def begin(self, request):
            del request
            return turn

        async def transition_claim(self, claimed, command):
            del command
            self.failures += 1
            return FailedRunReceipt(claimed.run_id, "cancelled", "cancelled", "Turn cancelled", True)

        async def heartbeat(self, claimed):
            del claimed

    class Prepared:
        async def aclose(self):
            closed.set()
            raise RuntimeError("late close failed")

    class Preparation:
        async def prepare(self, claimed, *, deadline):
            del claimed, deadline
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return Prepared()

    monkeypatch.setattr("fleet_rlm.chat.turn_coordinator._PREPARATION_CLEANUP_TIMEOUT_S", 0.01)
    store = Store()
    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(store, max_artifact_bytes=1024),
        preparation=Preparation(),
        runner=object(),  # type: ignore[arg-type]
    )
    task = asyncio.create_task(
        coordinator.open(OpenTurnCommand(access, session_id, TurnInput("prepare"), "key", run_id))
    )
    await started.wait()
    task.cancel()
    with caplog.at_level(logging.ERROR):
        with pytest.raises(asyncio.CancelledError):
            await task

        release.set()
        for _ in range(100):
            if closed.is_set():
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)

    assert closed.is_set()
    # BeginSettlement runs, but a failed late PreparedRun cleanup must not be
    # followed by complete_settling: the claim stays retained and the error is
    # reported (qredence fail-closed cleanup policy).
    assert store.failures == 1
    assert "late Turn preparation cleanup failed" in caplog.text
    assert "detached Run cleanup failed" in caplog.text


@pytest.mark.asyncio
async def test_inline_preparation_close_failure_fails_closed_on_claim_loss(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        FailedRunReceipt,
        RunLifecycleService,
        RunLifecycleUnavailableError,
        _RunClaimToken,
    )
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("prepare"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    started = asyncio.Event()
    closed = asyncio.Event()

    class Store:
        def __init__(self) -> None:
            self.commands: list[object] = []

        async def begin(self, request):
            del request
            return turn

        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import HeartbeatClaim

            if isinstance(command, HeartbeatClaim):
                raise RunLifecycleUnavailableError("Turn claim is no longer available")
            self.commands.append(command)
            return FailedRunReceipt(claimed.run_id, "failed", "stale_claim", "Turn failed", True)

    class Prepared:
        async def aclose(self):
            closed.set()
            raise RuntimeError("late close failed")

    class Preparation:
        async def prepare(self, claimed, *, deadline):
            del claimed, deadline
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Cancellation-resistant provider: it completes with resources
                # after cancel so the inline done-path owns the close.
                return Prepared()
            return Prepared()

    monkeypatch.setattr("fleet_rlm.chat.turn_coordinator._PREPARATION_CLEANUP_TIMEOUT_S", 1.0)
    store = Store()
    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(store, max_artifact_bytes=1024, heartbeat_seconds=0.01, stale_after_seconds=0.01),
        preparation=Preparation(),
        runner=object(),  # type: ignore[arg-type]
    )
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RunLifecycleUnavailableError, match="Turn claim is no longer available"):
            await coordinator.open(OpenTurnCommand(access, session_id, TurnInput("prepare"), "key", run_id))
        await started.wait()
        for _ in range(100):
            if closed.is_set():
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)

    from fleet_rlm.chat.run_claim import CompleteSettlement, RevokeClaim

    assert closed.is_set()
    # Claim loss revokes authority, but the failed inline PreparedRun close
    # must block the final settlement release instead of silently completing.
    assert any(isinstance(command, RevokeClaim) for command in store.commands)
    assert not any(isinstance(command, CompleteSettlement) for command in store.commands)
    assert "late Turn preparation cleanup failed" in caplog.text
