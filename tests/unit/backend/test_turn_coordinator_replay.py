"""Turn coordinator durable replay behavior."""

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
    replay = ReplayTurn(
        run_id,
        session_id,
        CommittedTurn(
            1,
            (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("hi")),
        ),
        3,
    )

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
