"""Tests for MLflow autologging wiring in the optimization background runner."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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


class TestBackgroundRunnerMlflowAvailable:
    """When MLflow is available, initialize + start_run are invoked."""

    def test_mlflow_init_and_start_run_called(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import fleet_rlm.api.routers.optimization as mod

        init_mock = MagicMock(return_value=True)
        ctx_mock = MagicMock()
        ctx_mock.__enter__ = MagicMock(return_value=ctx_mock)
        ctx_mock.__exit__ = MagicMock(return_value=False)
        start_run_mock = MagicMock(return_value=ctx_mock)
        log_metric_mock = MagicMock()
        log_params_mock = MagicMock()
        set_tags_mock = MagicMock()

        fake_result = {
            "train_examples": 4,
            "validation_examples": 1,
            "validation_score": 0.85,
            "output_path": None,
            "manifest_path": None,
        }
        run_mod_mock = MagicMock(return_value=fake_result)
        spec_mock = MagicMock()

        with (
            patch(
                "fleet_rlm.integrations.observability.mlflow_runtime.initialize_mlflow",
                init_mock,
            ),
            patch("mlflow.start_run", start_run_mock, create=True),
            patch("mlflow.log_metric", log_metric_mock, create=True),
            patch("mlflow.log_params", log_params_mock, create=True),
            patch("mlflow.set_tags", set_tags_mock, create=True),
            patch(
                "fleet_rlm.runtime.quality.module_registry.get_module_spec",
                return_value=spec_mock,
            ),
            patch(
                "fleet_rlm.runtime.quality.optimization_runner.run_module_optimization",
                run_mod_mock,
            ),
            patch(
                "fleet_rlm.integrations.local_store.update_optimization_run_phase",
                MagicMock(),
            ),
            patch(
                "fleet_rlm.integrations.local_store.complete_optimization_run",
                MagicMock(),
            ),
            patch(
                "fleet_rlm.integrations.local_store.fail_optimization_run",
                MagicMock(),
            ),
        ):
            mod._run_optimization_background(**_make_runner_kwargs(tmp_path))

        init_mock.assert_called_once()
        start_run_mock.assert_called_once()
        assert "GEPA::test-mod" in str(start_run_mock.call_args)
        log_params_mock.assert_called_once_with(
            {
                "gepa.auto": "light",
                "gepa.train_ratio": 0.8,
                "gepa.dataset_name": "data.jsonl",
            }
        )
        set_tags_mock.assert_called_once_with(
            {
                "fleet.optimizer": "GEPA",
                "fleet.optimization_source": "api_background",
                "fleet.program_spec": "QA",
                "fleet.module_slug": "test-mod",
            }
        )
        log_metric_mock.assert_any_call("gepa_train_examples", 4)
        log_metric_mock.assert_any_call("gepa_validation_examples", 1)
        log_metric_mock.assert_any_call("gepa_validation_score", 0.85)
        ctx_mock.__exit__.assert_called_once()


class TestBackgroundRunnerMlflowUnavailable:
    """When MLflow is unavailable, the optimization still succeeds."""

    def test_optimization_succeeds_without_mlflow(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import fleet_rlm.api.routers.optimization as mod

        init_mock = MagicMock(return_value=False)
        complete_mock = MagicMock()

        fake_result = {
            "train_examples": 4,
            "validation_examples": 1,
            "validation_score": 0.9,
            "output_path": None,
            "manifest_path": None,
        }
        run_mod_mock = MagicMock(return_value=fake_result)
        spec_mock = MagicMock()

        with (
            patch(
                "fleet_rlm.integrations.observability.mlflow_runtime.initialize_mlflow",
                init_mock,
            ),
            patch(
                "fleet_rlm.runtime.quality.module_registry.get_module_spec",
                return_value=spec_mock,
            ),
            patch(
                "fleet_rlm.runtime.quality.optimization_runner.run_module_optimization",
                run_mod_mock,
            ),
            patch(
                "fleet_rlm.integrations.local_store.update_optimization_run_phase",
                MagicMock(),
            ),
            patch(
                "fleet_rlm.integrations.local_store.complete_optimization_run",
                complete_mock,
            ),
            patch(
                "fleet_rlm.integrations.local_store.fail_optimization_run",
                MagicMock(),
            ),
        ):
            mod._run_optimization_background(**_make_runner_kwargs(tmp_path))

        run_mod_mock.assert_called_once()
        complete_mock.assert_called_once()
        assert complete_mock.call_args.kwargs.get("validation_score") == 0.9

    def test_mlflow_import_error_does_not_block(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even if mlflow import itself raises, the run proceeds."""
        import fleet_rlm.api.routers.optimization as mod

        complete_mock = MagicMock()
        fake_result = {
            "train_examples": 2,
            "validation_examples": 1,
            "output_path": None,
            "manifest_path": None,
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
                "fleet_rlm.runtime.quality.module_registry.get_module_spec",
                return_value=spec_mock,
            ),
            patch(
                "fleet_rlm.runtime.quality.optimization_runner.run_module_optimization",
                run_mod_mock,
            ),
            patch(
                "fleet_rlm.integrations.local_store.update_optimization_run_phase",
                MagicMock(),
            ),
            patch(
                "fleet_rlm.integrations.local_store.complete_optimization_run",
                complete_mock,
            ),
            patch(
                "fleet_rlm.integrations.local_store.fail_optimization_run",
                MagicMock(),
            ),
        ):
            mod._run_optimization_background(**_make_runner_kwargs(tmp_path))

        complete_mock.assert_called_once()


