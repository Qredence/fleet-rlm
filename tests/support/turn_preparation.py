"""Test adapter for exercising the functional Turn preparation boundary."""

from __future__ import annotations

from typing import Any

from fleet_rlm.sessions.run_state import ClaimedRun
from fleet_rlm.turn_preparation import PreparedTurn, TurnPreparationPlan, prepare_turn


class TestingRunPreparer:
    __test__ = False

    def __init__(self, **kwargs: Any) -> None:
        self.plan = TurnPreparationPlan(**kwargs)

    async def prepare(self, run: ClaimedRun, *, deadline: float) -> PreparedTurn:
        return await prepare_turn(self.plan, run, deadline=deadline)
