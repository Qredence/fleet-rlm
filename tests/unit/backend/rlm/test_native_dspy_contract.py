"""Contracts for Fleet's exact pinned DSPy RLM seam."""

from __future__ import annotations

import asyncio
import inspect
import sys
from datetime import date
from types import SimpleNamespace
from typing import Annotated, Any, ClassVar

import dspy
import pytest
from pydantic import Field, PlainSerializer

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.events import ToolEventView, observe_tool
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions, build_native_rlm
from fleet_rlm.rlm.result import prediction_result


def test_prediction_result_encodes_every_declared_output_by_annotation() -> None:
    from fleet_rlm.rlm.result import prediction_result

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
    from fleet_rlm.rlm.result import (
        PredictionOutputError,
        prediction_result,
    )

    class Report(dspy.Signature):
        answer: str = dspy.OutputField()
        payload: object = dspy.OutputField()

    with pytest.raises(PredictionOutputError, match="Turn output is invalid"):
        prediction_result(dspy.Prediction(answer=answer, payload=payload), Report)


def test_prediction_result_rejects_oversized_or_publicly_unsafe_outputs_without_mutation() -> None:
    from fleet_rlm.rlm.result import (
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
    from fleet_rlm.rlm.result import (
        PredictionOutputTooLargeError,
        prediction_result,
    )

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
    from fleet_rlm.rlm.result import prediction_result

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
    from fleet_rlm.rlm.result import (
        PredictionOutputError,
        prediction_result,
    )

    class Report(dspy.Signature):
        answer: str = dspy.OutputField()
        metadata: dict[str, str] = dspy.OutputField()

    with pytest.raises(PredictionOutputError, match="Turn output is invalid"):
        prediction_result(dspy.Prediction(answer=answer, metadata=metadata), Report)


def test_prediction_result_validates_and_serializes_complete_annotated_output() -> None:
    from fleet_rlm.rlm.result import (
        PredictionOutputError,
        prediction_result,
    )

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
    from fleet_rlm.rlm.result import prediction_result

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
    from fleet_rlm.rlm.result import normalize_prediction_trajectory

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
    from fleet_rlm.rlm.program import RLMOptions

    assert RLMOptions() == RLMOptions(
        max_iters=20,
        max_llm_calls=50,
        max_output_chars=10_000,
    )


def test_build_native_rlm_preserves_exact_public_constructor_inputs() -> None:
    from fleet_rlm.rlm.program import (
        RLMOptions,
        build_native_rlm,
    )

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
    assert first._interpreter_factory.__name__ == "daytona_provider_contract"
    assert set(first.tools) == {"_lookup"}
    assert first.generate_action.callbacks == []


def test_build_native_rlm_fails_closed_without_a_caller_owned_interpreter() -> None:
    from fleet_rlm.rlm.program import (
        RLMOptions,
        build_native_rlm,
    )
    from fleet_rlm.rlm.result import RLMConfigError

    rlm = build_native_rlm(signature="request -> answer", options=RLMOptions(max_iters=1))

    with pytest.raises(RLMConfigError, match="caller-owned interpreter"):
        rlm(request="missing interpreter")


@pytest.mark.asyncio
async def test_native_json_action_contract_parses_first_and_followup_iterations() -> None:
    from dspy.primitives.repl_types import REPLHistory
    from dspy.utils import DummyLM

    from fleet_rlm.rlm.program import (
        RLMOptions,
        build_native_rlm,
    )

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
    from fleet_rlm.rlm._dspy_compat import bind_native_rlm_observer
    from fleet_rlm.rlm.events import RLMReasoning
    from fleet_rlm.rlm.program import (
        RLMOptions,
        build_native_rlm,
    )

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
            from fleet_rlm.rlm._dspy_compat import wrap_final_output

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


def test_composition_version_guard_accepts_exact_final_3_3_1_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.rlm._dspy_compat import (
        CERTIFIED_DSPY_VERSION,
        UncertifiedDSpyVersionError,
        assert_dspy_version,
    )

    assert CERTIFIED_DSPY_VERSION == "3.3.1"
    monkeypatch.setattr(dspy, "__version__", "3.3.1")
    assert_dspy_version()  # the certified final release
    rejected_versions = (
        "3.3.0",
        "3.3.2",
        "3.3.1.dev1",
        "3.3.1a1",
        "3.3.1b1",
        "3.3.1rc1",
        "3.3.1.post1",
        # Literal string comparison must reject local segments that PEP 440
        # specifier equality would silently ignore.
        "3.3.1+local",
        "not-a-version",
        "3.2.9",
        "3.4.0",
        "4.0.0",
    )
    for reported in rejected_versions:
        monkeypatch.setattr(dspy, "__version__", reported)
        with pytest.raises(UncertifiedDSpyVersionError, match=r"exactly DSPy 3\.3\.1"):
            assert_dspy_version()


def test_composition_version_guard_error_is_bounded_and_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.rlm._dspy_compat import (
        UncertifiedDSpyVersionError,
        assert_dspy_version,
    )

    assert issubclass(UncertifiedDSpyVersionError, RuntimeError)
    hostile = "3.3.1+" + "x" * 5000
    monkeypatch.setattr(dspy, "__version__", hostile)
    with pytest.raises(UncertifiedDSpyVersionError) as caught:
        assert_dspy_version()
    message = str(caught.value)
    assert "exactly DSPy 3.3.1" in message
    assert hostile not in message
    assert len(message) <= 256


def test_rlm_usage_contract_accepts_only_the_exact_observed_shape() -> None:
    from fleet_rlm.rlm.result import validate_rlm_usage

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
    from fleet_rlm.rlm.result import (
        observed_usage,
        validate_rlm_usage,
    )

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
    from fleet_rlm.rlm._dspy_compat import _RLMTraceCallback

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
    monkeypatch.setattr("time.perf_counter", lambda: next(ticks))
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
    from fleet_rlm.rlm._dspy_compat import _RLMTraceCallback

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

    from fleet_rlm.rlm._dspy_compat import _RLMTraceCallback

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
    from fleet_rlm.rlm._dspy_compat import (
        _lm_input_profile,
        _lm_output_profile,
    )

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
    from fleet_rlm.rlm._dspy_compat import _RLMTraceCallback

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
    # P38-RLM-006: raw provider-response probing was removed with the
    # contraction; the history entry carries only usage and sentinels.
    root = SimpleNamespace(model="root-model", history=[{"usage": {"prompt_tokens": 99}}])
    ticks = iter((20.0, 20.5))
    monkeypatch.setattr("time.perf_counter", lambda: next(ticks))
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
    # P38-RLM-006: private provider timing/identity fields are gone.
    for removed in ("provider_response_ms", "litellm_overhead_ms", "callback_duration_ms", "provider_request_id"):
        assert removed not in calls.outputs[-1]
        assert removed not in callback.last_call_summary()
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
    from fleet_rlm.rlm._dspy_compat import _RLMReasoningCallback

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
    from fleet_rlm.rlm.result import observed_usage

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
    from fleet_rlm.rlm._dspy_compat import _lm_output_profile

    # Success path: adapter-parsed outputs arrive as a Mapping of signature fields.
    outputs = {"reasoning": "step", "code": "print(1)"}
    profile = _lm_output_profile(outputs)
    assert profile["response_keys"] == ("code", "reasoning")
    assert profile["response_chars"] == len("step") + len("print(1)")
    assert "response_preview" in profile


def test_lm_output_profile_degrades_unknown_shapes_without_raw_probing() -> None:
    from fleet_rlm.rlm._dspy_compat import _lm_output_profile

    # P38-RLM-006/011: raw LiteLLM ModelResponse shapes are never delivered by
    # the certified DSPy 3.3.1 legacy contract and are no longer probed.
    class _ChoicesLike:
        choices: ClassVar[list[dict[str, object]]] = [{"message": {"content": "secret"}, "finish_reason": "stop"}]

    assert _lm_output_profile(_ChoicesLike()) == {"response_keys": ()}

    # Genuinely unusable shapes still degrade to the historical empty-keys shape.
    assert _lm_output_profile(None) == {"response_keys": ()}
    assert _lm_output_profile(object()) == {"response_keys": ()}


def test_latest_lm_telemetry_reads_only_the_certified_legacy_history_entry() -> None:
    """P38-RLM-006/011: usage comes from the identity-matched legacy entry.

    The typed ``LMResponse`` fallback (``usage_as_dict``) and raw
    provider-response probing are deleted: a typed-shaped callback payload
    with no matching history entry degrades to unavailable, never to an
    estimate.
    """
    from types import SimpleNamespace

    from fleet_rlm.rlm._dspy_compat import _latest_lm_telemetry

    outputs = ["parsed"]
    lm = SimpleNamespace(
        history=[
            {"outputs": object(), "usage": {"prompt_tokens": 99, "completion_tokens": 1}},
            {"outputs": outputs, "usage": {"prompt_tokens": 4, "completion_tokens": 2}},
        ]
    )

    assert _latest_lm_telemetry(lm, 0, outputs) == {"prompt_tokens": 4, "completion_tokens": 2}

    class TypedResponse:
        def usage_as_dict(self) -> dict[str, int]:
            return {"prompt_tokens": 7}

    # A typed response with no matching history entry yields nothing.
    assert _latest_lm_telemetry(SimpleNamespace(history=[]), 0, TypedResponse()) == {}
    # Missing history or unknown payloads degrade to unavailable, not zero.
    assert _latest_lm_telemetry(SimpleNamespace(history=[]), 0, None) == {}
    assert _latest_lm_telemetry(SimpleNamespace(), 0, outputs) == {}


def test_lm_trace_callback_emits_token_usage_output_and_mlflow_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Observed per-call usage must reach the span and the delegation metrics.

    Regression coverage for traces where completed RLM.*_lm spans carried no
    ``mlflow.chat.tokenUsage`` attribute and metrics emitted all-zero
    ``lm_token_totals`` despite no provider usage ever being reported.
    """
    from types import SimpleNamespace

    from fleet_rlm.observability import turn_tracing
    from fleet_rlm.rlm._dspy_compat import _RLMTraceCallback
    from fleet_rlm.rlm.recursion import DelegationMetrics

    captured = SimpleNamespace(outputs=[], attributes={})

    class Span:
        def set_inputs(self, payload):
            """
            Store the supplied payload as the captured inputs.

            Parameters:
                payload: Input data to capture.
            """
            captured.inputs = payload

        def set_outputs(self, payload):
            """Store the provided payload as the captured outputs."""
            captured.outputs.append(payload)

        def set_attributes(self, payload):
            """
            Update the captured attributes with the supplied values.

            Parameters:
                payload (dict): Attribute names and values to record.
            """
            captured.attributes.update(payload)

        def set_status(self, status):
            captured.status = status

    class SpanContext:
        def __enter__(self):
            """
            Enter the context manager and provide a new span.

            Returns:
                Span: The newly created span.
            """
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
    monkeypatch.setattr("time.perf_counter", lambda: 0.0)

    metrics = DelegationMetrics()
    root = SimpleNamespace(model="root-model", history=[])
    callback = _RLMTraceCallback(root_lm=root, sub_lm=SimpleNamespace(model="sub-model"), metrics=metrics)

    observed_outputs = ["ok"]
    token = turn_tracing._fleet_trace_active.set(True)
    try:
        # A call whose provider reports usage: attribute + token_usage output.
        callback.on_lm_start("call-observed", root, {"prompt": "p"})
        root.history.append({"outputs": observed_outputs, "usage": {"prompt_tokens": 4, "completion_tokens": 2}})
        callback.on_lm_end("call-observed", observed_outputs)
    finally:
        turn_tracing._fleet_trace_active.reset(token)

    assert captured.outputs[-1]["token_usage"] == {"prompt_tokens": 4, "completion_tokens": 2}
    assert captured.attributes["mlflow.chat.tokenUsage"] == {
        "input_tokens": 4,
        "output_tokens": 2,
        "total_tokens": 6,
    }
    assert metrics.snapshot().lm_token_totals == (("root", 0, 4, 2, 6),)
    assert metrics.snapshot().token_usage_status == "observed"

    captured.outputs.clear()
    captured.attributes.clear()

    token = turn_tracing._fleet_trace_active.set(True)
    try:
        # A call whose provider reports nothing: no usage keys, no zero totals.
        callback.on_lm_start("call-unobserved", root, {"prompt": "p"})
        root.history.append({"outputs": observed_outputs, "usage": {}})
        callback.on_lm_end("call-unobserved", observed_outputs)
    finally:
        turn_tracing._fleet_trace_active.reset(token)

    assert "token_usage" not in captured.outputs[-1]
    assert "mlflow.chat.tokenUsage" not in captured.attributes
    assert metrics.snapshot().lm_token_totals == (("root", 0, 4, 2, 6),)


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
