"""Contracts for Fleet's exact pinned DSPy RLM seam."""

from __future__ import annotations

import sys
from datetime import date
from typing import Annotated, Any, ClassVar

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


def test_prediction_result_oversized_carries_sanitized_metrics_attrs() -> None:
    from fleet_rlm.rlm.dspy_contract import PredictionOutputTooLargeError, prediction_result

    class Report(dspy.Signature):
        answer: str = dspy.OutputField()
        metadata: dict[str, str] = dspy.OutputField()

    secret = "sk-live-abcdef123456"
    with pytest.raises(PredictionOutputTooLargeError) as excinfo:
        prediction_result(
            dspy.Prediction(answer=f"token={secret} " + "A" * 200, metadata={}),
            Report,
            max_output_chars=64,
        )
    exc = excinfo.value

    # Public message text stays exactly the closed-Literal string.
    assert str(exc) == "Turn output is too large"
    assert exc.public_message == "Turn output is too large"
    # Diagnostics ride typed attrs, not the message.
    assert isinstance(exc.output_chars, int) and exc.output_chars > 64
    assert isinstance(exc.output_preview, str)
    assert secret not in exc.output_preview  # secrets redacted
    assert len(exc.output_preview) <= 400  # bounded (sanitize_public_text max_len)


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


def test_prediction_trajectory_normalization_does_not_mutate_dspy_prediction() -> None:
    from fleet_rlm.rlm.dspy_contract import normalize_prediction_trajectory

    raw_trajectory = [{"reasoning": "inspect", "code": "value = 1", "output": "1"}]
    prediction = dspy.Prediction(trajectory=raw_trajectory)

    normalized = normalize_prediction_trajectory(prediction)

    assert normalized[0].reasoning == "inspect"
    assert normalized[0].code == "value = 1"
    assert normalized[0].output == "1"
    assert prediction.trajectory == raw_trajectory


def _lookup(value: str) -> str:
    """Return a value through a host tool."""
    return value


def test_rlm_options_match_the_product_defaults() -> None:
    from fleet_rlm.rlm.dspy_contract import RLMOptions

    assert RLMOptions() == RLMOptions(
        max_iters=20,
        max_llm_calls=50,
        max_output_chars=10_000,
    )


