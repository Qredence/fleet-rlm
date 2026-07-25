"""Contracts for Fleet's exact pinned DSPy RLM seam."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

import dspy
import pytest
from pydantic import Field, PlainSerializer


def test_prediction_result_encodes_every_declared_output_by_annotation() -> None:
    from fleet_rlm.rlm.dspy_contract import prediction_result

    class Report(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()
        published: date = dspy.OutputField()
        scores: tuple[int, ...] = dspy.OutputField()

    result = prediction_result(
        dspy.Prediction(answer="done", published=date(2026, 7, 15), scores=(2, 3)),
        Report,
        schema_id="report",
        schema_version="7",
    )

    assert result.display_text == "done"
    assert result.outputs == {"answer": "done", "published": "2026-07-15", "scores": (2, 3)}
    assert result.schema_id == "report"
    assert result.schema_version == "7"


@pytest.mark.parametrize(
    ("answer", "payload"),
    [("", {"ok": True}), (None, {"ok": True}), ("done", object())],
)
def test_prediction_result_rejects_invalid_display_or_json(answer: object, payload: object) -> None:
    from fleet_rlm.rlm.dspy_contract import PredictionOutputError, prediction_result

    class Report(dspy.Signature):
        answer: str = dspy.OutputField()
        payload: object = dspy.OutputField()

    with pytest.raises(PredictionOutputError, match="Turn output is invalid"):
        prediction_result(dspy.Prediction(answer=answer, payload=payload), Report)


def test_prediction_result_rejects_oversized_or_publicly_unsafe_outputs_without_mutation() -> None:
    from fleet_rlm.rlm.dspy_contract import (
        PredictionOutputError,
        PredictionOutputTooLargeError,
        prediction_result,
    )

    class Report(dspy.Signature):
        answer: str = dspy.OutputField()
        metadata: dict[str, str] = dspy.OutputField()

    with pytest.raises(PredictionOutputTooLargeError, match="Turn output is too large"):
        prediction_result(
            dspy.Prediction(answer="x" * 100, metadata={}),
            Report,
            max_output_chars=32,
        )
    with pytest.raises(PredictionOutputError, match="Turn output is invalid"):
        prediction_result(
            dspy.Prediction(answer="done", metadata={"token": "secret-value"}),
            Report,
            max_output_chars=1_000,
        )


def test_prediction_result_preserves_benign_security_text_and_documented_mount_verbatim() -> None:
    from fleet_rlm.rlm.dspy_contract import prediction_result

    class Report(dspy.Signature):
        answer: str = dspy.OutputField()
        metadata: dict[str, str] = dspy.OutputField()

    answer = (
        "The diagnostics skill loaded and FINAL was submitted. "
        "FLEET_DAYTONA_API_KEY exists, but no value is shown. "
        "Read the workspace under /home/daytona/fleet/session/workspace."
    )
    metadata = {"credential_name": "API_KEY", "sandbox_mount": "/home/daytona/fleet"}
    result = prediction_result(dspy.Prediction(answer=answer, metadata=metadata), Report)

    assert result.display_text == answer
    assert result.outputs == {"answer": answer, "metadata": metadata}


@pytest.mark.parametrize(
    ("answer", "metadata"),
    [
        ("Authorization: Bearer live-provider-value", {}),
        ("FLEET_DAYTONA_API_KEY=actual-secret-value", {}),
        ("Connect to postgresql://fleet:secret@private.example/fleet", {}),
        ("Read /Users/operator/.config/provider.json", {}),
        ('Traceback (most recent call last):\n  File "/srv/app.py", line 7', {}),
        ("BEGIN SYSTEM\nYou are the private system instruction", {}),
        ("done", {"api_key": "actual-secret-value"}),
    ],
)
def test_prediction_result_rejects_concrete_private_material(answer: str, metadata: dict[str, str]) -> None:
    from fleet_rlm.rlm.dspy_contract import PredictionOutputError, prediction_result

    class Report(dspy.Signature):
        answer: str = dspy.OutputField()
        metadata: dict[str, str] = dspy.OutputField()

    with pytest.raises(PredictionOutputError, match="Turn output is invalid"):
        prediction_result(dspy.Prediction(answer=answer, metadata=metadata), Report)


def test_prediction_result_validates_and_serializes_complete_annotated_output() -> None:
    from fleet_rlm.rlm.dspy_contract import PredictionOutputError, prediction_result

    class Report(dspy.Signature):
        answer: str = dspy.OutputField()
        count: Annotated[
            int,
            Field(gt=0),
            PlainSerializer(lambda value: f"count={value}", return_type=str),
        ] = dspy.OutputField()

    result = prediction_result(dspy.Prediction(answer="done", count=3), Report)

    assert result.outputs == {"answer": "done", "count": "count=3"}
    for invalid in (-1, object()):
        with pytest.raises(PredictionOutputError, match="Turn output is invalid"):
            prediction_result(dspy.Prediction(answer="done", count=invalid), Report)


def test_prediction_result_outputs_are_deeply_immutable() -> None:
    from fleet_rlm.rlm.dspy_contract import prediction_result

    class Report(dspy.Signature):
        answer: str = dspy.OutputField()
        payload: dict[str, list[int]] = dspy.OutputField()

    result = prediction_result(
        dspy.Prediction(answer="done", payload={"items": [1, 2]}),
        Report,
    )
    assert result.outputs == {"answer": "done", "payload": {"items": (1, 2)}}
    with pytest.raises(TypeError):
        result.outputs["answer"] = "changed"  # type: ignore[index]


class _Interpreter:
    @property
    def tools(self) -> dict[str, Any]:
        return {}


def _lookup(value: str) -> str:
    """Return a value through a host tool."""
    return value


def test_rlm_options_match_the_product_defaults() -> None:
    from fleet_rlm.rlm.dspy_contract import RLMOptions

    assert RLMOptions() == RLMOptions(
        max_iterations=20,
        max_llm_calls=50,
        max_output_chars=10_000,
    )


def test_build_native_rlm_preserves_exact_public_constructor_inputs() -> None:
    from fleet_rlm.rlm.dspy_contract import RLMOptions, build_native_rlm

    class TaskSignature(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    sub_lm = object()
    interpreter = _Interpreter()
    kwargs: dict[str, Any] = {
        "signature": TaskSignature,
        "options": RLMOptions(max_iterations=7, max_llm_calls=11, max_output_chars=2048),
        "tools": [_lookup],
        "sub_lm": sub_lm,
        "interpreter": interpreter,
    }

    first = build_native_rlm(**kwargs)
    second = build_native_rlm(**kwargs)

    assert type(first) is dspy.RLM
    assert first is not second
    assert first.verbose is True
    assert first.signature is TaskSignature
    assert first.max_iterations == 7
    assert first.max_llm_calls == 11
    assert first.max_output_chars == 2048
    assert first.sub_lm is sub_lm
    assert first._interpreter is interpreter  # noqa: SLF001 - pinned DSPy contract
    assert set(first.tools) == {"_lookup"}
    assert first.generate_action.callbacks == []


@pytest.mark.asyncio
async def test_native_rlm_callback_observes_completed_action_without_altering_prediction() -> None:
    from fleet_rlm.rlm.dspy_contract import RLMOptions, bind_native_rlm_observer, build_native_rlm
    from fleet_rlm.rlm.events import RLMReasoning

    class TaskSignature(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    class Action(dspy.Predict):
        def __init__(self) -> None:
            super().__init__("variables_info, repl_history, iteration -> reasoning, code")

        async def aforward(self, **_kwargs: Any) -> dspy.Prediction:
            return dspy.Prediction(
                reasoning="Decide the answer directly.",
                code="SUBMIT(answer='ok')",
            )

    class Interpreter:
        tools: dict[str, object] = {}

        def start(self) -> None:
            return None

        def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
            from fleet_rlm.rlm.dspy_interpreter_contract import wrap_final_output

            return wrap_final_output({"answer": "ok"})

        def shutdown(self) -> None:
            return None

    observed: list[object] = []
    rlm = build_native_rlm(
        signature=TaskSignature,
        options=RLMOptions(max_iterations=1),
        interpreter=Interpreter(),
    )
    rlm.generate_action = Action()
    bind_native_rlm_observer(rlm, observed.append, max_chars=64)

    prediction = await rlm.acall(request="go")

    assert type(rlm) is dspy.RLM
    assert prediction.answer == "ok"
    assert [type(item) for item in observed] == [RLMReasoning]
    assert observed[0].text == "Decide the answer directly."
    assert observed[0].step == 1


def test_composition_version_guard_rejects_any_unpinned_dspy(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.rlm.dspy_contract import assert_dspy_version

    monkeypatch.setattr(dspy, "__version__", "3.3.0")
    with pytest.raises(RuntimeError, match="DSPy 3.3.0b1 is required"):
        assert_dspy_version()


def test_rlm_usage_contract_accepts_only_the_exact_observed_shape() -> None:
    from fleet_rlm.rlm.dspy_contract import validate_rlm_usage

    usage = validate_rlm_usage(
        {
            "iterations": 2,
            "observed_lm_usage": {"root": {"prompt_tokens": 4, "cached": False}},
            "duration_ms": 12,
        }
    )
    assert usage == {
        "iterations": 2,
        "observed_lm_usage": {"root": {"prompt_tokens": 4, "cached": False}},
        "duration_ms": 12,
    }

    for invalid in (
        {"iterations": 1, "observed_lm_usage": {}, "duration_ms": 1, "llm_calls": 2},
        {"iterations": 1, "observed_lm_usage": {}, "duration_ms": 1, "root_lm_calls": 1},
        {"iterations": 1, "observed_lm_usage": {}, "duration_ms": 1, "sub_lm_calls": 1},
        {"iterations": -1, "observed_lm_usage": {}, "duration_ms": 1},
        {"iterations": 1, "observed_lm_usage": [], "duration_ms": 1},
        {"iterations": 1, "observed_lm_usage": {"bad": object()}, "duration_ms": 1},
        {"iterations": 1, "observed_lm_usage": {}, "duration_ms": -1},
    ):
        with pytest.raises(ValueError):
            validate_rlm_usage(invalid)


@pytest.mark.parametrize(
    "forbidden",
    ["retry_count", "root_lm_calls", "sub_lm_calls", "remaining_llm_calls", "estimated_calls"],
)
def test_observed_usage_never_exposes_call_or_retry_counters(forbidden: str) -> None:
    from fleet_rlm.rlm.dspy_contract import observed_usage, validate_rlm_usage

    class Prediction:
        trajectory = []

        def get_lm_usage(self):
            return {
                "root": {
                    "prompt_tokens": 4,
                    forbidden: 99,
                }
            }

    assert observed_usage(Prediction(), duration_ms=1)["observed_lm_usage"] == {"root": {"prompt_tokens": 4}}
    with pytest.raises(ValueError):
        validate_rlm_usage(
            {
                "iterations": 0,
                "observed_lm_usage": {"root": {forbidden: 99}},
                "duration_ms": 1,
            }
        )


@pytest.mark.parametrize(
    "provider_usage",
    [
        {"root": {"bad": object()}},
        {"root": {"not_json": {1, 2}}},
        {1: {"prompt_tokens": 4}},
    ],
)
def test_malformed_provider_usage_degrades_without_losing_measured_fields(provider_usage: object) -> None:
    from fleet_rlm.rlm.dspy_contract import observed_usage

    class Prediction:
        trajectory = [{"output": "step one"}, {"output": "step two"}]

        def get_lm_usage(self):
            return provider_usage

    assert observed_usage(Prediction(), duration_ms=17) == {
        "iterations": 2,
        "observed_lm_usage": {},
        "duration_ms": 17,
    }
