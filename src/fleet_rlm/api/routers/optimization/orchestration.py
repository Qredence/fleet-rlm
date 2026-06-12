"""Request orchestration helpers for GEPA optimization endpoints."""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, HTTPException

from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.integrations.database.repository_optimization import OptimizationRunCreateRequest
from fleet_rlm.integrations.llm_profiles.resolver import (
    build_lm_kwargs_from_resolved,
    resolve_role_config,
)
from fleet_rlm.integrations.llm_profiles.store import resolve_profile_store
from fleet_rlm.integrations.llm_profiles.types import LlmRoleBindingRecord
from fleet_rlm.quality.optimization_dispatch import run_optimization_from_request_fields

from ...runtime_services.common import run_blocking
from ...schemas.optimization import (
    GEPAOptimizationRequest,
    GEPAOptimizationResponse,
    OptimizationRunCreatedResponse,
)
from ._deps import (
    OPTIMIZATION_DATA_ROOT,
    OPTIMIZATION_TIMEOUT_SECONDS,
    _check_gepa_available,
    _parse_uuid_id,
    _require_workspace_id,
    _resolve_dataset_request,
    parse_run_uuid,
)

logger = logging.getLogger(__name__)
GEPA_OPTIMIZER_LABEL = "GEPA"


@dataclass(frozen=True)
class PreparedOptimizationRequest:
    """Resolved execution inputs shared by blocking and async optimization requests."""

    program_spec: str
    dataset_path: Path
    dataset_ref: str
    output_path: Path | None
    skill_path: str | None
    trace_bundle_paths: list[str]
    reflection_lm_config: dict[str, Any] | None


def ensure_optimizer_runtime_available() -> None:
    """Raise an HTTP error when the GEPA optimizer runtime is unavailable."""
    if not _check_gepa_available():
        raise HTTPException(
            status_code=503,
            detail="GEPA teleprompt module is not available.",
        )


def ensure_skill_optimization_runtime_available(request: GEPAOptimizationRequest) -> None:
    """Raise when skill optimization is requested without Daytona credentials."""
    if not request.skill_name and not request.skill_path:
        return
    if not os.environ.get("DAYTONA_API_KEY", "").strip():
        raise HTTPException(
            status_code=503,
            detail=(
                "Skill optimization requires Daytona because the RLM instruction proposer "
                "runs in an isolated sandbox. Configure DAYTONA_API_KEY or optimize a "
                "registered module instead."
            ),
        )


def resolve_effective_program_spec(request: GEPAOptimizationRequest) -> str:
    """Resolve the persisted run target label from a GEPA request."""
    if request.skill_name:
        return f"skill:{request.skill_name}"
    if request.skill_path:
        return f"skill:{request.skill_path}"
    if request.module_slug:
        from fleet_rlm.quality import module_registry

        spec = module_registry.get_module_spec(request.module_slug)
        if spec is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown module slug: {request.module_slug!r}",
            )
        return spec.program_spec
    if not request.program_spec:
        raise HTTPException(
            status_code=400,
            detail="Either module_slug, program_spec, skill_name, or skill_path must be provided.",
        )
    return request.program_spec


def resolve_output_path(output_path: str | None) -> Path | None:
    """Resolve an optional output path under the optimization artifact root."""
    if not output_path:
        return None
    if os.path.isabs(output_path):
        raise HTTPException(
            status_code=400,
            detail="Absolute paths are not allowed. Use a relative path.",
        )
    base_root = os.path.realpath(os.fspath(OPTIMIZATION_DATA_ROOT))
    safe_root = os.path.join(base_root, "")
    resolved_output = os.path.realpath(os.path.join(safe_root, output_path))
    if resolved_output != base_root and not resolved_output.startswith(safe_root):
        raise HTTPException(
            status_code=400,
            detail="Path escapes the allowed data directory.",
        )
    return Path(resolved_output)


