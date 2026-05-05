"""Tests for MLflow autologging wiring in the optimization background runner."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult


@pytest.fixture(autouse=True)
def _mock_planner_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fleet_rlm.api.routers.optimization.background._planner_execution_context",
        contextlib.nullcontext,
    )


def _make_persisted_identity() -> IdentityUpsertResult:
    return IdentityUpsertResult(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
    )


def _make_persistence_mock() -> MagicMock:
    """Return a mock persistence object with async protocol methods."""
    persistence = MagicMock()
    persistence.update_optimization_run_phase = AsyncMock(return_value=None)
    persistence.complete_optimization_run = AsyncMock(return_value=None)
    persistence.fail_optimization_run = AsyncMock(return_value=None)
    persistence.save_evaluation_results = AsyncMock(return_value=[])
    persistence.save_prompt_snapshots = AsyncMock(return_value=[])
    return persistence


def _make_runner_kwargs(tmp_path: Path) -> dict:
    dataset = tmp_path / "data.jsonl"
    dataset.write_text('{"question": "hi", "answer": "hello"}\n')
    return {
        "run_id": 1,
        "module_slug": "test-mod",
        "dataset_path": dataset,
        "program_spec": "QA",
        "output_path": None,
        "default_output_root": tmp_path,
        "auto": "light",
        "train_ratio": 0.8,
    }


def _run_local(**kwargs) -> None:
    """Synchronous wrapper for the async run_optimization_background."""
    from fleet_rlm.api.routers.optimization.background import (
        run_optimization_background,
    )

    persistence = kwargs.pop("persistence", None) or _make_persistence_mock()
    persisted_identity = (
        kwargs.pop("persisted_identity", None) or _make_persisted_identity()
    )
    asyncio.run(
        run_optimization_background(
            **kwargs,
            persistence=persistence,
            persisted_identity=persisted_identity,
        )
    )


def _review_bundle_result(tmp_path: Path) -> dict:
    output_path = tmp_path / "artifact.json"
    output_path.write_text('{"predictors": {}}', encoding="utf-8")
    manifest_path = tmp_path / "artifact.manifest.json"
    manifest_path.write_text('{"review_bundle": {"version": 1}}', encoding="utf-8")
    review_bundle = {
        "version": 1,
        "artifact": {
            "path": str(output_path),
            "manifest_path": str(manifest_path),
            "filename": output_path.name,
            "size_bytes": output_path.stat().st_size,
            "loader": "dspy.Module.load",
        },
        "holdout": {
            "split_reference": {
                "train_ratio": 0.8,
                "train_examples": 4,
                "validation_examples": 1,
                "validation_dataset_indexes": [4],
                "validation_range": {"start": 4, "end_exclusive": 5},
            },
            "baseline_score": 0.35,
            "optimized_score": 0.85,
            "score_delta": 0.5,
            "comparisons": [
                {
                    "validation_example_index": 0,
                    "dataset_row_index": 4,
                    "input_data": "{}",
                    "expected_output": "hello",
                    "baseline": {"predicted_output": "hi", "score": 0.35},
                    "optimized": {"predicted_output": "hello", "score": 0.85},
                }
            ],
        },
        "prompt_snapshots": {
            "matched_predictors": [
                {
                    "predictor_name": "predict",
                    "before_prompt": "Before.",
                    "after_prompt": "After.",
                }
            ],
            "total_snapshots": 2,
        },
        "reflection_model": {
            "model": "delegate-model",
            "source": "delegate",
        },
    }
    manifest_path.write_text(
        json.dumps({"review_bundle": review_bundle}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "train_examples": 4,
        "validation_examples": 1,
        "baseline_validation_score": 0.35,
        "validation_score": 0.85,
        "output_path": str(output_path),
        "manifest_path": str(manifest_path),
        "evaluation_results": [
            {
                "example_index": 0,
                "input_data": "{}",
                "expected_output": "hello",
                "predicted_output": "hello",
                "score": 0.85,
            }
        ],
        "prompt_snapshots": [
            {
                "predictor_name": "predict",
                "prompt_type": "before",
                "prompt_text": "Before.",
            },
            {
                "predictor_name": "predict",
                "prompt_type": "after",
                "prompt_text": "After.",
            },
        ],
        "review_bundle": review_bundle,
        "run_metadata": {
            "module_slug": "test-mod",
            "dataset_path": "data.jsonl",
            "review_bundle": review_bundle,
        },
    }


class TestBackgroundRunnerMlflowAvailable:
    """When MLflow is available, initialize + start_run are invoked."""

    def test_mlflow_init_and_start_run_called(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        init_mock = MagicMock(return_value=True)
        ctx_mock = MagicMock()
        ctx_mock.__enter__ = MagicMock(return_value=ctx_mock)
        ctx_mock.__exit__ = MagicMock(return_value=False)
        start_run_mock = MagicMock(return_value=ctx_mock)
        log_metric_mock = MagicMock()
        log_params_mock = MagicMock()
        set_tags_mock = MagicMock()
        log_dict_mock = MagicMock()
        log_artifact_mock = MagicMock()

        fake_result = _review_bundle_result(tmp_path)
        run_mod_mock = MagicMock(return_value=fake_result)
        spec_mock = MagicMock()
        persistence = _make_persistence_mock()

        with (
            patch(
                "fleet_rlm.integrations.observability.mlflow_runtime.initialize_mlflow",
                init_mock,
            ),
            patch("mlflow.start_run", start_run_mock, create=True),
            patch("mlflow.log_metric", log_metric_mock, create=True),
            patch("mlflow.log_params", log_params_mock, create=True),
            patch("mlflow.set_tags", set_tags_mock, create=True),
            patch("mlflow.log_dict", log_dict_mock, create=True),
            patch("mlflow.log_artifact", log_artifact_mock, create=True),
            patch(
                "fleet_rlm.quality.module_registry.get_module_spec",
                return_value=spec_mock,
            ),
            patch(
                "fleet_rlm.quality.optimization_runner.run_module_optimization",
                run_mod_mock,
            ),
        ):
            _run_local(**_make_runner_kwargs(tmp_path), persistence=persistence)

        init_mock.assert_called_once()
        start_run_mock.assert_called_once()
        assert "GEPA::test-mod" in str(start_run_mock.call_args)
        log_params_mock.assert_any_call(
            {
                "gepa.auto": "light",
                "gepa.train_ratio": 0.8,
                "gepa.dataset_name": "data.jsonl",
            }
        )
        log_params_mock.assert_any_call(
            {
                "gepa.validation_split_range": "4:5",
                "gepa.validation_split_count": 1,
            }
        )
        set_tags_mock.assert_any_call(
            {
                "fleet.optimizer": "GEPA",
                "fleet.optimization_source": "api_background",
                "fleet.program_spec": "QA",
                "fleet.module_slug": "test-mod",
            }
        )
        set_tags_mock.assert_any_call(
            {
                "gepa.reflection_model": "delegate-model",
                "gepa.reflection_model_source": "delegate",
                "fleet.optimization_run_id": "1",
            }
        )
        log_metric_mock.assert_any_call("gepa_train_examples", 4)
        log_metric_mock.assert_any_call("gepa_validation_examples", 1)
        log_metric_mock.assert_any_call("gepa_validation_score", 0.85)
        log_metric_mock.assert_any_call("gepa_baseline_validation_score", 0.35)
        log_metric_mock.assert_any_call("gepa_validation_score_delta", 0.5)
        log_dict_mock.assert_called_once_with(
            fake_result["review_bundle"],
            "optimization_review_bundle.json",
        )
        assert log_artifact_mock.call_count == 2
        ctx_mock.__exit__.assert_called_once()


class TestBackgroundRunnerMlflowUnavailable:
    """When MLflow is unavailable, the optimization still succeeds."""

    def test_optimization_succeeds_without_mlflow(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        init_mock = MagicMock(return_value=False)
        persistence = _make_persistence_mock()

        fake_result = _review_bundle_result(tmp_path) | {"validation_score": 0.9}
        run_mod_mock = MagicMock(return_value=fake_result)
        spec_mock = MagicMock()

        with (
            patch(
                "fleet_rlm.integrations.observability.mlflow_runtime.initialize_mlflow",
                init_mock,
            ),
            patch(
                "fleet_rlm.quality.module_registry.get_module_spec",
                return_value=spec_mock,
            ),
            patch(
                "fleet_rlm.quality.optimization_runner.run_module_optimization",
                run_mod_mock,
            ),
        ):
            _run_local(**_make_runner_kwargs(tmp_path), persistence=persistence)

        run_mod_mock.assert_called_once()
        persistence.complete_optimization_run.assert_called_once()
        call_kwargs = persistence.complete_optimization_run.call_args.kwargs
        assert call_kwargs.get("validation_score") == 0.9
        assert call_kwargs.get("metadata_json") == fake_result["run_metadata"]

    def test_mlflow_import_error_does_not_block(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even if mlflow import itself raises, the run proceeds."""
        persistence = _make_persistence_mock()
        fake_result = {
            "train_examples": 2,
            "validation_examples": 1,
            "output_path": None,
            "manifest_path": None,
            "run_metadata": {"module_slug": "test-mod"},
        }
        run_mod_mock = MagicMock(return_value=fake_result)
        spec_mock = MagicMock()

        original_import = (
            __builtins__.__import__
            if hasattr(__builtins__, "__import__")
            else __import__
        )

        def _fail_mlflow(name, *args, **kwargs):
            if name == "mlflow":
                raise ImportError("no mlflow")
            return original_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=_fail_mlflow),
            patch(
                "fleet_rlm.quality.module_registry.get_module_spec",
                return_value=spec_mock,
            ),
            patch(
                "fleet_rlm.quality.optimization_runner.run_module_optimization",
                run_mod_mock,
            ),
        ):
            _run_local(**_make_runner_kwargs(tmp_path), persistence=persistence)

        persistence.complete_optimization_run.assert_called_once()


