"""Behavioral coverage for heartbeat-driven Turn Claim revocation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest


def test_host_tool_rejects_calls_after_authority_revocation() -> None:
    import dspy

    from fleet_rlm.chat.run_authority import RunAuthority
    from fleet_rlm.rlm.events import ToolFailed, ToolStarted
    from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool

    authority = RunAuthority()
    effects: list[str] = []
    details: list[object] = []

    def write_workspace_text(value: str) -> str:
        effects.append(value)
        return value

    tool = observe_tool(
        dspy.Tool(write_workspace_text, name="write_workspace_text"),
        details.append,
        ToolEventView.metadata_only(),
        is_authorized=lambda: not authority.revoked,
    )
    authority.revoke()

    with pytest.raises(RuntimeError, match="no longer authorized"):
        tool(value="late write")
    assert effects == []
    assert [type(detail) for detail in details] == [ToolStarted, ToolFailed]


@pytest.mark.asyncio
async def test_heartbeat_supervision_covers_preparation() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService, RunLifecycleUnavailableError, RunStateError
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="preparation heartbeat",
    )

    class Store:
        begin = authoritative.begin
        commit = authoritative.commit
        request_cancel = authoritative.request_cancel

        async def transition_claim(self, turn, command):
            from fleet_rlm.chat.run_claim import HeartbeatClaim

            if isinstance(command, HeartbeatClaim):
                raise RunStateError("Turn claim is invalid")
            return await authoritative.transition_claim(turn, command)

    class Preparation:
        async def prepare(self, _turn, *, deadline):
            del deadline
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    class Runner:
        def stream(self, _execution):
            raise AssertionError("execution must not start")

    fenced = asyncio.Event()

    async def fence(requested_session_id):
        assert requested_session_id == session.id
        fenced.set()

    cleanup = RunCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(
            Store(),
            max_artifact_bytes=100,
            heartbeat_seconds=0.01,
            stale_after_seconds=0.06,
        ),
        preparation=Preparation(),
        runner=Runner(),
        cleanup=cleanup,
        claim_loss_fence=fence,
    )

    with pytest.raises(RunLifecycleUnavailableError):
        await coordinator.open(OpenTurnCommand(access, session.id, TurnInput("hello"), "preparation", uuid4()))
    await cleanup.shutdown(drain_seconds=1)
    assert fenced.is_set()


@pytest.mark.asyncio
async def test_transient_heartbeat_failure_recovers_without_ending_run() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunStarted
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="heartbeat retry",
    )
    attempts = 0

    class Store:
        begin = authoritative.begin
        commit = authoritative.commit
        request_cancel = authoritative.request_cancel

        async def transition_claim(self, turn, command):
            from fleet_rlm.chat.run_claim import HeartbeatClaim

            if not isinstance(command, HeartbeatClaim):
                return await authoritative.transition_claim(turn, command)
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("temporary database failure")
            return await authoritative.transition_claim(turn, command)

    run_id = uuid4()

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = None
        result_snapshot_sink = None

        async def aclose(self):
            return None

    class Preparation:
        async def prepare(self, _turn, *, deadline):
            del deadline
            await asyncio.sleep(0.08)
            return Prepared()

    class Stream:
        outcome = RLMOutcome(
            "completed",
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
        )

        def __init__(self):
            self._sent = False

        async def __anext__(self):
            if not self._sent:
                self._sent = True
                await asyncio.sleep(0.08)
                return EventRecorder(run_id, session.id).record(RunStarted(delivery="live"))
            raise StopAsyncIteration

        async def aclose(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    cleanup = RunCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(
            Store(),
            max_artifact_bytes=100,
            heartbeat_seconds=0.01,
            stale_after_seconds=1,
        ),
        preparation=Preparation(),
        runner=Runner(),
        cleanup=cleanup,
    )

    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("hello"), "retry", run_id)
        )
    ]

    assert attempts >= 2
    assert isinstance(events[-1].detail, RunCompleted)
    await cleanup.shutdown(drain_seconds=1)


@pytest.mark.asyncio
async def test_repeated_transient_failures_revoke_without_provider_fence() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.events import EventRecorder, RunFailed, RunStarted
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="heartbeat deadline",
    )

    class Store:
        begin = authoritative.begin
        commit = authoritative.commit
        request_cancel = authoritative.request_cancel

        async def transition_claim(self, turn, command):
            from fleet_rlm.chat.run_claim import HeartbeatClaim

            if isinstance(command, HeartbeatClaim):
                raise ConnectionError("database unavailable")
            return await authoritative.transition_claim(turn, command)

    run_id = uuid4()

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = None
        result_snapshot_sink = None

        async def aclose(self):
            return None

    class Preparation:
        async def prepare(self, _turn, *, deadline):
            del deadline
            return Prepared()

    class Stream:
        outcome = None

        def __init__(self):
            self._sent = False

        async def __anext__(self):
            if not self._sent:
                self._sent = True
                return EventRecorder(run_id, session.id).record(RunStarted(delivery="live"))
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def aclose(self):
            return None

        async def wait_owned(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    cleanup = RunCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(
            Store(),
            max_artifact_bytes=100,
            heartbeat_seconds=0.01,
            stale_after_seconds=0.04,
        ),
        preparation=Preparation(),
        runner=Runner(),
        cleanup=cleanup,
    )
    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("hello"), "deadline", run_id)
        )
    ]

    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "unavailable"
    await cleanup.shutdown(drain_seconds=1)


@pytest.mark.asyncio
async def test_claim_loss_wins_finalization_and_prevents_stale_commit() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService, RunStateError
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.events import RunFailed
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="finalization race",
    )
    release_commit = asyncio.Event()

    class Store:
        begin = authoritative.begin
        request_cancel = authoritative.request_cancel

        async def transition_claim(self, turn, command):
            from fleet_rlm.chat.run_claim import HeartbeatClaim

            if isinstance(command, HeartbeatClaim):
                raise RunStateError("Turn claim is invalid")
            return await authoritative.transition_claim(turn, command)

        async def commit(self, turn, committed, artifacts):
            await release_commit.wait()
            return await authoritative.commit(turn, committed, artifacts)

    run_id = uuid4()

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = None
        result_snapshot_sink = None

        async def aclose(self):
            return None

    class Preparation:
        async def prepare(self, _turn, *, deadline):
            del deadline
            return Prepared()

    class Stream:
        outcome = RLMOutcome(
            "completed",
            PredictionResult("stale", {"answer": "stale"}, "fleet.default", "1"),
        )

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self):
            run = authoritative._runs[run_id]
            assert (run.status, run.failure_code) == ("settling", "stale_claim")
            assert release_commit.is_set()

        async def wait_owned(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    async def fence(_session_id):
        run = authoritative._runs[run_id]
        assert (run.status, run.failure_code) == ("settling", "stale_claim")
        release_commit.set()

    cleanup = RunCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(
            Store(),
            max_artifact_bytes=100,
            heartbeat_seconds=0.01,
            stale_after_seconds=0.04,
        ),
        preparation=Preparation(),
        runner=Runner(),
        cleanup=cleanup,
        claim_loss_fence=fence,
    )
    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("hello"), "race", run_id)
        )
    ]
    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "unavailable"
    await cleanup.shutdown(drain_seconds=1)
    assert await authoritative.turn_records(session.id, access) == ()
    old_run = authoritative._runs[run_id]
    assert (old_run.status, old_run.failure_code) == ("failed", "stale_claim")


@pytest.mark.asyncio
async def test_invalid_heartbeat_revokes_run_fences_before_releasing_claim() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService, RunStateError
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.events import EventRecorder, RunFailed, RunStarted
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="heartbeat loss",
    )
    heartbeat_attempted = asyncio.Event()
    cleanup_order: list[str] = []

    class Store:
        begin = authoritative.begin
        commit = authoritative.commit
        request_cancel = authoritative.request_cancel

        async def transition_claim(self, turn, command):
            from fleet_rlm.chat.run_claim import HeartbeatClaim

            if not isinstance(command, HeartbeatClaim):
                return await authoritative.transition_claim(turn, command)
            heartbeat_attempted.set()
            raise RunStateError("Turn claim is invalid")

    run_id = uuid4()

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = None
        result_snapshot_sink = None

        async def aclose(self):
            cleanup_order.append("resources-closed")

    class Preparation:
        async def prepare(self, _turn, *, deadline):
            del deadline
            return Prepared()

    class Stream:
        outcome = None

        def __init__(self):
            self._sent = False
            self._closed = asyncio.Event()

        async def __anext__(self):
            if not self._sent:
                self._sent = True
                return EventRecorder(run_id, session.id).record(RunStarted(delivery="live"))
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def aclose(self):
            self._closed.set()

        async def wait_owned(self):
            cleanup_order.append("worker-stopped")

    class Runner:
        def stream(self, _execution):
            return Stream()

    async def fence(requested_session_id):
        assert requested_session_id == session.id
        cleanup_order.append("sandbox-fenced")

    cleanup = RunCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(
            Store(),
            max_artifact_bytes=100,
            heartbeat_seconds=0.01,
            stale_after_seconds=0.06,
        ),
        preparation=Preparation(),
        runner=Runner(),
        cleanup=cleanup,
        claim_loss_fence=fence,
    )

    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("hello"), "lost", run_id)
        )
    ]
    assert heartbeat_attempted.is_set()
    assert isinstance(events[-1].detail, RunFailed)
    assert events[-1].detail.code == "unavailable"
    assert events[-1].detail.message == "Turn failed"

    await cleanup.shutdown(drain_seconds=1)
    assert cleanup_order == ["sandbox-fenced", "worker-stopped", "resources-closed"]
    old_run = authoritative._runs[run_id]
    assert old_run.status == "failed"
    assert old_run.failure_code == "stale_claim"
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim

    replacement = await authoritative.begin(RunClaim(access, session.id, TurnInput("next"), "next", uuid4()))
    assert isinstance(replacement, ClaimedRun)


@pytest.mark.asyncio
async def test_post_commit_heartbeat_does_not_fail_committed_turn(caplog) -> None:
    """RC-8 regression: a heartbeat racing post-commit must never fail the live stream."""
    import logging

    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunFailed, RunStarted
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="commit beats heartbeat",
    )
    run_id = uuid4()
    snapshot_gate = asyncio.Event()

    class SnapshotSink:
        def result_path(self, session_id, requested_run_id):
            return f"/snapshots/{session_id}/{requested_run_id}.json"

        async def write(self, _location, _value):
            # Hold finalization open after the durable commit, exactly like the
            # overlapped volume round-trip in the incident.
            await snapshot_gate.wait()

        async def remove(self, _location):
            return None

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session.id)
        artifact_sink = None
        result_snapshot_sink = SnapshotSink()

        async def aclose(self):
            return None

    class Preparation:
        async def prepare(self, _turn, *, deadline):
            del deadline
            return Prepared()

    class Stream:
        outcome = RLMOutcome(
            "completed",
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
        )

        def __init__(self):
            self._sent = False

        async def __anext__(self):
            if not self._sent:
                self._sent = True
                return EventRecorder(run_id, session.id).record(RunStarted(delivery="live"))
            raise StopAsyncIteration

        async def aclose(self):
            return None

        async def wait_owned(self):
            return None

    class Runner:
        def stream(self, _execution):
            return Stream()

    cleanup = RunCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(
            authoritative,
            max_artifact_bytes=100,
            heartbeat_seconds=0.01,
            stale_after_seconds=5,
        ),
        preparation=Preparation(),
        runner=Runner(),
        cleanup=cleanup,
    )

    with caplog.at_level(logging.INFO):
        stream = await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("hello"), "commit-beats-heartbeat", run_id)
        )

        async def drain():
            return [event async for event in stream]

        drain_task = asyncio.ensure_future(drain())
        for _ in range(200):
            if authoritative._runs[run_id].status == "completed":
                break
            await asyncio.sleep(0.005)
        assert authoritative._runs[run_id].status == "completed"
        # Several heartbeat ticks now land on the already-committed row.
        await asyncio.sleep(0.1)
        snapshot_gate.set()
        events = await asyncio.wait_for(drain_task, 5)
    await cleanup.shutdown(drain_seconds=1)

    assert isinstance(events[-1].detail, RunCompleted)
    assert not any(isinstance(event.detail, RunFailed) for event in events)
    assert authoritative._runs[run_id].status == "completed"
    assert "claim heartbeat stopped after commit" in caplog.text
    assert "detached Turn cleanup failed" not in caplog.text


@pytest.mark.asyncio
async def test_claim_loss_cleanup_after_commit_is_a_benign_no_op(caplog) -> None:
    """Claim-loss cleanup racing a committed Turn logs and no-ops; commit state is untouched."""
    import logging

    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_execution import _ClaimHeartbeat
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        CommittedTurnReceipt,
        RunAlreadyCompletedError,
        RunClaim,
        RunFailure,
        RunLifecycleService,
        RunStateError,
    )
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.dspy_contract import PredictionResult, empty_rlm_usage
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    assert issubclass(RunAlreadyCompletedError, RunStateError)

    authoritative = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="cleanup loses to commit",
    )
    lifecycle = RunLifecycleService(authoritative, max_artifact_bytes=100)
    start = await lifecycle.begin(RunClaim(access, session.id, TurnInput("hello"), "begin", uuid4()))
    assert isinstance(start, ClaimedRun)
    receipt = await lifecycle.finish(
        start,
        RLMOutcome("completed", PredictionResult("done", {"answer": "done"}, "fleet.default", "1")),
    )
    assert isinstance(receipt, CommittedTurnReceipt)

    fenced: list[object] = []

    async def fence(requested_session_id):
        fenced.append(requested_session_id)

    cleanup = RunCleanupSupervisor()

    class Preparation:
        async def prepare(self, _turn, *, deadline):
            del deadline
            raise AssertionError("preparation must not start")

    class Runner:
        def stream(self, _execution):
            raise AssertionError("execution must not start")

    coordinator = TurnCoordinator(
        lifecycle=lifecycle,
        preparation=Preparation(),
        runner=Runner(),
        cleanup=cleanup,
        claim_loss_fence=fence,
    )
    heartbeat = _ClaimHeartbeat(asyncio.create_task(asyncio.sleep(60)), asyncio.Event())
    with caplog.at_level(logging.INFO):
        coordinator._submit_claim_loss_cleanup(start, heartbeat)
        await cleanup.shutdown(drain_seconds=1)

    run = authoritative._runs[start.run_id]
    assert (run.status, run.failure_code) == ("completed", None)
    assert fenced == []
    assert not start.authority.revoked
    assert "stale-claim revocation skipped for committed Run" in caplog.text
    assert "detached Turn cleanup failed" not in caplog.text

    # The strict lifecycle path keeps refusing to revoke a committed claim.
    with pytest.raises(RunAlreadyCompletedError):
        await lifecycle.revoke_claim(start, RunFailure("failed", "stale_claim", "Turn failed", empty_rlm_usage()))


@pytest.mark.asyncio
async def test_revoke_claim_guard_only_relaxes_committed_runs(caplog) -> None:
    """The guard funnel returns None for committed Runs; live claims still revoke durably."""
    import logging

    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        CommittedTurnReceipt,
        RunClaim,
        RunLifecycleService,
    )
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.dspy_contract import PredictionResult, empty_rlm_usage
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="guard funnel",
    )
    lifecycle = RunLifecycleService(authoritative, max_artifact_bytes=100)

    class Preparation:
        async def prepare(self, _turn, *, deadline):
            del deadline
            raise AssertionError("preparation must not start")

    class Runner:
        def stream(self, _execution):
            raise AssertionError("execution must not start")

    coordinator = TurnCoordinator(lifecycle=lifecycle, preparation=Preparation(), runner=Runner())

    committed = await lifecycle.begin(RunClaim(access, session.id, TurnInput("first"), "first", uuid4()))
    assert isinstance(committed, ClaimedRun)
    receipt = await lifecycle.finish(
        committed,
        RLMOutcome("completed", PredictionResult("done", {"answer": "done"}, "fleet.default", "1")),
    )
    assert isinstance(receipt, CommittedTurnReceipt)

    with caplog.at_level(logging.INFO):
        guarded = await coordinator._revoke_claim(committed, empty_rlm_usage())
    assert guarded is None
    assert "stale-claim revocation skipped for committed Run" in caplog.text
    assert (authoritative._runs[committed.run_id].status) == "completed"

    live = await lifecycle.begin(RunClaim(access, session.id, TurnInput("second"), "second", uuid4()))
    assert isinstance(live, ClaimedRun)
    revoked = await coordinator._revoke_claim(live, empty_rlm_usage())
    assert revoked is not None
    assert (revoked.terminal_status, revoked.failure_code, revoked.durable) == ("failed", "stale_claim", False)
    run = authoritative._runs[live.run_id]
    assert (run.status, run.failure_code) == ("settling", "stale_claim")
    released = await lifecycle.complete_settling(live)
    assert (released.terminal_status, released.durable) == ("failed", True)


@pytest.mark.asyncio
async def test_driver_claim_loss_cleanup_skips_settlement_release_after_commit(caplog) -> None:
    """Driver cleanup with claim_lost still closes resources but never settles a committed Run."""
    import logging

    from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_execution import RunExecutionDriver
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        CommittedTurnReceipt,
        RunAlreadyCompletedError,
        RunClaim,
        RunFailure,
        RunLifecycleService,
    )
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.dspy_contract import PredictionResult, empty_rlm_usage
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryRunStateStore()
    access = TurnAccess(uuid4(), uuid4())
    session = await InMemorySessionCatalog(authoritative).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="driver cleanup loses to commit",
    )
    lifecycle = RunLifecycleService(authoritative, max_artifact_bytes=100)
    start = await lifecycle.begin(RunClaim(access, session.id, TurnInput("hello"), "begin", uuid4()))
    assert isinstance(start, ClaimedRun)
    receipt = await lifecycle.finish(
        start,
        RLMOutcome("completed", PredictionResult("done", {"answer": "done"}, "fleet.default", "1")),
    )
    assert isinstance(receipt, CommittedTurnReceipt)

    fenced: list[object] = []

    async def fence(requested_session_id):
        fenced.append(requested_session_id)

    async def revoke_late(turn, usage):
        del usage
        failure = RunFailure("failed", "stale_claim", "Turn failed", empty_rlm_usage())
        try:
            return await lifecycle.revoke_claim(turn, failure)
        except RunAlreadyCompletedError:
            return None

    closed: list[str] = []

    class Prepared:
        async def aclose(self):
            closed.append("prepared")

    async def finalize() -> CommittedTurnReceipt:
        return receipt

    cleanup = RunCleanupSupervisor()
    driver = RunExecutionDriver(
        lifecycle=lifecycle,
        runner=None,  # type: ignore[arg-type] - cleanup never touches the runner
        projector=CommittedTurnEventProjector(),
        cleanup=cleanup,
        claim_loss_fence=fence,
        turn_timeout_seconds=60,
        revoke_claim=revoke_late,
    )
    with caplog.at_level(logging.INFO):
        driver._submit_cleanup(
            start,
            None,
            Prepared(),
            None,
            asyncio.ensure_future(finalize()),
            claim_lost=True,
            claim_loss_usage=empty_rlm_usage(),
        )
        await cleanup.shutdown(drain_seconds=1)

    run = authoritative._runs[start.run_id]
    assert (run.status, run.failure_code) == ("completed", None)
    assert fenced == []
    assert closed == ["prepared"]
    assert "detached Turn cleanup failed" not in caplog.text
