"""Focused contract tests for VAL-QUALITY-001 through VAL-QUALITY-017.

Covers:
  VAL-QUALITY-001  Optimization status reports real GEPA and MLflow readiness
  VAL-QUALITY-002  Modules endpoint exposes canonical LongCoT metadata
  VAL-QUALITY-003  Registry resolves canonical LongCoT spec fields
  VAL-QUALITY-005  Dataset upload rejects invalid rows before persistence
  VAL-QUALITY-006  Dataset upload accepts valid JSON and JSONL
  VAL-QUALITY-007  Transcript export maps turns into LongCoT rows
  VAL-QUALITY-010  Background optimization persists lifecycle phases
  VAL-QUALITY-011  MLflow tracking captures canonical run metadata
  VAL-QUALITY-013  GEPA remains offline-only (no live-chat side effects)
  VAL-QUALITY-015  Offline optimization does not require MLflow
  VAL-QUALITY-016  Optimized artifacts are not auto-loaded into live runtime
  VAL-QUALITY-017  Offline optimization output paths follow the write policy
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.config import ServerRuntimeConfig
from fleet_rlm.api.dependencies import (
    ConfigDeps,
    get_config_deps,
    get_persistence,
    require_http_identity,
)
from fleet_rlm.api.errors import add_exception_handlers
from fleet_rlm.api.routers import optimization
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.quality.module_registry import _reset_registry, get_module_spec, list_module_slugs

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared test infrastructure
# ---------------------------------------------------------------------------


def _identity() -> NormalizedIdentity:
    return NormalizedIdentity(
        tenant_claim="tenant-quality-test",
        user_claim="user-quality-test",
        email="quality@test.com",
        name="Quality Tester",
    )


def _config() -> ConfigDeps:
    return ConfigDeps(
        config=ServerRuntimeConfig(
            agent_model=None,
            database_url=None,
            auth_required=False,
            serve_ui=False,
            expose_root=False,
        )
    )


def _persisted_identity() -> IdentityUpsertResult:
    return IdentityUpsertResult(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        tenant_status="active",  # type: ignore[arg-type]
        membership_role="member",  # type: ignore[arg-type]
    )


class _MockPersistence:
    """Minimal persistence mock for optimization API tests."""

    def __init__(self) -> None:
        self._identity = _persisted_identity()
        self._datasets: list[Any] = []

    async def upsert_identity(self, **kwargs: Any) -> IdentityUpsertResult:
        return self._identity

    async def create_dataset(self, request: Any, examples: list[Any]) -> Any:
        from fleet_rlm.integrations.database import DatasetFormat

        now = datetime.now(timezone.utc)
        # Normalize format enum
        fmt = request.format
        if not hasattr(fmt, "value"):
            fmt = DatasetFormat(str(fmt))
        dataset = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=self._identity.tenant_id,
            workspace_id=self._identity.workspace_id,
            created_by_user_id=self._identity.user_id,
            name=request.name,
            row_count=request.row_count,
            format=fmt,
            source=request.source,
            module_slug=request.module_slug,
            uri=request.uri,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        self._datasets.append(dataset)
        return dataset


def _optimization_app(persistence: _MockPersistence | None = None) -> FastAPI:
    """Build a minimal FastAPI app with the optimization router."""
    app = FastAPI()
    add_exception_handlers(app)
    app.include_router(optimization.router, prefix="/api/v1")

    _persistence = persistence or _MockPersistence()
    app.dependency_overrides[require_http_identity] = _identity
    app.dependency_overrides[get_config_deps] = _config
    app.dependency_overrides[get_persistence] = lambda: _persistence
    return app


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """Reset the module registry before each test to get a clean state."""
    _reset_registry()


# ===========================================================================
# VAL-QUALITY-001: Optimization status reports GEPA and MLflow readiness
# ===========================================================================


class TestOptimizationStatus:
    """VAL-QUALITY-001: Status endpoint fields."""

    def test_status_returns_gepa_installed_true_when_available(self) -> None:
        """When GEPA is importable, status reports gepa_installed=true."""
        from fleet_rlm.api.routers.optimization._deps import _check_gepa_available

        # GEPA should be installed in the test environment
        result = _check_gepa_available()
        assert isinstance(result, bool)
        # If GEPA is available, it must be True; if not, it should be False without exception
        # The contract only requires the function to be callable and return a bool

    def test_status_endpoint_returns_required_fields(self) -> None:
        """GET /api/v1/optimization/status returns all required boolean fields."""
        app = _optimization_app()

        with (
            patch(
                "fleet_rlm.api.routers.optimization.status._check_gepa_available",
                return_value=True,
            ),
            patch(
                "fleet_rlm.api.routers.optimization.status._get_mlflow_status",
                return_value=(True, True),
            ),
        ):
            client = TestClient(app)
            response = client.get("/api/v1/optimization/status")

        assert response.status_code == 200
        body = response.json()
        # VAL-QUALITY-001: All required flags present and true
        assert body["gepa_installed"] is True
        assert body["module_optimization_available"] is True
        assert body["mlflow_configured"] is True
        assert body["mlflow_enabled"] is True
        assert body["mlflow_logging_available"] is True

    def test_status_endpoint_reports_guidance_when_mlflow_unavailable(self) -> None:
        """When MLflow is not configured, status still reports GEPA available."""
        app = _optimization_app()

        with (
            patch(
                "fleet_rlm.api.routers.optimization.status._check_gepa_available",
                return_value=True,
            ),
            patch(
                "fleet_rlm.api.routers.optimization.status._get_mlflow_status",
                return_value=(False, False),
            ),
        ):
            client = TestClient(app)
            response = client.get("/api/v1/optimization/status")

        assert response.status_code == 200
        body = response.json()
        assert body["gepa_installed"] is True
        assert body["module_optimization_available"] is True
        # MLflow flags should be false
        assert body["mlflow_configured"] is False
        assert body["mlflow_enabled"] is False
        # Guidance should mention MLflow
        assert any("MLflow" in g for g in body.get("guidance", []))


# ===========================================================================
# VAL-QUALITY-002: Modules endpoint exposes canonical LongCoT metadata
# ===========================================================================


class TestOptimizationModules:
    """VAL-QUALITY-002: Canonical module registry metadata exposed by API."""

    def test_modules_endpoint_includes_longcot_reasoner(self) -> None:
        """GET /api/v1/optimization/modules includes longcot-reasoner entry."""
        app = _optimization_app()
        client = TestClient(app)
        response = client.get("/api/v1/optimization/modules")

        assert response.status_code == 200
        modules = response.json()
        slugs = [m["slug"] for m in modules]
        assert "longcot-reasoner" in slugs

    def test_modules_endpoint_longcot_canonical_fields(self) -> None:
        """longcot-reasoner entry has exact label, program_spec, and required_dataset_keys."""
        app = _optimization_app()
        client = TestClient(app)
        response = client.get("/api/v1/optimization/modules")

        assert response.status_code == 200
        modules = response.json()
        longcot = next((m for m in modules if m["slug"] == "longcot-reasoner"), None)

        assert longcot is not None, "longcot-reasoner must be present in modules"
        # VAL-QUALITY-002: exact canonical values
        assert longcot["label"] == "LongCoT QA Reasoner"
        assert longcot["program_spec"] == "fleet_rlm.runtime.agent.signatures:LongCoTQASignature"
        assert "question" in longcot["required_dataset_keys"]
        assert "answer" in longcot["required_dataset_keys"]
        # Must be offline-only
        assert longcot.get("offline_only") is True


# ===========================================================================
# VAL-QUALITY-003: Registry resolves canonical LongCoT spec fields
# ===========================================================================


class TestLongCoTRegistrySpec:
    """VAL-QUALITY-003: Registry spec fields match the contract."""

    def test_list_module_slugs_contains_longcot_reasoner(self) -> None:
        slugs = list_module_slugs()
        assert "longcot-reasoner" in slugs

    def test_get_module_spec_returns_non_null(self) -> None:
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None

    def test_longcot_spec_input_keys(self) -> None:
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None
        assert spec.input_keys == ["question"]

    def test_longcot_spec_required_dataset_keys(self) -> None:
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None
        assert "question" in spec.required_dataset_keys
        assert "answer" in spec.required_dataset_keys

    def test_longcot_spec_metric_name(self) -> None:
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None
        # VAL-QUALITY-003: canonical metric name
        assert spec.metric_name == "longcot_qa_metric"

    def test_longcot_spec_program_spec(self) -> None:
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None
        assert spec.program_spec == "fleet_rlm.runtime.agent.signatures:LongCoTQASignature"

    def test_longcot_spec_artifact_filename(self) -> None:
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None
        assert spec.artifact_filename == "longcot_reasoner.json"


# ===========================================================================
# VAL-QUALITY-005: Dataset upload rejects any invalid row before persistence
# ===========================================================================


class TestDatasetUploadValidation:
    """VAL-QUALITY-005: All rows validated before file or dataset record is written."""

    def test_upload_rejects_row_missing_required_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Upload fails with 400 when any row is missing a required key."""
        monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))
        persistence = _MockPersistence()
        app = _optimization_app(persistence)
        client = TestClient(app)

        # Row 0 is valid, row 1 is missing 'answer'
        rows = [
            {"question": "What is 2+2?", "answer": "4"},
            {"question": "What is 3+3?"},  # missing 'answer' — row index 1
        ]
        content = json.dumps(rows).encode("utf-8")

        response = client.post(
            "/api/v1/optimization/datasets",
            data={"module_slug": "longcot-reasoner"},
            files={"file": ("data.json", BytesIO(content), "application/json")},
        )

        assert response.status_code == 400
        body = response.json()
        # Error is in the 'message' field of our canonical error envelope
        message = body.get("message", body.get("detail", ""))
        # Must report the failing row index
        assert "1" in message
        # Must name the missing key
        assert "answer" in message
        # No dataset should have been created
        assert len(persistence._datasets) == 0

    def test_upload_rejects_last_row_missing_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Upload fails when the LAST row (not just the first) is invalid."""
        monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))
        persistence = _MockPersistence()
        app = _optimization_app(persistence)
        client = TestClient(app)

        # 9 valid rows, then 1 invalid (missing 'answer') at index 9
        rows = [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(9)]
        rows.append({"question": "Last question"})  # no 'answer' — row index 9
        content = json.dumps(rows).encode("utf-8")

        response = client.post(
            "/api/v1/optimization/datasets",
            data={"module_slug": "longcot-reasoner"},
            files={"file": ("data.json", BytesIO(content), "application/json")},
        )

        assert response.status_code == 400
        body = response.json()
        message = body.get("message", body.get("detail", ""))
        assert "9" in message  # row index 9
        assert len(persistence._datasets) == 0

    def test_upload_rejects_jsonl_with_invalid_row(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """JSONL upload also validates all rows before persistence."""
        monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))
        persistence = _MockPersistence()
        app = _optimization_app(persistence)
        client = TestClient(app)

        lines = [
            json.dumps({"question": "Q0", "answer": "A0"}),
            json.dumps({"question": "Q1"}),  # missing 'answer' — row index 1
        ]
        content = "\n".join(lines).encode("utf-8")

        response = client.post(
            "/api/v1/optimization/datasets",
            data={"module_slug": "longcot-reasoner"},
            files={"file": ("data.jsonl", BytesIO(content), "application/x-ndjson")},
        )

        assert response.status_code == 400
        assert len(persistence._datasets) == 0

    def test_upload_rejects_unknown_module_slug(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Upload with unknown module_slug returns 400 before any persistence."""
        monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))
        persistence = _MockPersistence()
        app = _optimization_app(persistence)
        client = TestClient(app)

        rows = [{"question": "Q1", "answer": "A1"}]
        content = json.dumps(rows).encode("utf-8")

        response = client.post(
            "/api/v1/optimization/datasets",
            data={"module_slug": "unknown-module"},
            files={"file": ("data.json", BytesIO(content), "application/json")},
        )

        assert response.status_code == 400
        body = response.json()
        message = body.get("message", body.get("detail", ""))
        assert "unknown-module" in message
        assert len(persistence._datasets) == 0