def test_background_runner_marks_planner_bootstrap_failure_as_failed(
    tmp_path: Path,
) -> None:
    persistence = _make_persistence_mock()

    with (
        patch(
            "fleet_rlm.api.routers.optimization.background._planner_execution_context",
            side_effect=RuntimeError("planner bootstrap failed"),
        ),
        patch(
            "fleet_rlm.quality.module_registry.get_module_spec",
            return_value=MagicMock(),
        ),
    ):
        _run_local(**_make_runner_kwargs(tmp_path), persistence=persistence)

    persistence.fail_optimization_run.assert_called_once()
    call_kwargs = persistence.fail_optimization_run.call_args.kwargs
    assert call_kwargs.get("error") == "planner bootstrap failed"
    persistence.complete_optimization_run.assert_not_called()


def test_local_module_optimization_uses_run_blocking(tmp_path: Path) -> None:
    from fleet_rlm.api.routers.optimization.background import (
        OPTIMIZATION_TIMEOUT_SECONDS,
    )

    persistence = _make_persistence_mock()
    fake_result = {
        "train_examples": 3,
        "validation_examples": 1,
        "validation_score": 0.91,
        "output_path": None,
        "manifest_path": None,
    }
    run_mod_mock = MagicMock(return_value=fake_result)
    spec_mock = MagicMock()
    run_blocking_calls: list[tuple[object, int]] = []

    async def _fake_run_blocking(func, *, timeout):
        run_blocking_calls.append((func, timeout))
        return func()

    with (
        patch(
            "fleet_rlm.quality.module_registry.get_module_spec",
            return_value=spec_mock,
        ),
        patch(
            "fleet_rlm.quality.optimization_runner.run_module_optimization",
            run_mod_mock,
        ),
        patch(
            "fleet_rlm.api.routers.optimization.background.run_blocking",
            _fake_run_blocking,
        ),
    ):
        _run_local(**_make_runner_kwargs(tmp_path), persistence=persistence)

    assert len(run_blocking_calls) == 1
    assert run_blocking_calls[0][1] == OPTIMIZATION_TIMEOUT_SECONDS
    run_mod_mock.assert_called_once()
    assert run_mod_mock.call_args.kwargs.get("run_id") == 1
    persistence.complete_optimization_run.assert_called_once()


