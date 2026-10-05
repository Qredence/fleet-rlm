"""Turn coordinator cancellation during commit."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import pytest

from tests.support.turn_settlement import TestingRunSettlement


@pytest.mark.asyncio
async def test_turn_capture_finalizes_as_client_disconnect_on_stream_close(tmp_path: Path) -> None:
    """Closing a suspended Turn stream after three events ends the capture as a disconnect."""
    from fleet_rlm.observability.turn_capture import TurnCaptureStore
    from fleet_rlm.rlm.events import EventRecorder, Status
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        FailedRunReceipt,
        _RunClaimToken,
    )
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("capture"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    class Store:
        async def begin(self, request):
            del request
            return turn

        async def transition_claim(self, claimed, command):
            from fleet_rlm.rlm.result import empty_rlm_usage
            from fleet_rlm.sessions.run_claim import FailClaim
            from fleet_rlm.sessions.run_state import RunFailure

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            return FailedRunReceipt(
                claimed.run_id,
                failure.terminal_status,
                failure.failure_code,
                failure.public_message,
                True,
            )

        async def heartbeat(self, claimed):
            del claimed

    class Prepared:
        execution = object()
        artifact_sink = None
        result_snapshot_sink = None
        post_commit_memory_promotion = None

        async def aclose(self):
            return None

    class Preparation:
        async def prepare(self, claimed, *, deadline):
            del claimed, deadline
            return Prepared()

    recorder = EventRecorder(run_id, session_id)

    class Stream:
        outcome = None

        def __init__(self) -> None:
            self.emitted = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.emitted >= 3:
                # Suspend mid-Turn so the client close is the only stop site.
                await asyncio.Event().wait()
            self.emitted += 1
            return recorder.record(Status("execution", "running", f"step {self.emitted}"))

        async def aclose(self):
            return None

    class Runner:
        def stream(self, execution):
            del execution
            return Stream()

    capture_store = TurnCaptureStore(root=tmp_path, enabled=True, retention_days=14, max_captures=10)
    coordinator = TurnRuntime(
        lifecycle=TestingRunSettlement(Store(), max_artifact_bytes=1024),
        preparation=Preparation(),
        runner=Runner(),
        event_capture=capture_store,
    )

    owner = await coordinator.open(OpenTurnCommand(access, session_id, TurnInput("capture"), "key", run_id))
    seen = []
    async for event in owner:
        seen.append(event)
        if len(seen) == 3:
            break
    await owner.aclose()
    capture_store.aclose()

    assert len(seen) == 3
    path = capture_store.captures_root / str(session_id) / f"{run_id}.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["record"] == "capture_opened"
    assert len(lines) == 5
    assert [line["kind"] for line in lines[1:-1]] == ["status", "status", "status"]
    assert [line["sequence"] for line in lines[1:-1]] == [1, 2, 3]
    assert lines[-1]["record"] == "capture_closed"
    assert lines[-1]["stop_reason"] == "client_disconnect"
    assert lines[-1]["complete"] is False
    assert lines[-1]["truncated"] is False
    assert lines[-1]["event_count"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_succeeds", [False])
async def test_coordinator_settles_commit_after_cancellation(commit_succeeds: bool) -> None:

    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        CommittedTurnReceipt,
        FailedRunReceipt,
        _RunClaimToken,
    )
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime

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
            from fleet_rlm.rlm.result import empty_rlm_usage
            from fleet_rlm.sessions.run_claim import FailClaim
            from fleet_rlm.sessions.run_state import RunFailure

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
    coordinator = TurnRuntime(
        lifecycle=TestingRunSettlement(store, max_artifact_bytes=1024),
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
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        FailedRunReceipt,
        _RunClaimToken,
    )
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime

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

    coordinator = TurnRuntime(
        lifecycle=TestingRunSettlement(Store(), max_artifact_bytes=1024),
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
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        FailedRunReceipt,
        _RunClaimToken,
    )
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime

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

    monkeypatch.setattr("fleet_rlm.turns._PREPARATION_CLEANUP_TIMEOUT_S", 0.01)
    store = Store()
    coordinator = TurnRuntime(
        lifecycle=TestingRunSettlement(store, max_artifact_bytes=1024),
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

    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        FailedRunReceipt,
        _RunClaimToken,
    )
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime

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

    monkeypatch.setattr("fleet_rlm.turns._PREPARATION_CLEANUP_TIMEOUT_S", 0.01)
    store = Store()
    coordinator = TurnRuntime(
        lifecycle=TestingRunSettlement(store, max_artifact_bytes=1024),
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
    # BeginSettlement runs, but a failed late PreparedTurn cleanup must not be
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

    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        FailedRunReceipt,
        RunLifecycleUnavailableError,
        _RunClaimToken,
    )
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime

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
            from fleet_rlm.sessions.run_claim import HeartbeatClaim

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

    monkeypatch.setattr("fleet_rlm.turns._PREPARATION_CLEANUP_TIMEOUT_S", 1.0)
    store = Store()
    coordinator = TurnRuntime(
        lifecycle=TestingRunSettlement(
            store, max_artifact_bytes=1024, heartbeat_seconds=0.01, stale_after_seconds=0.01
        ),
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

    from fleet_rlm.sessions.run_claim import CompleteSettlement, RevokeClaim

    assert closed.is_set()
    # Claim loss revokes authority, but the failed inline PreparedTurn close
    # must block the final settlement release instead of silently completing.
    assert any(isinstance(command, RevokeClaim) for command in store.commands)
    assert not any(isinstance(command, CompleteSettlement) for command in store.commands)
    assert "late Turn preparation cleanup failed" in caplog.text


@pytest.mark.asyncio
async def test_cancellation_during_artifact_write_waits_then_removes_written_path() -> None:
    from hashlib import sha256

    from fleet_rlm.workspace.artifacts import ArtifactCandidate
    from tests.support.turn_lifecycle import claimed_run, completed_outcome

    turn = claimed_run()
    data = b"artifact"
    candidate = ArtifactCandidate(
        uuid4(),
        turn.access.user_id,
        turn.access.workspace_id,
        turn.session_id,
        turn.run_id,
        "text",
        None,
        "text/plain",
        len(data),
        sha256(data).hexdigest(),
        "/staging/a",
        "/artifacts/a",
    )
    write_started, release_write = asyncio.Event(), asyncio.Event()

    class Store:
        async def commit(self, *args):
            raise AssertionError(args)

        async def transition_claim(self, *args):
            raise AssertionError(args)

    class Sink:
        values: ClassVar[dict[object, object]] = {candidate.staging_path: data}

        async def read(self, location, *, max_bytes):
            del max_bytes
            return self.values[location]

        async def write(self, location, value):
            write_started.set()
            await release_write.wait()
            self.values[location] = value

        async def remove(self, location):
            self.values.pop(location, None)

    sink = Sink()
    task = asyncio.create_task(
        TestingRunSettlement(Store(), max_artifact_bytes=1024).finish(
            turn,
            completed_outcome(
                usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2}, candidates=(candidate,)
            ),
            artifact_sink=sink,
        )
    )
    await write_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_write.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert candidate.durable_path not in sink.values


@pytest.mark.asyncio
async def test_cancellation_during_snapshot_write_waits_then_removes_snapshot() -> None:
    from tests.support.turn_lifecycle import claimed_run, completed_outcome

    turn = claimed_run()
    write_started, release_write = asyncio.Event(), asyncio.Event()

    class Store:
        async def commit(self, *args):
            raise AssertionError(args)

        async def transition_claim(self, *args):
            raise AssertionError(args)

    class Snapshot:
        path = f"/sessions/{turn.session_id}/runs/{turn.run_id}/result.json"
        values: ClassVar[dict[str, bytes]] = {}

        def result_path(self, session_id, run_id):
            del session_id, run_id
            return self.path

        async def write(self, location, value):
            write_started.set()
            await release_write.wait()
            self.values[location] = value

        async def remove(self, location):
            self.values.pop(location, None)

    snapshot = Snapshot()
    task = asyncio.create_task(
        TestingRunSettlement(Store(), max_artifact_bytes=1024).finish(
            turn,
            completed_outcome(
                usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2},
            ),
            result_snapshot_sink=snapshot,
        )
    )
    await write_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_write.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert snapshot.values == {}


@pytest.mark.asyncio
async def test_cancelled_commit_failure_settles_repeatedly_cancelled_rollback() -> None:
    from tests.support.turn_lifecycle import claimed_run, completed_outcome

    turn = claimed_run()
    commit_started, release_commit = asyncio.Event(), asyncio.Event()
    remove_started, release_remove = asyncio.Event(), asyncio.Event()

    class Store:
        async def commit(self, claimed, committed, artifacts):
            del claimed, committed, artifacts
            commit_started.set()
            await release_commit.wait()
            raise RuntimeError("commit failed")

        async def transition_claim(self, *args):
            raise AssertionError(args)

    class Snapshot:
        path = f"/sessions/{turn.session_id}/runs/{turn.run_id}/result.json"
        values: ClassVar[dict[str, bytes]] = {}

        def result_path(self, session_id, run_id):
            del session_id, run_id
            return self.path

        async def write(self, location, value):
            self.values[location] = value

        async def remove(self, location):
            remove_started.set()
            await release_remove.wait()
            self.values.pop(location, None)

    snapshot = Snapshot()
    task = asyncio.create_task(
        TestingRunSettlement(Store(), max_artifact_bytes=1024).finish(
            turn,
            completed_outcome(
                usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2},
            ),
            result_snapshot_sink=snapshot,
        )
    )
    await commit_started.wait()
    task.cancel()
    release_commit.set()
    await remove_started.wait()
    task.cancel()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_remove.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert snapshot.values == {}


@pytest.mark.asyncio
async def test_cancelled_commit_that_succeeds_retains_snapshot_and_receipt() -> None:
    from fleet_rlm.sessions.run_state import CommittedTurnReceipt
    from tests.support.turn_lifecycle import claimed_run, completed_outcome

    turn = claimed_run()
    commit_started, release_commit = asyncio.Event(), asyncio.Event()

    class Store:
        failures = 0

        async def commit(self, claimed, committed, artifacts):
            del artifacts
            commit_started.set()
            await release_commit.wait()
            return CommittedTurnReceipt(claimed.run_id, 1, committed, ())

        async def transition_claim(self, *args):
            self.failures += 1
            raise AssertionError(args)

    class Snapshot:
        path = f"/sessions/{turn.session_id}/runs/{turn.run_id}/result.json"
        values: ClassVar[dict[str, bytes]] = {}

        def result_path(self, session_id, run_id):
            del session_id, run_id
            return self.path

        async def write(self, location, value):
            self.values[location] = value

        async def remove(self, location):
            self.values.pop(location, None)

    store, snapshot = Store(), Snapshot()
    task = asyncio.create_task(
        TestingRunSettlement(store, max_artifact_bytes=1024).finish(
            turn,
            completed_outcome(
                usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2},
            ),
            result_snapshot_sink=snapshot,
        )
    )
    await commit_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_commit.set()

    receipt = await task
    assert isinstance(receipt, CommittedTurnReceipt)
    assert snapshot.values.keys() == {snapshot.path}
    assert store.failures == 0


@pytest.mark.asyncio
async def test_cancelled_settlement_persists_bounded_tombstone_in_turn_listing() -> None:
    from fleet_rlm.rlm.result import empty_rlm_usage
    from fleet_rlm.sessions.history import dspy_history_for_claim
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        RunClaim,
        RunFailure,
    )
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="cancelled attempt",
    )
    lifecycle = TestingRunSettlement(store, max_artifact_bytes=1024)

    turn = await lifecycle.begin(RunClaim(access, session.id, TurnInput("draft the report"), "key-cancel", uuid4()))
    settle = await lifecycle.settle(turn, RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()))
    assert (settle.terminal_status, settle.durable) == ("cancelled", False)
    assert await store.turn_records(session.id, access) == ()

    final = await lifecycle.complete_settling(turn)
    assert (final.terminal_status, final.durable) == ("cancelled", True)

    records = await store.turn_records(session.id, access)
    assert [type(record).__name__ for record in records] == ["UserTurnRecord", "AssistantTurnRecord"]
    user, assistant = records
    assert user.input.text == "draft the report"
    assert user.sequence + 1 == assistant.sequence
    status, usage, text = assistant.committed.parts
    assert (status.type, status.phase, status.status, status.message) == ("status", "cancelled", "cancelled", None)
    assert dict(usage.value) == dict(empty_rlm_usage())
    assert text.text == "Turn cancelled"

    retried = await lifecycle.begin(RunClaim(access, session.id, TurnInput("draft the report"), "key-cancel", uuid4()))
    assert isinstance(retried, ClaimedRun)
    assert retried.run_id != turn.run_id

    assert [(message.role, message.content) for message in retried.history.messages] == [
        ("user", "draft the report"),
        ("assistant", "Turn cancelled"),
    ]

    assert list(dspy_history_for_claim(retried).messages) == []


@pytest.mark.asyncio
async def test_preparation_failclaim_cancelled_persists_tombstone_with_observed_usage() -> None:
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        RunClaim,
        RunFailure,
    )
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="preparation cancel",
    )
    lifecycle = TestingRunSettlement(store, max_artifact_bytes=1024)

    turn = await lifecycle.begin(RunClaim(access, session.id, TurnInput("gather two facts"), "key-prep", uuid4()))
    usage = {"iterations": 3, "observed_lm_usage": {"root": {"total_tokens": 12}}, "duration_ms": 7}
    receipt = await lifecycle.finish(turn, RunFailure("cancelled", "cancelled", "Turn cancelled", usage))

    assert (receipt.terminal_status, receipt.durable) == ("cancelled", True)
    records = await store.turn_records(session.id, access)
    assert len(records) == 2
    assistant = records[-1]
    assert dict(assistant.committed.parts[1].value) == usage
    assert assistant.committed.text == "Turn cancelled"


@pytest.mark.asyncio
async def test_tombstone_sequences_interleave_with_committed_turns() -> None:
    from fleet_rlm.rlm.result import (
        PredictionResult,
        RLMOutcome,
        empty_rlm_usage,
    )
    from fleet_rlm.sessions.committed_turn import CommittedTurnCodec
    from fleet_rlm.sessions.models import AssistantTurnRecord, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        CommittedTurnReceipt,
        RunClaim,
        RunFailure,
    )
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="interleaved",
    )
    lifecycle = TestingRunSettlement(store, max_artifact_bytes=1024)

    first = await lifecycle.begin(RunClaim(access, session.id, TurnInput("one"), "key-1", uuid4()))
    committed = await lifecycle.finish(
        first,
        RLMOutcome(
            "completed",
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            usage=empty_rlm_usage(),
        ),
    )
    assert isinstance(committed, CommittedTurnReceipt)

    second = await lifecycle.begin(RunClaim(access, session.id, TurnInput("two"), "key-2", uuid4()))
    await lifecycle.settle(second, RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()))
    await lifecycle.complete_settling(second)

    records = await store.turn_records(session.id, access)
    assert [record.sequence for record in records] == [1, 2, 3, 4]
    assert [type(record).__name__ for record in records] == [
        "UserTurnRecord",
        "AssistantTurnRecord",
        "UserTurnRecord",
        "AssistantTurnRecord",
    ]
    assert records[1].committed.text == "done"
    assert records[3].committed.text == "Turn cancelled"
    for record in records:
        if isinstance(record, AssistantTurnRecord):
            assert CommittedTurnCodec.decode(CommittedTurnCodec.encode(record.committed)) == record.committed
