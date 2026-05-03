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

from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

from ...runtime_services.common import run_blocking
from ._deps import OPTIMIZATION_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


def _planner_execution_context() -> Any:
    """Build a thread-local DSPy context for offline optimization work."""
    import dspy

    from fleet_rlm.runtime.config import build_dspy_context, get_planner_lm_from_env

    planner_lm = get_planner_lm_from_env()
    if planner_lm is None:
        # Fall back to the already-configured in-process DSPy LM (e.g. configured at
        # server start via fleet-rlm bootstrap) rather than hard-failing.
        planner_lm = dspy.settings.lm
    if planner_lm is None:
        raise RuntimeError(
            "DSPy LM is not configured. Set DSPY_LM_MODEL and DSPY_LLM_API_KEY "
            "in the environment before running offline optimization."
        )
    return build_dspy_context(lm=planner_lm)


def _run_module_optimization_with_thread_config(
    *,
    module_slug: str,
    dataset_path: Path,
    output_path: Path | None,
    default_output_root: Path | None,
    auto: Literal["light", "medium", "heavy"],
    train_ratio: float,
    run_id: int | None,
) -> dict[str, Any]:
    """Configure DSPy context inside the worker thread before module optimization."""
    from fleet_rlm.runtime.quality.module_registry import get_module_spec
    from fleet_rlm.runtime.quality.optimization_runner import run_module_optimization

    spec = get_module_spec(module_slug)
    if spec is None:
        raise ValueError(f"Unknown module slug: {module_slug!r}")
    with _planner_execution_context():
        return cast(
            dict[str, Any],
            run_module_optimization(
                spec,
                dataset_path=dataset_path,
                output_path=output_path,
                default_output_root=default_output_root,
                train_ratio=train_ratio,
                auto=auto,
                run_id=run_id,
            ),
        )


def _run_program_optimization_with_thread_config(
    *,
    dataset_path: Path,
    program_spec: str,
    output_path: Path | None,
    auto: Literal["light", "medium", "heavy"],
    train_ratio: float,
) -> dict[str, Any]:
    """Configure DSPy context inside the worker thread before generic GEPA optimization."""
    from fleet_rlm.runtime.quality.gepa_optimization import optimize_program_with_gepa

    with _planner_execution_context():
        return optimize_program_with_gepa(
            dataset_path=dataset_path,
            program_spec=program_spec,
            output_path=output_path,
            auto=auto,
            train_ratio=train_ratio,
            source="api_background",
        )


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
            metadata_json=result.get("run_metadata"),
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
            save_evaluation_results,
            save_prompt_snapshots,
            update_optimization_run_phase,
        )

        self._update_phase = update_optimization_run_phase
        self._complete = complete_optimization_run
        self._fail = fail_optimization_run
        self._save_evaluation_results = save_evaluation_results
        self._save_prompt_snapshots = save_prompt_snapshots

    async def update_phase(self, run_id: str | int, phase: str) -> None:
        try:
            self._update_phase(int(run_id), phase=phase)
        except Exception:
            logger.debug("Failed to update phase for run %s", run_id)

    async def save_results(self, run_id: str | int, result: dict) -> None:
        try:
            self._save_evaluation_results(
                int(run_id),
                list(result.get("evaluation_results", [])),
            )
            self._save_prompt_snapshots(
                int(run_id),
                list(result.get("prompt_snapshots", [])),
            )
        except Exception:
            logger.debug("Failed to persist local review artifacts for run %s", run_id)

    async def complete(self, run_id: str | int, result: dict) -> None:
        self._complete(
            int(run_id),
            train_examples=result.get("train_examples", 0),
            validation_examples=result.get("validation_examples", 0),
            validation_score=result.get("validation_score"),
            output_path=result.get("output_path"),
            manifest_path=result.get("manifest_path"),
            metadata_json=result.get("run_metadata"),
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
        log_gepa_mlflow_result_metadata,
        log_gepa_mlflow_run_metadata,
    )

    store: _RepoPersistence | _LocalPersistence
    if persistence == "repo":
        if repository is None or identity is None:
            raise ValueError(
                "repository and identity are required for repo persistence"
            )
        store = _RepoPersistence(repository, identity)
    else:
        store = _LocalPersistence()

    # -- MLflow autologging (best-effort, never blocks the run) -----------
    mlflow_ctx: Any = None
    _mlflow_log_metric: Any = None
    _mlflow_log_params: Any = None
    _mlflow_set_tags: Any = None
    _mlflow_log_dict: Any = None
    _mlflow_log_artifact: Any = None
    if module_slug:
        try:
            import mlflow
        except ImportError:
            logger.debug("MLflow package unavailable for run %s", run_id, exc_info=True)
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
                    _mlflow_log_dict = getattr(mlflow, "log_dict", None)
                    _mlflow_log_artifact = getattr(mlflow, "log_artifact", None)
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
                logger.debug("MLflow setup skipped for run %s", run_id, exc_info=True)

    try:
        await store.update_phase(run_id, "loading")

        if module_slug:
            await store.update_phase(run_id, "compiling")

            result = await run_blocking(
                partial(
                    _run_module_optimization_with_thread_config,
                    module_slug=module_slug,
                    dataset_path=dataset_path,
                    output_path=output_path,
                    default_output_root=default_output_root,
                    train_ratio=train_ratio,
                    auto=auto,
                    run_id=None if persistence == "repo" else int(run_id),
                ),
                timeout=OPTIMIZATION_TIMEOUT_SECONDS,
            )
        else:
            await store.update_phase(run_id, "compiling")

            result = await run_blocking(
                partial(
                    _run_program_optimization_with_thread_config,
                    dataset_path=dataset_path,
                    program_spec=program_spec,
                    output_path=output_path,
                    auto=auto,
                    train_ratio=train_ratio,
                ),
                timeout=OPTIMIZATION_TIMEOUT_SECONDS,
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
            log_gepa_mlflow_result_metadata(
                result=result,
                run_id=run_id,
                log_metric=cast(Any, _mlflow_log_metric),
                log_params=cast(Any, _mlflow_log_params),
                set_tags=cast(Any, _mlflow_set_tags),
                log_dict=cast(Any, _mlflow_log_dict),
                log_artifact=cast(Any, _mlflow_log_artifact),
            )
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