def test_local_background_persists_review_artifacts_from_runner_result(
    tmp_path: Path,
) -> None:
    persistence = _make_persistence_mock()
    fake_result = _review_bundle_result(tmp_path)
    run_mod_mock = MagicMock(return_value=fake_result)
    spec_mock = MagicMock()

    with (
        patch(
            "fleet_rlm.quality.module_registry.get_module_spec",
            return_value=spec_mock,
        ),
        patch(
            "fleet_rlm.quality.optimization_runner.run_module_optimization",
            run_mod_mock,
        ),
    ):
        _run_local(**_make_runner_kwargs(tmp_path), persistence=persistence)

    persistence.save_evaluation_results.assert_called_once()
    persistence.save_prompt_snapshots.assert_called_once()
    persistence.complete_optimization_run.assert_called_once()


def test_resolve_dataset_request_accepts_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.api.routers.optimization import _deps
    from fleet_rlm.api.schemas.optimization import GEPAOptimizationRequest

    data_root = tmp_path / "optimization-data"
    dataset = data_root / "nested" / "examples.jsonl"
    dataset.parent.mkdir(parents=True)
    dataset.write_text('{"question": "hi", "answer": "hello"}\n', encoding="utf-8")
    monkeypatch.setattr(_deps, "OPTIMIZATION_DATA_ROOT", data_root.resolve())

    request = GEPAOptimizationRequest(
        dataset_path="nested/examples.jsonl",
        program_spec="qa",
    )

    resolved, dataset_ref = asyncio.run(
        _deps._resolve_dataset_request(
            request,
            persistence=MagicMock(),
            persisted_identity=_make_persisted_identity(),
        )
    )

    assert resolved == dataset.resolve()
    assert dataset_ref == "nested/examples.jsonl"


