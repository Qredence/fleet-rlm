"""Prepared-context runner contract."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest


def test_trajectory_projection_is_optional_and_fail_soft() -> None:
    from fleet_rlm.rlm.events import RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.runner import _trajectory_details

    assert _trajectory_details(SimpleNamespace(), max_chars=100) == []
    assert _trajectory_details(SimpleNamespace(trajectory="malformed"), max_chars=100) == []
    assert [
        type(item)
        for item in _trajectory_details(
            SimpleNamespace(trajectory=[None, {"reasoning": "usable"}]),
            max_chars=100,
        )
    ] == [StepStarted, RLMReasoning, StepFinished]


@pytest.mark.asyncio
async def test_runner_uses_supported_async_call_and_returns_typed_outcome() -> None:
    from fleet_rlm.rlm.budgets import RunBudget, RunBudgetLedger
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, StepFinished, StepStarted
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
        budget = None
        tools = None

        def create(self, **kwargs):
            assert "observer" not in kwargs
            self.budget = kwargs["budget"]
            self.tools = kwargs["tools"]
            factory = self

            class Program:
                async def acall(self, **call_kwargs):
                    assert call_kwargs["request"] == "answer"
                    assert threading.get_ident() != main_thread
                    interpreter.observer(StepStarted(1))
                    interpreter.observer(RLMCode("answer = helper(value='sample')", 1))
                    assert factory.tools[0](value="sample") == "done:sample"
                    interpreter.observer(RLMOutput("FINAL submitted", 1))
                    interpreter.observer(StepFinished(1, 1))
                    return SimpleNamespace(
                        answer="42",
                        trajectory=[
                            {
                                "reasoning": "Use the registered helper.",
                                "code": "answer = helper(value='sample')",
                                "output": "FINAL: {'answer': '42'}",
                            }
                        ],
                    )

            return Program()

    class Interpreter:
        observer = None

        def bind_observer(self, observer, *, max_chars):
            assert max_chars == RunBudget().max_output_chars
            self.observer = observer

    def helper(value: str) -> str:
        return f"done:{value}"

    async def not_cancelled():
        return False

    factory = Factory()
    interpreter = Interpreter()
    main_thread = threading.get_ident()
    context = RLMExecutionContext(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "answer",
        (),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RunBudgetLedger(RunBudget()),
        asyncio.get_running_loop().time() + 10,
        interpreter,
        (),
        Capabilities(),
        not_cancelled,
        (),
    )
    stream = RLMRunner(factory=factory).stream(context)
    Capabilities.blueprint = TurnCapabilityBlueprint(tools=(helper,))
    events = [event async for event in stream]

    assert [event.kind for event in events] == [
        "run.started",
        "status",
        "step.started",
        "rlm.code",
        "tool.started",
        "tool.completed",
        "rlm.output",
        "step.finished",
        "rlm.reasoning",
    ]
    assert stream.outcome is not None
    assert stream.outcome.text == "42"
    assert stream.outcome.succeeded
    assert factory.budget is context.budget.budget
    assert isinstance(factory.tools[0], dspy.Tool)
