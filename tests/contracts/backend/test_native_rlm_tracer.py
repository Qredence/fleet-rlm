"""Contract for native RLM execution with product-owned boundary tracing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.daytona.in_process import InProcessInterpreterBackend
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.events import RLMCode, RLMOutput, StepFinished, StepStarted, ToolCompleted, ToolStarted
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool
from fleet_rlm.sessions.models import TurnAccess
from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint


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


class _InvalidThenValidSubmit:
    def __init__(self) -> None:
        self.calls = 0

    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        self.calls += 1
        code = "SUBMIT(wrong='invalid')" if self.calls == 1 else "SUBMIT(answer='repaired')"
        return dspy.Prediction(reasoning="repair invalid typed submit", code=code)


class _NeverSubmit:
    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        return dspy.Prediction(reasoning="inspect", code="value = 42")


class _TypedExtract:
    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        return dspy.Prediction(answer="extracted")


@pytest.mark.asyncio
async def test_native_rlm_preserves_state_tools_submit_prediction_and_trajectory() -> None:
    observed: list[object] = []

    def helper(value: str) -> str:
        return f"done:{value}"

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_observer(observed.append, max_chars=1_000)
    rlm = RLMFactory().create(
        models=RLMModelBundle(root_lm=object(), sub_lm=object()),  # type: ignore[arg-type]
        options=RLMOptions(max_iterations=2),
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


@pytest.mark.asyncio
async def test_native_rlm_repairs_invalid_submit_and_typed_extract_fallback() -> None:
    models = RLMModelBundle(root_lm=object(), sub_lm=object())  # type: ignore[arg-type]
    repaired = RLMFactory().create(
        models=models,
        options=RLMOptions(max_iterations=2),
        interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        signature="request -> answer: str",
    )
    repaired.generate_action = _InvalidThenValidSubmit()
    repaired_prediction = await repaired.acall(request="repair")

    extracted = RLMFactory().create(
        models=models,
        options=RLMOptions(max_iterations=1),
        interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        signature="request -> answer: str",
    )
    extracted.generate_action = _NeverSubmit()
    extracted.extract = _TypedExtract()
    extracted_prediction = await extracted.acall(request="extract")

    assert repaired_prediction.answer == "repaired"
    assert len(repaired_prediction.trajectory) == 2
    assert extracted_prediction.answer == "extracted"
    assert extracted_prediction.final_reasoning == "Extract forced final output"


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True], ids=["invalid-submit-repair", "typed-extract"])
async def test_runner_completes_native_repair_and_extract_as_prediction_result(fallback: bool) -> None:
    from fleet_rlm.rlm.context import RLMExecutionContext

    class Capabilities:
        blueprint = TurnCapabilityBlueprint()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

    class NativeFactory:
        def create(self, **kwargs):
            rlm = RLMFactory().create(**kwargs)
            if fallback:
                rlm.generate_action = _NeverSubmit()
                rlm.extract = _TypedExtract()
            else:
                rlm.generate_action = _InvalidThenValidSubmit()
            return rlm

    async def not_cancelled() -> bool:
        return False

    options = RLMOptions(max_iterations=1 if fallback else 2)
    context = RLMExecutionContext(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "complete natively",
        (),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        options,
        asyncio.get_running_loop().time() + 10,
        DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        (),
        Capabilities(),
        not_cancelled,
        (),
    )
    stream = RLMRunner(factory=NativeFactory()).stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None
    assert stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == ("extracted" if fallback else "repaired")
    assert stream.outcome.prediction.outputs == {"answer": "extracted" if fallback else "repaired"}
