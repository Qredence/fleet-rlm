"""Tests for runtime/quality/optimization_runner.py shared GEPA pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import dspy
import pytest

from fleet_rlm.runtime.quality.module_registry import ModuleOptimizationSpec
from fleet_rlm.runtime.quality.optimization_runner import (
    _capture_prompt_snapshots,
    _evaluate_per_example,
    run_module_optimization,
)


# -- Fakes for GEPA and Evaluate ---------------------------------------------


class _FakeSignature:
    """Fake signature with an instructions attribute."""

    def __init__(self, instructions: str = "Do the thing.") -> None:
        self.instructions = instructions


class _FakePredictor:
    """Fake predictor with a .signature.instructions chain."""

    def __init__(self, instructions: str = "Do the thing.") -> None:
        self.signature = _FakeSignature(instructions)


class _FakeModule:
    """Fake DSPy module with named_predictors()."""

    def __init__(
        self, predictors: list[tuple[str, _FakePredictor]] | None = None
    ) -> None:
        self._predictors = predictors or []

    def named_predictors(self) -> list[tuple[str, _FakePredictor]]:
        return self._predictors


class _FakeOptimizedProgram(_FakeModule):
    """Fake optimized program returned by FakeGEPA."""

    def __init__(
        self,
        predictors: list[tuple[str, _FakePredictor]] | None = None,
    ) -> None:
        super().__init__(predictors)
        self.saved_path: str | None = None

    def save(self, path: str) -> None:
        self.saved_path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("{}")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return MagicMock()


class _FakeGEPA:
    """Fake GEPA optimizer that captures compile args."""

    def __init__(
        self,
        metric: Any = None,
        auto: str | None = None,
        reflection_lm: Any = None,
        **kwargs: Any,
    ) -> None:
        self.metric = metric
        self.auto = auto
        self.reflection_lm = reflection_lm

    def compile(
        self, program: Any, trainset: Any = None, valset: Any = None
    ) -> _FakeOptimizedProgram:
        return _FakeOptimizedProgram()


class _FakeEvaluate:
    """Fake dspy.Evaluate that returns a fixed score."""

    def __init__(self, devset: Any = None, metric: Any = None, **kwargs: Any) -> None:
        pass

    def __call__(self, program: Any) -> float:
        return 0.85


# -- Test helpers -------------------------------------------------------------


def _make_spec(tmp_path: Path) -> ModuleOptimizationSpec:
    """Build a test spec with simple converters."""

    def _converter(rows: list[dict]) -> list[dspy.Example]:
        return [
            dspy.Example(q=str(r.get("q", "")), a=str(r.get("a", ""))).with_inputs("q")
            for r in rows
            if isinstance(r, dict) and "q" in r and "a" in r
        ]

    def _metric_builder() -> Any:
        from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

        def metric(gold: Any, pred: Any, **kw: Any) -> ScoreWithFeedback:
            return ScoreWithFeedback(score=1.0, feedback="ok")

        return metric

    return ModuleOptimizationSpec(
        module_slug="test-mod",
        label="Test Module",
        program_spec="test:Module",
        artifact_filename="test_module.json",
        input_keys=["q"],
        required_dataset_keys=["q", "a"],
        module_factory=lambda: MagicMock(),
        row_converter=_converter,
        metric_builder=_metric_builder,
        metric_name="test_metric",
    )


def _write_dataset(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "dataset.json"
    p.write_text(json.dumps(rows))
    return p


@pytest.fixture(autouse=True)
def _mock_dspy_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure DSPy GEPA/Evaluate and LM setup are mocked for all tests."""
    monkeypatch.setattr("dspy.teleprompt.GEPA", _FakeGEPA, raising=False)
    monkeypatch.setattr("dspy.Evaluate", _FakeEvaluate, raising=False)
    # Mock the LM config so tests don't need real API keys
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimization_runner._resolve_reflection_lm",
        lambda: MagicMock(),
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimization_runner._ensure_dspy_configured",
        lambda: None,
    )


# -- Tests: helper functions --------------------------------------------------


