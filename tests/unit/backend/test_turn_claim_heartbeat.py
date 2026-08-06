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
    from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService, TurnLifecycleUnavailableError, TurnStateError
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryTurnStateStore()
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
            from fleet_rlm.chat.turn_claim import HeartbeatClaim

            if isinstance(command, HeartbeatClaim):
                raise TurnStateError("Turn claim is invalid")
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

    cleanup = TurnCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=TurnLifecycleService(
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

    with pytest.raises(TurnLifecycleUnavailableError):
        await coordinator.open(OpenTurnCommand(access, session.id, TurnInput("hello"), "preparation", uuid4()))
    await cleanup.shutdown(drain_seconds=1)
    assert fenced.is_set()


@pytest.mark.asyncio
async def test_transient_heartbeat_failure_recovers_without_ending_run() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunStarted
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryTurnStateStore()
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
            from fleet_rlm.chat.turn_claim import HeartbeatClaim

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

    cleanup = TurnCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=TurnLifecycleService(
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
    from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.events import EventRecorder, RunFailed, RunStarted
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryTurnStateStore()
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
            from fleet_rlm.chat.turn_claim import HeartbeatClaim

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

    cleanup = TurnCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=TurnLifecycleService(
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
    from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService, TurnStateError
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.events import RunFailed
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryTurnStateStore()
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
            from fleet_rlm.chat.turn_claim import HeartbeatClaim

            if isinstance(command, HeartbeatClaim):
                raise TurnStateError("Turn claim is invalid")
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

    cleanup = TurnCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=TurnLifecycleService(
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
    from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService, TurnStateError
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.events import EventRecorder, RunFailed, RunStarted
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    authoritative = InMemoryTurnStateStore()
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
            from fleet_rlm.chat.turn_claim import HeartbeatClaim

            if not isinstance(command, HeartbeatClaim):
                return await authoritative.transition_claim(turn, command)
            heartbeat_attempted.set()
            raise TurnStateError("Turn claim is invalid")

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

    cleanup = TurnCleanupSupervisor()
    coordinator = TurnCoordinator(
        lifecycle=TurnLifecycleService(
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
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn

    replacement = await authoritative.begin(BeginTurn(access, session.id, TurnInput("next"), "next", uuid4()))
    assert isinstance(replacement, ExecuteTurn)
