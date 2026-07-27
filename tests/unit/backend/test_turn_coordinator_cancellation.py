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
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import (
        CommittedTurnReceipt,
        ExecuteTurn,
        FailedRunReceipt,
        TurnLifecycleService,
        _TurnClaimToken,
    )
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        run_id,
        session_id,
        access,
        TurnInput("hi"),
        SessionHistory(),
        not_cancelled,
        _TurnClaimToken(uuid4()),
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
            from fleet_rlm.chat.turn_claim import FailClaim
            from fleet_rlm.chat.turn_lifecycle import TurnFailure
            from fleet_rlm.rlm.dspy_contract import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = TurnFailure(
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
        lifecycle=TurnLifecycleService(store, max_artifact_bytes=1024),
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
