"""Contracts for Fleet's exact pinned DSPy RLM seam."""

from __future__ import annotations

import asyncio
import inspect
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import dspy
import pytest

from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import (
    BackendExecutionResult,
    DaytonaCodeInterpreter,
    InProcessInterpreterBackend,
    OutputCallback,
)
from fleet_rlm.rlm.adapter import FleetJSONAdapter
from fleet_rlm.rlm.events import ToolEventView, observe_tool
from fleet_rlm.rlm.execution import RLMProviderContractError, probe_root_lm
from fleet_rlm.rlm.output_contract import bind_output_contract
from fleet_rlm.rlm.program import RLMOptions
from fleet_rlm.rlm.result import prediction_result
from tests.support.native_rlm import build_native_rlm_for_test
from tests.support.scripted_lm import _IterationActionSignature, _ScriptedLM


def _use_context_span_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep payload-only callback tests on their existing fake span API."""
    from fleet_rlm.observability import tracing

    start = tracing.start_turn_span

    def mocked_start(name: str, **kwargs: Any):
        kwargs.pop("callback_span", None)
        return start(name, **kwargs)

    monkeypatch.setattr(tracing, "start_turn_span", mocked_start)


def test_prediction_result_rejects_oversized_or_publicly_unsafe_outputs_without_mutation() -> None:
    from fleet_rlm.rlm.result import (
        PredictionOutputError,
        PredictionOutputTooLargeError,
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
    )

    class Report(dspy.Signature):
        answer: str = dspy.OutputField()
        metadata: dict[str, str] = dspy.OutputField()

    with pytest.raises(PredictionOutputError, match="Turn output is invalid"):
        prediction_result(dspy.Prediction(answer=answer, metadata=metadata), Report)


class _ChildFindings(dspy.Signature):
    answer: str = dspy.OutputField()
    evidence: list[str] = dspy.OutputField()
    gaps: list[str] = dspy.OutputField()
    result_files: list[str] = dspy.OutputField()


_CHILD_SCRATCH = "/tmp/fleet/child-data/3f0c/4"
_REDACTED = frozenset({"evidence", "gaps"})


def _child_prediction(**overrides: object) -> dspy.Prediction:
    fields: dict[str, object] = {
        "answer": '{"row_count": 117712}',
        "evidence": [f"Staged copy {_CHILD_SCRATCH}/exports/sweep-ledger.csv matches the manifest."],
        "gaps": [f"Scratch was located by search at {_CHILD_SCRATCH}."],
        "result_files": [],
    }
    fields.update(overrides)
    return dspy.Prediction(**fields)


def test_prediction_result_redacts_private_paths_only_in_listed_fields() -> None:
    result = prediction_result(_child_prediction(), _ChildFindings, path_redacted_fields=_REDACTED)

    assert result.display_text == '{"row_count": 117712}'
    assert result.outputs["evidence"] == ("Staged copy [path] matches the manifest.",)
    assert result.outputs["gaps"] == ("Scratch was located by search at [path].",)
    # The documented Volume mount is not private and stays verbatim.
    mount = prediction_result(
        _child_prediction(evidence=["Read /home/daytona/fleet/notes.md"]),
        _ChildFindings,
        path_redacted_fields=_REDACTED,
    )
    assert mount.outputs["evidence"] == ("Read /home/daytona/fleet/notes.md",)


@pytest.mark.parametrize(
    "overrides",
    [
        {"answer": f"Totals are in {_CHILD_SCRATCH}/results.json"},
        {"result_files": [f"{_CHILD_SCRATCH}/results.json"]},
        {"evidence": ["token=abc123secretvalue"]},
        {"evidence": ["Connect to postgresql://fleet:secret@private.example/fleet"]},
        {"evidence": [f"{_CHILD_SCRATCH}/cfg?api_key=abc123secretvalue"]},
        {"gaps": ['Traceback (most recent call last):\n  File "/srv/app.py", line 7']},
    ],
)
def test_prediction_result_redaction_keeps_every_other_rule_strict(overrides: dict[str, object]) -> None:
    from fleet_rlm.rlm.result import PredictionOutputError

    with pytest.raises(PredictionOutputError, match="Turn output is invalid"):
        prediction_result(_child_prediction(**overrides), _ChildFindings, path_redacted_fields=_REDACTED)


def test_prediction_result_without_redacted_fields_still_rejects_evidence_paths() -> None:
    from fleet_rlm.rlm.result import PredictionOutputError

    with pytest.raises(PredictionOutputError, match="Turn output is invalid"):
        prediction_result(_child_prediction(), _ChildFindings)


def test_prediction_result_outputs_are_deeply_immutable() -> None:

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


def _lookup(value: str) -> str:
    """Return a value through a host tool."""
    return value


@pytest.mark.asyncio
async def test_native_json_action_contract_parses_first_and_followup_iterations() -> None:
    from dspy.primitives.repl_types import REPLHistory
    from dspy.utils import DummyLM

    from fleet_rlm.rlm.program import (
        RLMOptions,
    )

    adapter = dspy.JSONAdapter(use_native_function_calling=True)
    lm = DummyLM(
        [
            {"reasoning": "Inspect the request.", "code": "print(request)"},
            {"reasoning": "Use the observed value.", "code": "SUBMIT(answer='ok')"},
        ],
        adapter=adapter,
    )
    rlm = build_native_rlm_for_test(
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
    from fleet_rlm.rlm.events import RLMReasoning, bind_native_rlm_observer
    from fleet_rlm.rlm.program import (
        RLMOptions,
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
            """Execute code with optional variables and return a wrapped result."""
            del code, variables
            from fleet_rlm.daytona.interpreter import wrap_final_output

            return wrap_final_output({"answer": "ok"})

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    observed: list[object] = []
    interpreter = Interpreter()
    rlm = build_native_rlm_for_test(
        signature=TaskSignature,
        options=RLMOptions(max_iters=1),
    )
    rlm.generate_action = Action()
    bind_native_rlm_observer(rlm, observed.append, max_chars=64)

    prediction = await rlm.acall(interpreter_factory=lambda: interpreter, request="go")

    assert type(rlm) is dspy.RLM
    assert prediction.answer == "ok"
    assert interpreter.shutdown_calls == 1
    assert [type(item) for item in observed] == [RLMReasoning]
    assert observed[0].text == "Decide the answer directly."
    assert observed[0].step == 1
    interpreter.shutdown()


def test_composition_version_guard_error_is_bounded_and_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.rlm.program import (
        UncertifiedDSpyVersionError,
        assert_dspy_version,
    )

    assert issubclass(UncertifiedDSpyVersionError, RuntimeError)
    hostile = "3.4.0+" + "x" * 5000
    monkeypatch.setattr(dspy, "__version__", hostile)
    with pytest.raises(UncertifiedDSpyVersionError) as caught:
        assert_dspy_version()
    message = str(caught.value)
    assert "exactly DSPy 3.4.0" in message
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
    _use_context_span_mock(monkeypatch)
    from types import SimpleNamespace

    from fleet_rlm.observability import tracing as turn_tracing
    from fleet_rlm.rlm.events import _RLMTraceCallback

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
    _use_context_span_mock(monkeypatch)
    from types import SimpleNamespace

    from fleet_rlm.daytona.errors import ProviderRequestError
    from fleet_rlm.observability import tracing as turn_tracing
    from fleet_rlm.rlm.events import _RLMTraceCallback

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

    from fleet_rlm.rlm.events import _RLMTraceCallback

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


def test_lm_trace_callback_records_reasoning_tokens_from_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_context_span_mock(monkeypatch)
    from types import SimpleNamespace

    from fleet_rlm.observability import tracing as turn_tracing
    from fleet_rlm.rlm.events import _RLMTraceCallback

    captured = SimpleNamespace(outputs=[])

    class Span:
        def set_inputs(self, _payload):
            return None

        def set_outputs(self, payload):
            captured.outputs.append(payload)

        def set_attributes(self, _payload):
            return None

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

    outputs = [{"text": "", "reasoning_content": "hidden think"}]
    root = SimpleNamespace(model="root-model", history=[])
    callback = _RLMTraceCallback(root_lm=root, sub_lm=SimpleNamespace(model="sub-model"))
    token = turn_tracing._fleet_trace_active.set(True)
    try:
        callback.on_lm_start("call-1", root, {"prompt": "p"})
        root.history.append(
            {
                "outputs": outputs,
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 20,
                    "completion_tokens_details": {"reasoning_tokens": 18},
                },
            }
        )
        callback.on_lm_end("call-1", outputs)
    finally:
        turn_tracing._fleet_trace_active.reset(token)

    assert captured.outputs[-1]["has_reasoning_content"] is True
    assert captured.outputs[-1]["reasoning_tokens"] == 18
    assert callback.last_call_summary()["reasoning_tokens"] == 18
    assert callback.last_call_summary()["has_reasoning_content"] is True


def test_lm_trace_profiles_include_bounded_readable_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.observability import tracing
    from fleet_rlm.rlm.events import (
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


def test_lm_trace_previews_keep_system_prompt_text_and_redact_urls() -> None:
    from fleet_rlm.rlm.events import _trace_preview

    preview = _trace_preview("BEGIN SYSTEM use https://example.invalid/private for context")

    assert "BEGIN SYSTEM" in preview
    assert "[redacted-url]" in preview


def test_lm_trace_callback_keeps_diagnostics_without_duplicate_token_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_context_span_mock(monkeypatch)
    from types import SimpleNamespace

    from fleet_rlm.observability import tracing as turn_tracing
    from fleet_rlm.rlm.events import _RLMTraceCallback

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
    assert "token_usage" not in calls.outputs[-1]
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
    assert calls.attributes[0] == {
        "role": "root",
        "model": "root-model",
        "call_index": 1,
        "input_keys": ["prompt"],
        "prompt_chars": len("child-prompt-sentinel"),
        "history_length_before": 1,
        "recursive_depth": 1,
    }
    assert len(calls.attributes) == 1


def test_reasoning_callback_spans_the_complete_root_action(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_context_span_mock(monkeypatch)
    from types import SimpleNamespace

    from fleet_rlm.observability import tracing as turn_tracing
    from fleet_rlm.rlm.events import _RLMReasoningCallback

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


def test_lm_output_profile_degrades_unknown_shapes_without_raw_probing() -> None:
    from fleet_rlm.rlm.events import _lm_output_profile

    # P38-RLM-006/011: raw LiteLLM ModelResponse shapes are never delivered by
    # the certified DSPy 3.4.0 legacy contract and are no longer probed.
    class _ChoicesLike:
        choices: ClassVar[list[dict[str, object]]] = [{"message": {"content": "secret"}, "finish_reason": "stop"}]

    assert _lm_output_profile(_ChoicesLike()) == {"response_keys": ()}

    # Genuinely unusable shapes still degrade to the historical empty-keys shape.
    assert _lm_output_profile(None) == {"response_keys": ()}
    assert _lm_output_profile(object()) == {"response_keys": ()}


def test_latest_lm_telemetry_falls_back_to_stored_response_usage() -> None:
    """Empty ``history['usage']`` can still carry counts on the stored response.

    Databricks AI Gateway / DeepSeek sometimes omit the top-level usage mapping
    while the same history entry's ``response.usage`` has token counts. That is
    a history-local fallback, not live provider probing. When both are empty,
    usage stays unavailable rather than a fabricated zero.
    """
    from types import SimpleNamespace

    from fleet_rlm.rlm.events import _latest_lm_telemetry

    outputs = ["ok"]
    recovered = _latest_lm_telemetry(
        SimpleNamespace(
            history=[
                {
                    "outputs": outputs,
                    "usage": {},
                    "response": SimpleNamespace(
                        usage=SimpleNamespace(
                            prompt_tokens=4,
                            completion_tokens=9,
                            completion_tokens_details={"reasoning_tokens": 7},
                        )
                    ),
                }
            ]
        ),
        0,
        outputs,
    )
    assert recovered["prompt_tokens"] == 4
    assert recovered["completion_tokens"] == 9
    assert recovered["completion_tokens_details"] == {"reasoning_tokens": 7}

    assert (
        _latest_lm_telemetry(
            SimpleNamespace(history=[{"outputs": outputs, "usage": {}, "response": object()}]),
            0,
            outputs,
        )
        == {}
    )


def test_lm_trace_callback_avoids_duplicate_mlflow_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fleet metrics retain observed usage while DSPy autolog owns the LLM span."""
    _use_context_span_mock(monkeypatch)
    from types import SimpleNamespace

    from fleet_rlm.observability import tracing as turn_tracing
    from fleet_rlm.rlm.events import _RLMTraceCallback
    from fleet_rlm.rlm.recursion import DelegationMetrics

    captured = SimpleNamespace(outputs=[], attributes={}, span_types=[])

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
        start_span=lambda **kwargs: (captured.span_types.append(kwargs.get("span_type")), SpanContext())[1],
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
        # A call whose provider reports usage is counted once in Fleet metrics.
        callback.on_lm_start("call-observed", root, {"prompt": "p"})
        root.history.append({"outputs": observed_outputs, "usage": {"prompt_tokens": 4, "completion_tokens": 2}})
        callback.on_lm_end("call-observed", observed_outputs)
    finally:
        turn_tracing._fleet_trace_active.reset(token)

    assert "token_usage" not in captured.outputs[-1]
    assert "mlflow.chat.tokenUsage" not in captured.attributes
    assert captured.span_types == ["CHAIN"]
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
    bind_output_contract(interpreter, Report)
    rlm = build_native_rlm_for_test(
        signature=Report,
        options=RLMOptions(max_iters=1),
        verbose=False,
    )
    rlm.generate_action = _Actions('SUBMIT(answer="done", note=None)')
    rlm.extract = _NeverExtract()
    try:
        prediction = await rlm.acall(interpreter_factory=lambda: interpreter, request="defaults")
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
    rlm = build_native_rlm_for_test(signature=Report, options=RLMOptions(max_iters=2), verbose=False)
    actions = _Actions(
        'SUBMIT(answer="done", count=None, note=None)',
        'SUBMIT(answer="done", count=3, note=None)',
    )
    rlm.generate_action = actions
    try:
        prediction = await rlm.acall(interpreter_factory=lambda: interpreter, request="nullable")
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
    rlm = build_native_rlm_for_test(signature=Report, options=RLMOptions(max_iters=2), verbose=False)
    actions = _Actions(
        'SUBMIT(answer="done", score=float("nan"), payload={"ok": "no"})',
        'SUBMIT(answer="done", score=1.5, payload={"ok": "yes"})',
    )
    rlm.generate_action = actions
    try:
        prediction = await rlm.acall(interpreter_factory=lambda: interpreter, request="strict")
    finally:
        interpreter.shutdown()

    assert prediction.score == 1.5
    assert prediction.payload == {"ok": "yes"}
    assert actions.calls == 2
    assert "non-finite" in prediction.trajectory[0]["output"].lower()


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

    class _Bridge:
        def run(self, awaitable: Any, **_kwargs: Any) -> Any:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(awaitable)
            finally:
                loop.close()

    sync_wrapped = observe_tool(dspy.Tool(sync_tool), sync_events.append, ToolEventView.metadata_only())
    async_wrapped = observe_tool(
        dspy.Tool(async_tool),
        async_events.append,
        ToolEventView.metadata_only(),
        async_bridge=_Bridge(),
    )

    sync_result = sync_wrapped.func(value=4, note=None)
    # DSPy Tools are synchronous at the interpreter boundary; direct callers
    # outside the worker loop use the short-lived credential-free path.
    async_result = await asyncio.to_thread(async_wrapped.func, value=4, note=None)

    assert sync_result == async_result == {"value": 4, "note": None}
    assert [type(event).__name__ for event in sync_events] == [
        "ToolStarted",
        "ToolCompleted",
    ]
    assert [type(event).__name__ for event in async_events] == [
        "ToolStarted",
        "ToolCompleted",
    ]


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
    rlm = build_native_rlm_for_test(signature="request -> answer: str", options=RLMOptions(max_iters=1), verbose=False)

    assert inspect.signature(rlm._interpreter_factory).parameters == {}
    assert sentinel.shutdown_calls == 0