def test_resolve_dataset_request_accepts_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.api.routers.optimization as mod

    data_root = tmp_path / "optimization-data"
    dataset = data_root / "nested" / "examples.jsonl"
    dataset.parent.mkdir(parents=True)
    dataset.write_text('{"question": "hi", "answer": "hello"}\n', encoding="utf-8")
    monkeypatch.setattr(mod, "OPTIMIZATION_DATA_ROOT", data_root.resolve())

    request = mod.GEPAOptimizationRequest(
        dataset_path="nested/examples.jsonl",
        program_spec="qa",
    )

    resolved, dataset_ref = asyncio.run(mod._resolve_dataset_request(request))

    assert resolved == dataset.resolve()
    assert dataset_ref == "nested/examples.jsonl"


def test_resolve_dataset_request_rejects_path_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.api.routers.optimization as mod
    from fastapi import HTTPException

    data_root = tmp_path / "optimization-data"
    data_root.mkdir(parents=True)
    monkeypatch.setattr(mod, "OPTIMIZATION_DATA_ROOT", data_root.resolve())

    request = mod.GEPAOptimizationRequest(
        dataset_path="../secrets.jsonl",
        program_spec="qa",
    )

    with pytest.raises(HTTPException, match="Path escapes the allowed data directory."):
        asyncio.run(mod._resolve_dataset_request(request))


@pytest.mark.parametrize("module_slug", [None, ""])
def test_custom_program_path_does_not_open_outer_mlflow_run(
    tmp_path: Path,
    module_slug: str | None,
) -> None:
    import fleet_rlm.api.routers.optimization as mod

    start_run_mock = MagicMock()
    complete_mock = MagicMock()
    fake_result = {
        "train_examples": 3,
        "validation_examples": 1,
        "validation_score": 0.92,
        "output_path": None,
        "manifest_path": None,
    }
    optimize_mock = MagicMock(return_value=fake_result)

    kwargs = _make_runner_kwargs(tmp_path)
    kwargs["module_slug"] = module_slug

    with (
        patch("mlflow.start_run", start_run_mock, create=True),
        patch(
            "fleet_rlm.runtime.quality.gepa_optimization.optimize_program_with_gepa",
            optimize_mock,
        ),
        patch(
            "fleet_rlm.integrations.local_store.update_optimization_run_phase",
            MagicMock(),
        ),
        patch(
            "fleet_rlm.integrations.local_store.complete_optimization_run",
            complete_mock,
        ),
        patch(
            "fleet_rlm.integrations.local_store.fail_optimization_run",
            MagicMock(),
        ),
    ):
        mod._run_optimization_background(**kwargs)

    start_run_mock.assert_not_called()
    optimize_mock.assert_called_once()
    assert optimize_mock.call_args.kwargs.get("source") == "api_background"
    complete_mock.assert_called_once()
