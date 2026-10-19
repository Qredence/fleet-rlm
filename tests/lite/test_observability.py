from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


def test_mlflow_autolog_traces_runtime_inference_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.observability as memory_and_eval

    calls: dict[str, Any] = {}
    monkeypatch.setattr(memory_and_eval.mlflow, "set_tracking_uri", lambda uri: calls.setdefault("uri", uri))
    monkeypatch.setattr(
        memory_and_eval.mlflow.tracing,
        "configure",
        lambda **kwargs: calls.setdefault("processors", kwargs),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "set_experiment",
        lambda name: calls.setdefault("experiment", name),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "dspy",
        SimpleNamespace(autolog=lambda **kwargs: calls.setdefault("autolog", kwargs)),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "get_experiment_by_name",
        lambda _name: SimpleNamespace(experiment_id="experiment-id"),
    )

    experiment_id = memory_and_eval.configure_mlflow_observability(
        "fleet-rlm-test",
        "sqlite:///mlflow-test.db",
    )

    assert experiment_id == "experiment-id"
    assert calls == {
        "uri": "sqlite:///mlflow-test.db",
        "experiment": "fleet-rlm-test",
        "processors": {"span_processors": [memory_and_eval.sanitize_trace_span]},
        "autolog": {
            "log_traces": True,
            "log_traces_from_compile": False,
            "log_traces_from_eval": False,
        },
    }


def test_trace_span_sanitization_redacts_secret_fields_and_bounds_content():
    import fleet_rlm.observability as memory_and_eval

    class Span:
        def __init__(self):
            self.inputs = {"token": "secret-value", "task": "Bearer abcdefghijklmnopqrstuvwxyz"}
            self.outputs = {"answer": "x" * (memory_and_eval._TRACE_STRING_LIMIT + 1)}

        def set_inputs(self, value):
            self.inputs = value

        def set_outputs(self, value):
            self.outputs = value

    span = Span()
    memory_and_eval.sanitize_trace_span(span)
    assert span.inputs["token"] == "[redacted]"
    assert "[redacted]" in span.inputs["task"]
    assert len(span.outputs["answer"]) < memory_and_eval._TRACE_STRING_LIMIT + 100


def test_trace_shows_reasoning_text_but_streams_still_redact_it() -> None:
    import fleet_rlm.observability as memory_and_eval

    cleaned = memory_and_eval.trace_value(
        {"reasoning_content": "Plan: use api_key=hidden", "api_key": "value"},
        nodes=[0],
    )
    assert cleaned == {"reasoning_content": "Plan: use api_key=[redacted]", "api_key": "[redacted]"}
    assert '"reasoning_content": "[redacted]"' in memory_and_eval.activity_preview({"reasoning_content": "private"})


def test_typesafe_route_filters_the_incompatible_mlflow_callback() -> None:
    from fleet_rlm.dspy_support import without_mlflow_dspy_callback

    mlflow_callback = type("MlflowCallback", (), {"__module__": "mlflow.dspy.callback"})()
    sse_callback = object()

    assert without_mlflow_dspy_callback([mlflow_callback, sse_callback]) == [sse_callback]