class TestCapturePromptSnapshots:
    """Tests for _capture_prompt_snapshots helper."""

    def test_captures_before_snapshots(self) -> None:
        module = _FakeModule(
            [
                ("predict", _FakePredictor("Classify the input.")),
                ("refine", _FakePredictor("Refine the answer.")),
            ]
        )
        result = _capture_prompt_snapshots(module, "before")
        assert len(result) == 2
        assert result[0]["predictor_name"] == "predict"
        assert result[0]["prompt_type"] == "before"
        assert result[0]["prompt_text"] == "Classify the input."
        assert result[1]["predictor_name"] == "refine"
        assert result[1]["prompt_text"] == "Refine the answer."

    def test_captures_after_snapshots(self) -> None:
        module = _FakeModule([("gen", _FakePredictor("Generate code."))])
        result = _capture_prompt_snapshots(module, "after")
        assert len(result) == 1
        assert result[0]["prompt_type"] == "after"

    def test_skips_predictors_without_instructions(self) -> None:
        predictor = _FakePredictor("Has instructions")
        no_sig = MagicMock(spec=[])  # No signature attr
        module = _FakeModule([("good", predictor), ("empty", no_sig)])
        result = _capture_prompt_snapshots(module, "before")
        assert len(result) == 1
        assert result[0]["predictor_name"] == "good"

    def test_returns_empty_on_exception(self) -> None:
        """Best-effort: any error returns empty list without raising."""
        module = MagicMock()
        module.named_predictors.side_effect = RuntimeError("boom")
        result = _capture_prompt_snapshots(module, "before")
        assert result == []

    def test_returns_empty_for_no_predictors(self) -> None:
        module = _FakeModule([])
        result = _capture_prompt_snapshots(module, "before")
        assert result == []


