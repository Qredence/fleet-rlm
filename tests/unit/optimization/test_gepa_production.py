"""Production GEPA orchestration and development MLflow correlation contracts.

* ``test_gepa_production.py``: Production GEPA orchestration contracts remain strict and reproducible.
* ``test_mlflow_observability.py``: Unit contracts for development GEPA MLflow correlation.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import dspy
import pytest

from fleet_rlm.optimization.dataset import EXPORT_SCHEMA
from fleet_rlm.optimization.evidence import StrictDaytonaPolicyBinding, validate_strict_daytona_proof
from fleet_rlm.optimization.gepa_runner import (
    CandidateRoundBudget,
    OptimizationPreflightError,
    initialize_preflight_evidence,
    preflight,
    require_live_execution_capability,
    run_authoritative_gepa,
    run_development_smoke,
)
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


class _IncompatibleStudent(dspy.Module):
    def __init__(self) -> None:
        super().__init__()
        self.other_predict = dspy.Predict("question -> answer")


def build_baseline_student() -> _Student:
    """Importable factory used by the state-only candidate reload process."""
    return _Student()


def build_incompatible_student() -> _IncompatibleStudent:
    """Return an importable but structurally incompatible student."""
    return _IncompatibleStudent()


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
        student.predict.signature = student.predict.signature.with_instructions(
            "Verify typed submissions and submit validated Python results."
        )
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

    held_out_instructions = []

    def evaluate_reloaded(program, rows):
        assert len(rows) == 5
        held_out_instructions.append(program.predict.signature.instructions)
        return {
            "complete": True,
            "quality": 0.8,
            "p95_seconds": 1.0,
            "cost_usd": 2.0,
        }

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
        held_out_evaluator=evaluate_reloaded,
        student_factory="tests.unit.optimization.test_gepa_production:build_baseline_student",
    )

    assert result["schema"] == "fleet.phase6-gepa-campaign/v1"
    assert result["promotion_eligible"] is False
    assert result["detailed_results"]["total_metric_calls"] == 160
    assert held_out_instructions == ["Verify typed submissions and submit validated Python results."]
    assert result["module_state_sha256"] == result["fresh_process_state_sha256"]
    assert result["instruction_sha256"] == result["fresh_process_instruction_sha256"]
    assert (tmp_path / "production-run" / "production-result.json").is_file()
    receipt_text = (tmp_path / "production-run" / "production-result.json").read_text()
    assert "Verify typed submissions" not in receipt_text


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
            student_factory="tests.unit.optimization.test_gepa_production:build_baseline_student",
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
            student_factory="tests.unit.optimization.test_gepa_production:build_baseline_student",
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
            student_factory="tests.unit.optimization.test_gepa_production:build_baseline_student",
        )


def test_authoritative_gepa_fails_closed_when_saved_state_is_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setattr(dspy, "GEPA", _GEPA)
    from fleet_rlm.optimization import gepa_runner

    original_reload = gepa_runner._fresh_process_reload

    def remove_candidate_state(state_path, **kwargs):
        state_path.unlink()
        return original_reload(state_path, **kwargs)

    monkeypatch.setattr(gepa_runner, "_fresh_process_reload", remove_candidate_state)
    with pytest.raises(OptimizationPreflightError, match="reconstruction failed"):
        _run_test_campaign(
            tmp_path, student_factory="tests.unit.optimization.test_gepa_production:build_baseline_student"
        )
    receipt = (tmp_path / "reload-fail" / "production-result.json").read_text()
    assert json.loads(receipt)["state"] == "failed"


def test_student_factory_arguments_reject_credentials_without_blocking_generation_limits():
    from fleet_rlm.optimization.gepa_runner import _validated_factory_kwargs

    factory = "tests.unit.optimization.test_gepa_production:build_baseline_student"
    with pytest.raises(OptimizationPreflightError, match="credential-shaped"):
        _validated_factory_kwargs(factory, {"api_key": "must-not-be-passed"})
    assert _validated_factory_kwargs(factory, {"max_tokens": 2048}) == {"max_tokens": 2048}


def _run_test_campaign(tmp_path: Path, *, student_factory: str):
    proof = validate_strict_daytona_proof(_block_all_receipt())
    return run_authoritative_gepa(
        student=_Student(),
        trainset=[{"question": f"train-{index}"} for index in range(15)],
        selection_set=[{"question": f"selection-{index}"} for index in range(5)],
        held_out_set=[{"question": f"held-out-{index}"} for index in range(5)],
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
        max_metric_calls=160,
        evidence_root=tmp_path,
        run_id="reload-fail",
        held_out_evaluator=lambda *_args: {
            "complete": True,
            "quality": 0.8,
            "p95_seconds": 1.0,
            "cost_usd": 2.0,
        },
        student_factory=student_factory,
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
    mlflow.__path__ = []  # type: ignore[attr-defined]
    mlflow.start_span = lambda **_kwargs: Context()  # type: ignore[attr-defined]
    mlflow.get_last_active_trace_id = lambda: "trace-123"  # type: ignore[attr-defined]
    mlflow.update_current_trace = lambda **kwargs: calls.trace_updates.append(kwargs)  # type: ignore[attr-defined]
    entities = ModuleType("mlflow.entities")
    entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]
    mlflow_genai = ModuleType("mlflow.genai")
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.genai", mlflow_genai)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)
    monkeypatch.setattr("fleet_rlm.config.loader.load_runtime_settings", object)
    observability_path = str(Path(__file__).parents[3] / "src" / "fleet_rlm" / "observability")
    observability = ModuleType("fleet_rlm.observability")
    observability.__path__ = [observability_path]  # type: ignore[attr-defined]
    tracing = ModuleType("fleet_rlm.observability.tracing")
    tracing.configure_tracing = lambda _settings: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fleet_rlm.observability", observability)
    monkeypatch.setitem(sys.modules, "fleet_rlm.observability.tracing", tracing)

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


# --- from test_gepa_runner.py -----------------------------------------
_USD_CAP_MARKERS = ("max_total_cost_usd", "max_token_cost", "max-total-cost-usd", "max_reflection_cost")


def _export() -> dict:
    return {
        "schema": EXPORT_SCHEMA,
        "records": [
            {
                "record_id": f"r-{index:03d}",
                "task": {"query": f"synthetic question {index}"},
                "output_contract": {"schema": "answer-v1"},
                "expectations": {"expected_response": "synthetic"},
                "provenance": {"redaction_version": "v1"},
            }
            for index in range(25)
        ],
    }


def _write_export(tmp_path: Path) -> Path:
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(_export()), encoding="utf-8")
    return export_path


def test_preflight_translates_candidate_rounds_and_hides_sealed_ids(tmp_path: Path) -> None:
    receipt = preflight(export_path=_write_export(tmp_path), split_seed=9)

    assert receipt["gepa_evaluator_call_budget"] == {"exploration": 40, "continuation": 120, "total": 160}
    assert receipt["release_blocked"] is True
    assert "r-" not in str(receipt["split"]["sealed_test"])
    for marker in _USD_CAP_MARKERS:
        assert marker not in str(receipt)


def test_preflight_rejects_obsolete_usd_reflection_cost_cap(tmp_path: Path) -> None:
    """The unsupported USD reflection-cost cap is removed, not tolerated."""
    export_path = _write_export(tmp_path)

    with pytest.raises(TypeError):
        preflight(export_path=export_path, split_seed=0, max_total_cost_usd=1.0)  # type: ignore[call-arg]

    for entrypoint in (preflight, run_development_smoke):
        parameters = inspect.signature(entrypoint).parameters
        for marker in ("max_total_cost_usd", "max_token_cost", "max_reflection_cost"):
            assert marker not in parameters


def test_run_development_smoke_rejects_obsolete_usd_reflection_cost_cap(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        run_development_smoke(  # type: ignore[call-arg]
            export_path=_write_export(tmp_path),
            split_seed=0,
            max_metric_calls=1,
            evidence_root=tmp_path / "evidence",
            run_id="run-1",
            max_total_cost_usd=1.0,
        )


def test_smoke_requires_bounded_metric_call_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    with pytest.raises(OptimizationPreflightError, match="max_metric_calls"):
        run_development_smoke(
            export_path=_write_export(tmp_path),
            split_seed=0,
            max_metric_calls=0,
            evidence_root=tmp_path / "evidence",
            run_id="run-1",
        )


def test_smoke_gepa_dependency_failure_is_bounded_and_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VAL-PKG-024: a missing optimizer dependency fails closed with an install hint."""
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.invalid")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dummy")

    real_import = __import__

    def _blocked_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "gepa" or name.startswith("gepa."):
            raise ImportError("No module named 'gepa'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _blocked_import)

    with pytest.raises(OptimizationPreflightError, match=r"fleet-rlm\[optimize\]") as excinfo:
        run_development_smoke(
            export_path=_write_export(tmp_path),
            split_seed=0,
            max_metric_calls=1,
            evidence_root=tmp_path / "evidence",
            run_id="run-1",
        )
    # No import traceback escapes the boundary.
    assert "Traceback" not in str(excinfo.value)


def test_development_smoke_runs_official_gepa_with_bounded_metric_calls(tmp_path: Path) -> None:
    """Official surface: gepa.optimize + custom adapter, no retired API, no USD cap."""
    gepa = pytest.importorskip("gepa")
    from fleet_rlm.optimization.gepa_runner import _DevelopmentInstructionAdapter

    seed_text = "verify typed answers and submit them with python."
    stub_proposal = "verify typed outputs, submit via python tools, and keep answers concise."

    def _stub_reflection_lm(prompt: str) -> str:
        assert isinstance(prompt, str) and prompt.strip()
        return f"```\n{stub_proposal}\n```"

    train = [{"query": f"train question {index}"} for index in range(4)]
    selection = [{"query": f"selection question {index}"} for index in range(2)]
    requested_cap = 1
    result = gepa.optimize(
        seed_candidate={"system_prompt": seed_text},
        trainset=train,
        valset=selection,
        adapter=_DevelopmentInstructionAdapter(),
        reflection_lm=_stub_reflection_lm,
        reflection_minibatch_size=1,
        max_metric_calls=requested_cap,
        run_dir=str(tmp_path / "gepa-run"),
        seed=3,
        track_best_outputs=False,
        display_progress_bar=False,
    )

    observed = result.total_metric_calls
    assert observed is not None and observed >= requested_cap
    allowed = requested_cap + max(len(selection), 1)
    assert observed <= allowed


def test_live_execution_fails_closed_until_isolation_is_proven() -> None:
    with pytest.raises(OptimizationPreflightError, match="blocked"):
        require_live_execution_capability()
    assert CandidateRoundBudget().evaluator_calls(selection_records=5)["total"] == 160


def test_preflight_evidence_is_write_once(tmp_path: Path) -> None:
    receipt = preflight(export_path=_write_export(tmp_path), split_seed=0)
    evidence = initialize_preflight_evidence(evidence_root=tmp_path, run_id="run-1", receipt=receipt)
    assert (evidence / "manifest.json").is_file()
    with pytest.raises(Exception, match="already exists"):
        initialize_preflight_evidence(evidence_root=tmp_path, run_id="run-1", receipt=receipt)