def resolve_skill_path(skill_path: str | None) -> str | None:
    """Resolve a skill path with the same artifact-root policy as output paths."""
    resolved = resolve_output_path(skill_path)
    return str(resolved) if resolved is not None else None


def resolve_trace_bundle_paths(paths: list[str] | None) -> list[str]:
    """Resolve trace bundle paths under the optimization artifact root."""
    if not paths:
        return []
    resolved_paths: list[str] = []
    for raw_path in paths:
        normalized = str(raw_path or "").strip()
        if not normalized:
            raise HTTPException(
                status_code=400,
                detail="trace_bundle_paths entries must be non-empty relative paths.",
            )
        resolved = resolve_output_path(normalized)
        if resolved is None:
            raise HTTPException(
                status_code=400,
                detail="trace_bundle_paths entries must be non-empty relative paths.",
            )
        resolved_paths.append(str(resolved))
    return resolved_paths


async def resolve_reflection_lm_config(
    request: GEPAOptimizationRequest,
    persistence_deps: Any,
) -> dict[str, Any] | None:
    """Resolve an optional saved provider/model selection into DSPy LM kwargs."""
    if not request.reflection_profile_id and not request.reflection_model_id:
        return None
    if not request.reflection_profile_id or not request.reflection_model_id:
        raise HTTPException(
            status_code=400,
            detail="reflection_profile_id and reflection_model_id must be provided together.",
        )
    try:
        profile_uuid = uuid.UUID(request.reflection_profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid reflection_profile_id.") from exc

    store = resolve_profile_store(persistence_deps.db_manager)
    profile = await store.get_profile(profile_uuid)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Reflection profile {request.reflection_profile_id} not found.")

    resolved = resolve_role_config(
        role="delegate",
        binding=LlmRoleBindingRecord(
            role="delegate",
            profile_id=profile.id,
            model_id=request.reflection_model_id,
        ),
        profile=profile,
    )
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail="Selected reflection profile is missing credentials or model configuration.",
        )
    return {
        "profile_id": str(profile.id),
        "profile_name": profile.name,
        "model_id": request.reflection_model_id,
        "litellm_model": resolved.litellm_model,
        "lm_kwargs": build_lm_kwargs_from_resolved(resolved, max_tokens=32_000),
    }


