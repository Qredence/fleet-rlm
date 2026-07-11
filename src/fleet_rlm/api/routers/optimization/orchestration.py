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

from fleet_rlm.db.repos.identity import IdentityUpsertResult
from fleet_rlm.db.repos.optimization import OptimizationRunCreateRequest
from fleet_rlm.integrations.llm_profiles.resolver import (
    build_lm_kwargs_from_resolved,
    resolve_role_config,
)
from fleet_rlm.integrations.llm_profiles.store import resolve_profile_store
from fleet_rlm.integrations.llm_profiles.types import LlmRoleBindingRecord
from fleet_rlm.integrations.persistence_protocol import UnsupportedLocalCapabilityError
from fleet_rlm.quality.contracts import OptimizationRunSpec
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
from .managed_resolution import (
    build_optimization_run_spec,
    effective_wall_clock_seconds,
    resolve_approved_dataset,
    resolve_managed_target,
)

logger = logging.getLogger(__name__)
GEPA_OPTIMIZER_LABEL = "GEPA"


@dataclass(frozen=True, slots=True)
class PreparedOptimizationRequest:
    """Resolved execution inputs shared by blocking and async optimization requests."""

    program_spec: str
    dataset_path: Path
    dataset_ref: str
    dataset_id: uuid.UUID | None
    output_path: Path | None
    skill_path: str | None
    trace_bundle_paths: list[str]
    reflection_lm_config: dict[str, Any] | None
    task_lm_config: dict[str, Any] | None
    search_config: dict[str, Any]
    max_full_evals: int | None
    run_spec: OptimizationRunSpec | None
    run_fingerprint: str | None
    timeout_seconds: int


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
    persisted_identity: IdentityUpsertResult,
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

    store = resolve_profile_store(persistence_deps.db_manager, identity=persisted_identity)
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
        "resolved_model_id": resolved.resolved_model_id,
        "provider_type": resolved.provider_type,
        "lm_kwargs": build_lm_kwargs_from_resolved(resolved, max_tokens=32_000),
    }