@pytest.mark.asyncio
async def test_caller_owned_interpreter_tool_injection_output_metadata_and_trajectory() -> None:
    import json

    class ItemPayload(dspy.SandboxSerializable):
        def __init__(self, key: str, value: int) -> None:
            """Initialize an instance with a key and associated value."""
            self.key = key
            self.value = value

        def sandbox_setup(self) -> str:
            """Provide the sandbox initialization code used by the test interpreter."""
            return "import json as _test_json"

        def to_sandbox(self) -> bytes:
            """
            Serialize the key-value pair for sandbox transfer.

            Returns:
                bytes: UTF-8 encoded JSON representation of the key-value pair.
            """
            return json.dumps({"key": self.key, "value": self.value}).encode()

        def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
            """
            Generate a sandbox assignment statement that deserializes a JSON expression.
            """
            return f"{var_name} = _test_json.loads({data_expr})"

        def rlm_preview(self, max_chars: int = 100) -> str:
            """Return a concise representation containing the payload key."""
            del max_chars
            return f"ItemPayload(key={self.key})"

    class TaskSignature(dspy.Signature):
        request: str = dspy.InputField()
        payload: ItemPayload = dspy.InputField()
        answer: str = dspy.OutputField()
        summary: str = dspy.OutputField(default="default-summary")

    def lookup_tool(text: str) -> str:
        """Formats a lookup value with a found prefix.

        Returns:
            The lookup value prefixed with ``"found:"``.
        """
        return f"found:{text}"

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    bind_output_contract(interpreter, TaskSignature)

    rlm = build_native_rlm_for_test(
        signature=TaskSignature,
        tools=[lookup_tool],
        options=RLMOptions(max_iters=2),
        verbose=False,
    )

    action1 = 'val = lookup_tool(text=payload["key"])\nprint(val)'
    action2 = "SUBMIT(answer=val)"
    rlm.generate_action = _Actions(action1, action2)
    rlm.extract = _NeverExtract()

    try:
        prediction = await rlm.acall(
            interpreter_factory=lambda: interpreter,
            request="fetch",
            payload=ItemPayload("test-key", 42),
        )
    finally:
        interpreter.shutdown()

    assert "lookup_tool" in interpreter.tools
    assert "llm_query" in interpreter.tools
    assert prediction.answer == "found:test-key"
    assert prediction.summary == "default-summary"
    assert len(prediction.trajectory) >= 2
    assert prediction.trajectory[0]["code"] == action1
    assert "found:test-key" in prediction.trajectory[0]["output"]
    assert prediction.trajectory[1]["code"] == action2


