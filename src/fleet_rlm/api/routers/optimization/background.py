"""Background task execution for GEPA optimization runs.

This module merges the former ``_run_optimization_background`` (Postgres
persistence) and ``_run_optimization_background_local`` (legacy local-store
persistence) into a single function parameterised by a ``persistence``
argument.
"""

from __future__ import annotations

import logging
import uuid
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

from fleet_rlm.integrations.database.types import IdentityUpsertResult

from ...runtime_services.common import run_blocking
from ._deps import OPTIMIZATION_TIMEOUT_SECONDS, configure_planner_from_env

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Persistence helpers — thin wrappers that abstract DB vs local-store
# ---------------------------------------------------------------------------


class _RepoPersistence:
    """Postgres persistence via the FleetRepository."""

    def __init__(self, repository: Any, identity: IdentityUpsertResult) -> None:
        self.repository = repository
        self.identity = identity

    async def update_phase(self, run_id: str | int, phase: str) -> None:
        run_uuid = uuid.UUID(str(run_id))
        await self.repository.update_optimization_run_phase(
            tenant_id=self.identity.tenant_id,
            run_id=run_uuid,
            workspace_id=self.identity.workspace_id,
            created_by_user_id=self.identity.user_id,
            phase=phase,
        )

    async def save_results(self, run_id: str | int, result: dict) -> None:
        run_uuid = uuid.UUID(str(run_id))
        await self.repository.save_evaluation_results(
            tenant_id=self.identity.tenant_id,
            run_id=run_uuid,
            workspace_id=self.identity.workspace_id,
            created_by_user_id=self.identity.user_id,
            results=result.get("evaluation_results", []),
        )
        await self.repository.save_prompt_snapshots(
            tenant_id=self.identity.tenant_id,
            run_id=run_uuid,
            workspace_id=self.identity.workspace_id,
            created_by_user_id=self.identity.user_id,
            snapshots=result.get("prompt_snapshots", []),
        )

    async def complete(self, run_id: str | int, result: dict) -> None:
        run_uuid = uuid.UUID(str(run_id))
        await self.repository.complete_optimization_run(
            tenant_id=self.identity.tenant_id,
            run_id=run_uuid,
            workspace_id=self.identity.workspace_id,
            created_by_user_id=self.identity.user_id,
            train_examples=result.get("train_examples", 0),
            validation_examples=result.get("validation_examples", 0),
            validation_score=result.get("validation_score"),
            output_path=result.get("output_path"),
            manifest_path=result.get("manifest_path"),
        )

    async def fail(self, run_id: str | int, error: str) -> None:
        run_uuid = uuid.UUID(str(run_id))
        await self.repository.fail_optimization_run(
            tenant_id=self.identity.tenant_id,
            run_id=run_uuid,
            workspace_id=self.identity.workspace_id,
            created_by_user_id=self.identity.user_id,
            error=error,
        )


class _LocalPersistence:
    """Legacy local-store persistence (synchronous, thread-based)."""

    def __init__(self) -> None:
        from fleet_rlm.integrations.local_store import (
            complete_optimization_run,
            fail_optimization_run,
            update_optimization_run_phase,
        )

        self._update_phase = update_optimization_run_phase
        self._complete = complete_optimization_run
        self._fail = fail_optimization_run

    async def update_phase(self, run_id: str | int, phase: str) -> None:
        try:
            self._update_phase(int(run_id), phase=phase)
        except Exception:
            logger.debug("Failed to update phase for run %s", run_id)

    async def save_results(self, run_id: str | int, result: dict) -> None:
        # Local store does not persist per-example evaluation results
        pass

    async def complete(self, run_id: str | int, result: dict) -> None:
        self._complete(
            int(run_id),
            train_examples=result.get("train_examples", 0),
            validation_examples=result.get("validation_examples", 0),
            validation_score=result.get("validation_score"),
            output_path=result.get("output_path"),
            manifest_path=result.get("manifest_path"),
        )

    async def fail(self, run_id: str | int, error: str) -> None:
        self._fail(int(run_id), error=error)


# ---------------------------------------------------------------------------
# Unified background task
# ---------------------------------------------------------------------------


