"""P35-C native DSPy 3.3.1 contract matrix.

These lanes deliberately exercise the public native RLM path with the
credential-free in-process interpreter.  The native RLM remains the owner of
iteration, history, typed Prediction construction, and extraction fallback;
Fleet only supplies the caller-owned interpreter and validates the result.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.events import (
    ToolEventView,
    observe_tool,
)
from fleet_rlm.rlm.program import (
    RLMModelBundle,
    RLMOptions,
    build_native_rlm,
)
from fleet_rlm.rlm.result import prediction_result


class _Actions:
    def __init__(self, *codes: str) -> None:
        self._codes = iter(codes)
        self.calls = 0

    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        self.calls += 1
        return dspy.Prediction(reasoning=f"native action {self.calls}", code=next(self._codes))


class _NeverExtract:
    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        raise AssertionError("native typed SUBMIT should have completed before extraction")


@pytest.mark.asyncio
async def test_native_submit_honors_required_defaults_and_nullable_outputs() -> None:
    class Report(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()
        count: int = dspy.OutputField(default=7)
        tags: list[str] = dspy.OutputField(default_factory=list)
        note: str | None = dspy.OutputField()

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    rlm = build_native_rlm(
        signature=Report,
        options=RLMOptions(max_iters=1),
        verbose=False,
    )
    rlm.generate_action = _Actions('SUBMIT(answer="done", note=None)')
    rlm.extract = _NeverExtract()
    try:
        prediction = await rlm.acall(interpreter, request="defaults")
    finally:
        interpreter.shutdown()

    assert prediction.answer == "done"
    assert prediction.count == 7
    assert prediction.tags == []
    assert prediction.note is None
    assert prediction.final_reasoning == "native action 1"
    assert prediction.trajectory[0]["output"].startswith("FINAL:")


@pytest.mark.asyncio
async def test_native_submit_preserves_explicit_none_and_rejects_non_nullable_none() -> None:
    class Report(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()
        count: int = dspy.OutputField()
        note: str | None = dspy.OutputField(default="default")

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    rlm = build_native_rlm(signature=Report, options=RLMOptions(max_iters=2), verbose=False)
    actions = _Actions(
        'SUBMIT(answer="done", count=None, note=None)',
        'SUBMIT(answer="done", count=3, note=None)',
    )
    rlm.generate_action = actions
    try:
        prediction = await rlm.acall(interpreter, request="nullable")
    finally:
        interpreter.shutdown()

    assert prediction.count == 3
    assert prediction.note is None
    assert actions.calls == 2
    assert "Type Error" in prediction.trajectory[0]["output"]


@pytest.mark.asyncio
async def test_native_submit_rejects_non_json_values_and_non_finite_numbers() -> None:
    class Report(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()
        score: float = dspy.OutputField()
        payload: dict[str, str] = dspy.OutputField()

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    rlm = build_native_rlm(signature=Report, options=RLMOptions(max_iters=2), verbose=False)
    actions = _Actions(
        'SUBMIT(answer="done", score=float("nan"), payload={"ok": "no"})',
        'SUBMIT(answer="done", score=1.5, payload={"ok": "yes"})',
    )
    rlm.generate_action = actions
    try:
        prediction = await rlm.acall(interpreter, request="strict")
    finally:
        interpreter.shutdown()

    assert prediction.score == 1.5
    assert prediction.payload == {"ok": "yes"}
    assert actions.calls == 2
    assert "non-finite" in prediction.trajectory[0]["output"].lower()


def test_prediction_result_applies_declared_defaults_without_mutating_prediction() -> None:
    class Report(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()
        tags: list[str] = dspy.OutputField(default_factory=list)
        note: str | None = dspy.OutputField(default=None)

    prediction = dspy.Prediction(answer="done")
    result = prediction_result(prediction, Report)

    assert result.outputs == {"answer": "done", "tags": (), "note": None}
    assert not hasattr(prediction, "tags")
    assert not hasattr(prediction, "note")


def test_tool_result_serialization_rejects_non_json_values_without_coercion() -> None:
    events: list[object] = []

    def unsupported() -> object:
        return {1: "not a string key"}

    wrapped = observe_tool(dspy.Tool(unsupported), events.append, ToolEventView.metadata_only())
    with pytest.raises(Exception, match="Tool result is invalid"):
        wrapped.func()
    assert not any(type(event).__name__ == "ToolCompleted" for event in events)


@pytest.mark.asyncio
async def test_sync_and_async_tools_have_equivalent_results_and_lifecycle() -> None:
    sync_events: list[object] = []
    async_events: list[object] = []

    def sync_tool(value: int, note: str | None = None) -> dict[str, Any]:
        return {"value": value, "note": note}

    async def async_tool(value: int, note: str | None = None) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"value": value, "note": note}

    sync_wrapped = observe_tool(dspy.Tool(sync_tool), sync_events.append, ToolEventView.metadata_only())
    async_wrapped = observe_tool(dspy.Tool(async_tool), async_events.append, ToolEventView.metadata_only())

    sync_result = sync_wrapped.func(value=4, note=None)
    async_result = async_wrapped.func(value=4, note=None)

    assert sync_result == async_result == {"value": 4, "note": None}
    assert [type(event).__name__ for event in sync_events] == [
        "ToolStarted",
        "ToolCompleted",
    ]
    assert [type(event).__name__ for event in async_events] == [
        "ToolStarted",
        "ToolCompleted",
    ]


def test_broker_value_encoding_is_strict_and_preserves_supported_values() -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    assert DaytonaHttpToolBroker._encode_value({"nested": [True, None, 1.5]}) == {"nested": [True, None, 1.5]}
    with pytest.raises(DaytonaAdapterError, match="unsupported"):
        DaytonaHttpToolBroker._encode_value({1: "coercion is forbidden"})
    with pytest.raises(DaytonaAdapterError, match="unsupported"):
        DaytonaHttpToolBroker._encode_value(float("nan"))
    with pytest.raises(DaytonaAdapterError, match="unsupported"):
        DaytonaHttpToolBroker._encode_value({1, 2})


def test_native_option_mapping_is_one_to_one_for_root_and_child_policy() -> None:
    options = RLMOptions(max_iters=3, max_llm_calls=5, max_output_chars=17)
    root = SimpleNamespace(copy=lambda **_kwargs: root)
    sub = SimpleNamespace(copy=lambda **_kwargs: sub)
    bundle = RLMModelBundle(root, sub)

    rlm = build_native_rlm(signature="request -> answer", options=options, sub_lm=sub, verbose=False)

    assert (rlm.max_iters, rlm.max_llm_calls, rlm.max_output_chars) == (3, 5, 17)
    assert bundle.root_lm is root
    assert bundle.sub_lm is sub


def test_native_contract_does_not_construct_or_shutdown_caller_owned_interpreter() -> None:
    class Sentinel:
        def __init__(self) -> None:
            self.shutdown_calls = 0

        def start(self) -> None:
            return None

        def execute(self, _code: str, _variables: dict[str, Any] | None = None) -> Any:
            from dspy import FinalOutput

            return FinalOutput({"answer": "done"})

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    sentinel = Sentinel()
    rlm = build_native_rlm(signature="request -> answer: str", options=RLMOptions(max_iters=1), verbose=False)

    assert inspect.signature(rlm._interpreter_factory).parameters == {}
    assert sentinel.shutdown_calls == 0