def _raise_prediction_output_failure(case: str) -> None:
    """Raise the ``PredictionOutputError`` for one named validation site."""
    from fleet_rlm.rlm.result import (
        PredictionResult,
        _strict_json,
        normalize_prediction_trajectory,
    )

    builders = {
        "trajectory_not_a_sequence": lambda: normalize_prediction_trajectory(SimpleNamespace(trajectory=123)),
        "trajectory_step_not_mapping": lambda: normalize_prediction_trajectory(SimpleNamespace(trajectory=["nope"])),
        "trajectory_step_field_not_text": lambda: normalize_prediction_trajectory(
            SimpleNamespace(trajectory=[{"reasoning": 1}])
        ),
        "display_text_missing": lambda: PredictionResult("   ", {"answer": "y"}, "schema", "1"),
        "outputs_not_a_mapping": lambda: PredictionResult("x", ["not-a-mapping"], "schema", "1"),
        "schema_identity_missing": lambda: PredictionResult("x", {"answer": "y"}, "", "1"),
        "output_float_not_finite": lambda: _strict_json(float("inf")),
        "output_key_not_text": lambda: _strict_json({1: "x"}),
        "output_type_unsupported": lambda: _strict_json(object()),
    }
    builders[case]()


@pytest.mark.parametrize(
    "case",
    (
        "trajectory_not_a_sequence",
        "trajectory_step_not_mapping",
        "trajectory_step_field_not_text",
        "display_text_missing",
        "outputs_not_a_mapping",
        "schema_identity_missing",
        "output_float_not_finite",
        "output_key_not_text",
        "output_type_unsupported",
    ),
)
def test_prediction_output_error_names_the_failing_validation(case: str) -> None:
    """Every rejection site reports a distinguishable internal cause.

    The public message is a single closed literal, so without ``cause_type`` all
    of these collapse into one opaque string and a failed live run cannot be
    triaged without reproducing it.
    """
    from fleet_rlm.rlm.result import PredictionOutputError

    with pytest.raises(PredictionOutputError) as raised:
        _raise_prediction_output_failure(case)

    assert raised.value.cause_type == case
    # The public contract stays frozen regardless of the internal cause.
    assert raised.value.public_message == "Turn output is invalid"
    assert str(raised.value) == "Turn output is invalid"
    assert raised.value.status == "failed"