async def run_optimization_background(
    *,
    run_id: str | int,
    persistence: Literal["repo", "local"],
    repository: Any | None = None,
    identity: IdentityUpsertResult | None = None,
    module_slug: str | None,
    dataset_path: Path,
    program_spec: str,
    output_path: Path | None,
    default_output_root: Path | None,
    auto: Literal["light", "medium", "heavy"],
    train_ratio: float,
) -> None:
    """Execute GEPA optimization in a background task.

    ``persistence`` controls how run state is tracked:
    * ``"repo"`` -- Postgres via *repository* + *identity* (requires both).
    * ``"local"`` -- Legacy local-store (SQLite).
    """
    from fleet_rlm.runtime.quality.gepa_optimization import (
        log_gepa_mlflow_run_metadata,
    )

    store: _RepoPersistence | _LocalPersistence
    if persistence == "repo":
        assert repository is not None and identity is not None
        store = _RepoPersistence(repository, identity)
    else:
        store = _LocalPersistence()

    # -- MLflow autologging (best-effort, never blocks the run) -----------
    mlflow_ctx: Any = None
    _mlflow_log_metric: Any = None
    _mlflow_log_params: Any = None
    _mlflow_set_tags: Any = None
    if module_slug:
        try:
            import mlflow
        except ImportError:
            logger.debug(
                "MLflow package unavailable for run %s", run_id, exc_info=True
            )
        else:
            from fleet_rlm.integrations.observability.config import MlflowConfig
            from fleet_rlm.integrations.observability.mlflow_runtime import (
                initialize_mlflow,
            )

            try:
                resolved_cfg = MlflowConfig.from_env().model_copy(
                    update={
                        "dspy_log_compiles": True,
                        "dspy_log_evals": True,
                        "dspy_log_traces_from_compile": True,
                        "dspy_log_traces_from_eval": True,
                    }
                )
                if initialize_mlflow(resolved_cfg):
                    start_run = getattr(mlflow, "start_run", None)
                    _mlflow_log_metric = getattr(mlflow, "log_metric", None)
                    _mlflow_log_params = getattr(mlflow, "log_params", None)
                    _mlflow_set_tags = getattr(mlflow, "set_tags", None)
                    run_label = f"GEPA::{module_slug}"
                    if start_run is not None:
                        mlflow_ctx = cast(Any, start_run)(run_name=run_label)
                        mlflow_ctx.__enter__()
                        log_gepa_mlflow_run_metadata(
                            dataset_path=dataset_path,
                            program_spec=program_spec,
                            auto=auto,
                            train_ratio=train_ratio,
                            module_slug=module_slug,
                            source="api_background",
                            log_params=cast(Any, _mlflow_log_params),
                            set_tags=cast(Any, _mlflow_set_tags),
                        )
                else:
                    logger.debug(
                        "MLflow unavailable for run %s -- proceeding without tracking",
                        run_id,
                    )
            except Exception:
                logger.debug(
                    "MLflow setup skipped for run %s", run_id, exc_info=True
                )

    try:
        configure_planner_from_env()
        await store.update_phase(run_id, "loading")

        if module_slug:
            from fleet_rlm.runtime.quality.module_registry import get_module_spec
            from fleet_rlm.runtime.quality.optimization_runner import (
                run_module_optimization,
            )

            spec = get_module_spec(module_slug)
            if spec is None:
                raise ValueError(f"Unknown module slug: {module_slug!r}")
            await store.update_phase(run_id, "compiling")

            if persistence == "repo":
                result = dict(
                    await run_blocking(
                        partial(
                            run_module_optimization,
                            spec,
                            dataset_path=dataset_path,
                            output_path=output_path,
                            default_output_root=default_output_root,
                            train_ratio=train_ratio,
                            auto=auto,
                            run_id=None,
                        ),
                        timeout=OPTIMIZATION_TIMEOUT_SECONDS,
                    )
                )
            else:
                result = dict(
                    run_module_optimization(
                        spec,
                        dataset_path=dataset_path,
                        output_path=output_path,
                        default_output_root=default_output_root,
                        train_ratio=train_ratio,
                        auto=auto,
                        run_id=int(run_id),
                    )
                )
        else:
            from fleet_rlm.runtime.quality.gepa_optimization import (
                optimize_program_with_gepa,
            )

            await store.update_phase(run_id, "compiling")

            if persistence == "repo":
                result = await run_blocking(
                    partial(
                        optimize_program_with_gepa,
                        dataset_path=dataset_path,
                        program_spec=program_spec,
                        output_path=output_path,
                        auto=auto,
                        train_ratio=train_ratio,
                        source="api_background",
                    ),
                    timeout=OPTIMIZATION_TIMEOUT_SECONDS,
                )
            else:
                result = optimize_program_with_gepa(
                    dataset_path=dataset_path,
                    program_spec=program_spec,
                    output_path=output_path,
                    auto=auto,
                    train_ratio=train_ratio,
                    source="api_background",
                )

        # Log validation score to MLflow when available
        try:
            if _mlflow_log_metric is not None:
                cast(Any, _mlflow_log_metric)(
                    "gepa_train_examples", result.get("train_examples", 0)
                )
                cast(Any, _mlflow_log_metric)(
                    "gepa_validation_examples", result.get("validation_examples", 0)
                )
                val_score = result.get("validation_score")
                if val_score is not None:
                    cast(Any, _mlflow_log_metric)("gepa_validation_score", val_score)
        except Exception:
            logger.debug("Failed to log GEPA metrics to MLflow for run %s", run_id)

        await store.update_phase(run_id, "saving")
        await store.save_results(run_id, result)
        await store.complete(run_id, result)
    except Exception as exc:
        logger.exception("Background GEPA optimization failed for run %s", run_id)
        try:
            await store.fail(run_id, str(exc))
        except Exception:
            logger.exception("Failed to mark run %s as failed", run_id)
    finally:
        # Clean up the MLflow run context if one was opened
        if mlflow_ctx is not None:
            try:
                mlflow_ctx.__exit__(None, None, None)
            except Exception:
                logger.debug("Failed to close MLflow run for run %s", run_id)