def build_run_metadata(
    *,
    request: GEPAOptimizationRequest,
    dataset_ref: str,
    reflection_lm_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the persistent metadata shared by blocking and async GEPA runs."""
    trace_bundle_paths = resolve_trace_bundle_paths(list(request.trace_bundle_paths))
    metadata: dict[str, Any] = {
        "dataset_path": dataset_ref,
        "skill_name": request.skill_name,
        "skill_path": request.skill_path,
        "max_metric_calls": request.max_metric_calls,
        "trace_bundle_paths": trace_bundle_paths,
        "distilled_trace_bundle_path": trace_bundle_paths[0] if trace_bundle_paths else None,
    }
    if reflection_lm_config:
        metadata.update(
            {
                "reflection_profile_id": reflection_lm_config.get("profile_id"),
                "reflection_profile_name": reflection_lm_config.get("profile_name"),
                "reflection_model_id": reflection_lm_config.get("model_id"),
                "reflection_litellm_model": reflection_lm_config.get("litellm_model"),
            }
        )
    return metadata


async def prepare_optimization_request(
    *,
    request: GEPAOptimizationRequest,
    persistence: Any,
    persistence_deps: Any,
    persisted_identity: IdentityUpsertResult,
) -> PreparedOptimizationRequest:
    """Resolve all execution inputs for one GEPA optimization request."""
    ensure_skill_optimization_runtime_available(request)
    program_spec = resolve_effective_program_spec(request)
    dataset_path, dataset_ref = await _resolve_dataset_request(
        request,
        persistence=persistence,
        persisted_identity=persisted_identity,
    )
    return PreparedOptimizationRequest(
        program_spec=program_spec,
        dataset_path=dataset_path,
        dataset_ref=dataset_ref,
        output_path=resolve_output_path(request.output_path),
        skill_path=resolve_skill_path(request.skill_path),
        trace_bundle_paths=resolve_trace_bundle_paths(list(request.trace_bundle_paths)),
        reflection_lm_config=await resolve_reflection_lm_config(request, persistence_deps),
    )


async def create_blocking_run_record(
    *,
    request: GEPAOptimizationRequest,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    program_spec: str,
    dataset_ref: str,
    reflection_lm_config: dict[str, Any] | None,
) -> str | None:
    """Best-effort persistence record creation for the blocking endpoint."""
    workspace_id = _require_workspace_id(persisted_identity)
    dataset_uuid = (
        _parse_uuid_id(
            request.dataset_id,
            detail=f"Dataset {request.dataset_id} not found.",
        )
        if request.dataset_id is not None
        else None
    )
    try:
        created_run = await persistence.create_optimization_run(
            OptimizationRunCreateRequest(
                tenant_id=persisted_identity.tenant_id,
                workspace_id=workspace_id,
                created_by_user_id=persisted_identity.user_id,
                optimizer=GEPA_OPTIMIZER_LABEL,
                program_spec=program_spec,
                module_slug=request.module_slug,
                dataset_id=dataset_uuid,
                auto=request.auto,
                train_ratio=request.train_ratio,
                metadata_json=build_run_metadata(
                    request=request,
                    dataset_ref=dataset_ref,
                    reflection_lm_config=reflection_lm_config,
                ),
            )
        )
        return str(created_run.id)
    except Exception as exc:
        logger.exception("Failed to create optimization run record", exc_info=exc)
        raise HTTPException(
            status_code=500,
            detail="Failed to create optimization run record.",
        ) from exc


async def execute_blocking_optimization(
    *,
    request: GEPAOptimizationRequest,
    dataset: Path,
    output_path: Path | None,
    resolved_skill_path: str | None,
    program_spec: str,
    reflection_lm_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run one blocking optimization request through the worker pool."""

    return await run_blocking(
        partial(
            run_optimization_from_request_fields,
            module_slug=request.module_slug,
            program_spec=program_spec,
            dataset_path=dataset,
            output_path=output_path,
            default_output_root=OPTIMIZATION_DATA_ROOT,
            auto=request.auto,
            max_metric_calls=request.max_metric_calls,
            train_ratio=request.train_ratio,
            optimizer=request.optimizer,
            run_id=None,
            skill_name=request.skill_name,
            skill_path=resolved_skill_path,
            trace_bundle_paths=resolve_trace_bundle_paths(list(request.trace_bundle_paths)),
            reflection_lm_config=reflection_lm_config,
        ),
        timeout=OPTIMIZATION_TIMEOUT_SECONDS,
    )


async def mark_blocking_run_failed(
    *,
    db_run_id: str | None,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    error: str,
) -> None:
    """Best-effort failure persistence for a blocking endpoint run."""
    if db_run_id is None:
        return
    try:
        from .run_persistence import persist_optimization_run_failure

        run_uuid = parse_run_uuid(db_run_id)
        await persist_optimization_run_failure(
            persistence=persistence,
            persisted_identity=persisted_identity,
            run_uuid=run_uuid,
            error=error,
        )
    except Exception:
        logger.exception("Failed to mark GEPA optimization run %s as failed", db_run_id)


async def mark_blocking_run_complete(
    *,
    db_run_id: str | None,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    result: dict[str, Any],
) -> None:
    """Best-effort completion persistence for a blocking endpoint run."""
    if db_run_id is None:
        return
    try:
        from .run_persistence import persist_optimization_run_success

        run_uuid = parse_run_uuid(db_run_id)
        await persist_optimization_run_success(
            persistence=persistence,
            persisted_identity=persisted_identity,
            run_uuid=run_uuid,
            result=result,
        )
    except Exception:
        logger.exception("Failed to mark GEPA optimization run %s as complete", db_run_id)


def blocking_optimization_response(
    *,
    result: dict[str, Any],
    request: GEPAOptimizationRequest,
    program_spec: str,
) -> GEPAOptimizationResponse:
    """Convert a successful blocking run result to the public response schema."""
    return GEPAOptimizationResponse(
        ok=True,
        optimizer=result.get("optimizer", "GEPA"),
        program_spec=result.get("program_spec", program_spec),
        train_examples=result.get("train_examples", 0),
        validation_examples=result.get("validation_examples", 0),
        validation_score=result.get("validation_score"),
        output_path=result.get("output_path"),
        manifest_path=result.get("manifest_path"),
        feedback_summary=result.get("feedback_summary"),
        module_slug=request.module_slug,
        reflection_profile_id=result.get("run_metadata", {}).get("reflection_profile_id"),
        reflection_model_id=result.get("run_metadata", {}).get("reflection_model_id"),
        distilled_trace_bundle_path=result.get("run_metadata", {}).get("distilled_trace_bundle_path"),
    )


def failed_blocking_optimization_response(
    *,
    exc: Exception,
    request: GEPAOptimizationRequest,
    program_spec: str,
) -> GEPAOptimizationResponse:
    """Convert a blocking run exception to the public failure response schema."""
    return GEPAOptimizationResponse(
        ok=False,
        program_spec=program_spec,
        train_examples=0,
        validation_examples=0,
        module_slug=request.module_slug,
        error=str(exc),
    )


async def create_async_run_and_enqueue(
    *,
    request: GEPAOptimizationRequest,
    background_tasks: BackgroundTasks,
    persistence: Any,
    persistence_deps: Any,
    persisted_identity: IdentityUpsertResult,
) -> OptimizationRunCreatedResponse:
    """Create an async optimization run and enqueue its background task."""
    prepared = await prepare_optimization_request(
        request=request,
        persistence=persistence,
        persistence_deps=persistence_deps,
        persisted_identity=persisted_identity,
    )

    db_row = await persistence.create_optimization_run(
        OptimizationRunCreateRequest(
            tenant_id=persisted_identity.tenant_id,
            workspace_id=_require_workspace_id(persisted_identity),
            created_by_user_id=persisted_identity.user_id,
            optimizer=GEPA_OPTIMIZER_LABEL,
            program_spec=prepared.program_spec,
            module_slug=request.module_slug,
            dataset_id=(
                _parse_uuid_id(
                    request.dataset_id,
                    detail=f"Dataset {request.dataset_id} not found.",
                )
                if request.dataset_id is not None
                else None
            ),
            auto=request.auto,
            train_ratio=request.train_ratio,
            metadata_json=build_run_metadata(
                request=request,
                dataset_ref=prepared.dataset_ref,
                reflection_lm_config=prepared.reflection_lm_config,
            ),
        )
    )
    run_id = str(db_row.id)
    from .background import run_optimization_background

    background_tasks.add_task(
        run_optimization_background,
        run_id=run_id,
        persistence=persistence,
        persisted_identity=persisted_identity,
        module_slug=request.module_slug,
        dataset_path=prepared.dataset_path,
        program_spec=prepared.program_spec,
        output_path=prepared.output_path,
        default_output_root=OPTIMIZATION_DATA_ROOT,
        auto=request.auto,
        max_metric_calls=request.max_metric_calls,
        train_ratio=request.train_ratio,
        optimizer=request.optimizer,
        skill_name=request.skill_name,
        skill_path=prepared.skill_path,
        trace_bundle_paths=prepared.trace_bundle_paths,
        reflection_lm_config=prepared.reflection_lm_config,
    )
    return OptimizationRunCreatedResponse(run_id=run_id, status="running")