class TestEvaluatePerExample:
    """Tests for _evaluate_per_example helper."""

    def test_scores_all_examples(self) -> None:
        examples = [
            dspy.Example(q="q1", a="a1").with_inputs("q"),
            dspy.Example(q="q2", a="a2").with_inputs("q"),
        ]
        program = MagicMock(return_value=MagicMock())

        def metric(gold: Any, pred: Any) -> float:
            return 0.75

        results = _evaluate_per_example(program, examples, metric)
        assert len(results) == 2
        assert all(r["score"] == 0.75 for r in results)
        assert results[0]["example_index"] == 0
        assert results[1]["example_index"] == 1
        # input_data is JSON-serialized
        parsed = json.loads(results[0]["input_data"])
        assert parsed["q"] == "q1"

    def test_handles_score_with_feedback(self) -> None:
        """Unwraps ScoreWithFeedback objects to get the numeric score."""
        from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

        examples = [dspy.Example(q="q1", a="a1").with_inputs("q")]
        program = MagicMock(return_value=MagicMock())

        def metric(gold: Any, pred: Any) -> ScoreWithFeedback:
            return ScoreWithFeedback(score=0.9, feedback="good")

        results = _evaluate_per_example(program, examples, metric)
        assert len(results) == 1
        assert results[0]["score"] == 0.9

    def test_individual_failure_scores_zero(self) -> None:
        """A failing example gets score 0.0 without killing the batch."""
        examples = [
            dspy.Example(q="ok", a="a1").with_inputs("q"),
            dspy.Example(q="boom", a="a2").with_inputs("q"),
        ]
        call_count = 0

        def _program(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("inference failed")
            return MagicMock()

        def metric(gold: Any, pred: Any) -> float:
            return 1.0

        results = _evaluate_per_example(_program, examples, metric)
        assert len(results) == 2
        assert results[0]["score"] == 1.0
        assert results[1]["score"] == 0.0

    def test_empty_validation_set(self) -> None:
        results = _evaluate_per_example(MagicMock(), [], lambda g, p: 1.0)
        assert results == []

    def test_expected_output_extraction(self) -> None:
        """Extracts 'answer' or 'output' attribute from the example."""
        ex = dspy.Example(q="q1", answer="correct").with_inputs("q")
        program = MagicMock(return_value=MagicMock())
        results = _evaluate_per_example(program, [ex], lambda g, p: 1.0)
        assert results[0]["expected_output"] == "correct"


# -- Tests: integration -------------------------------------------------------


def test_run_module_optimization_end_to_end(tmp_path: Path) -> None:
    spec = _make_spec(tmp_path)
    dataset_path = _write_dataset(
        tmp_path, [{"q": f"q{i}", "a": f"a{i}"} for i in range(10)]
    )
    output_path = tmp_path / "output" / "test_module.json"

    result = run_module_optimization(
        spec,
        dataset_path=dataset_path,
        output_path=output_path,
        train_ratio=0.8,
        auto="light",
    )

    assert result["train_examples"] == 8
    assert result["validation_examples"] == 2
    # Per-example eval with metric returning score=1.0 → average = 1.0
    assert result["validation_score"] == 1.0
    assert result["optimizer"] == "GEPA"
    assert result["program_spec"] == "test:Module"
    assert Path(result["output_path"]).name == "test_module.json"
    assert Path(result["manifest_path"]).exists()

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["train_examples"] == 8
    assert manifest["metric"] == "test_metric"


def test_run_module_optimization_no_validation(tmp_path: Path) -> None:
    spec = _make_spec(tmp_path)
    dataset_path = _write_dataset(
        tmp_path, [{"q": f"q{i}", "a": f"a{i}"} for i in range(5)]
    )

    result = run_module_optimization(
        spec,
        dataset_path=dataset_path,
        output_path=tmp_path / "out.json",
        train_ratio=1.0,
        auto="light",
    )

    # train_ratio=1.0 with >1 examples still keeps at least 1 for validation
    assert result["train_examples"] == 4
    assert result["validation_examples"] == 1
    assert result["validation_score"] is not None


def test_run_module_optimization_single_example_skips_eval(tmp_path: Path) -> None:
    """With only 1 example, all go to train and eval is skipped."""
    spec = _make_spec(tmp_path)
    dataset_path = _write_dataset(tmp_path, [{"q": "q1", "a": "a1"}])

    result = run_module_optimization(
        spec,
        dataset_path=dataset_path,
        output_path=tmp_path / "out.json",
        train_ratio=0.8,
        auto="light",
    )

    assert result["train_examples"] == 1
    assert result["validation_examples"] == 0
    assert result["validation_score"] is None


def test_run_module_optimization_with_run_id_persists_artifacts(
    tmp_path: Path,
) -> None:
    """When run_id is provided, eval results and snapshots are persisted."""
    spec = _make_spec(tmp_path)
    dataset_path = _write_dataset(
        tmp_path, [{"q": f"q{i}", "a": f"a{i}"} for i in range(10)]
    )

    with patch(
        "fleet_rlm.runtime.quality.optimization_runner._persist_run_artifacts"
    ) as mock_persist:
        run_module_optimization(
            spec,
            dataset_path=dataset_path,
            output_path=tmp_path / "out.json",
            train_ratio=0.8,
            auto="light",
            run_id=42,
        )

        mock_persist.assert_called_once()
        call_args = mock_persist.call_args
        assert call_args[0][0] == 42  # run_id
        per_example = call_args[0][1]
        assert len(per_example) == 2  # 2 validation examples
        assert all(r["score"] == 1.0 for r in per_example)


def test_run_module_optimization_without_run_id_skips_persist(
    tmp_path: Path,
) -> None:
    """When run_id is None (default), _persist_run_artifacts is not called."""
    spec = _make_spec(tmp_path)
    dataset_path = _write_dataset(
        tmp_path, [{"q": f"q{i}", "a": f"a{i}"} for i in range(10)]
    )

    with patch(
        "fleet_rlm.runtime.quality.optimization_runner._persist_run_artifacts"
    ) as mock_persist:
        run_module_optimization(
            spec,
            dataset_path=dataset_path,
            output_path=tmp_path / "out.json",
            train_ratio=0.8,
            auto="light",
        )

        mock_persist.assert_not_called()