async def resolve_task_lm_config(
    request: GEPAOptimizationRequest,
    persistence_deps: Any,
    persisted_identity: IdentityUpsertResult,
) -> dict[str, Any] | None:
    """Resolve the explicit task-model profile for canonical Phase 8 runs."""
    selection = request.task_model
    if selection is None:
        return None
    try:
        profile_uuid = uuid.UUID(selection.profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid task_model.profile_id.") from exc
    store = resolve_profile_store(persistence_deps.db_manager, identity=persisted_identity)
    profile = await store.get_profile(profile_uuid)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Task profile {selection.profile_id} not found.")
    resolved = resolve_role_config(
        role="planner",
        binding=LlmRoleBindingRecord(role="planner", profile_id=profile.id, model_id=selection.model_id),
        profile=profile,
    )
    if resolved is None:
        raise HTTPException(
            status_code=400, detail="Selected task profile is missing credentials or model configuration."
        )
    return {
        "profile_id": str(profile.id),
        "profile_name": profile.name,
        "model_id": selection.model_id,
        "resolved_model_id": resolved.resolved_model_id,
        "provider_type": resolved.provider_type,
        "adapter": "chat",
        "lm_kwargs": build_lm_kwargs_from_resolved(resolved, max_tokens=16_000),
    }


def request_max_full_evals(request: GEPAOptimizationRequest) -> int | None:
    budget = request.budget
    return int(budget.value) if budget is not None and budget.kind == "max_full_evals" else None


def build_run_metadata(
    *,
    request: GEPAOptimizationRequest,
    dataset_ref: str,
    reflection_lm_config: dict[str, Any] | None,
    task_lm_config: dict[str, Any] | None = None,
    run_spec: OptimizationRunSpec | None = None,
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
        "protocol_version": "phase8-v1" if request.target is not None else "legacy",
        "metric_profile_id": request.metric_profile_id,
        "budget": request.budget.model_dump(mode="json") if request.budget else None,
        "search": request.search.model_dump(mode="json"),
        "tracking": request.tracking.model_dump(mode="json"),
    }
    if reflection_lm_config:
        metadata.update(
            {
                "reflection_profile_id": reflection_lm_config.get("profile_id"),
                "reflection_profile_name": reflection_lm_config.get("profile_name"),
                "reflection_model_id": reflection_lm_config.get("model_id"),
                "reflection_resolved_model_id": reflection_lm_config.get("resolved_model_id"),
            }
        )
    if task_lm_config:
        metadata["task_model"] = {
            key: task_lm_config.get(key)
            for key in ("profile_id", "profile_name", "model_id", "resolved_model_id", "adapter")
        }
    if run_spec is not None:
        metadata["run_spec"] = run_spec.model_dump(mode="json")
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
    run_spec = None
    run_fingerprint = None
    if request.target is not None:
        target_spec = resolve_managed_target(request)
        program_spec = target_spec.program_spec
        dataset_path, resolved_dataset_id = await resolve_approved_dataset(
            request, persistence=persistence, persisted_identity=persisted_identity
        )
        dataset_ref = str(resolved_dataset_id)
    else:
        target_spec = None
        program_spec = resolve_effective_program_spec(request)
        dataset_path, dataset_ref = await _resolve_dataset_request(
            request,
            persistence=persistence,
            persisted_identity=persisted_identity,
        )
        resolved_dataset_id = (
            _parse_uuid_id(request.dataset_id, detail=f"Dataset {request.dataset_id} not found.")
            if request.dataset_id is not None
            else None
        )
    reflection_lm_config = await resolve_reflection_lm_config(request, persistence_deps, persisted_identity)
    task_lm_config = await resolve_task_lm_config(request, persistence_deps, persisted_identity)
    if target_spec is not None:
        if reflection_lm_config is None or task_lm_config is None or request.budget is None:
            raise HTTPException(status_code=400, detail="Canonical optimization model and budget resolution failed.")
        run_spec, run_fingerprint = build_optimization_run_spec(
            request,
            target_spec=target_spec,
            task_lm_config=task_lm_config,
            reflection_lm_config=reflection_lm_config,
        )
    timeout_seconds = effective_wall_clock_seconds(
        requested_seconds=(
            request.budget.wall_clock_seconds if request.budget is not None else OPTIMIZATION_TIMEOUT_SECONDS
        ),
        process_ceiling_seconds=OPTIMIZATION_TIMEOUT_SECONDS,
    )
    return PreparedOptimizationRequest(
        program_spec=program_spec,
        dataset_path=dataset_path,
        dataset_ref=dataset_ref,
        dataset_id=resolved_dataset_id,
        output_path=resolve_output_path(request.output_path),
        skill_path=resolve_skill_path(request.skill_path),
        trace_bundle_paths=resolve_trace_bundle_paths(list(request.trace_bundle_paths)),
        reflection_lm_config=reflection_lm_config,
        task_lm_config=task_lm_config,
        search_config=request.search.model_dump(mode="python"),
        max_full_evals=request_max_full_evals(request),
        run_spec=run_spec,
        run_fingerprint=run_fingerprint,
        timeout_seconds=timeout_seconds,
    )


async def create_blocking_run_record(
    *,
    request: GEPAOptimizationRequest,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    program_spec: str,
    dataset_ref: str,
    reflection_lm_config: dict[str, Any] | None,
    task_lm_config: dict[str, Any] | None = None,
    run_spec: OptimizationRunSpec | None = None,
    run_fingerprint: str | None = None,
    dataset_id: uuid.UUID | None = None,
) -> str | None:
    """Best-effort persistence record creation for the blocking endpoint."""
    workspace_id = _require_workspace_id(persisted_identity)
    try:
        created_run = await persistence.create_optimization_run(
            OptimizationRunCreateRequest(
                tenant_id=persisted_identity.tenant_id,
                workspace_id=workspace_id,
                created_by_user_id=persisted_identity.user_id,
                optimizer=GEPA_OPTIMIZER_LABEL,
                program_spec=program_spec,
                module_slug=request.module_slug,
                dataset_id=dataset_id,
                auto=request.auto,
                train_ratio=request.train_ratio,
                run_fingerprint=run_fingerprint,
                metadata_json=build_run_metadata(
                    request=request,
                    dataset_ref=dataset_ref,
                    reflection_lm_config=reflection_lm_config,
                    task_lm_config=task_lm_config,
                    run_spec=run_spec,
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
    task_lm_config: dict[str, Any] | None = None,
    run_spec: OptimizationRunSpec | None = None,
    timeout_seconds: int = OPTIMIZATION_TIMEOUT_SECONDS,
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
            max_full_evals=request_max_full_evals(request),
            train_ratio=request.train_ratio,
            optimizer=request.optimizer,
            run_id=None,
            skill_name=request.skill_name,
            skill_path=resolved_skill_path,
            trace_bundle_paths=resolve_trace_bundle_paths(list(request.trace_bundle_paths)),
            reflection_lm_config=reflection_lm_config,
            task_lm_config=task_lm_config,
            search_config=request.search.model_dump(mode="python"),
            run_spec=run_spec,
        ),
        timeout=timeout_seconds,
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
            dataset_id=prepared.dataset_id,
            auto=request.auto,
            train_ratio=request.train_ratio,
            run_fingerprint=prepared.run_fingerprint,
            metadata_json=build_run_metadata(
                request=request,
                dataset_ref=prepared.dataset_ref,
                reflection_lm_config=prepared.reflection_lm_config,
                task_lm_config=prepared.task_lm_config,
                run_spec=prepared.run_spec,
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
        max_full_evals=prepared.max_full_evals,
        train_ratio=request.train_ratio,
        optimizer=request.optimizer,
        skill_name=request.skill_name,
        skill_path=prepared.skill_path,
        trace_bundle_paths=prepared.trace_bundle_paths,
        reflection_lm_config=prepared.reflection_lm_config,
        task_lm_config=prepared.task_lm_config,
        search_config=prepared.search_config,
        run_spec=prepared.run_spec,
        timeout_seconds=prepared.timeout_seconds,
    )
    return OptimizationRunCreatedResponse(run_id=run_id, status="running")


def _metadata_dict(row: Any) -> dict[str, Any]:
    metadata = getattr(row, "metadata_json", None) or {}
    if isinstance(metadata, str):
        try:
            import json

            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    return metadata if isinstance(metadata, dict) else {}


async def resume_async_run_and_enqueue(
    *,
    run_id: str,
    background_tasks: BackgroundTasks,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    expected_fingerprint: str | None = None,
) -> OptimizationRunCreatedResponse:
    """Explicitly resume a terminal run after fingerprint validation and re-enqueue GEPA.

    Order is intentional: validate fingerprint + dataset first, *then* mark the run
    queued and enqueue. Never leave a zombie ``queued`` run without a worker.
    """
    import json
    import tempfile
    from pathlib import Path

    from fleet_rlm.quality.checkpointing import (
        ResumeNotAllowedError,
        build_run_fingerprint,
        require_resumable_status,
        require_resume_fingerprint,
    )
    from fleet_rlm.quality.contracts import OptimizationRunSpec

    run_uuid = _parse_uuid_id(run_id, detail=f"Optimization run {run_id} not found.")
    try:
        row = await persistence.get_optimization_run(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
        )
    except UnsupportedLocalCapabilityError as exc:
        raise HTTPException(status_code=501, detail="Resume requires managed Postgres persistence.") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"Optimization run {run_id} not found.")

    try:
        require_resumable_status(row.status)
    except ResumeNotAllowedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    metadata = _metadata_dict(row)
    raw_spec = metadata.get("run_spec")
    if not isinstance(raw_spec, dict):
        raise HTTPException(
            status_code=409,
            detail="Run is missing an immutable run_spec and cannot be resumed safely.",
        )
    try:
        run_spec = OptimizationRunSpec.model_validate(raw_spec)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail="Stored run_spec is invalid; cannot recompute resume fingerprint.",
        ) from exc

    recomputed = build_run_fingerprint(run_spec)
    try:
        require_resume_fingerprint(
            stored_fingerprint=getattr(row, "run_fingerprint", None) or recomputed,
            expected_fingerprint=expected_fingerprint or recomputed,
        )
        if getattr(row, "run_fingerprint", None) and row.run_fingerprint != recomputed:
            raise ResumeNotAllowedError("Stored run fingerprint does not match recomputed run_spec fingerprint.")
    except ResumeNotAllowedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Resolve dataset before mutating run status.
    dataset_path: Path | None = None
    dataset_uuid = getattr(row, "dataset_id", None)
    if dataset_uuid is None:
        # Prefer managed id from run_spec, then metadata keys.
        for candidate in (
            run_spec.dataset_version_id,
            metadata.get("dataset_version_id"),
            metadata.get("dataset_path"),
        ):
            if candidate is None:
                continue
            try:
                dataset_uuid = uuid.UUID(str(candidate))
                break
            except ValueError:
                path_candidate = Path(str(candidate))
                if path_candidate.is_file():
                    dataset_path = path_candidate
                    break

    if dataset_path is None:
        if dataset_uuid is None:
            raise HTTPException(status_code=409, detail="Run has no resolvable dataset for resume.")
        dataset_row = await persistence.get_dataset(
            tenant_id=persisted_identity.tenant_id,
            dataset_id=dataset_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
        )
        if dataset_row is None:
            raise HTTPException(status_code=409, detail="Optimization dataset is unavailable.")
        output_key = None
        ds_meta = getattr(dataset_row, "metadata_json", None) or {}
        if isinstance(ds_meta, dict):
            raw_key = ds_meta.get("output_key")
            if isinstance(raw_key, str) and raw_key.strip():
                output_key = raw_key.strip()
        examples, _total = await persistence.list_dataset_examples(
            tenant_id=persisted_identity.tenant_id,
            dataset_id=dataset_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
            limit=10_000,
            offset=0,
        )
        if not examples:
            raise HTTPException(status_code=409, detail="Approved dataset for resume has no examples.")
        tmp = Path(tempfile.mkdtemp(prefix="fleet-gepa-resume-", dir=str(OPTIMIZATION_DATA_ROOT)))
        dataset_path = tmp / "dataset.jsonl"
        with dataset_path.open("w", encoding="utf-8") as handle:
            for example in examples:
                row_payload = dict(getattr(example, "input_json", {}) or {})
                expected = getattr(example, "expected_output", None)
                if expected is not None:
                    key = output_key or "output"
                    row_payload.setdefault(key, expected)
                meta = getattr(example, "metadata_json", None) or {}
                if isinstance(meta, dict):
                    for meta_key, meta_val in meta.items():
                        row_payload.setdefault(str(meta_key), meta_val)
                partition = getattr(example, "partition", None)
                if partition:
                    row_payload["partition"] = str(partition)
                handle.write(json.dumps(row_payload, ensure_ascii=True) + "\n")

    assert dataset_path is not None

    try:
        queued = await persistence.resume_optimization_run(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            expected_fingerprint=recomputed,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
        )
    except ResumeNotAllowedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnsupportedLocalCapabilityError as exc:
        raise HTTPException(status_code=501, detail="Resume requires managed Postgres persistence.") from exc
    if queued is None:
        raise HTTPException(status_code=404, detail=f"Optimization run {run_id} not found.")

    budget = metadata.get("budget") if isinstance(metadata.get("budget"), dict) else {}
    max_metric_calls = metadata.get("max_metric_calls")
    max_full_evals = None
    auto: Any = "light"
    if isinstance(budget, dict):
        kind = budget.get("kind")
        if kind == "max_metric_calls":
            max_metric_calls = budget.get("value", max_metric_calls)
        elif kind == "max_full_evals":
            max_full_evals = budget.get("value")
        elif kind == "auto":
            auto = budget.get("value") or "light"
    # Prefer budget from validated run_spec.
    if run_spec.budget.kind == "max_metric_calls":
        max_metric_calls = run_spec.budget.value
    elif run_spec.budget.kind == "max_full_evals":
        max_full_evals = run_spec.budget.value
    elif run_spec.budget.kind == "auto":
        auto = run_spec.budget.value

    search_config = (
        run_spec.search.model_dump(mode="json")
        if run_spec is not None
        else (metadata.get("search") if isinstance(metadata.get("search"), dict) else None)
    )
    reflection_lm_config = None
    if metadata.get("reflection_profile_id") or metadata.get("reflection_model_id"):
        reflection_lm_config = {
            "profile_id": metadata.get("reflection_profile_id"),
            "model_id": metadata.get("reflection_model_id") or metadata.get("reflection_resolved_model_id"),
        }
    task_lm_config = metadata.get("task_model") if isinstance(metadata.get("task_model"), dict) else None
    module_slug = metadata.get("module_slug") or (
        run_spec.target.target_id if run_spec.target.kind == "module" else None
    )
    skill_name = metadata.get("skill_name")
    if skill_name is None and run_spec.target.kind == "skill":
        skill_name = run_spec.target.target_id
    skill_path = metadata.get("skill_path")
    trace_bundle_paths = (
        list(metadata.get("trace_bundle_paths") or []) if isinstance(metadata.get("trace_bundle_paths"), list) else []
    )
    train_ratio = float(getattr(row, "train_ratio", 0.8) or 0.8)
    program_spec = str(getattr(row, "program_spec", "") or "module")

    from .background import run_optimization_background

    background_tasks.add_task(
        run_optimization_background,
        run_id=str(queued.id),
        persistence=persistence,
        persisted_identity=persisted_identity,
        module_slug=module_slug,
        dataset_path=dataset_path,
        program_spec=program_spec,
        output_path=Path(str(getattr(row, "output_path"))) if getattr(row, "output_path", None) else None,
        default_output_root=OPTIMIZATION_DATA_ROOT,
        auto=auto if auto in {"light", "medium", "heavy"} else "light",
        max_metric_calls=int(max_metric_calls) if max_metric_calls is not None else None,
        max_full_evals=int(max_full_evals) if max_full_evals is not None else None,
        train_ratio=train_ratio,
        optimizer="gepa",
        skill_name=str(skill_name) if skill_name else None,
        skill_path=str(skill_path) if skill_path else None,
        trace_bundle_paths=trace_bundle_paths,
        reflection_lm_config=reflection_lm_config,
        task_lm_config=task_lm_config,
        search_config=search_config,
        run_spec=run_spec,
        timeout_seconds=OPTIMIZATION_TIMEOUT_SECONDS,
    )
    return OptimizationRunCreatedResponse(run_id=str(queued.id), status="queued")