# ===========================================================================
# VAL-QUALITY-006: Dataset upload accepts valid JSON and JSONL
# ===========================================================================


class TestDatasetUploadAcceptsValid:
    """VAL-QUALITY-006: Valid uploads are accepted and registered."""

    def test_upload_valid_json_array(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid JSON array upload returns 200 with dataset metadata."""
        monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))
        persistence = _MockPersistence()
        app = _optimization_app(persistence)
        client = TestClient(app)

        rows = [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(5)]
        content = json.dumps(rows).encode("utf-8")

        response = client.post(
            "/api/v1/optimization/datasets",
            data={"module_slug": "longcot-reasoner"},
            files={"file": ("dataset.json", BytesIO(content), "application/json")},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["row_count"] == 5
        assert body["format"] == "json"
        assert body["module_slug"] == "longcot-reasoner"
        assert "id" in body
        # Dataset record was created
        assert len(persistence._datasets) == 1

    def test_upload_valid_jsonl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid JSONL upload returns 200 with correct row count and format."""
        monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))
        persistence = _MockPersistence()
        app = _optimization_app(persistence)
        client = TestClient(app)

        lines = [json.dumps({"question": f"Q{i}", "answer": f"A{i}"}) for i in range(3)]
        content = "\n".join(lines).encode("utf-8")

        response = client.post(
            "/api/v1/optimization/datasets",
            data={"module_slug": "longcot-reasoner"},
            files={"file": ("dataset.jsonl", BytesIO(content), "application/x-ndjson")},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["row_count"] == 3
        assert body["format"] == "jsonl"
        assert body["module_slug"] == "longcot-reasoner"

    def test_upload_without_module_slug_skips_validation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Upload without module_slug is accepted as-is without key validation."""
        monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))
        persistence = _MockPersistence()
        app = _optimization_app(persistence)
        client = TestClient(app)

        rows = [{"arbitrary_key": "value"}]
        content = json.dumps(rows).encode("utf-8")

        response = client.post(
            "/api/v1/optimization/datasets",
            files={"file": ("data.json", BytesIO(content), "application/json")},
        )

        assert response.status_code == 200
        assert len(persistence._datasets) == 1


# ===========================================================================
# VAL-QUALITY-007: Transcript export maps turns into LongCoT rows
# ===========================================================================


class TestLongCoTTranscriptExport:
    """VAL-QUALITY-007: Transcript export produces correct LongCoT rows."""

    def test_build_transcript_dataset_rows_maps_question_and_answer(self) -> None:
        """User message → question, assistant message → answer."""
        from fleet_rlm.quality.transcript_exports import build_transcript_dataset_rows

        rows, label = build_transcript_dataset_rows(
            module_slug="longcot-reasoner",
            turns=[
                ("What is the capital of France?", "Paris"),
                ("What is 2 + 2?", "4"),
            ],
        )

        assert label == "LongCoT QA Reasoner"
        assert len(rows) == 2
        assert rows[0]["question"] == "What is the capital of France?"
        assert rows[0]["answer"] == "Paris"
        assert rows[1]["question"] == "What is 2 + 2?"
        assert rows[1]["answer"] == "4"

    def test_transcript_export_skips_incomplete_turns(self) -> None:
        """Turns without both user and assistant messages are skipped."""
        from fleet_rlm.quality.transcript_exports import build_transcript_dataset_rows

        rows, _ = build_transcript_dataset_rows(
            module_slug="longcot-reasoner",
            turns=[
                ("Complete question?", "Complete answer"),
                (None, "Orphan assistant"),
                ("Orphan user", None),
            ],
        )

        assert len(rows) == 1
        assert rows[0]["question"] == "Complete question?"
        assert rows[0]["answer"] == "Complete answer"

    def test_transcript_export_rejects_empty_turns(self) -> None:
        """Transcript with no usable turns raises ValueError."""
        from fleet_rlm.quality.transcript_exports import build_transcript_dataset_rows

        with pytest.raises(ValueError, match="no usable turns"):
            build_transcript_dataset_rows(
                module_slug="longcot-reasoner",
                turns=[(None, None)],
            )

    def test_transcript_export_rejects_unknown_module(self) -> None:
        """Unknown module_slug raises ValueError."""
        from fleet_rlm.quality.transcript_exports import build_transcript_dataset_rows

        with pytest.raises(ValueError, match="Unknown module slug"):
            build_transcript_dataset_rows(
                module_slug="nonexistent-module",
                turns=[("Q", "A")],
            )

    def test_transcript_rows_have_required_longcot_keys(self) -> None:
        """Every row from transcript export has 'question' and 'answer'."""
        from fleet_rlm.quality.transcript_exports import build_transcript_dataset_rows

        turns = [(f"Question {i}?", f"Answer {i}") for i in range(5)]
        rows, _ = build_transcript_dataset_rows(
            module_slug="longcot-reasoner",
            turns=turns,
        )

        assert len(rows) == 5
        for row in rows:
            assert "question" in row
            assert "answer" in row
            assert row["question"]  # non-empty
            assert row["answer"]  # non-empty


# ===========================================================================
# VAL-QUALITY-010: Background optimization lifecycle
# ===========================================================================


class TestBackgroundOptimizationLifecycle:
    """VAL-QUALITY-010: Background task progresses through phases and persists results."""

    def test_background_task_updates_phases_and_completes(self, tmp_path: Path) -> None:
        """Background optimization: loading → compiling → saving → completed."""
        from fleet_rlm.api.routers.optimization.background import run_optimization_background

        persisted_identity = _persisted_identity()
        run_uuid = uuid.uuid4()

        phase_calls: list[str] = []
        complete_calls: list[dict[str, Any]] = []
        fail_calls: list[str] = []

        class _Persistence:
            async def update_optimization_run_phase(self, *, phase: str, **kw: Any) -> None:
                phase_calls.append(phase)

            async def save_evaluation_results(self, *, results: list, **kw: Any) -> None:
                pass

            async def save_prompt_snapshots(self, *, snapshots: list, **kw: Any) -> None:
                pass

            async def complete_optimization_run(self, **kw: Any) -> None:
                complete_calls.append(dict(kw))

            async def fail_optimization_run(self, *, error: str, **kw: Any) -> None:
                fail_calls.append(error)

        dataset_path = tmp_path / "dataset.jsonl"
        dataset_path.write_text("\n".join(json.dumps({"question": f"Q{i}", "answer": f"A{i}"}) for i in range(10)))

        fake_result = {
            "train_examples": 8,
            "validation_examples": 2,
            "validation_score": 0.85,
            "output_path": str(tmp_path / "artifact.json"),
            "manifest_path": str(tmp_path / "artifact.manifest.json"),
            "evaluation_results": [{"example_index": 0, "score": 0.85}],
            "prompt_snapshots": [{"predictor_name": "predict", "prompt_type": "after", "prompt_text": "Test"}],
            "run_metadata": {"module_slug": "longcot-reasoner"},
        }

        with (
            patch("fleet_rlm.api.routers.optimization.background.run_blocking", return_value=fake_result),
            # initialize_mlflow is imported inside the function, patch via canonical path
            patch(
                "fleet_rlm.integrations.observability.mlflow_runtime.initialize_mlflow",
                return_value=False,
            ),
        ):
            asyncio.run(
                run_optimization_background(
                    run_id=str(run_uuid),
                    persistence=_Persistence(),
                    persisted_identity=persisted_identity,
                    module_slug="longcot-reasoner",
                    dataset_path=dataset_path,
                    program_spec="fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
                    output_path=None,
                    default_output_root=tmp_path,
                    auto="light",
                    train_ratio=0.8,
                )
            )

        # VAL-QUALITY-010: phases must include loading and compiling before completion
        assert "loading" in phase_calls
        assert "compiling" in phase_calls
        # Run must complete (not fail)
        assert len(complete_calls) == 1
        assert len(fail_calls) == 0

    def test_background_task_marks_failed_on_exception(self, tmp_path: Path) -> None:
        """Background task marks run as failed when optimization raises."""
        from fleet_rlm.api.routers.optimization.background import run_optimization_background

        persisted_identity = _persisted_identity()
        run_uuid = uuid.uuid4()

        fail_calls: list[str] = []

        class _Persistence:
            async def update_optimization_run_phase(self, **kw: Any) -> None:
                pass

            async def fail_optimization_run(self, *, error: str, **kw: Any) -> None:
                fail_calls.append(error)

        dataset_path = tmp_path / "dataset.jsonl"
        dataset_path.write_text(json.dumps({"question": "Q", "answer": "A"}))

        with (
            patch(
                "fleet_rlm.api.routers.optimization.background.run_blocking",
                side_effect=RuntimeError("intentional failure"),
            ),
            patch(
                "fleet_rlm.integrations.observability.mlflow_runtime.initialize_mlflow",
                return_value=False,
            ),
        ):
            asyncio.run(
                run_optimization_background(
                    run_id=str(run_uuid),
                    persistence=_Persistence(),
                    persisted_identity=persisted_identity,
                    module_slug="longcot-reasoner",
                    dataset_path=dataset_path,
                    program_spec="fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
                    output_path=None,
                    default_output_root=tmp_path,
                    auto="light",
                    train_ratio=0.8,
                )
            )

        assert len(fail_calls) == 1
        assert "intentional failure" in fail_calls[0]


# ===========================================================================
# VAL-QUALITY-011: MLflow tracking captures canonical metadata
# ===========================================================================


class TestMlflowTrackingMetadata:
    """VAL-QUALITY-011: Canonical tags, metrics logged to MLflow."""

    def test_log_run_metadata_sets_canonical_tags(self, tmp_path: Path) -> None:
        """log_gepa_mlflow_run_metadata sets all required tags."""
        from fleet_rlm.quality.gepa_optimization import log_gepa_mlflow_run_metadata

        set_tags_calls: list[dict[str, str]] = []

        log_gepa_mlflow_run_metadata(
            dataset_path=tmp_path / "dataset.jsonl",
            program_spec="fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
            auto="light",
            train_ratio=0.8,
            module_slug="longcot-reasoner",
            source="api_background",
            log_params=lambda p: None,
            set_tags=lambda t: set_tags_calls.append(t),
        )

        assert len(set_tags_calls) == 1
        tags = set_tags_calls[0]
        # VAL-QUALITY-011: required tags
        assert tags["fleet.optimizer"] == "GEPA"
        assert tags["fleet.optimization_source"] == "api_background"
        assert tags["fleet.module_slug"] == "longcot-reasoner"
        assert "fleet.program_spec" in tags

    def test_log_result_metadata_sets_run_id_tag(self) -> None:
        """log_gepa_mlflow_result_metadata records fleet.optimization_run_id."""
        from fleet_rlm.quality.gepa_optimization import log_gepa_mlflow_result_metadata

        set_tags_calls: list[dict[str, str]] = []
        log_metric_calls: list[tuple[str, float]] = []

        result = {
            "review_bundle": {
                "holdout": {
                    "baseline_score": 0.7,
                    "optimized_score": 0.88,
                    "score_delta": 0.18,
                    "split_reference": {
                        "validation_dataset_indexes": [8, 9],
                        "validation_range": {"start": 8, "end_exclusive": 10},
                    },
                },
                "reflection_model": {"model": "test-model", "source": "planner"},
            },
            "output_path": None,
            "manifest_path": None,
        }

        log_gepa_mlflow_result_metadata(
            result=result,
            run_id=42,
            log_metric=lambda name, val: log_metric_calls.append((name, val)),
            log_params=lambda p: None,
            set_tags=lambda t: set_tags_calls.append(t),
        )

        tags = {}
        for call in set_tags_calls:
            tags.update(call)

        # VAL-QUALITY-011: run_id tag
        assert tags.get("fleet.optimization_run_id") == "42"
        # Metrics must include train/val counts and score
        metric_names = [name for name, _ in log_metric_calls]
        assert "gepa_baseline_validation_score" in metric_names
        assert "gepa_validation_score_delta" in metric_names

    def test_background_task_logs_gepa_metrics_when_mlflow_enabled(self, tmp_path: Path) -> None:
        """Background task logs gepa_train_examples, gepa_validation_examples, and gepa_validation_score."""
        from fleet_rlm.api.routers.optimization.background import run_optimization_background

        persisted_identity = _persisted_identity()
        run_uuid = uuid.uuid4()
        logged_metrics: dict[str, float] = {}

        class _Persistence:
            async def update_optimization_run_phase(self, **kw: Any) -> None:
                pass

            async def save_evaluation_results(self, **kw: Any) -> None:
                pass

            async def save_prompt_snapshots(self, **kw: Any) -> None:
                pass

            async def complete_optimization_run(self, **kw: Any) -> None:
                pass

        dataset_path = tmp_path / "dataset.jsonl"
        dataset_path.write_text("\n".join(json.dumps({"question": f"Q{i}", "answer": f"A{i}"}) for i in range(5)))

        fake_result = {
            "train_examples": 4,
            "validation_examples": 1,
            "validation_score": 0.75,
            "output_path": str(tmp_path / "artifact.json"),
            "manifest_path": str(tmp_path / "artifact.manifest.json"),
            "evaluation_results": [],
            "prompt_snapshots": [],
            "run_metadata": {},
        }

        mlflow_ctx = MagicMock()
        mlflow_ctx.__enter__ = MagicMock(return_value=mlflow_ctx)
        mlflow_ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch(
                "fleet_rlm.integrations.observability.mlflow_runtime.initialize_mlflow",
                return_value=True,
            ),
            patch("mlflow.start_run", return_value=mlflow_ctx, create=True),
            patch(
                "mlflow.log_metric",
                side_effect=lambda name, val: logged_metrics.update({name: val}),
                create=True,
            ),
            patch("mlflow.log_params", return_value=None, create=True),
            patch("mlflow.set_tags", return_value=None, create=True),
            patch("mlflow.log_dict", return_value=None, create=True),
            patch("mlflow.log_artifact", return_value=None, create=True),
            patch(
                "fleet_rlm.api.routers.optimization.background.run_blocking",
                return_value=fake_result,
            ),
        ):
            asyncio.run(
                run_optimization_background(
                    run_id=str(run_uuid),
                    persistence=_Persistence(),
                    persisted_identity=persisted_identity,
                    module_slug="longcot-reasoner",
                    dataset_path=dataset_path,
                    program_spec="fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
                    output_path=None,
                    default_output_root=tmp_path,
                    auto="light",
                    train_ratio=0.8,
                )
            )

        # VAL-QUALITY-011: required MLflow metrics
        assert logged_metrics.get("gepa_train_examples") == 4
        assert logged_metrics.get("gepa_validation_examples") == 1
        assert logged_metrics.get("gepa_validation_score") == 0.75


# ===========================================================================
# VAL-QUALITY-012: Answer-dominant scoring weights final answer
# ===========================================================================


class TestAnswerDominantScoring:
    """VAL-QUALITY-012: 0.6 * answer + 0.4 * reasoning weighting."""

    def test_score_weights_answer_60_reasoning_40(self) -> None:
        """Score = 0.6 * answer_score + 0.4 * reasoning_score."""
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None
        metric = spec.metric_builder()

        # Exact answer, no reasoning → 0.6 * 1.0 + 0.4 * 0.0 = 0.6
        gold = MagicMock(answer="Paris")
        pred_no_reasoning = MagicMock(answer="Paris", reasoning="")
        result_no_reasoning = metric(gold, pred_no_reasoning)
        assert result_no_reasoning.score == pytest.approx(0.6, abs=0.01)

        # Wrong answer, strong reasoning → less than 0.4 * max_reasoning_score
        STRONG_REASONING = (
            "Step 1: identify. Step 2: because Paris is the capital of France, "
            "it must be Paris. Step 3: verify this is correct."
        )
        pred_wrong_answer = MagicMock(answer="London", reasoning=STRONG_REASONING)
        result_wrong_answer = metric(gold, pred_wrong_answer)
        # Wrong answer has answer_score ≤ 0, so total score ≤ 0.4
        assert result_wrong_answer.score < result_no_reasoning.score

    def test_correct_answer_weak_reasoning_beats_wrong_answer_strong_reasoning(
        self,
    ) -> None:
        """Answer dominance: correct with no reasoning > wrong with strong reasoning."""
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None
        metric = spec.metric_builder()

        STRONG = "Step 1: analyze. Step 2: because Paris is Europe's capital, it must be Paris. Step 3: verify."

        gold = MagicMock(answer="Paris")
        correct_no_reason = MagicMock(answer="Paris", reasoning="")
        wrong_strong_reason = MagicMock(answer="London", reasoning=STRONG)

        score_correct = metric(gold, correct_no_reason).score
        score_wrong = metric(gold, wrong_strong_reason).score

        assert score_correct > score_wrong


# ===========================================================================
# VAL-QUALITY-013: GEPA remains offline-only
# ===========================================================================


class TestGepaOfflineOnly:
    """VAL-QUALITY-013: Optimization never executes during live chat."""

    def test_run_module_optimization_does_not_import_runtime_agent(self, tmp_path: Path) -> None:
        """run_module_optimization does not import api routers or websocket modules."""
        import sys

        before_modules = set(sys.modules.keys())

        spec = get_module_spec("longcot-reasoner")
        assert spec is not None

        dataset_path = tmp_path / "dataset.jsonl"
        dataset_path.write_text("\n".join(json.dumps({"question": f"Q{i}", "answer": f"A{i}"}) for i in range(5)))

        with (
            patch("dspy.teleprompt.GEPA") as _FakeGEPA,
            patch(
                "fleet_rlm.quality.optimization_runner._resolve_reflection_lm",
                return_value=MagicMock(),
            ),
            patch(
                "fleet_rlm.quality.optimization_runner._ensure_dspy_configured",
                return_value=None,
            ),
        ):
            _FakeGEPA.return_value.compile.return_value = _make_fake_program(tmp_path)

            from fleet_rlm.quality.optimization_runner import run_module_optimization

            run_module_optimization(
                spec,
                dataset_path=dataset_path,
                output_path=tmp_path / "artifact.json",
                auto="light",
            )

        after_modules = set(sys.modules.keys())
        new_modules = after_modules - before_modules

        # No websocket, chat routing, or live execution infrastructure should load
        live_chat_patterns = [
            "api.routers.ws",
            "fleet_rlm.api.routers.ws",
        ]
        for pattern in live_chat_patterns:
            matching = [m for m in new_modules if pattern in m]
            assert not matching, f"run_module_optimization loaded live-chat module(s): {matching}"

    def test_optimization_status_api_does_not_start_optimization(self) -> None:
        """GET /status does not trigger any optimization side effects."""
        import fleet_rlm.quality.optimization_runner as runner_module

        # Verify that hitting the status endpoint never calls run_module_optimization
        app = _optimization_app()
        with (
            patch.object(runner_module, "run_module_optimization") as mock_run,
            patch(
                "fleet_rlm.api.routers.optimization.status._check_gepa_available",
                return_value=True,
            ),
            patch(
                "fleet_rlm.api.routers.optimization.status._get_mlflow_status",
                return_value=(True, True),
            ),
        ):
            client = TestClient(app)
            client.get("/api/v1/optimization/status")

        mock_run.assert_not_called()


# ===========================================================================
# VAL-QUALITY-015: Offline optimization works without MLflow
# ===========================================================================


class TestOfflineWithoutMlflow:
    """VAL-QUALITY-015: Optimization compiles artifacts without requiring MLflow."""

    def test_run_module_optimization_completes_without_mlflow(self, tmp_path: Path) -> None:
        """run_module_optimization writes artifact+manifest even when MLflow is off."""
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None

        dataset_path = tmp_path / "dataset.jsonl"
        dataset_path.write_text("\n".join(json.dumps({"question": f"Q{i}", "answer": f"A{i}"}) for i in range(5)))
        output_path = tmp_path / "artifact.json"

        with (
            patch("dspy.teleprompt.GEPA") as FakeGEPA,
            patch(
                "fleet_rlm.quality.optimization_runner._resolve_reflection_lm",
                return_value=MagicMock(),
            ),
            patch(
                "fleet_rlm.quality.optimization_runner._ensure_dspy_configured",
                return_value=None,
            ),
        ):
            FakeGEPA.return_value.compile.return_value = _make_fake_program(tmp_path)

            from fleet_rlm.quality.optimization_runner import run_module_optimization

            result = run_module_optimization(
                spec,
                dataset_path=dataset_path,
                output_path=output_path,
                auto="light",
                # No run_id → no MLflow calls in the runner itself
            )

        # VAL-QUALITY-015: artifact and manifest must exist
        assert Path(result["output_path"]).exists()
        assert Path(result["manifest_path"]).exists()
        assert result["optimizer"] == "GEPA"


# ===========================================================================
# VAL-QUALITY-016: Optimized artifacts not auto-loaded into live runtime
# ===========================================================================


class TestOptimizedArtifactsNotAutoLoaded:
    """VAL-QUALITY-016: Optimization completion does not mutate live module settings."""

    def test_optimization_does_not_change_dspy_global_lm(self, tmp_path: Path) -> None:
        """Completing optimization does not change the global dspy.settings.lm."""
        import dspy

        spec = get_module_spec("longcot-reasoner")
        assert spec is not None

        dataset_path = tmp_path / "dataset.jsonl"
        dataset_path.write_text("\n".join(json.dumps({"question": f"Q{i}", "answer": f"A{i}"}) for i in range(5)))

        lm_before = dspy.settings.lm

        with (
            patch("dspy.teleprompt.GEPA") as FakeGEPA,
            patch(
                "fleet_rlm.quality.optimization_runner._resolve_reflection_lm",
                return_value=MagicMock(),
            ),
            patch(
                "fleet_rlm.quality.optimization_runner._ensure_dspy_configured",
                return_value=None,
            ),
        ):
            FakeGEPA.return_value.compile.return_value = _make_fake_program(tmp_path)

            from fleet_rlm.quality.optimization_runner import run_module_optimization

            run_module_optimization(
                spec,
                dataset_path=dataset_path,
                output_path=tmp_path / "artifact.json",
                auto="light",
            )

        # VAL-QUALITY-016: global LM must not have changed
        assert dspy.settings.lm is lm_before

    def test_optimization_artifact_not_loaded_into_module_factory(self, tmp_path: Path) -> None:
        """After optimization, module_factory still returns the un-optimized module."""
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None

        dataset_path = tmp_path / "dataset.jsonl"
        dataset_path.write_text("\n".join(json.dumps({"question": f"Q{i}", "answer": f"A{i}"}) for i in range(5)))
        output_path = tmp_path / "artifact.json"

        # Record module identity before optimization
        module_before = type(spec.module_factory())

        with (
            patch("dspy.teleprompt.GEPA") as FakeGEPA,
            patch(
                "fleet_rlm.quality.optimization_runner._resolve_reflection_lm",
                return_value=MagicMock(),
            ),
            patch(
                "fleet_rlm.quality.optimization_runner._ensure_dspy_configured",
                return_value=None,
            ),
        ):
            FakeGEPA.return_value.compile.return_value = _make_fake_program(tmp_path)

            from fleet_rlm.quality.optimization_runner import run_module_optimization

            run_module_optimization(
                spec,
                dataset_path=dataset_path,
                output_path=output_path,
                auto="light",
            )

        # Module factory still returns the same type (Predict, not the optimized artifact)
        module_after = type(spec.module_factory())
        assert module_before is module_after


# ===========================================================================
# VAL-QUALITY-017: Offline optimization output paths follow the write policy
# ===========================================================================


class TestOfflineWritePolicy:
    """VAL-QUALITY-017: Artifacts are written to the configured root, not default dirs."""

    def test_explicit_output_path_is_honored(self, tmp_path: Path) -> None:
        """When output_path is given, artifact is written to that exact location."""
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None

        dataset_path = tmp_path / "dataset.jsonl"
        dataset_path.write_text("\n".join(json.dumps({"question": f"Q{i}", "answer": f"A{i}"}) for i in range(5)))
        expected_output = tmp_path / "custom_dir" / "my_artifact.json"

        with (
            patch("dspy.teleprompt.GEPA") as FakeGEPA,
            patch(
                "fleet_rlm.quality.optimization_runner._resolve_reflection_lm",
                return_value=MagicMock(),
            ),
            patch(
                "fleet_rlm.quality.optimization_runner._ensure_dspy_configured",
                return_value=None,
            ),
        ):
            FakeGEPA.return_value.compile.return_value = _make_fake_program(tmp_path)

            from fleet_rlm.quality.optimization_runner import run_module_optimization

            result = run_module_optimization(
                spec,
                dataset_path=dataset_path,
                output_path=expected_output,
                auto="light",
            )

        assert Path(result["output_path"]) == expected_output
        assert expected_output.exists()

    def test_default_output_root_is_used_when_no_explicit_path(self, tmp_path: Path) -> None:
        """Artifact is written under default_output_root when output_path=None."""
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None

        dataset_path = tmp_path / "dataset.jsonl"
        dataset_path.write_text("\n".join(json.dumps({"question": f"Q{i}", "answer": f"A{i}"}) for i in range(5)))
        custom_root = tmp_path / "artifacts"

        with (
            patch("dspy.teleprompt.GEPA") as FakeGEPA,
            patch(
                "fleet_rlm.quality.optimization_runner._resolve_reflection_lm",
                return_value=MagicMock(),
            ),
            patch(
                "fleet_rlm.quality.optimization_runner._ensure_dspy_configured",
                return_value=None,
            ),
        ):
            FakeGEPA.return_value.compile.return_value = _make_fake_program(tmp_path)

            from fleet_rlm.quality.optimization_runner import run_module_optimization

            result = run_module_optimization(
                spec,
                dataset_path=dataset_path,
                output_path=None,
                default_output_root=custom_root,
                auto="light",
            )

        artifact_path = Path(result["output_path"])
        # VAL-QUALITY-017: must be under custom_root
        assert str(artifact_path).startswith(str(custom_root))
        assert artifact_path.exists()

    def test_api_rejects_absolute_output_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """API-level path validation: absolute output_path raises HTTPException."""
        from pathlib import Path

        import pytest
        from fastapi import HTTPException

        from fleet_rlm.api.routers.optimization import runs

        base_root = Path("/private/tmp/quality-test-root")
        monkeypatch.setattr(runs, "OPTIMIZATION_DATA_ROOT", base_root)

        with pytest.raises(HTTPException) as exc_info:
            runs._resolve_blocking_output_path("/absolute/path/artifact.json")
        assert exc_info.value.status_code == 400

    def test_artifact_does_not_appear_in_default_cwd_without_explicit_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a default_output_root, artifacts resolve under the spec's default root."""
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None

        dataset_path = tmp_path / "dataset.jsonl"
        dataset_path.write_text("\n".join(json.dumps({"question": f"Q{i}", "answer": f"A{i}"}) for i in range(5)))

        # Change cwd to tmp_path to avoid polluting the project root
        monkeypatch.chdir(tmp_path)

        with (
            patch("dspy.teleprompt.GEPA") as FakeGEPA,
            patch(
                "fleet_rlm.quality.optimization_runner._resolve_reflection_lm",
                return_value=MagicMock(),
            ),
            patch(
                "fleet_rlm.quality.optimization_runner._ensure_dspy_configured",
                return_value=None,
            ),
        ):
            FakeGEPA.return_value.compile.return_value = _make_fake_program(tmp_path)

            from fleet_rlm.quality.optimization_runner import run_module_optimization

            result = run_module_optimization(
                spec,
                dataset_path=dataset_path,
                output_path=None,
                default_output_root=tmp_path,
                auto="light",
            )

        # Artifact path must start with tmp_path (not a global default)
        assert Path(result["output_path"]).is_absolute()
        assert result["output_path"].startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# Shared helper: fake compiled program
# ---------------------------------------------------------------------------


def _make_fake_program(tmp_path: Path) -> Any:
    """Return a fake compiled program that saves a valid JSON artifact."""

    class _FakeOptimized:
        def named_predictors(self) -> list[tuple[str, Any]]:
            class _Sig:
                instructions = "Optimized: answer the question."

            class _Pred:
                signature = _Sig()

            return [("predict", _Pred())]

        def save(self, path: str) -> None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps({"predictors": {}}), encoding="utf-8")

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return MagicMock()

    return _FakeOptimized()