def test_prediction_result_reports_fine_grained_causes_through_the_public_path() -> None:
    """Namely causes survive ``prediction_result`` rather than being relabelled.

    ``PredictionOutputError`` is a ``ValueError``, so a generic handler around the
    output-validation loop would otherwise swallow a specific cause.
    """
    from fleet_rlm.rlm.result import PredictionOutputError

    class Report(dspy.Signature):
        answer: str = dspy.OutputField()
        metadata: dict[str, str] = dspy.OutputField()

    with pytest.raises(PredictionOutputError) as empty_answer:
        prediction_result(dspy.Prediction(answer="   ", metadata={}), Report)
    assert empty_answer.value.cause_type == "answer_missing"

    with pytest.raises(PredictionOutputError) as unsafe:
        prediction_result(
            dspy.Prediction(answer="done", metadata={"token": "secret-value"}),
            Report,
            max_output_chars=1_000,
        )
    assert unsafe.value.cause_type == "declared_output_rejected"


def test_prediction_output_failure_category_is_bounded_and_detail_reaches_traces() -> None:
    """MLflow metadata carries a closed category plus the fine-grained detail."""
    from fleet_rlm.observability.diagnostics import trace_failure_category, trace_failure_details
    from fleet_rlm.rlm.result import PredictionOutputError, PredictionOutputTooLargeError

    invalid = PredictionOutputError(cause_type="display_text_missing")
    too_large = PredictionOutputTooLargeError(output_chars=9_999, output_preview="preview")

    assert trace_failure_category(invalid) == "prediction_output_invalid"
    assert trace_failure_category(too_large) == "prediction_output_too_large"
    assert trace_failure_details(invalid)["failure_detail"] == "display_text_missing"

    # No cause means no detail key, so span outputs stay uniform.
    assert "failure_detail" not in trace_failure_details(RuntimeError("boom"))


