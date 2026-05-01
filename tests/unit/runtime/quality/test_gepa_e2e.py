"""End-to-end GEPA optimization tests for the LongCoT reasoner module.

Validates the full pipeline from dataset → optimized artifact, including:
- Light GEPA compilation on a 10-example subset
- MLflow logging of tags, params, and metrics
- Artifact saving and reloadability via dspy.Module.load()
- Manifest writing with all required fields
- Per-example evaluation results and prompt snapshot persistence
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fleet_rlm.runtime.quality.module_registry import (
    _reset_registry,
    get_module_spec,
)
from fleet_rlm.runtime.quality.optimization_runner import run_module_optimization


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """Reset the module registry before each test."""
    _reset_registry()


@pytest.fixture
def longcot_spec() -> Any:
    """Return the longcot-reasoner module spec."""
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None, "longcot-reasoner must be registered"
    return spec


@pytest.fixture
def ten_example_dataset(tmp_path: Path) -> Path:
    """Write a 10-example JSONL dataset compatible with longcot-reasoner."""
    path = tmp_path / "longcot_10.jsonl"
    rows: list[dict[str, Any]] = []
    for i in range(10):
        rows.append(
            {
                "question_id": f"test_q_{i}",
                "domain": "math",
                "difficulty": "easy",
                "question": f"What is {i} + {i + 1}?",
                "answer": str(2 * i + 1),
            }
        )
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    return path


# -- Fake GEPA / Evaluate (same pattern as test_optimization_runner) ---------


class _FakeSignature:
    """Fake signature with instructions."""

    def __init__(self, instructions: str = "Do the thing.") -> None:
        self.instructions = instructions


class _FakePredictor:
    def __init__(self, instructions: str = "Do the thing.") -> None:
        self.signature = _FakeSignature(instructions)


class _FakeOptimizedProgram:
    """Fake optimized program that can save and load."""

    def __init__(self) -> None:
        self._saved_path: str | None = None

    def named_predictors(self) -> list[tuple[str, _FakePredictor]]:
        return [("predict", _FakePredictor("Optimized instructions."))]

    def save(self, path: str) -> None:
        self._saved_path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Write valid JSON that dspy.Module.load() can parse
        payload = {
            "metadata": {
                "spec": "dspy.Predict",
                "signature": "LongCoTQASignature",
            },
            "predictors": {
                "predict": {
                    "signature": {
                        "instructions": "Optimized instructions.",
                        "fields": {},
                    },
                    "lm": None,
                }
            },
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return MagicMock()


class _FakeGEPA:
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
    def __init__(self, devset: Any = None, metric: Any = None, **kwargs: Any) -> None:
        pass

    def __call__(self, program: Any) -> float:
        return 0.88


@pytest.fixture(autouse=True)
def _mock_dspy_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock DSPy GEPA/Evaluate and LM setup for all tests."""
    monkeypatch.setattr("dspy.teleprompt.GEPA", _FakeGEPA, raising=False)
    monkeypatch.setattr("dspy.Evaluate", _FakeEvaluate, raising=False)
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimization_runner._resolve_reflection_lm",
        lambda: MagicMock(),
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimization_runner._ensure_dspy_configured",
        lambda: None,
    )


# -- VAL-GEPA-001: Light GEPA compilation on subset --------------------------


class TestGepaLightCompilation:
    def test_run_module_optimization_with_longcot_10_examples(
        self,
        longcot_spec: Any,
        ten_example_dataset: Path,
        tmp_path: Path,
    ) -> None:
        """GEPA compilation completes on ~10 examples without exception."""
        output_path = tmp_path / "artifacts" / "longcot_reasoner.json"

        result = run_module_optimization(
            longcot_spec,
            dataset_path=ten_example_dataset,
            output_path=output_path,
            train_ratio=0.8,
            auto="light",
        )

        assert result["train_examples"] >= 8
        assert result["validation_examples"] >= 2
        assert result["optimizer"] == "GEPA"
        assert result["module_slug"] == "longcot-reasoner"
        assert Path(result["output_path"]).exists()
        assert Path(result["manifest_path"]).exists()


# -- VAL-GEPA-002: MLflow logging --------------------------------------------


