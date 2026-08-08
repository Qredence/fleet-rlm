"""Cancellation ownership for commit-gated lifecycle side effects."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import ClassVar
from uuid import uuid4

import pytest


def _turn():
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    async def not_cancelled() -> bool:
        return False

    return ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )


def _outcome(*, candidates=()):
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome

    return RLMOutcome(
        "completed",
        PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
        usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2},
        artifact_candidates=candidates,
    )


@pytest.mark.asyncio
async def test_cancellation_during_artifact_write_waits_then_removes_written_path() -> None:
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService

    turn = _turn()
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
        TurnLifecycleService(Store(), max_artifact_bytes=1024).finish(
            turn,
            _outcome(candidates=(candidate,)),
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
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService

    turn = _turn()
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
        TurnLifecycleService(Store(), max_artifact_bytes=1024).finish(
            turn,
            _outcome(),
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
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService

    turn = _turn()
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
        TurnLifecycleService(Store(), max_artifact_bytes=1024).finish(
            turn,
            _outcome(),
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
    from fleet_rlm.chat.turn_lifecycle import CommittedTurnReceipt, TurnLifecycleService

    turn = _turn()
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
        TurnLifecycleService(store, max_artifact_bytes=1024).finish(
            turn,
            _outcome(),
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
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn, TurnFailure, TurnLifecycleService
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="cancelled attempt",
    )
    lifecycle = TurnLifecycleService(store, max_artifact_bytes=1024)

    turn = await lifecycle.begin(BeginTurn(access, session.id, TurnInput("draft the report"), "key-cancel", uuid4()))
    settle = await lifecycle.settle(turn, TurnFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()))
    assert (settle.terminal_status, settle.durable) == ("cancelled", False)
    # Nothing is listed while the claim is still settling.
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

    # The cancelled attempt is bounded audit: no evidence parts, retry is fresh.
    retried = await lifecycle.begin(BeginTurn(access, session.id, TurnInput("draft the report"), "key-cancel", uuid4()))
    assert isinstance(retried, ExecuteTurn)
    assert retried.run_id != turn.run_id

    # Session History records the closed attempt pair, never evidence.
    assert [(message.role, message.content) for message in retried.history.messages] == [
        ("user", "draft the report"),
        ("assistant", "Turn cancelled"),
    ]


@pytest.mark.asyncio
async def test_preparation_failclaim_cancelled_persists_tombstone_with_observed_usage() -> None:
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, TurnFailure, TurnLifecycleService
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="preparation cancel",
    )
    lifecycle = TurnLifecycleService(store, max_artifact_bytes=1024)

    turn = await lifecycle.begin(BeginTurn(access, session.id, TurnInput("gather two facts"), "key-prep", uuid4()))
    usage = {"iterations": 3, "observed_lm_usage": {"root": {"total_tokens": 12}}, "duration_ms": 7}
    receipt = await lifecycle.finish(turn, TurnFailure("cancelled", "cancelled", "Turn cancelled", usage))

    assert (receipt.terminal_status, receipt.durable) == ("cancelled", True)
    records = await store.turn_records(session.id, access)
    assert len(records) == 2
    assistant = records[-1]
    assert dict(assistant.committed.parts[1].value) == usage
    assert assistant.committed.text == "Turn cancelled"


@pytest.mark.asyncio
async def test_tombstone_sequences_interleave_with_committed_turns() -> None:
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, CommittedTurnReceipt, TurnFailure, TurnLifecycleService
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.rlm.dspy_contract import PredictionResult, empty_rlm_usage
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.committed_turn import CommittedTurnCodec
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryTurnStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="interleaved",
    )
    lifecycle = TurnLifecycleService(store, max_artifact_bytes=1024)

    first = await lifecycle.begin(BeginTurn(access, session.id, TurnInput("one"), "key-1", uuid4()))
    committed = await lifecycle.finish(
        first,
        RLMOutcome(
            "completed",
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            usage=empty_rlm_usage(),
        ),
    )
    assert isinstance(committed, CommittedTurnReceipt)

    second = await lifecycle.begin(BeginTurn(access, session.id, TurnInput("two"), "key-2", uuid4()))
    await lifecycle.settle(second, TurnFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()))
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
    # Tombstones survive the strict JSON codec unchanged (cursor pages decode them).
    from fleet_rlm.sessions.models import AssistantTurnRecord

    for record in records:
        if isinstance(record, AssistantTurnRecord):
            assert CommittedTurnCodec.decode(CommittedTurnCodec.encode(record.committed)) == record.committed