def test_prediction_output_cause_type_rejects_unbounded_labels() -> None:
    """The cause vocabulary is bounded so failure telemetry cannot drift."""
    from fleet_rlm.rlm.result import PredictionOutputError

    with pytest.raises(ValueError, match="invalid prediction cause_type"):
        PredictionOutputError(cause_type="NotSnakeCase")

    with pytest.raises(TypeError):
        PredictionOutputError()  # type: ignore[call-arg]  # cause_type is required


_FIXTURES = Path(__file__).parents[1] / "fixtures" / "rlm"
_EXPECTED = ({"A": 7, "B": 7, "C": 8, "D": 12, "E": 2}, 6, ("D",))


def _interpret(request: str) -> tuple[dict[str, int], int, tuple[str, ...]]:
    registers = dict.fromkeys("ABCDE", 0)
    true_count = 0
    instructions = re.findall(r"(?m)^(\d+)\. (ADD|SUB|COPY|SWAP|IFPOS|IFEVEN|DOUBLE|MOD) (.+)$", request)
    assert [int(number) for number, _, _ in instructions] == list(range(1, 41))

    def execute(operation: str, operands: str) -> None:
        nonlocal true_count
        parts = operands.split()
        x = parts[0].rstrip(":")
        assert x in registers
        if operation in {"IFPOS", "IFEVEN"}:
            condition = registers[x] > 0 if operation == "IFPOS" else registers[x] % 2 == 0
            if condition:
                true_count += 1
                inner_operation, inner_operands = operands.split(": ", 1)[1].split(" ", 1)
                execute(inner_operation, inner_operands)
        elif operation == "ADD":
            registers[x] += int(parts[1])
        elif operation == "SUB":
            registers[x] -= int(parts[1])
        elif operation == "COPY":
            registers[x] = registers[parts[1]]
        elif operation == "SWAP":
            registers[x], registers[parts[1]] = registers[parts[1]], registers[x]
        elif operation == "DOUBLE":
            registers[x] *= 2
        elif operation == "MOD":
            registers[x] %= int(parts[1])
        else:
            raise AssertionError(operation)

    for _, operation, operands in instructions:
        execute(operation, operands)
    largest = max(registers.values())
    return registers, true_count, tuple(name for name, value in registers.items() if value == largest)


