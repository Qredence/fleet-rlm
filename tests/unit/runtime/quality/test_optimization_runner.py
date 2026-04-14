"""Tests for runtime/quality/optimization_runner.py shared GEPA pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import dspy
import pytest

from fleet_rlm.runtime.quality.module_registry import ModuleOptimizationSpec
from fleet_rlm.runtime.quality.optimization_runner import run_module_optimization


# -- Fakes for GEPA and Evaluate ---------------------------------------------


class _FakeOptimizedProgram:
    """Fake optimized program returned by FakeGEPA."""

    def __init__(self) -> None:
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


# -- Tests --------------------------------------------------------------------


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
    assert result["validation_score"] == 0.85
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
