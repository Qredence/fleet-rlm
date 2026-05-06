"""End-to-end API tests for GEPA optimization with the longcot-reasoner module.

Validates:
- VAL-GEPA-006: Blocking API returns 200 with ok=true and artifact paths
- VAL-GEPA-007: Async optimization run transitions to completed
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.config import ServerRuntimeConfig
from fleet_rlm.api.main import create_app
from fleet_rlm.integrations import local_store


@pytest.fixture
def ten_example_dataset(tmp_path: Path) -> Path:
    """Create a 10-example JSONL dataset for the longcot module."""
    path = tmp_path / "longcot_10.jsonl"
    rows = [
        {
            "question_id": f"api_q_{i}",
            "domain": "math",
            "difficulty": "easy",
            "question": f"What is {i} + {i + 1}?",
            "answer": str(2 * i + 1),
        }
        for i in range(10)
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    return path


@pytest.fixture
def _local_store_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate local store to a temp database."""
    db_path = tmp_path / "local.db"
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))
    local_store._engines.clear()


def _fake_optimization_result(output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('{"predictors": {}}', encoding="utf-8")
    manifest_path = output_path.with_suffix(".manifest.json")
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
                "train_examples": 8,
                "validation_examples": 2,
                "validation_dataset_indexes": [8, 9],
                "validation_range": {"start": 8, "end_exclusive": 10},
            },
            "baseline_score": 0.42,
            "optimized_score": 0.87,
            "score_delta": 0.45,
            "comparisons": [
                {
                    "validation_example_index": 0,
                    "dataset_row_index": 8,
                    "input_data": "{}",
                    "expected_output": "1",
                    "baseline": {
                        "predicted_output": "0",
                        "score": 0.2,
                    },
                    "optimized": {
                        "predicted_output": "1",
                        "score": 1.0,
                    },
                },
                {
                    "validation_example_index": 1,
                    "dataset_row_index": 9,
                    "input_data": "{}",
                    "expected_output": "3",
                    "baseline": {
                        "predicted_output": "2",
                        "score": 0.64,
                    },
                    "optimized": {
                        "predicted_output": "3",
                        "score": 0.74,
                    },
                },
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
        json.dumps(
            {
                "dataset_path": "longcot_10.jsonl",
                "module": "fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
                "train_examples": 8,
                "validation_examples": 2,
                "validation_score": 0.87,
                "optimizer": "GEPA",
                "metric": "longcot_qa_metric",
                "auto": "light",
                "module_slug": "longcot-reasoner",
                "output_path": str(output_path),
                "artifact": review_bundle["artifact"],
                "review_bundle": review_bundle,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "train_examples": 8,
        "validation_examples": 2,
        "baseline_validation_score": 0.42,
        "validation_score": 0.87,
        "output_path": str(output_path),
        "manifest_path": str(manifest_path),
        "optimizer": "GEPA",
        "program_spec": "fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
        "module_slug": "longcot-reasoner",
        "evaluation_results": [
            {
                "example_index": 0,
                "input_data": "{}",
                "expected_output": "1",
                "predicted_output": "1",
                "score": 1.0,
            },
            {
                "example_index": 1,
                "input_data": "{}",
                "expected_output": "3",
                "predicted_output": "3",
                "score": 0.74,
            },
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
            "module_slug": "longcot-reasoner",
            "dataset_path": "longcot_10.jsonl",
            "review_bundle": review_bundle,
        },
    }


@pytest.fixture
def client_with_patched_deps(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a TestClient with GEPA/MLflow availability patched."""
    from fleet_rlm.api.routers.optimization import runs as opt_runs

    monkeypatch.setattr(opt_runs, "_check_gepa_available", lambda: True)
    monkeypatch.setattr(opt_runs, "_get_mlflow_status", lambda: (True, True))
    monkeypatch.setattr(
        "fleet_rlm.api.routers.optimization.background._planner_execution_context",
        contextlib.nullcontext,
    )

    app = create_app(
        config=ServerRuntimeConfig(
            app_env="local",
            database_required=False,
        )
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def client_with_mlflow_unavailable(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a TestClient where GEPA is installed but MLflow is unavailable."""
    from fleet_rlm.api.routers.optimization import runs as opt_runs

    monkeypatch.setattr(opt_runs, "_check_gepa_available", lambda: True)
    monkeypatch.setattr(opt_runs, "_get_mlflow_status", lambda: (True, False))
    monkeypatch.setattr(
        "fleet_rlm.api.routers.optimization.background._planner_execution_context",
        contextlib.nullcontext,
    )

    app = create_app(
        config=ServerRuntimeConfig(
            app_env="local",
            database_required=False,
        )
    )
    with TestClient(app) as client:
        yield client


class TestBlockingOptimizationApi:
    """VAL-GEPA-006: Blocking API returns success with artifact paths."""

    def test_blocking_run_returns_200_with_ok_true(
        self,
        auth_headers: dict[str, str],
        ten_example_dataset: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _local_store_isolation: None,
        client_with_patched_deps: TestClient,
    ) -> None:
        output_path = tmp_path / "artifact.json"
        fake_result = _fake_optimization_result(output_path)

        data_root = tmp_path / "optimization-data"
        data_root.mkdir(parents=True)
        dataset_nested = data_root / ten_example_dataset.name
        dataset_nested.write_text(ten_example_dataset.read_text())
        monkeypatch.setattr(
            "fleet_rlm.api.routers.optimization._deps.OPTIMIZATION_DATA_ROOT",
            data_root.resolve(),
        )

        with patch(
            "fleet_rlm.quality.optimization_runner.run_module_optimization",
            return_value=fake_result,
        ):
            response = client_with_patched_deps.post(
                "/api/v1/optimization/run",
                headers=auth_headers,
                json={
                    "module_slug": "longcot-reasoner",
                    "dataset_path": str(ten_example_dataset.name),
                    "auto": "light",
                    "train_ratio": 0.8,
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["module_slug"] == "longcot-reasoner"
        assert payload["train_examples"] == 8
        assert payload["validation_examples"] == 2
        assert payload["validation_score"] == 0.87
        assert payload["output_path"] is not None
        assert payload["manifest_path"] is not None

    def test_blocking_run_validates_response_schema(
        self,
        auth_headers: dict[str, str],
        ten_example_dataset: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _local_store_isolation: None,
        client_with_patched_deps: TestClient,
    ) -> None:
        from fleet_rlm.api.schemas.optimization import GEPAOptimizationResponse

        output_path = tmp_path / "artifact.json"
        fake_result = _fake_optimization_result(output_path)

        data_root = tmp_path / "optimization-data"
        data_root.mkdir(parents=True)
        dataset_nested = data_root / ten_example_dataset.name
        dataset_nested.write_text(ten_example_dataset.read_text())
        monkeypatch.setattr(
            "fleet_rlm.api.routers.optimization._deps.OPTIMIZATION_DATA_ROOT",
            data_root.resolve(),
        )

        with patch(
            "fleet_rlm.quality.optimization_runner.run_module_optimization",
            return_value=fake_result,
        ):
            response = client_with_patched_deps.post(
                "/api/v1/optimization/run",
                headers=auth_headers,
                json={
                    "module_slug": "longcot-reasoner",
                    "dataset_path": str(ten_example_dataset.name),
                    "auto": "light",
                    "train_ratio": 0.8,
                },
            )

        assert response.status_code == 200
        validated = GEPAOptimizationResponse.model_validate(response.json())
        assert validated.ok is True
        assert validated.module_slug == "longcot-reasoner"

    def test_module_run_succeeds_without_mlflow(
        self,
        auth_headers: dict[str, str],
        ten_example_dataset: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _local_store_isolation: None,
        client_with_mlflow_unavailable: TestClient,
    ) -> None:
        output_path = tmp_path / "artifact.json"
        fake_result = _fake_optimization_result(output_path)

        data_root = tmp_path / "optimization-data"
        data_root.mkdir(parents=True)
        dataset_nested = data_root / ten_example_dataset.name
        dataset_nested.write_text(ten_example_dataset.read_text())
        monkeypatch.setattr(
            "fleet_rlm.api.routers.optimization._deps.OPTIMIZATION_DATA_ROOT",
            data_root.resolve(),
        )

        with patch(
            "fleet_rlm.quality.optimization_runner.run_module_optimization",
            return_value=fake_result,
        ):
            response = client_with_mlflow_unavailable.post(
                "/api/v1/optimization/run",
                headers=auth_headers,
                json={
                    "module_slug": "longcot-reasoner",
                    "dataset_path": str(ten_example_dataset.name),
                    "auto": "light",
                    "train_ratio": 0.8,
                },
            )

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_custom_run_still_requires_mlflow(
        self,
        auth_headers: dict[str, str],
        ten_example_dataset: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _local_store_isolation: None,
        client_with_mlflow_unavailable: TestClient,
    ) -> None:
        data_root = tmp_path / "optimization-data"
        data_root.mkdir(parents=True)
        dataset_nested = data_root / ten_example_dataset.name
        dataset_nested.write_text(ten_example_dataset.read_text())
        monkeypatch.setattr(
            "fleet_rlm.api.routers.optimization._deps.OPTIMIZATION_DATA_ROOT",
            data_root.resolve(),
        )

        response = client_with_mlflow_unavailable.post(
            "/api/v1/optimization/run",
            headers=auth_headers,
            json={
                "program_spec": "pkg.module:build_program",
                "dataset_path": str(ten_example_dataset.name),
                "auto": "light",
                "train_ratio": 0.8,
            },
        )

        assert response.status_code == 503
        assert "Custom GEPA optimization requires" in response.json()["detail"]


class TestAsyncOptimizationApi:
    """VAL-GEPA-007: Async run transitions from running to completed."""

    def test_async_run_creates_and_completes(
        self,
        auth_headers: dict[str, str],
        ten_example_dataset: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _local_store_isolation: None,
        client_with_patched_deps: TestClient,
    ) -> None:
        output_path = tmp_path / "artifact.json"
        fake_result = _fake_optimization_result(output_path)

        data_root = tmp_path / "optimization-data"
        data_root.mkdir(parents=True)
        dataset_nested = data_root / ten_example_dataset.name
        dataset_nested.write_text(ten_example_dataset.read_text())
        monkeypatch.setattr(
            "fleet_rlm.api.routers.optimization._deps.OPTIMIZATION_DATA_ROOT",
            data_root.resolve(),
        )

        with patch(
            "fleet_rlm.quality.optimization_runner.run_module_optimization",
            return_value=fake_result,
        ):
            create_resp = client_with_patched_deps.post(
                "/api/v1/optimization/runs",
                headers=auth_headers,
                json={
                    "module_slug": "longcot-reasoner",
                    "dataset_path": str(ten_example_dataset.name),
                    "auto": "light",
                    "train_ratio": 0.8,
                },
            )

        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["status"] == "running"
        run_id = created["run_id"]
        assert run_id is not None

        # Poll until completed (background tasks run synchronously in TestClient)
        status_resp = client_with_patched_deps.get(
            f"/api/v1/optimization/runs/{run_id}",
            headers=auth_headers,
        )
        assert status_resp.status_code == 200
        status_payload = status_resp.json()
        assert status_payload["status"] in ("running", "completed")

        # In TestClient background tasks execute after the response,
        # so a small delay/retry helps ensure completion.
        for _ in range(10):
            status_resp = client_with_patched_deps.get(
                f"/api/v1/optimization/runs/{run_id}",
                headers=auth_headers,
            )
            status_payload = status_resp.json()
            if status_payload["status"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.1))

        assert status_payload["status"] == "completed"
        assert status_payload["validation_score"] == 0.87
        assert status_payload["train_examples"] == 8
        assert status_payload["validation_examples"] == 2

    def test_async_run_persists_evaluation_results(
        self,
        auth_headers: dict[str, str],
        ten_example_dataset: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _local_store_isolation: None,
        client_with_patched_deps: TestClient,
    ) -> None:
        output_path = tmp_path / "artifact.json"
        fake_result = _fake_optimization_result(output_path)

        data_root = tmp_path / "optimization-data"
        data_root.mkdir(parents=True)
        dataset_nested = data_root / ten_example_dataset.name
        dataset_nested.write_text(ten_example_dataset.read_text())
        monkeypatch.setattr(
            "fleet_rlm.api.routers.optimization._deps.OPTIMIZATION_DATA_ROOT",
            data_root.resolve(),
        )

        with patch(
            "fleet_rlm.quality.optimization_runner.run_module_optimization",
            return_value=fake_result,
        ):
            create_resp = client_with_patched_deps.post(
                "/api/v1/optimization/runs",
                headers=auth_headers,
                json={
                    "module_slug": "longcot-reasoner",
                    "dataset_path": str(ten_example_dataset.name),
                    "auto": "light",
                    "train_ratio": 0.8,
                },
            )

        assert create_resp.status_code == 200
        run_id = create_resp.json()["run_id"]

        # Wait for background completion
        for _ in range(10):
            status_resp = client_with_patched_deps.get(
                f"/api/v1/optimization/runs/{run_id}",
                headers=auth_headers,
            )
            if status_resp.json()["status"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.1))

        # Fetch evaluation results
        results_resp = client_with_patched_deps.get(
            f"/api/v1/optimization/runs/{run_id}/results",
            headers=auth_headers,
        )
        assert results_resp.status_code == 200
        results_payload = results_resp.json()
        assert results_payload["total"] >= 2
        assert len(results_payload["items"]) >= 2
        mean_score = sum(item["score"] for item in results_payload["items"]) / len(results_payload["items"])
        assert mean_score == pytest.approx(0.87, abs=1e-9)

        # Fetch prompt snapshots via compare endpoint
        compare_resp = client_with_patched_deps.get(
            "/api/v1/optimization/runs/compare",
            headers=auth_headers,
            params={"run_ids": str(run_id)},
        )
        assert compare_resp.status_code == 200
        compare_payload = compare_resp.json()
        assert len(compare_payload["runs"]) == 1
        run_data = compare_payload["runs"][0]
        prompt_types = {s["prompt_type"] for s in run_data["prompt_snapshots"]}
        assert "before" in prompt_types or "after" in prompt_types

    def test_async_run_manifest_records_review_bundle_consistently(
        self,
        auth_headers: dict[str, str],
        ten_example_dataset: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _local_store_isolation: None,
        client_with_patched_deps: TestClient,
    ) -> None:
        output_path = tmp_path / "artifact.json"
        fake_result = _fake_optimization_result(output_path)

        data_root = tmp_path / "optimization-data"
        data_root.mkdir(parents=True)
        dataset_nested = data_root / ten_example_dataset.name
        dataset_nested.write_text(ten_example_dataset.read_text())
        monkeypatch.setattr(
            "fleet_rlm.api.routers.optimization._deps.OPTIMIZATION_DATA_ROOT",
            data_root.resolve(),
        )

        with patch(
            "fleet_rlm.quality.optimization_runner.run_module_optimization",
            return_value=fake_result,
        ):
            create_resp = client_with_patched_deps.post(
                "/api/v1/optimization/runs",
                headers=auth_headers,
                json={
                    "module_slug": "longcot-reasoner",
                    "dataset_path": str(ten_example_dataset.name),
                    "auto": "light",
                    "train_ratio": 0.8,
                },
            )

        run_id = create_resp.json()["run_id"]
        run_resp = client_with_patched_deps.get(
            f"/api/v1/optimization/runs/{run_id}",
            headers=auth_headers,
        )
        assert run_resp.status_code == 200
        run_payload = run_resp.json()
        assert run_payload["status"] == "completed"
        assert run_payload["manifest_path"] == fake_result["manifest_path"]

        manifest = json.loads(Path(fake_result["manifest_path"]).read_text(encoding="utf-8"))
        holdout = manifest["review_bundle"]["holdout"]
        assert holdout["baseline_score"] == 0.42
        assert holdout["optimized_score"] == run_payload["validation_score"]
        assert holdout["split_reference"]["validation_dataset_indexes"] == [8, 9]
        assert manifest["review_bundle"]["reflection_model"]["source"] == "delegate"
        assert manifest["review_bundle"]["prompt_snapshots"]["matched_predictors"][0]["predictor_name"] == "predict"