@pytest.mark.parametrize("name", ["register_trace_exact.txt"])
def test_register_request_oracle(name: str) -> None:
    request = (_FIXTURES / name).read_text()
    assert _interpret(request) == _EXPECTED


def test_exact_trace_retains_trailing_text() -> None:
    request = (_FIXTURES / "register_trace_exact.txt").read_text()
    assert "</parameter>\n</invoke>" in request
    assert "Want me to:" in request


def test_fenced_python_actions_fail_closed() -> None:
    from dspy.utils.exceptions import AdapterParseError

    lm = _ScriptedLM(["```python\nprint(request)\n```"])
    with pytest.raises(AdapterParseError), dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        dspy.Predict(_IterationActionSignature)(iteration="1/12")
    assert len(lm.calls) == 3


_PI_REQUEST = "Tell me the 1492252th digits after the decimal point of Pi"
_EXPECTED_DIGIT = "5"


def test_pi_answer_submits_after_bounded_verification() -> None:
    class Actions:
        def __init__(self) -> None:
            self.codes: list[str] = []

        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            if not self.codes:
                code = (
                    f"pi_digit = {_EXPECTED_DIGIT!r}\n"
                    f"reference_digit = {_EXPECTED_DIGIT!r}\n"
                    "assert pi_digit == reference_digit\n"
                    "print(pi_digit)"
                )
            else:
                code = "SUBMIT(answer=pi_digit)"
            self.codes.append(code)
            return dspy.Prediction(reasoning="Use the bounded check, then submit", code=code)

    actions = Actions()
    rlm = dspy.RLM("request -> answer: str", max_iters=2)
    rlm.generate_action = actions
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    try:
        prediction = asyncio.run(rlm.acall(interpreter_factory=lambda: interpreter, request=_PI_REQUEST))
    finally:
        interpreter.shutdown()

    assert prediction.answer == _EXPECTED_DIGIT
    assert prediction.trajectory[0]["output"].strip() == _EXPECTED_DIGIT
    assert actions.codes[-1] == "SUBMIT(answer=pi_digit)"
    assert len(actions.codes) == 2
    assert all("Decimal" not in code and "Gauss" not in code for code in actions.codes)