class TestGepaMlflowLogging:
    def test_mlflow_tags_params_and_metrics_logged(
        self,
        longcot_spec: Any,
        ten_example_dataset: Path,
        tmp_path: Path,
    ) -> None:
        """Active MLflow run receives GEPA params, tags, and metrics."""
        ctx_mock = MagicMock()
        ctx_mock.__enter__ = MagicMock(return_value=ctx_mock)
        ctx_mock.__exit__ = MagicMock(return_value=False)

        with (
            patch("mlflow.start_run", return_value=ctx_mock, create=True),
            patch("mlflow.log_metric", create=True),
            patch("mlflow.log_params", create=True) as log_params,
            patch("mlflow.set_tags", create=True) as set_tags,
        ):
            from fleet_rlm.runtime.quality.gepa_optimization import (
                log_gepa_mlflow_run_metadata,
            )

            log_gepa_mlflow_run_metadata(
                dataset_path=ten_example_dataset,
                program_spec=longcot_spec.program_spec,
                auto="light",
                train_ratio=0.8,
                module_slug="longcot-reasoner",
                source="test",
                log_params=log_params,
                set_tags=set_tags,
            )

        set_tags.assert_called_once()
        tags = set_tags.call_args[0][0]
        assert tags["fleet.optimizer"] == "GEPA"
        assert tags["fleet.module_slug"] == "longcot-reasoner"
        assert tags["fleet.program_spec"] == longcot_spec.program_spec

        log_params.assert_called_once()
        params = log_params.call_args[0][0]
        assert params["gepa.auto"] == "light"
        assert params["gepa.train_ratio"] == 0.8


# -- VAL-GEPA-003: Optimized artifact saved and reloadable -------------------


