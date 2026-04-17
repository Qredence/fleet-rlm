"""Tests for ``fleet_rlm.runtime.quality.gepa_optimization``."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import dspy

from fleet_rlm.runtime.quality.gepa_optimization import (
    build_gepa_feedback_metric,
    optimize_program_with_gepa,
)


def _gold(text: str) -> SimpleNamespace:
    return SimpleNamespace(assistant_response=text)


def _pred(text: str) -> SimpleNamespace:
    return SimpleNamespace(assistant_response=text)


class TestBuildGepaFeedbackMetric:
    def test_default_metric_returns_score_with_feedback(self) -> None:
        metric = build_gepa_feedback_metric()
        result = metric(_gold("hello"), _pred("hello"))
        # ScoreWithFeedback or float
        from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

        assert isinstance(result, ScoreWithFeedback)
        assert result.score == 1.0
        assert isinstance(result.feedback, str)

    def test_default_metric_mismatch(self) -> None:
        metric = build_gepa_feedback_metric()
        result = metric(_gold("hello"), _pred("goodbye"))
        from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

        assert isinstance(result, ScoreWithFeedback)
        assert result.score < 1.0

    def test_custom_score_fn_scalar(self) -> None:
        def custom(gold, pred, trace=None):
            return 0.42

        metric = build_gepa_feedback_metric(score_fn=custom)
        result = metric(_gold("a"), _pred("b"))
        assert result == 0.42

    def test_custom_score_fn_tuple(self) -> None:
        def custom(gold, pred, trace=None):
            return 0.7, "custom feedback"

        metric = build_gepa_feedback_metric(score_fn=custom)
        result = metric(_gold("a"), _pred("b"))
        from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

        assert isinstance(result, ScoreWithFeedback)
        assert result.score == 0.7
        assert result.feedback == "custom feedback"

    def test_accepts_gepa_extra_args(self) -> None:
        metric = build_gepa_feedback_metric()
        # GEPA passes pred_name and pred_trace
        result = metric(
            _gold("hello"),
            _pred("hello"),
            trace=None,
            pred_name="predict",
            pred_trace=None,
        )
        from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

        assert isinstance(result, (float, ScoreWithFeedback))

    def test_respects_output_key_for_default_metric(self) -> None:
        metric = build_gepa_feedback_metric(output_key="summary")
        result = metric(
            SimpleNamespace(summary="hello"),
            SimpleNamespace(summary="hello"),
        )
        from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

        assert isinstance(result, ScoreWithFeedback)
        assert result.score == 1.0

    def test_dspy_evaluate_accepts_gepa_feedback_metric(self) -> None:
        class Echo(dspy.Module):
            def forward(self, question: str):
                return dspy.Prediction(assistant_response=question)

        example = dspy.Example(
            question="hello",
            assistant_response="hello",
        ).with_inputs("question")
        result = dspy.Evaluate(
            devset=[example],
            metric=build_gepa_feedback_metric(),
        )(Echo())
        assert float(result) == 100.0


class _FakeOptimizedProgram:
    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("{}")


class _FakeGEPA:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def compile(self, program, trainset=None, valset=None):
        return _FakeOptimizedProgram()


class _FakeEvaluate:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __call__(self, program) -> float:
        return 0.91


def test_optimize_program_with_gepa_logs_mlflow_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dataset_path = tmp_path / "annotated-traces.json"
    dataset_path.write_text("[]", encoding="utf-8")
    output_path = tmp_path / "optimized.json"

    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.gepa_optimization.initialize_mlflow",
        lambda config: True,
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.gepa_optimization.load_trace_rows",
        lambda path: [{"question": "hi", "assistant_response": "hello"}],
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.gepa_optimization.rows_to_examples",
        lambda rows, input_keys=None, output_key="assistant_response": [
            "example-a",
            "example-b",
        ],
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.gepa_optimization.split_examples",
        lambda examples, train_ratio=0.8: (["train-example"], ["val-example"]),
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.gepa_optimization.build_program",
        lambda program_spec: MagicMock(name="program"),
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.gepa_optimization.build_gepa_feedback_metric",
        lambda output_key="assistant_response", score_fn=None: MagicMock(name="metric"),
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.gepa_optimization.GEPA",
        _FakeGEPA,
    )
    monkeypatch.setattr(dspy, "Evaluate", _FakeEvaluate)
    monkeypatch.setattr(
        "fleet_rlm.runtime.config.get_delegate_lm_from_env",
        lambda: MagicMock(name="delegate-lm"),
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.config.get_planner_lm_from_env",
        lambda: None,
    )

    ctx_mock = MagicMock()
    ctx_mock.__enter__ = MagicMock(return_value=ctx_mock)
    ctx_mock.__exit__ = MagicMock(return_value=False)

    with (
        patch("mlflow.start_run", return_value=ctx_mock, create=True) as start_run_mock,
        patch("mlflow.log_metric", create=True) as log_metric_mock,
        patch("mlflow.log_params", create=True) as log_params_mock,
        patch("mlflow.set_tags", create=True) as set_tags_mock,
    ):
        result = optimize_program_with_gepa(
            dataset_path=dataset_path,
            program_spec="pkg.module:build_program",
            output_path=output_path,
            auto="medium",
            train_ratio=0.75,
            source="api_background",
        )

    start_run_mock.assert_called_once()
    log_params_mock.assert_called_once_with(
        {
            "gepa.auto": "medium",
            "gepa.train_ratio": 0.75,
            "gepa.dataset_name": "annotated-traces.json",
        }
    )
    set_tags_mock.assert_called_once_with(
        {
            "fleet.optimizer": "GEPA",
            "fleet.optimization_source": "api_background",
            "fleet.program_spec": "pkg.module:build_program",
        }
    )
    log_metric_mock.assert_any_call("gepa_train_examples", 1)
    log_metric_mock.assert_any_call("gepa_validation_examples", 1)
    log_metric_mock.assert_any_call("gepa_validation_score", 0.91)
    assert result["optimizer"] == "GEPA"
    assert result["validation_score"] == 0.91
