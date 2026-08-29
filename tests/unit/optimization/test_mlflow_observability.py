"""Unit contracts for development GEPA MLflow correlation."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from fleet_rlm.optimization.mlflow_observability import development_gepa_trace


def test_development_gepa_trace_uses_only_aggregate_metadata(monkeypatch) -> None:
    calls = SimpleNamespace(inputs=None, outputs=None, trace_updates=[], status=None)

    class Span:
        request_id = "trace-123"

        def set_inputs(self, value):
            calls.inputs = value

        def set_outputs(self, value):
            calls.outputs = value

        def set_status(self, value):
            calls.status = value

    class Context:
        def __enter__(self):
            return Span()

        def __exit__(self, *_args):
            return None

    mlflow = ModuleType("mlflow")
    mlflow.start_span = lambda **_kwargs: Context()  # type: ignore[attr-defined]
    mlflow.get_last_active_trace_id = lambda: "trace-123"  # type: ignore[attr-defined]
    mlflow.update_current_trace = lambda **kwargs: calls.trace_updates.append(kwargs)  # type: ignore[attr-defined]
    entities = ModuleType("mlflow.entities")
    entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)
    monkeypatch.setattr("fleet_rlm.config.loader.load_runtime_settings", lambda: object())
    monkeypatch.setattr("fleet_rlm.observability.tracing.configure_tracing", lambda _settings: None)

    metadata = {
        "schema": "fleet.development-gepa-smoke/v1",
        "run_id": "development-gepa-smoke-1",
        "dataset_sha256": "a" * 64,
        "train_records": 15,
        "selection_records": 5,
        "max_metric_calls": 2,
        "engine": "gepa",
        "environment": "development",
        "synthetic": True,
        "candidate_execution": "disabled",
        "promotion_eligible": False,
        "production_authorized": False,
    }

    with development_gepa_trace(metadata=metadata) as trace:
        assert trace.trace_id == "trace-123"

    assert calls.inputs == metadata
    assert calls.outputs == {"status": "completed"}
    assert calls.trace_updates[0]["tags"] == {
        "fleet.trace_kind": "optimization_development_smoke",
        "fleet.optimizer": "gepa",
        "fleet.environment": "development",
    }


def test_development_gepa_trace_rejects_content_metadata() -> None:
    try:
        with development_gepa_trace(metadata={"candidate": "private instruction"}):
            raise AssertionError("content metadata must be rejected before tracing")
    except ValueError as exc:
        assert "unsupported" in str(exc)