def test_valid_pi_submit_action_needs_no_parse_repair() -> None:
    lm = _ScriptedLM(['{"reasoning":"bounded check passed","code":"SUBMIT(answer=\'5\')"}'])

    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        action = dspy.Predict(_IterationActionSignature)(iteration="2/2")

    assert action.code == "SUBMIT(answer='5')"
    assert len(lm.calls) == 1


def test_broker_timeout_stops_native_rlm_and_closes_interpreter() -> None:
    class Actions:
        calls = 0

        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            self.calls += 1
            return dspy.Prediction(reasoning="compute", code="print('working')")

    class TimedOutBackend:
        closed = False

        def run(
            self,
            code: str,
            variables: dict[str, object] | None = None,
            *,
            on_stdout: OutputCallback | None = None,
        ) -> BackendExecutionResult:
            del code, variables, on_stdout
            raise DaytonaAdapterError("sandbox execution request timed out", cause_type="BrokerExecutionTimeout")

        def close(self) -> None:
            self.closed = True

    actions = Actions()
    backend = TimedOutBackend()
    interpreter = DaytonaCodeInterpreter(backend=backend)
    rlm = dspy.RLM("request -> answer: str", max_iters=2)
    rlm.generate_action = actions
    try:
        with pytest.raises(DaytonaAdapterError):
            asyncio.run(rlm.acall(interpreter_factory=lambda: interpreter, request=_PI_REQUEST))
    finally:
        interpreter.shutdown()

    assert actions.calls == 1
    assert backend.closed


