"""Production GEPA orchestration and development MLflow correlation contracts.

* ``test_gepa_production.py``: Production GEPA orchestration contracts remain strict and reproducible.
* ``test_mlflow_observability.py``: Unit contracts for development GEPA MLflow correlation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import dspy
import pytest

from fleet_rlm.optimization.evidence import StrictDaytonaPolicyBinding, validate_strict_daytona_proof
from fleet_rlm.optimization.gepa_runner import OptimizationPreflightError, run_authoritative_gepa
from fleet_rlm.optimization.metric import ScoreFeedback, TrustedGEPAFeedbackMetric
from fleet_rlm.optimization.mlflow_observability import development_gepa_trace
from tests.unit.optimization.test_evidence import _block_all_receipt


# --- from test_gepa_production.py -------------------------------------
class _Student(dspy.Module):
    def __init__(self) -> None:
        super().__init__()
        self.predict = dspy.Predict("question -> answer")

    def forward(self, question: str) -> dspy.Prediction:
        return self.predict(question=question)


class _Details:
    def to_dict(self):
        return {
            "candidates": [{"predict": "safe instructions"}],
            "val_aggregate_scores": [0.5, 0.75],
            "total_metric_calls": 160,
            "num_full_val_evals": 4,
            "best_idx": 1,
        }


class _GEPA:
    def __init__(self, **kwargs):
        assert kwargs["auto"] is None
        assert kwargs["max_metric_calls"] == 160
        assert kwargs["track_stats"] is True
        assert kwargs["track_best_outputs"] is True
        self.kwargs = kwargs

    def compile(self, student, *, trainset, valset):
        assert len(trainset) == 15
        assert len(valset) == 5
        student.detailed_results = _Details()
        return student


def _student_metric(_gold, _pred, **_kwargs):
    return ScoreFeedback(1.0, "all machine-checkable expectations passed")


def _strict_policy() -> StrictDaytonaPolicyBinding:
    return StrictDaytonaPolicyBinding(
        policy_id="b" * 64,
        snapshot="fleet-safe-v1",
        gateway_domains=(),
        auto_stop_interval_seconds=300,
        auto_delete_interval_seconds=0,
        network_block_all=True,
    )


def test_authoritative_gepa_requires_explicit_production_budget_and_reload(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setattr(dspy, "GEPA", _GEPA)
    proof = validate_strict_daytona_proof(_block_all_receipt())
    metric = TrustedGEPAFeedbackMetric(_student_metric, "c" * 64)
    train = [{"question": f"train-{index}"} for index in range(15)]
    selection = [{"question": f"selection-{index}"} for index in range(5)]
    held_out = [{"question": f"held-out-{index}"} for index in range(5)]

    result = run_authoritative_gepa(
        student=_Student(),
        trainset=train,
        selection_set=selection,
        held_out_set=held_out,
        metric=metric,
        task_lm=dspy.LM(model="task-model"),
        reflection_lm=dspy.LM(model="reflection-model"),
        task_model_id="task-model-v1",
        reflection_model_id="reflection-model-v1",
        strict_proof=proof,
        strict_policy=_strict_policy(),
        dataset_sha256="b" * 64,
        scorer_sha256="c" * 64,
        capability_coverage_sha256="d" * 64,
        capability_coverage_verified=True,
        seed=42,
        max_metric_calls=160,
        evidence_root=tmp_path,
        run_id="production-run",
        held_out_evaluator=lambda _program, _rows: {
            "complete": True,
            "quality": 0.8,
            "p95_seconds": 1.0,
            "cost_usd": 2.0,
        },
        fresh_process_reload=lambda digest: digest,
    )

    assert result["schema"] == "fleet.phase6-gepa-campaign/v1"
    assert result["promotion_eligible"] is False
    assert result["detailed_results"]["total_metric_calls"] == 160
    assert (tmp_path / "production-run" / "production-result.json").is_file()


def test_authoritative_gepa_rejects_budget_not_bound_to_eight_plus_twenty_four_rounds(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_LIVE", "1")
    proof = validate_strict_daytona_proof(_block_all_receipt())
    with pytest.raises(OptimizationPreflightError, match=r"8\+24-round"):
        run_authoritative_gepa(
            student=_Student(),
            trainset=[{}] * 15,
            selection_set=[{}] * 5,
            held_out_set=[{}] * 5,
            metric=TrustedGEPAFeedbackMetric(_student_metric, "c" * 64),
            task_lm=dspy.LM(model="task-model"),
            reflection_lm=dspy.LM(model="reflection-model"),
            task_model_id="task-model-v1",
            reflection_model_id="reflection-model-v1",
            strict_proof=proof,
            strict_policy=_strict_policy(),
            dataset_sha256="b" * 64,
            scorer_sha256="c" * 64,
            capability_coverage_sha256="d" * 64,
            capability_coverage_verified=True,
            seed=42,
            max_metric_calls=5,
            evidence_root=tmp_path,
            run_id="budget-fail",
            held_out_evaluator=lambda *_args: {"complete": True, "quality": 1, "p95_seconds": 1, "cost_usd": 1},
            fresh_process_reload=lambda digest: digest,
        )


def test_authoritative_gepa_rejects_strict_proof_for_different_policy(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FLEET_LIVE", "1")
    proof = validate_strict_daytona_proof(_block_all_receipt())
    with pytest.raises(OptimizationPreflightError, match="does not match evaluator policy"):
        run_authoritative_gepa(
            student=_Student(),
            trainset=[{}] * 15,
            selection_set=[{}] * 5,
            held_out_set=[{}] * 5,
            metric=TrustedGEPAFeedbackMetric(_student_metric, "c" * 64),
            task_lm=dspy.LM(model="task-model"),
            reflection_lm=dspy.LM(model="reflection-model"),
            task_model_id="task-model-v1",
            reflection_model_id="reflection-model-v1",
            strict_proof=proof,
            strict_policy=StrictDaytonaPolicyBinding(
                policy_id="different-policy",
                snapshot="fleet-safe-v1",
                gateway_domains=(),
                auto_stop_interval_seconds=300,
                auto_delete_interval_seconds=0,
            ),
            dataset_sha256="b" * 64,
            scorer_sha256="c" * 64,
            capability_coverage_sha256="d" * 64,
            capability_coverage_verified=True,
            seed=42,
            max_metric_calls=160,
            evidence_root=tmp_path,
            run_id="policy-fail",
            held_out_evaluator=lambda *_args: {"complete": True, "quality": 1, "p95_seconds": 1, "cost_usd": 1},
            fresh_process_reload=lambda digest: digest,
        )


def test_authoritative_gepa_rejects_scorer_identity_mismatch(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FLEET_LIVE", "1")
    proof = validate_strict_daytona_proof(_block_all_receipt())
    with pytest.raises(OptimizationPreflightError, match="scorer identity"):
        run_authoritative_gepa(
            student=_Student(),
            trainset=[{}] * 15,
            selection_set=[{}] * 5,
            held_out_set=[{}] * 5,
            metric=TrustedGEPAFeedbackMetric(_student_metric, "a" * 64),
            task_lm=dspy.LM(model="task-model"),
            reflection_lm=dspy.LM(model="reflection-model"),
            task_model_id="task-model-v1",
            reflection_model_id="reflection-model-v1",
            strict_proof=proof,
            strict_policy=_strict_policy(),
            dataset_sha256="b" * 64,
            scorer_sha256="c" * 64,
            capability_coverage_sha256="d" * 64,
            capability_coverage_verified=True,
            seed=42,
            max_metric_calls=160,
            evidence_root=tmp_path,
            run_id="scorer-fail",
            held_out_evaluator=lambda *_args: {"complete": True, "quality": 1, "p95_seconds": 1, "cost_usd": 1},
            fresh_process_reload=lambda digest: digest,
        )


# --- from test_mlflow_observability.py --------------------------------
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