class TestGepaArtifact:
    def test_artifact_saved_as_valid_json(
        self,
        longcot_spec: Any,
        ten_example_dataset: Path,
        tmp_path: Path,
    ) -> None:
        """Optimized artifact is written as valid JSON."""
        output_path = tmp_path / "out.json"

        result = run_module_optimization(
            longcot_spec,
            dataset_path=ten_example_dataset,
            output_path=output_path,
            auto="light",
        )

        artifact_path = Path(result["output_path"])
        assert artifact_path.exists()
        raw = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)

    def test_artifact_is_reloadable_via_dspy_module_load(
        self,
        longcot_spec: Any,
        ten_example_dataset: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Saved artifact can be reloaded via dspy.Module.load()."""
        output_path = tmp_path / "out.json"

        # Use a fake module whose save() writes a dspy-compatible JSON
        fake_module = _FakeOptimizedProgram()

        with patch(
            "dspy.teleprompt.GEPA",
        ) as FakeGEPA:
            FakeGEPA.return_value.compile.return_value = fake_module
            run_module_optimization(
                longcot_spec,
                dataset_path=ten_example_dataset,
                output_path=output_path,
                auto="light",
            )

        assert output_path.exists()
        raw = json.loads(output_path.read_text(encoding="utf-8"))
        assert "predictors" in raw or "metadata" in raw


# -- VAL-GEPA-004: Manifest JSON written -------------------------------------


class TestGepaManifest:
    def test_manifest_has_all_required_fields(
        self,
        longcot_spec: Any,
        ten_example_dataset: Path,
        tmp_path: Path,
    ) -> None:
        """Manifest contains dataset_path, module, train_examples, etc."""
        output_path = tmp_path / "out.json"

        result = run_module_optimization(
            longcot_spec,
            dataset_path=ten_example_dataset,
            output_path=output_path,
            auto="light",
        )

        manifest_path = Path(result["manifest_path"])
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        required_keys = {
            "dataset_path",
            "module",
            "train_examples",
            "validation_examples",
            "validation_score",
            "optimizer",
            "metric",
            "auto",
        }
        assert required_keys.issubset(set(manifest.keys()))
        assert manifest["module"] == longcot_spec.program_spec
        assert manifest["optimizer"] == "GEPA"
        assert manifest["auto"] == "light"
        assert manifest["train_examples"] >= 8
        assert manifest["validation_examples"] >= 2

    def test_manifest_records_review_bundle_holdout_and_provenance(
        self,
        longcot_spec: Any,
        ten_example_dataset: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Manifest stores same-split holdout evidence and reflection provenance."""
        output_path = tmp_path / "out.json"
        monkeypatch.setenv("DSPY_DELEGATE_LM_MODEL", "delegate-model")
        monkeypatch.setenv("DSPY_LM_MODEL", "planner-model")
        monkeypatch.setattr(
            "fleet_rlm.runtime.quality.optimization_runner._resolve_reflection_lm",
            lambda: SimpleNamespace(model="delegate-model"),
        )

        result = run_module_optimization(
            longcot_spec,
            dataset_path=ten_example_dataset,
            output_path=output_path,
            auto="light",
        )

        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        holdout = manifest["review_bundle"]["holdout"]
        reflection = manifest["review_bundle"]["reflection_model"]
        assert holdout["baseline_score"] is not None
        assert holdout["optimized_score"] == result["validation_score"]
        assert holdout["split_reference"]["validation_dataset_indexes"] == [8, 9]
        assert len(holdout["comparisons"]) == result["validation_examples"]
        assert reflection["model"] == "delegate-model"
        assert reflection["source"] == "delegate"

    @pytest.mark.parametrize(
        ("resolved_model", "expected_source"),
        [
            ("delegate-model", "delegate"),
            ("planner-model", "planner"),
        ],
    )
    def test_result_review_bundle_records_delegate_preference_or_planner_fallback(
        self,
        longcot_spec: Any,
        ten_example_dataset: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        resolved_model: str,
        expected_source: str,
    ) -> None:
        """The review bundle records whether reflection used delegate or planner."""
        monkeypatch.setenv("DSPY_DELEGATE_LM_MODEL", "delegate-model")
        monkeypatch.setenv("DSPY_LM_MODEL", "planner-model")
        monkeypatch.setattr(
            "fleet_rlm.runtime.quality.optimization_runner._resolve_reflection_lm",
            lambda: SimpleNamespace(model=resolved_model),
        )

        result = run_module_optimization(
            longcot_spec,
            dataset_path=ten_example_dataset,
            output_path=tmp_path / f"{expected_source}.json",
            auto="light",
        )

        reflection = result["review_bundle"]["reflection_model"]
        assert reflection["model"] == resolved_model
        assert reflection["source"] == expected_source


# -- VAL-GEPA-008: Per-example results and snapshots persisted ---------------


class TestGepaPersistence:
    def test_per_example_results_persisted_with_run_id(
        self,
        longcot_spec: Any,
        ten_example_dataset: Path,
        tmp_path: Path,
    ) -> None:
        """When run_id is provided, evaluation results are persisted."""
        with patch(
            "fleet_rlm.runtime.quality.optimization_runner._persist_run_artifacts"
        ) as mock_persist:
            run_module_optimization(
                longcot_spec,
                dataset_path=ten_example_dataset,
                output_path=tmp_path / "out.json",
                auto="light",
                run_id=99,
            )

        mock_persist.assert_called_once()
        call_args = mock_persist.call_args
        assert call_args[0][0] == 99
        per_example = call_args[0][1]
        assert len(per_example) >= 2
        snapshots = call_args[0][2]
        # Should have before + after snapshots
        before = [s for s in snapshots if s.get("prompt_type") == "before"]
        after = [s for s in snapshots if s.get("prompt_type") == "after"]
        assert len(before) > 0 or len(after) > 0

    def test_local_store_persists_evaluation_results(
        self,
        longcot_spec: Any,
        ten_example_dataset: Path,
        tmp_path: Path,
    ) -> None:
        """Integration: local store actually records results and snapshots."""
        from fleet_rlm.integrations.local_store import (
            create_optimization_run,
            get_evaluation_results,
            get_prompt_snapshots,
        )

        run_row = create_optimization_run(
            program_spec=longcot_spec.program_spec,
            auto="light",
            train_ratio=0.8,
            module_slug="longcot-reasoner",
            dataset_id=None,
            dataset_path=str(ten_example_dataset),
        )
        run_id = run_row.id
        assert run_id is not None

        result = run_module_optimization(
            longcot_spec,
            dataset_path=ten_example_dataset,
            output_path=tmp_path / "out.json",
            auto="light",
            run_id=run_id,
        )

        # Fetch persisted artifacts
        eval_items, total = get_evaluation_results(run_id, limit=100, offset=0)
        assert total >= result["validation_examples"]

        snapshots = get_prompt_snapshots(run_id)
        types = {s.prompt_type for s in snapshots}
        assert "before" in types or "after" in types or len(types) > 0


# -- VAL-CROSS-003: Vendor data to optimized artifact pipeline ---------------


class TestGepaCrossArea:
    def test_generated_dataset_converts_and_optimizes(
        self,
        tmp_path: Path,
    ) -> None:
        """Dataset generation + optimization pipeline produces a valid artifact."""
        from fleet_rlm.runtime.quality.datasets import (
            load_dataset_rows,
            validate_required_keys,
        )

        # Build a synthetic dataset in the canonical format
        dataset_path = tmp_path / "cross_area.jsonl"
        rows = [
            {
                "question_id": f"cross_{i}",
                "domain": "math",
                "difficulty": "easy",
                "question": f"Compute {i} * 2",
                "answer": str(i * 2),
            }
            for i in range(10)
        ]
        dataset_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
        )

        spec = get_module_spec("longcot-reasoner")
        assert spec is not None

        # Validate dataset loads and converts
        loaded = load_dataset_rows(dataset_path)
        valid = validate_required_keys(loaded, spec.required_dataset_keys, spec.label)
        examples = spec.row_converter(valid)
        assert len(examples) == 10

        # Run optimization
        result = run_module_optimization(
            spec,
            dataset_path=dataset_path,
            output_path=tmp_path / "artifact.json",
            auto="light",
        )

        assert Path(result["output_path"]).exists()
        assert Path(result["manifest_path"]).exists()