def _probe_interpreter():
    return DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())


def _probe_child_runtime(call_index: int, *, profile: str = "semantic-child"):
    del profile
    from fleet_rlm.daytona.runtime import ChildRuntimeLease

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    return ChildRuntimeLease(
        interpreter=interpreter,
        sandbox_id=f"provider-probe-{call_index}",
        volume_id="in-process",
        volume_subpath=f"recursive/provider-probe/run/{call_index}",
        _close=interpreter.shutdown,
    )


@pytest.mark.asyncio
async def test_provider_probe_requires_multiple_native_actions_and_typed_submit() -> None:
    lm = dspy.utils.DummyLM(
        [
            {"reasoning": "initialize", "code": "marker = 'probe-slice'"},
            {"reasoning": "delegate", "code": "child = rlm_query(task='Classify', inputs=[], context=marker)"},
            {
                "reasoning": "child submit",
                "code": "SUBMIT(answer='child-ok', evidence=[], gaps=[], result_files=[])",
            },
            {"reasoning": "submit", "code": "SUBMIT(answer=child['answer'])"},
        ],
        adapter=dspy.JSONAdapter(),
    )

    result = await probe_root_lm(lm, interpreter_factory=_probe_interpreter, child_runtime_factory=_probe_child_runtime)

    assert result.iterations == 3
    assert result.termination_mode == "typed_submit"


@pytest.mark.asyncio
async def test_provider_probe_rejects_unparseable_native_provider_output() -> None:
    lm = dspy.utils.DummyLM(
        [{"answer": "provider-native tool tokens"}],
        adapter=dspy.JSONAdapter(),
    )

    with pytest.raises(RLMProviderContractError, match="unparseable"):
        await probe_root_lm(lm, interpreter_factory=_probe_interpreter, child_runtime_factory=_probe_child_runtime)


@pytest.mark.asyncio
async def test_provider_probe_reports_native_extraction_fallback_for_forced_final_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.execution as provider_probe

    class FakeRecursiveExecutor:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self.tool = object()

        def summary(self) -> SimpleNamespace:
            return SimpleNamespace(call_count=1)

        def wait_owned(self) -> None:
            pass

    class FakeRLM:
        def __call__(self, *, interpreter_factory, **kwargs):
            assert callable(interpreter_factory)
            assert "probe" in kwargs
            return SimpleNamespace(
                trajectory=["step-1", "step-2", "step-3"],
                answer="child-ok",
                final_reasoning="Extract forced final output",
            )

    def build_fake_rlm(*_args, **_kwargs):
        return FakeRLM()

    monkeypatch.setattr(provider_probe, "RecursiveRLMExecutor", FakeRecursiveExecutor)
    monkeypatch.setattr(provider_probe, "build_native_rlm", build_fake_rlm)

    result = await provider_probe.probe_root_lm(
        dspy.utils.DummyLM([], adapter=dspy.JSONAdapter()),
        interpreter_factory=_probe_interpreter,
        child_runtime_factory=_probe_child_runtime,
    )

    assert result.iterations == 3
    assert result.termination_mode == "native_extraction_fallback"