def test_optimization_timeout_respects_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    from fleet_rlm.api.routers.optimization import _deps

    monkeypatch.setenv("FLEET_RLM_OPTIMIZATION_TIMEOUT_SECONDS", "1234")
    reloaded = importlib.reload(_deps)
    try:
        assert reloaded.OPTIMIZATION_TIMEOUT_SECONDS == 1234
    finally:
        monkeypatch.delenv("FLEET_RLM_OPTIMIZATION_TIMEOUT_SECONDS", raising=False)
        importlib.reload(reloaded)


def test_resolve_dataset_request_rejects_path_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from fleet_rlm.api.routers.optimization import _deps
    from fleet_rlm.api.schemas.optimization import GEPAOptimizationRequest

    data_root = tmp_path / "optimization-data"
    data_root.mkdir(parents=True)
    monkeypatch.setattr(_deps, "OPTIMIZATION_DATA_ROOT", data_root.resolve())

    request = GEPAOptimizationRequest(
        dataset_path="../secrets.jsonl",
        program_spec="qa",
    )

    with pytest.raises(HTTPException, match="Path escapes the allowed data directory."):
        asyncio.run(
            _deps._resolve_dataset_request(
                request,
                persistence=MagicMock(),
                persisted_identity=_make_persisted_identity(),
            )
        )


@pytest.mark.parametrize("module_slug", [None, ""])
def test_custom_program_path_does_not_open_outer_mlflow_run(
    tmp_path: Path,
    module_slug: str | None,
) -> None:
    from fleet_rlm.api.routers.optimization.background import (
        OPTIMIZATION_TIMEOUT_SECONDS,
    )

    start_run_mock = MagicMock()
    persistence = _make_persistence_mock()
    fake_result = {
        "train_examples": 3,
        "validation_examples": 1,
        "validation_score": 0.92,
        "output_path": None,
        "manifest_path": None,
    }
    optimize_mock = MagicMock(return_value=fake_result)
    run_blocking_calls: list[tuple[object, int]] = []

    async def _fake_run_blocking(func, *, timeout):
        run_blocking_calls.append((func, timeout))
        return func()

    kwargs = _make_runner_kwargs(tmp_path)
    kwargs["module_slug"] = module_slug

    with (
        patch("mlflow.start_run", start_run_mock, create=True),
        patch(
            "fleet_rlm.quality.gepa_optimization.optimize_program_with_gepa",
            optimize_mock,
        ),
        patch(
            "fleet_rlm.api.routers.optimization.background.run_blocking",
            _fake_run_blocking,
        ),
    ):
        _run_local(**kwargs, persistence=persistence)

    start_run_mock.assert_not_called()
    assert len(run_blocking_calls) == 1
    assert run_blocking_calls[0][1] == OPTIMIZATION_TIMEOUT_SECONDS
    optimize_mock.assert_called_once()
    assert optimize_mock.call_args.kwargs.get("source") == "api_background"
    persistence.complete_optimization_run.assert_called_once()
