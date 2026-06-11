"""Background task execution for optimization runs.

This module delegates run state tracking to the unified ``PersistenceProtocol``
so that the same background logic works for both Postgres and local-store
backends.  Both registry modules and ad-hoc ``module:attr`` program specs run
through :func:`fleet_rlm.quality.optimization_runner.run_module_optimization`;
MLflow tracking is best-effort and never blocks the run.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.quality import module_registry, optimization_runner

from ...runtime_services.common import run_blocking
from ._deps import OPTIMIZATION_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


def _run_optimization_in_thread(
    *,
    module_slug: str | None,
    program_spec: str,
    dataset_path: Path,
    output_path: Path | None,
    default_output_root: Path | None,
    auto: Literal["light", "medium", "heavy"],
    train_ratio: float,
    optimizer: optimization_runner.OptimizerName,
    run_id: int | None,
) -> dict[str, Any]:
    """Run the unified optimization pipeline inside a worker thread.

    The runner scopes its own ``dspy.context`` (planner LM from the
    environment), so no extra DSPy configuration is needed here.
    """
    if module_slug:
        spec = module_registry.get_module_spec(module_slug)
        if spec is None:
            raise ValueError(f"Unknown module slug: {module_slug!r}")
    else:
        spec = optimization_runner.spec_for_program(program_spec)
    return cast(
        dict[str, Any],
        optimization_runner.run_module_optimization(
            spec,
            dataset_path=dataset_path,
            output_path=output_path,
            default_output_root=default_output_root,
            train_ratio=train_ratio,
            auto=auto,
            optimizer=optimizer,
            run_id=run_id,
        ),
    )


def _resolve_run_uuid(run_id: str | int) -> uuid.UUID:
    """Convert a canonical run identifier to a UUID for the persistence protocol."""
    if isinstance(run_id, uuid.UUID):
        return run_id
    try:
        return uuid.UUID(str(run_id))
    except ValueError as exc:
        raise RuntimeError(f"Invalid run_id: {run_id}") from exc


def _try_int_run_id(run_id: str | int) -> int | None:
    """Try to extract an integer run id for MLflow / module optimization tracking."""
    _ = run_id
    return None


# ---------------------------------------------------------------------------
# MLflow logging helpers (best-effort)
# ---------------------------------------------------------------------------


def log_optimization_mlflow_run_metadata(
    *,
    dataset_path: Path,
    program_spec: str,
    auto: Literal["light", "medium", "heavy"] | None,
    train_ratio: float,
    optimizer_label: str,
    module_slug: str | None = None,
    source: str,
    log_params: Callable[[dict[str, Any]], Any] | None = None,
    set_tags: Callable[[dict[str, str]], Any] | None = None,
) -> None:
    """Attach consistent optimization metadata to the active MLflow run."""

    if log_params is not None:
        cast(Any, log_params)(
            {
                "gepa.auto": auto or "none",
                "gepa.train_ratio": train_ratio,
                "gepa.dataset_name": dataset_path.name,
            }
        )

    tags = {
        "fleet.optimizer": optimizer_label,
        "fleet.optimization_source": source,
        "fleet.program_spec": program_spec,
    }
    if module_slug:
        tags["fleet.module_slug"] = module_slug

    if set_tags is not None:
        cast(Any, set_tags)(tags)


def log_optimization_mlflow_result_metadata(
    *,
    result: dict[str, Any],
    run_id: str | int | None = None,
    log_metric: Callable[[str, float], Any] | None = None,
    log_params: Callable[[dict[str, Any]], Any] | None = None,
    set_tags: Callable[[dict[str, str]], Any] | None = None,
    log_dict: Callable[[dict[str, Any], str], Any] | None = None,
    log_artifact: Callable[..., Any] | None = None,
) -> None:
    """Attach review-bundle metrics, provenance, and artifacts to MLflow."""
    review_bundle = result.get("review_bundle")
    if not isinstance(review_bundle, dict):
        return

    holdout = review_bundle.get("holdout")
    holdout_dict = holdout if isinstance(holdout, dict) else {}
    split_reference = holdout_dict.get("split_reference")
    split_reference_dict = split_reference if isinstance(split_reference, dict) else {}
    validation_range = split_reference_dict.get("validation_range")
    validation_range_dict = validation_range if isinstance(validation_range, dict) else {}
    reflection_model = review_bundle.get("reflection_model")
    reflection_model_dict = reflection_model if isinstance(reflection_model, dict) else {}

    if log_metric is not None:
        baseline_score = holdout_dict.get("baseline_score")
        score_delta = holdout_dict.get("score_delta")
        if baseline_score is not None:
            cast(Any, log_metric)("gepa_baseline_validation_score", float(baseline_score))
        if score_delta is not None:
            cast(Any, log_metric)("gepa_validation_score_delta", float(score_delta))

    if log_params is not None:
        params: dict[str, Any] = {}
        start = validation_range_dict.get("start")
        end_exclusive = validation_range_dict.get("end_exclusive")
        if start is not None and end_exclusive is not None:
            params["gepa.validation_split_range"] = f"{start}:{end_exclusive}"
        validation_indexes = split_reference_dict.get("validation_dataset_indexes")
        if isinstance(validation_indexes, list):
            params["gepa.validation_split_count"] = len(validation_indexes)
        if params:
            cast(Any, log_params)(params)

    if set_tags is not None:
        tags: dict[str, str] = {}
        model_name = reflection_model_dict.get("model")
        model_source = reflection_model_dict.get("source")
        if model_name:
            tags["gepa.reflection_model"] = str(model_name)
        if model_source:
            tags["gepa.reflection_model_source"] = str(model_source)
        if run_id is not None:
            tags["fleet.optimization_run_id"] = str(run_id)
        if tags:
            cast(Any, set_tags)(tags)

    if log_dict is not None:
        cast(Any, log_dict)(review_bundle, "optimization_review_bundle.json")

    if log_artifact is not None:
        for path_key in ("manifest_path", "output_path"):
            raw_path = result.get(path_key)
            if not raw_path:
                continue
            path = Path(str(raw_path))
            if path.exists():
                cast(Any, log_artifact)(str(path), artifact_path="optimization")


# ---------------------------------------------------------------------------
# Unified background task
# ---------------------------------------------------------------------------


async def run_optimization_background(
    *,
    run_id: str | int,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    module_slug: str | None,
    dataset_path: Path,
    program_spec: str,
    output_path: Path | None,
    default_output_root: Path | None,
    auto: Literal["light", "medium", "heavy"],
    train_ratio: float,
    optimizer: optimization_runner.OptimizerName = "gepa",
) -> None:
    """Execute an optimization run in a background task.

    Run state is tracked through the unified *persistence* backend.
    """
    run_uuid = _resolve_run_uuid(run_id)
    int_run_id = _try_int_run_id(run_id)
    optimizer_label = "GEPA" if optimizer == "gepa" else "MIPROv2"
    run_target = module_slug or program_spec

    # -- MLflow autologging (best-effort, never blocks the run) -----------
    mlflow_ctx: Any = None
    _mlflow_log_metric: Any = None
    _mlflow_log_params: Any = None
    _mlflow_set_tags: Any = None
    _mlflow_log_dict: Any = None
    _mlflow_log_artifact: Any = None
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
                run_label = f"{optimizer_label}::{run_target}"
                if start_run is not None:
                    mlflow_ctx = cast(Any, start_run)(run_name=run_label)
                    mlflow_ctx.__enter__()
                    log_optimization_mlflow_run_metadata(
                        dataset_path=dataset_path,
                        program_spec=program_spec,
                        auto=auto,
                        train_ratio=train_ratio,
                        optimizer_label=optimizer_label,
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
        await persistence.update_optimization_run_phase(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
            phase="loading",
        )
        await persistence.update_optimization_run_phase(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
            phase="compiling",
        )

        result = await run_blocking(
            partial(
                _run_optimization_in_thread,
                module_slug=module_slug,
                program_spec=program_spec,
                dataset_path=dataset_path,
                output_path=output_path,
                default_output_root=default_output_root,
                train_ratio=train_ratio,
                auto=auto,
                optimizer=optimizer,
                run_id=int_run_id,
            ),
            timeout=OPTIMIZATION_TIMEOUT_SECONDS,
        )

        # Log validation score to MLflow when available
        try:
            if _mlflow_log_metric is not None:
                cast(Any, _mlflow_log_metric)("gepa_train_examples", result.get("train_examples", 0))
                cast(Any, _mlflow_log_metric)("gepa_validation_examples", result.get("validation_examples", 0))
                val_score = result.get("validation_score")
                if val_score is not None:
                    cast(Any, _mlflow_log_metric)("gepa_validation_score", val_score)
                log_optimization_mlflow_result_metadata(
                    result=result,
                    run_id=run_id,
                    log_metric=cast(Any, _mlflow_log_metric),
                    log_params=cast(Any, _mlflow_log_params),
                    set_tags=cast(Any, _mlflow_set_tags),
                    log_dict=cast(Any, _mlflow_log_dict),
                    log_artifact=cast(Any, _mlflow_log_artifact),
                )
        except Exception:
            logger.debug("Failed to log optimization metrics to MLflow for run %s", run_id)

        await persistence.update_optimization_run_phase(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
            phase="saving",
        )
        await persistence.save_evaluation_results(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
            results=result.get("evaluation_results", []),
        )
        await persistence.save_prompt_snapshots(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
            snapshots=result.get("prompt_snapshots", []),
        )
        await persistence.complete_optimization_run(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
            train_examples=result.get("train_examples", 0),
            validation_examples=result.get("validation_examples", 0),
            validation_score=result.get("validation_score"),
            output_path=result.get("output_path"),
            manifest_path=result.get("manifest_path"),
            metadata_json=result.get("run_metadata"),
        )
    except Exception as exc:
        logger.exception("Background optimization failed for run %s", run_id)
        try:
            await persistence.fail_optimization_run(
                tenant_id=persisted_identity.tenant_id,
                run_id=run_uuid,
                workspace_id=persisted_identity.workspace_id,
                created_by_user_id=persisted_identity.user_id,
                error=str(exc),
            )
        except Exception:
            logger.exception("Failed to mark run %s as failed", run_id)
    finally:
        # Clean up the MLflow run context if one was opened
        if mlflow_ctx is not None:
            try:
                mlflow_ctx.__exit__(None, None, None)
            except Exception:
                logger.debug("Failed to close MLflow run for run %s", run_id)