def test_build_native_rlm_preserves_exact_public_constructor_inputs() -> None:
    from fleet_rlm.rlm.dspy_contract import RLMOptions, build_native_rlm

    class TaskSignature(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    sub_lm = object()
    kwargs: dict[str, Any] = {
        "signature": TaskSignature,
        "options": RLMOptions(max_iters=7, max_llm_calls=11, max_output_chars=2048),
        "tools": [_lookup],
        "sub_lm": sub_lm,
    }

    first = build_native_rlm(**kwargs)
    second = build_native_rlm(**kwargs)

    assert type(first) is dspy.RLM
    assert first is not second
    assert first.verbose is True
    assert first.signature is TaskSignature
    assert first.max_iters == 7
    assert first.max_llm_calls == 11
    assert first.max_output_chars == 2048
    assert first.sub_lm is sub_lm
    assert not hasattr(first, "_interpreter")
    assert first._interpreter_factory.__name__ == "_missing_caller_owned_interpreter"
    assert set(first.tools) == {"_lookup"}
    assert first.generate_action.callbacks == []


def test_build_native_rlm_fails_closed_without_a_caller_owned_interpreter() -> None:
    from fleet_rlm.rlm.dspy_contract import RLMOptions, build_native_rlm
    from fleet_rlm.rlm.errors import RLMConfigError

    rlm = build_native_rlm(signature="request -> answer", options=RLMOptions(max_iters=1))

    with pytest.raises(RLMConfigError, match="caller-owned interpreter"):
        rlm(request="missing interpreter")


@pytest.mark.asyncio
async def test_native_json_action_contract_parses_first_and_followup_iterations() -> None:
    from dspy.primitives.repl_types import REPLHistory
    from dspy.utils import DummyLM

    from fleet_rlm.rlm.dspy_contract import RLMOptions, build_native_rlm

    adapter = dspy.JSONAdapter(use_native_function_calling=True)
    lm = DummyLM(
        [
            {"reasoning": "Inspect the request.", "code": "print(request)"},
            {"reasoning": "Use the observed value.", "code": "SUBMIT(answer='ok')"},
        ],
        adapter=adapter,
    )
    rlm = build_native_rlm(
        signature="request -> answer",
        options=RLMOptions(max_iters=2),
        sub_lm=lm,
        verbose=False,
    )
    history = REPLHistory()

    with dspy.context(lm=lm, adapter=adapter):
        first = await rlm.generate_action.acall(
            variables_info=["request: str"],
            repl_history=history,
            iteration="1/2",
        )
        history = history.append(
            reasoning=first.reasoning,
            code=first.code,
            output="sample",
        )
        second = await rlm.generate_action.acall(
            variables_info=["request: str"],
            repl_history=history,
            iteration="2/2",
        )

    assert (first.reasoning, first.code) == ("Inspect the request.", "print(request)")
    assert (second.reasoning, second.code) == ("Use the observed value.", "SUBMIT(answer='ok')")
    assert len(lm.history) == 2


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
        tools: ClassVar[dict[str, object]] = {}

        def __init__(self) -> None:
            self.shutdown_calls = 0

        def start(self) -> None:
            return None

        def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
            del code, variables
            from fleet_rlm.rlm.dspy_interpreter_contract import wrap_final_output

            return wrap_final_output({"answer": "ok"})

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    observed: list[object] = []
    interpreter = Interpreter()
    rlm = build_native_rlm(
        signature=TaskSignature,
        options=RLMOptions(max_iters=1),
    )
    rlm.generate_action = Action()
    bind_native_rlm_observer(rlm, observed.append, max_chars=64)

    prediction = await rlm.acall(interpreter, request="go")

    assert type(rlm) is dspy.RLM
    assert prediction.answer == "ok"
    assert interpreter.shutdown_calls == 0
    assert [type(item) for item in observed] == [RLMReasoning]
    assert observed[0].text == "Decide the answer directly."
    assert observed[0].step == 1
    interpreter.shutdown()


def test_composition_version_guard_accepts_3_3_x_and_rejects_other_minors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.rlm.dspy_contract import assert_dspy_version

    monkeypatch.setattr(dspy, "__version__", "3.3.0")
    assert_dspy_version()  # pinned release
    monkeypatch.setattr(dspy, "__version__", "3.3.1")
    assert_dspy_version()  # patch within the pinned minor
    monkeypatch.setattr(dspy, "__version__", "3.3.7.post1")
    assert_dspy_version()  # post-release within the supported minor
    monkeypatch.setattr(dspy, "__version__", "3.4.0")
    with pytest.raises(RuntimeError, match=r"DSPy 3.3.x is required"):
        assert_dspy_version()
    for prerelease in ("3.3.0.dev1", "3.3.0rc1", "3.3.0b1"):
        monkeypatch.setattr(dspy, "__version__", prerelease)
        with pytest.raises(RuntimeError, match=r"DSPy 3.3.x release is required"):
            assert_dspy_version()
    monkeypatch.setattr(dspy, "__version__", "not-a-version")
    with pytest.raises(RuntimeError, match=r"DSPy 3.3.x release is required"):
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
        trajectory: ClassVar[list[object]] = []

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


def test_lm_trace_callback_records_role_and_failure_category(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fleet_rlm.observability import turn_tracing
    from fleet_rlm.rlm.dspy_contract import _RLMTraceCallback

    calls = SimpleNamespace(outputs=[])

    class Span:
        def set_inputs(self, payload):
            calls.inputs = payload

        def set_outputs(self, payload):
            calls.outputs.append(payload)

        def set_attributes(self, payload):
            calls.attributes = payload

        def set_status(self, status):
            calls.status = status

    class SpanContext:
        def __enter__(self):
            return Span()

        def __exit__(self, *_args):
            return None

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: Span(),
        start_span=lambda **_kwargs: SpanContext(),
    )
    fake_entities = SimpleNamespace(SpanType=SimpleNamespace(CHAIN="CHAIN", LLM="LLM"))
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", fake_entities)
    root = SimpleNamespace(model="root-model")
    ticks = iter((10.0, 10.125))
    monkeypatch.setattr("fleet_rlm.rlm.dspy_contract.time.perf_counter", lambda: next(ticks))
    callback = _RLMTraceCallback(root_lm=root, sub_lm=SimpleNamespace(model="sub-model"))

    class SecretError(Exception):
        def __str__(self) -> str:
            return "payment failed api_key=topsecret"

    boom = SecretError()

    token = turn_tracing._fleet_trace_active.set(True)
    try:
        callback.on_lm_start("call-1", root, {"prompt": "readable prompt"})
        callback.on_lm_end("call-1", [], boom)
    finally:
        turn_tracing._fleet_trace_active.reset(token)

    assert calls.inputs == {
        "role": "root",
        "model": "root-model",
        "call_id": "call-1",
        "call_index": 1,
        "input_keys": ["prompt"],
        "prompt_chars": 15,
        "prompt_preview": "readable prompt",
        "context_chars": 15,
        "history_length_before": None,
        "recursive_depth": 0,
    }
    assert calls.outputs[-1] == {
        "request_status": "failed",
        "failure_category": "unknown",
        "response_keys": [],
        "call_index": 1,
        "wall_time_ms": 125.0,
        "phase_status": "failed",
        "error_kind": "SecretError",
        "provider_status_category": "none",
        "detail": "payment failed [redacted]",
    }
    assert calls.status == "ERROR"


def test_lm_trace_callback_records_classified_failure_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed LM call must carry the *classified* provider failure on its span.

    Regression coverage for traces such as tr-db96 where the root LM span was
    ERROR with an empty message and ``failure_category: unknown``: the span
    must record a bounded, sanitized error kind and status class so the model
    that failed is debuggable without a live gateway.
    """
    from types import SimpleNamespace

    from fleet_rlm.daytona.errors import ProviderRequestError
    from fleet_rlm.observability import turn_tracing
    from fleet_rlm.rlm.dspy_contract import _RLMTraceCallback

    captured = SimpleNamespace(outputs=[])

    class Span:
        def set_inputs(self, payload):
            captured.inputs = payload

        def set_outputs(self, payload):
            captured.outputs.append(payload)

        def set_attributes(self, payload):
            captured.attributes = payload

        def set_status(self, status):
            captured.status = status

    # The span the callback actually finishes is the one opened by start_span;
    # get_current_active_span (a separate handle) must not mask its attributes.
    span = Span()

    class SpanContext:
        def __enter__(self):
            return span

        def __exit__(self, *_args):
            return None

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: span,
        start_span=lambda **_kwargs: SpanContext(),
    )
    fake_entities = SimpleNamespace(SpanType=SimpleNamespace(CHAIN="CHAIN", LLM="LLM"))
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", fake_entities)

    root = SimpleNamespace(model="root-model")
    callback = _RLMTraceCallback(root_lm=root, sub_lm=SimpleNamespace(model="sub-model"))
    boom = ProviderRequestError(
        "404 Not Found: model api_key=topsecret is unavailable",
        cause_type="NotFoundError",
        status_code=404,
    )

    token = turn_tracing._fleet_trace_active.set(True)
    try:
        callback.on_lm_start("call-404", root, {"prompt": "p"})
        callback.on_lm_end("call-404", [], boom)
    finally:
        turn_tracing._fleet_trace_active.reset(token)

    span_outputs = captured.outputs[-1]
    assert span_outputs["request_status"] == "failed"
    assert span_outputs["phase_status"] == "failed"
    assert span_outputs["failure_category"] == "request_validation"
    assert span_outputs["error_kind"] == "ProviderRequestError"
    assert span_outputs["provider_status_category"] == "4xx"
    # The classified kinds also ride on span attributes for the UI.
    attrs = captured.attributes
    assert attrs["fleet.error.kind"] == "ProviderRequestError"
    assert attrs["fleet.error.category"] == "request_validation"
    assert attrs["fleet.error.status"] == "4xx"
    # The sanitized details must be present but free of the embedded secret.
    assert "detail" in span_outputs
    assert "topsecret" not in str(span_outputs)
    assert "topsecret" not in str(attrs)
    # last_call_summary must mirror the classified failure.
    summary = callback.last_call_summary()
    assert summary["failure_category"] == "request_validation"
    assert summary["error_kind"] == "ProviderRequestError"
    assert "topsecret" not in str(summary)


def test_lm_trace_callback_keeps_structural_last_call_summary() -> None:
    from types import SimpleNamespace

    from fleet_rlm.rlm.dspy_contract import _RLMTraceCallback

    root = SimpleNamespace(model="root-model", history=[])
    callback = _RLMTraceCallback(root_lm=root, sub_lm=SimpleNamespace(model="sub-model"))

    callback.on_lm_start("call-1", root, {"prompt": "sensitive prompt"})
    callback.on_lm_end("call-1", [])

    summary = callback.last_call_summary()

    assert summary["role"] == "root"
    assert summary["call_index"] == 1
    assert summary["request_status"] == "completed"
    assert summary["response_keys"] == ()
    assert "response_preview" not in summary
    assert "sensitive prompt" not in str(summary)


def test_lm_trace_profiles_include_bounded_readable_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.observability import tracing
    from fleet_rlm.rlm.dspy_contract import _lm_input_profile, _lm_output_profile

    monkeypatch.setattr(tracing, "_TRACE_CONTENT_MAX_CHARS", 256)

    inputs = _lm_input_profile(
        {
            "prompt": "readable prompt " + "x" * 400,
            "messages": [{"role": "user", "content": "readable message"}],
        }
    )
    outputs = _lm_output_profile({"content": "readable answer"})

    assert inputs["prompt_preview"].startswith("readable prompt")
    assert len(inputs["prompt_preview"]) <= 256
    assert "readable message" in inputs["messages_preview"]
    assert "readable answer" in outputs["response_preview"]


def test_lm_trace_callback_records_call_specific_usage_and_standard_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fleet_rlm.observability import turn_tracing
    from fleet_rlm.rlm.dspy_contract import _RLMTraceCallback

    calls = SimpleNamespace(outputs=[], attributes=[])

    class Span:
        def set_inputs(self, payload):
            calls.inputs = payload

        def set_outputs(self, payload):
            calls.outputs.append(payload)

        def set_attributes(self, payload):
            calls.attributes.append(payload)

        def set_status(self, _status):
            return None

    class SpanContext:
        def __enter__(self):
            return Span()

        def __exit__(self, *_args):
            return None

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: Span(),
        start_span=lambda **_kwargs: SpanContext(),
    )
    fake_entities = SimpleNamespace(SpanType=SimpleNamespace(CHAIN="CHAIN", LLM="LLM"))
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", fake_entities)
    response = SimpleNamespace(
        _hidden_params={
            "_response_ms": 401.25,
            "litellm_overhead_time_ms": 12.5,
            "callback_duration_ms": 3.75,
            "litellm_call_id": "fallback-call-id",
            "additional_headers": {
                "llm_provider-x-request-id": "provider-request-7",
                "authorization": "must-not-be-traced",
            },
            "provider_response": "must-not-be-traced",
        }
    )
    root = SimpleNamespace(model="root-model", history=[{"usage": {"prompt_tokens": 99}}])
    ticks = iter((20.0, 20.5))
    monkeypatch.setattr("fleet_rlm.rlm.dspy_contract.time.perf_counter", lambda: next(ticks))
    callback = _RLMTraceCallback(root_lm=root, sub_lm=SimpleNamespace(model="sub-model"), recursive_depth=1)

    token = turn_tracing._fleet_trace_active.set(True)
    try:
        callback.on_lm_start("call-2", root, {"prompt": "child-prompt-sentinel"})
        root.history.append(
            {
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                    "completion_tokens_details": SimpleNamespace(
                        model_dump=lambda: {"reasoning_tokens": 2, "video_tokens": 9}
                    ),
                    "cache_read_input_tokens": 4,
                    "prompt_cache_hit_tokens": 4,
                    "unsafe_usage": "must-not-be-traced",
                },
                "response": response,
                "prompt": "must-not-be-traced",
                "outputs": "must-not-be-traced",
            }
        )
        callback.on_lm_end(
            "call-2",
            {
                "content": "child-answer-sentinel",
                "reasoning": "child-reasoning-sentinel",
                "code": "child-code-sentinel",
            },
        )
    finally:
        turn_tracing._fleet_trace_active.reset(token)

    assert calls.inputs["recursive_depth"] == 1
    assert calls.inputs["call_index"] == 1
    assert calls.inputs["prompt_chars"] == len("child-prompt-sentinel")
    assert calls.inputs["history_length_before"] == 1
    assert calls.outputs[-1]["token_usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
        "completion_tokens_details": {"reasoning_tokens": 2},
        "cache_read_input_tokens": 4,
        "prompt_cache_hit_tokens": 4,
    }
    assert calls.outputs[-1]["response_keys"] == ["code", "content", "reasoning"]
    assert calls.outputs[-1]["wall_time_ms"] == 500.0
    assert calls.outputs[-1]["provider_response_ms"] == 401.25
    assert calls.outputs[-1]["litellm_overhead_ms"] == 12.5
    assert calls.outputs[-1]["callback_duration_ms"] == 3.75
    assert calls.outputs[-1]["provider_request_id"] == "provider-request-7"
    assert "authorization" not in str(calls.outputs[-1])
    assert "must-not-be-traced" not in str(calls.outputs[-1])
    assert "child-prompt-sentinel" not in str(calls.inputs)
    assert "child-answer-sentinel" not in str(calls.outputs[-1])
    assert "child-reasoning-sentinel" not in str(calls.outputs[-1])
    assert "child-code-sentinel" not in str(calls.outputs[-1])
    assert calls.attributes == [
        {
            "mlflow.chat.tokenUsage": {
                "input_tokens": 7,
                "output_tokens": 3,
                "total_tokens": 10,
                "cache_read_tokens": 4,
            }
        }
    ]


def test_reasoning_callback_spans_the_complete_root_action(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fleet_rlm.observability import turn_tracing
    from fleet_rlm.rlm.dspy_contract import _RLMReasoningCallback

    outputs: list[dict[str, object]] = []

    class Span:
        def set_inputs(self, _payload):
            return None

        def set_outputs(self, payload):
            outputs.append(payload)

        def set_status(self, _status):
            return None

    class SpanContext:
        def __enter__(self):
            return Span()

        def __exit__(self, *_args):
            return None

    monkeypatch.setitem(
        sys.modules,
        "mlflow",
        SimpleNamespace(get_current_active_span=lambda: Span(), start_span=lambda **_kwargs: SpanContext()),
    )
    monkeypatch.setitem(
        sys.modules,
        "mlflow.entities",
        SimpleNamespace(SpanType=SimpleNamespace(CHAIN="CHAIN", LLM="LLM")),
    )
    observed: list[object] = []
    callback = _RLMReasoningCallback(observed.append, max_chars=100)

    token = turn_tracing._fleet_trace_active.set(True)
    try:
        callback.on_module_start("module-1", object(), {})
        callback.on_module_end("module-1", dspy.Prediction(reasoning="reason", code="answer = 1"))
    finally:
        turn_tracing._fleet_trace_active.reset(token)

    assert outputs[-1] == {
        "action_status": "parsed",
        "reasoning_chars": 6,
        "code_chars": 10,
        "reasoning_preview": "reason",
        "code_preview": "answer = 1",
        "phase_status": "completed",
    }
    assert len(observed) == 1


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
        trajectory: ClassVar[list[object]] = [{"output": "step one"}, {"output": "step two"}]

        def get_lm_usage(self):
            return provider_usage

    assert observed_usage(Prediction(), duration_ms=17) == {
        "iterations": 2,
        "observed_lm_usage": {},
        "duration_ms": 17,
    }


def test_lm_output_profile_reads_mapping_of_parsed_fields() -> None:
    from fleet_rlm.rlm.dspy_contract import _lm_output_profile

    # Success path: adapter-parsed outputs arrive as a Mapping of signature fields.
    outputs = {"reasoning": "step", "code": "print(1)"}
    profile = _lm_output_profile(outputs)
    assert profile["response_keys"] == ("code", "reasoning")
    assert profile["response_chars"] == len("step") + len("print(1)")
    assert "response_preview" in profile


def test_lm_output_profile_reads_model_response_choices_content() -> None:
    from litellm import ModelResponse

    from fleet_rlm.rlm.dspy_contract import _lm_output_profile

    # Build a ModelResponse whose choices carry the JSON completion in message.content.
    outputs = ModelResponse(
        choices=[
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": '{"reasoning": "r", "code": "c"}',
                },
            }
        ]
    )
    profile = _lm_output_profile(outputs)
    assert profile["response_keys"] == ("content", "finish_reason")
    # response_chars sums every string value in the emitted mapping:
    # content (31) + finish_reason "stop" (4) = 35.
    assert profile["response_chars"] == len('{"reasoning": "r", "code": "c"}') + len("stop")
    assert "response_preview" in profile


def test_lm_output_profile_reads_string_and_unknown_shapes() -> None:
    from fleet_rlm.rlm.dspy_contract import _lm_output_profile

    # A bare string completion is keyed as ("content",).
    profile = _lm_output_profile('{"reasoning": "x"}')
    assert profile["response_keys"] == ("content",)
    assert profile["response_chars"] == len('{"reasoning": "x"}')

    # Genuinely unusable shapes still degrade to the historical empty-keys shape.
    assert _lm_output_profile(None) == {"response_keys": ()}
    assert _lm_output_profile(object()) == {"response_keys": ()}
