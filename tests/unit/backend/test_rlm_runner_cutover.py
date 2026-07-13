"""Prepared-context runner contract."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_runner_uses_supported_async_call_and_returns_typed_outcome() -> None:
    from fleet_rlm.rlm.budgets import RunBudget, RunBudgetLedger
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.observable import RLMDetail, RLMDetailKind
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint

    class Capabilities:
        blueprint = TurnCapabilityBlueprint()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        async def aclose(self):
            return None

    class Factory:
        observer = None
        budget = None

        def create(self, **kwargs):
            self.observer = kwargs["observer"]
            self.budget = kwargs["budget"]
            factory = self

            class Program:
                tool_calls_used = 0
                sub_lm_calls_used = 0

                async def acall(self, **call_kwargs):
                    assert call_kwargs["request"] == "answer"
                    factory.observer(RLMDetail(RLMDetailKind.STEP_STARTED, {"step": 1}))
                    factory.observer(RLMDetail(RLMDetailKind.STEP_FINISHED, {"step": 1}))
                    return SimpleNamespace(answer="42")

            return Program()

    async def not_cancelled():
        return False

    factory = Factory()
    context = RLMExecutionContext(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "answer",
        (),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RunBudgetLedger(RunBudget()),
        asyncio.get_running_loop().time() + 10,
        object(),
        (),
        Capabilities(),
        not_cancelled,
        (),
    )
    stream = RLMRunner(factory=factory).stream(context)
    events = [event async for event in stream]

    assert [event.kind for event in events] == [
        "run.started",
        "status",
        "step.started",
        "step.finished",
    ]
    assert stream.outcome is not None
    assert stream.outcome.text == "42"
    assert stream.outcome.succeeded
    assert factory.budget is context.budget.budget
