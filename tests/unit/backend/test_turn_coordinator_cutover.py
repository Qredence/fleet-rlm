"""Coordinator ordering across replay and live settlement."""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_replay_bypasses_preparation_and_runner() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import ReplayTurn
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    run_id, session_id = uuid4(), uuid4()
    replay = ReplayTurn(run_id, session_id, CommittedTurn(1, (UsagePart({}), TextPart("hi"))), 3)

    class Lifecycle:
        async def begin(self, request):
            return replay

    class Never:
        def __getattr__(self, name):
            raise AssertionError(name)

    command = OpenTurnCommand(TurnAccess(uuid4(), uuid4()), session_id, TurnInput("hi"), "key", run_id)
    opened = await TurnCoordinator(lifecycle=Lifecycle(), preparation=Never(), runner=Never()).open(command)
    events = [event async for event in opened]

    assert [event.kind for event in events] == [
        "run.started",
        "status",
        "usage",
        "text.delta",
        "text.completed",
        "run.completed",
    ]
    assert events[-1].detail.delivery == "replay"


@pytest.mark.asyncio
async def test_live_commit_projects_suffix_before_terminal_and_then_closes() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import CommittedTurnReceipt, ExecuteTurn, _TurnClaimToken
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled():
        return False

    turn = ExecuteTurn(
        run_id, session_id, access, TurnInput("hi"), SessionHistory(), not_cancelled, _TurnClaimToken(uuid4())
    )
    committed = CommittedTurn(1, (UsagePart({"iterations": 1}), TextPart("done")))
    operations: list[str] = []

    class Lifecycle:
        async def begin(self, request):
            return turn

        async def finish(self, claimed, resolution, *, artifact_sink=None):
            operations.append("finish")
            return CommittedTurnReceipt(run_id, 1, committed, ())

    class Prepared:
        execution = object()
        artifact_sink = object()

        async def aclose(self):
            operations.append("close")

    class Preparation:
        async def prepare(self, claimed):
            operations.append("prepare")
            return Prepared()

    class Stream:
        outcome = RLMOutcome(terminal_status="completed", text="done", usage={"iterations": 1})

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self):
            return None

    class Runner:
        def stream(self, execution):
            operations.append("run")
            return Stream()

    command = OpenTurnCommand(access, session_id, TurnInput("hi"), "key", run_id)
    opened = await TurnCoordinator(lifecycle=Lifecycle(), preparation=Preparation(), runner=Runner()).open(command)
    events = [event async for event in opened]

    assert [event.kind for event in events] == ["usage", "text.delta", "text.completed", "run.completed"]
    assert operations == ["prepare", "run", "finish", "close"]
