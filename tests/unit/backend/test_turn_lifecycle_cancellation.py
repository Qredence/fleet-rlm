"""Cancellation ownership for commit-gated lifecycle side effects."""

from __future__ import annotations

import asyncio
from hashlib import sha256
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
        values = {candidate.staging_path: data}

        async def read(self, location, *, max_bytes):
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
        values: dict[str, bytes] = {}

        def result_path(self, session_id, run_id):
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
            commit_started.set()
            await release_commit.wait()
            raise RuntimeError("commit failed")

        async def transition_claim(self, *args):
            raise AssertionError(args)

    class Snapshot:
        path = f"/sessions/{turn.session_id}/runs/{turn.run_id}/result.json"
        values: dict[str, bytes] = {}

        def result_path(self, session_id, run_id):
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
            commit_started.set()
            await release_commit.wait()
            return CommittedTurnReceipt(claimed.run_id, 1, committed, ())

        async def transition_claim(self, *args):
            self.failures += 1
            raise AssertionError(args)

    class Snapshot:
        path = f"/sessions/{turn.session_id}/runs/{turn.run_id}/result.json"
        values: dict[str, bytes] = {}

        def result_path(self, session_id, run_id):
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
