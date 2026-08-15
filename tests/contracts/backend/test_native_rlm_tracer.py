"""Contract for native RLM execution with product-owned boundary tracing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.context import (
    ExecutionRuntime,
    RLMExecutionSpec,
    RunIdentity,
    SessionView,
)
from fleet_rlm.rlm.dspy_contract import RLMOptions, bind_native_rlm_observer
from fleet_rlm.rlm.events import (
    RLMCode,
    RLMOutput,
    RLMReasoning,
    StepFinished,
    StepStarted,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
)
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool
from fleet_rlm.sessions.models import TurnAccess


class _ActionPredictor(dspy.Predict):
    def __init__(self) -> None:
        super().__init__("variables_info, repl_history, iteration -> reasoning, code")


class _StatefulActionPredictor(_ActionPredictor):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def aforward(self, **_kwargs: Any) -> dspy.Prediction:
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


class _InvalidThenValidSubmit(_ActionPredictor):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def aforward(self, **_kwargs: Any) -> dspy.Prediction:
        self.calls += 1
        code = "SUBMIT(wrong='invalid')" if self.calls == 1 else "SUBMIT(answer='repaired')"
        return dspy.Prediction(reasoning="repair invalid typed submit", code=code)


class _InvalidToolThenSubmit(_ActionPredictor):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def aforward(self, **_kwargs: Any) -> dspy.Prediction:
        self.calls += 1
        code = "value = helper(value=123)" if self.calls == 1 else "SUBMIT(answer='repaired')"
        return dspy.Prediction(reasoning="repair invalid host Tool input", code=code)


class _NeverSubmit(_ActionPredictor):
    async def aforward(self, **_kwargs: Any) -> dspy.Prediction:
        return dspy.Prediction(reasoning="inspect", code="value = 42")


class _TypedExtract:
    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        return dspy.Prediction(answer="extracted")


class _ThreeIterationActions(_ActionPredictor):
    def __init__(self) -> None:
        super().__init__()
        self.histories: list[object] = []
        self.calls = 0

    async def aforward(self, **kwargs: Any) -> dspy.Prediction:
        from dspy.primitives.repl_types import REPLHistory

        history = kwargs["repl_history"]
        assert type(history) is REPLHistory
        self.histories.append(history)
        self.calls += 1
        if self.calls == 1:
            assert len(history.entries) == 0
            return dspy.Prediction(
                reasoning="initialize an accumulator",
                code="values = [1, 2, 3]\n_out = 'initialized'",
            )
        if self.calls == 2:
            assert len(history.entries) == 1
            assert history.entries[0].reasoning == "initialize an accumulator"
            assert "values = [1, 2, 3]" in history.entries[0].code
            assert "initialized" in history.entries[0].output
            return dspy.Prediction(
                reasoning="reuse the existing accumulator",
                code="values.append(4)\n_out = sum(values)",
            )
        assert len(history.entries) == 2
        assert history.entries[1].reasoning == "reuse the existing accumulator"
        assert "values.append(4)" in history.entries[1].code
        assert "10" in history.entries[1].output
        return dspy.Prediction(
            reasoning="submit the verified result",
            code="SUBMIT(answer=str(sum(values)))",
        )


class _FreshTurnAction(_ActionPredictor):
    def __init__(self) -> None:
        super().__init__()
        self.history: object | None = None

    async def aforward(self, **kwargs: Any) -> dspy.Prediction:
        from dspy.primitives.repl_types import REPLHistory

        history = kwargs["repl_history"]
        assert type(history) is REPLHistory
        assert len(history.entries) == 0
        self.history = history
        return dspy.Prediction(
            reasoning="confirm a fresh interpreter",
            code="assert 'values' not in globals()\nSUBMIT(answer='fresh')",
        )


class _CapturingExtract:
    def __init__(self) -> None:
        self.history: object | None = None

    async def acall(self, **kwargs: Any) -> dspy.Prediction:
        from dspy.primitives.repl_types import REPLHistory

        history = kwargs["repl_history"]
        assert type(history) is REPLHistory
        self.history = history
        return dspy.Prediction(answer="extracted")


class _TwoIterationNoSubmit(_ActionPredictor):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def aforward(self, **_kwargs: Any) -> dspy.Prediction:
        self.calls += 1
        if self.calls == 1:
            return dspy.Prediction(reasoning="initialize", code="values = [1]\n_out = values")
        return dspy.Prediction(reasoning="extend", code="values.append(2)\n_out = values")


@pytest.mark.asyncio
async def test_native_rlm_preserves_state_tools_submit_prediction_and_trajectory() -> None:
    observed: list[object] = []

    def helper(value: str) -> str:
        return f"done:{value}"

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_observer(observed.append, max_chars=1_000)
    rlm = RLMFactory().create(
        models=RLMModelBundle(root_lm=object(), sub_lm=object()),  # type: ignore[arg-type]
        options=RLMOptions(max_iters=2),
        tools=(observe_tool(dspy.Tool(helper), observed.append, ToolEventView.metadata_only()),),
        signature="request -> answer",
    )
    rlm.generate_action = _StatefulActionPredictor()
    bind_native_rlm_observer(rlm, observed.append, max_chars=1_000)

    prediction = await rlm.acall(interpreter, request="run the deterministic contract")

    assert isinstance(rlm, dspy.RLM)
    assert rlm.verbose is True
    assert isinstance(prediction, dspy.Prediction)
    assert prediction.answer == "done:a"
    assert len(prediction.trajectory) == 2
    assert prediction.trajectory[0]["output"] == "done:a"
    assert prediction.trajectory[1]["output"].startswith("FINAL:")
    assert [type(item) for item in observed] == [
        RLMReasoning,
        StepStarted,
        RLMCode,
        ToolStarted,
        ToolCompleted,
        RLMOutput,
        StepFinished,
        RLMReasoning,
        StepStarted,
        RLMCode,
        RLMOutput,
        StepFinished,
    ]
    assert [item.text for item in observed if isinstance(item, RLMReasoning)] == [
        "Call the registered helper and retain the result.",
        "Submit the retained result.",
    ]
    interpreter.shutdown()


@pytest.mark.asyncio
async def test_native_repl_history_and_python_state_are_isolated_per_turn() -> None:
    models = RLMModelBundle(root_lm=object(), sub_lm=object())  # type: ignore[arg-type]
    first_interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    first = RLMFactory().create(
        models=models,
        options=RLMOptions(max_iters=3),
        signature="request -> answer: str",
    )
    actions = _ThreeIterationActions()
    first.generate_action = actions

    prediction = await first.acall(first_interpreter, request="complete the accumulator contract")

    assert prediction.answer == "10"
    assert prediction.final_reasoning == "submit the verified result"
    assert [entry["reasoning"] for entry in prediction.trajectory] == [
        "initialize an accumulator",
        "reuse the existing accumulator",
        "submit the verified result",
    ]
    assert [entry["code"] for entry in prediction.trajectory] == [
        "values = [1, 2, 3]\n_out = 'initialized'",
        "values.append(4)\n_out = sum(values)",
        "SUBMIT(answer=str(sum(values)))",
    ]
    assert len(actions.histories) == 3

    second_interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    second = RLMFactory().create(
        models=models,
        options=RLMOptions(max_iters=1),
        signature="request -> answer: str",
    )
    fresh_action = _FreshTurnAction()
    second.generate_action = fresh_action

    fresh_prediction = await second.acall(second_interpreter, request="confirm transient state was discarded")

    assert first is not second
    assert first_interpreter is not second_interpreter
    assert fresh_prediction.answer == "fresh"
    assert fresh_action.history is not None
    assert fresh_action.history is not actions.histories[0]
    first_interpreter.shutdown()
    second_interpreter.shutdown()


@pytest.mark.asyncio
async def test_native_extract_fallback_receives_accumulated_repl_history() -> None:
    extractor = _CapturingExtract()
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    rlm = RLMFactory().create(
        models=RLMModelBundle(root_lm=object(), sub_lm=object()),  # type: ignore[arg-type]
        options=RLMOptions(max_iters=2),
        signature="request -> answer: str",
    )
    rlm.generate_action = _TwoIterationNoSubmit()
    rlm.extract = extractor

    prediction = await rlm.acall(interpreter, request="exercise extraction")

    assert extractor.history is not None
    assert len(extractor.history.entries) == 2
    assert [entry.reasoning for entry in extractor.history.entries] == ["initialize", "extend"]
    assert prediction.answer == "extracted"
    assert prediction.final_reasoning == "Extract forced final output"
    assert [entry["reasoning"] for entry in prediction.trajectory] == ["initialize", "extend"]
    interpreter.shutdown()


@pytest.mark.asyncio
async def test_native_rlm_repairs_invalid_submit_and_typed_extract_fallback() -> None:
    models = RLMModelBundle(root_lm=object(), sub_lm=object())  # type: ignore[arg-type]
    repaired_interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    repaired = RLMFactory().create(
        models=models,
        options=RLMOptions(max_iters=2),
        signature="request -> answer: str",
    )
    repaired.generate_action = _InvalidThenValidSubmit()
    repaired_prediction = await repaired.acall(repaired_interpreter, request="repair")

    extracted_interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    extracted = RLMFactory().create(
        models=models,
        options=RLMOptions(max_iters=1),
        signature="request -> answer: str",
    )
    extracted.generate_action = _NeverSubmit()
    extracted.extract = _TypedExtract()
    extracted_prediction = await extracted.acall(extracted_interpreter, request="extract")

    assert repaired_prediction.answer == "repaired"
    assert len(repaired_prediction.trajectory) == 2
    assert extracted_prediction.answer == "extracted"
    assert extracted_prediction.final_reasoning == "Extract forced final output"
    repaired_interpreter.shutdown()
    extracted_interpreter.shutdown()


@pytest.mark.asyncio
async def test_native_rlm_rejects_invalid_host_tool_type_before_host_logic() -> None:
    observed: list[object] = []
    host_calls = 0

    def helper(value: str) -> str:
        nonlocal host_calls
        host_calls += 1
        return value

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_observer(observed.append, max_chars=1_000)
    rlm = RLMFactory().create(
        models=RLMModelBundle(root_lm=object(), sub_lm=object()),  # type: ignore[arg-type]
        options=RLMOptions(max_iters=2),
        tools=(observe_tool(dspy.Tool(helper), observed.append, ToolEventView.metadata_only()),),
        signature="request -> answer: str",
    )
    rlm.generate_action = _InvalidToolThenSubmit()

    prediction = await rlm.acall(interpreter, request="repair invalid host Tool input")

    assert prediction.answer == "repaired"
    assert host_calls == 0
    assert any(isinstance(item, ToolStarted) for item in observed)
    assert any(isinstance(item, ToolFailed) for item in observed)
    assert not any(isinstance(item, ToolCompleted) for item in observed)
    interpreter.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True], ids=["invalid-submit-repair", "typed-extract"])
async def test_runner_completes_native_repair_and_extract_as_prediction_result(fallback: bool) -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import (
        RLMExecutionContext,
    )

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        def drain_memory_candidates(self):
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

    options = RLMOptions(max_iters=1 if fallback else 2)
    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="complete natively",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=options,
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        capabilities=Capabilities(),
    )
    stream = RLMRunner(factory=NativeFactory()).stream(context)
    events = [event async for event in stream]

    assert stream.outcome is not None
    assert stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == ("extracted" if fallback else "repaired")
    assert stream.outcome.prediction.outputs == {"answer": "extracted" if fallback else "repaired"}
    reasoning_events = [event for event in events if isinstance(event.detail, RLMReasoning)]
    assert reasoning_events
    assert all(event.detail.text.strip() for event in reasoning_events)
