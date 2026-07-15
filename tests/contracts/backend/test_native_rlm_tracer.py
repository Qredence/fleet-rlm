"""Contract for native RLM execution with product-owned boundary tracing."""

from __future__ import annotations

from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.in_process import InProcessInterpreterBackend
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
from fleet_rlm.rlm.budgets import RunBudget
from fleet_rlm.rlm.events import RLMCode, RLMOutput, StepFinished, StepStarted, ToolCompleted, ToolStarted
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool


class _StatefulActionPredictor:
    def __init__(self) -> None:
        self.calls = 0

    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        self.calls += 1
        if self.calls == 1:
            return dspy.Prediction(
                reasoning="Call the registered helper and retain the result.",
                code="value = helper(value='a')\n_out = value",
            )
        return dspy.Prediction(
            reasoning="Submit the retained result.",
            code="SUBMIT(answer=value)",
        )


@pytest.mark.asyncio
async def test_native_rlm_preserves_state_tools_submit_prediction_and_trajectory() -> None:
    observed: list[object] = []

    def helper(value: str) -> str:
        return f"done:{value}"

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_observer(observed.append, max_chars=1_000)
    rlm = RLMFactory().create(
        models=RLMModelBundle(root_lm=object(), sub_lm=object()),  # type: ignore[arg-type]
        budget=RunBudget(max_iterations=2),
        interpreter=interpreter,
        tools=(observe_tool(helper, observed.append, ToolEventView(max_chars=1_000)),),
        signature="request -> answer",
    )
    rlm.generate_action = _StatefulActionPredictor()

    prediction = await rlm.acall(request="run the deterministic contract")

    assert type(rlm) is dspy.RLM
    assert isinstance(prediction, dspy.Prediction)
    assert prediction.answer == "done:a"
    assert len(prediction.trajectory) == 2
    assert prediction.trajectory[0]["output"] == "done:a"
    assert prediction.trajectory[1]["output"].startswith("FINAL:")
    assert [type(item) for item in observed] == [
        StepStarted,
        RLMCode,
        ToolStarted,
        ToolCompleted,
        RLMOutput,
        StepFinished,
        StepStarted,
        RLMCode,
        RLMOutput,
        StepFinished,
    ]
