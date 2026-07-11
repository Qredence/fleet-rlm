"""Fail-closed resolution helpers for canonical Phase 8 optimization requests."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from fleet_rlm.db.repos.identity import IdentityUpsertResult
from fleet_rlm.quality.checkpointing import build_run_fingerprint
from fleet_rlm.quality.contracts import OptimizationRunSpec
from fleet_rlm.quality.dataset_versions import canonical_dataset_sha256, partition_value, validate_approval_partitions
from fleet_rlm.quality.datasets import load_dataset_rows
from fleet_rlm.quality.module_registry import MetricProfile, ModuleOptimizationSpec, get_module_spec
from fleet_rlm.quality.skill_optimization import spec_for_skill


def resolve_managed_target(request: Any) -> ModuleOptimizationSpec:
    target = request.target
    if target is None:
        raise HTTPException(status_code=400, detail="Managed optimization target is required.")
    spec = get_module_spec(target.id) if target.kind == "module" else spec_for_skill(skill_name=target.id)
    if spec is None:
        raise HTTPException(status_code=400, detail="Unknown managed optimization target.")
    if target.version != spec.target_version:
        raise HTTPException(status_code=400, detail="Managed optimization target version does not match.")
    return spec


def require_metric_profile(spec: ModuleOptimizationSpec, requested_profile_id: str) -> MetricProfile:
    profile = spec.metric_profile
    if profile is None:
        raise HTTPException(status_code=400, detail="Managed target does not expose a Metric Profile.")
    if requested_profile_id != profile.qualified_id:
        raise HTTPException(status_code=400, detail="Requested Metric Profile does not match the managed target.")
    return profile


def effective_wall_clock_seconds(*, requested_seconds: int, process_ceiling_seconds: int) -> int:
    return min(requested_seconds, process_ceiling_seconds)


async def resolve_approved_dataset(
    request: Any,
    *,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
) -> tuple[Path, uuid.UUID]:
    if not getattr(persistence, "supports_managed_dataset_versions", False):
        raise HTTPException(status_code=503, detail="Managed Dataset Versions require Postgres persistence.")
    from ._deps import _parse_uuid_id

    dataset_id = _parse_uuid_id(
        request.dataset_version_id,
        detail=f"Dataset {request.dataset_version_id} not found.",
    )
    dataset = await persistence.get_dataset(
        tenant_id=persisted_identity.tenant_id,
        dataset_id=dataset_id,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
    )
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset {request.dataset_version_id} not found.")
    if dataset.eligibility != "approved":
        raise HTTPException(status_code=409, detail="Dataset Version is not approved.")
    metadata = dict(getattr(dataset, "metadata_json", {}) or {})
    expected_target = request.target.id
    actual_target = metadata.get("module_slug") if request.target.kind == "module" else metadata.get("skill_name")
    if actual_target != expected_target:
        raise HTTPException(status_code=409, detail="Dataset Version belongs to a different managed target.")
    if not dataset.uri or not Path(dataset.uri).is_file() or not dataset.content_sha256:
        raise HTTPException(status_code=409, detail="Dataset Version integrity evidence is unavailable.")
    try:
        rows = load_dataset_rows(Path(dataset.uri))
        validate_approval_partitions(partition_value(row) for row in rows)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Dataset Version content failed integrity validation.") from exc
    if canonical_dataset_sha256(rows) != dataset.content_sha256:
        raise HTTPException(status_code=409, detail="Dataset Version content digest does not match its stored value.")
    return Path(dataset.uri).resolve(), dataset.id


def build_optimization_run_spec(
    request: Any,
    *,
    target_spec: ModuleOptimizationSpec,
    task_lm_config: dict[str, Any],
    reflection_lm_config: dict[str, Any],
) -> tuple[OptimizationRunSpec, str]:
    profile = require_metric_profile(target_spec, str(request.metric_profile_id))
    run_spec = OptimizationRunSpec.model_validate(
        {
            "target": {
                "kind": request.target.kind,
                "target_id": request.target.id,
                "version": target_spec.target_version,
            },
            "dataset_version_id": request.dataset_version_id,
            "metric_profile_id": profile.qualified_id,
            "task_model": {
                "profile_id": task_lm_config["profile_id"],
                "model_id": task_lm_config["resolved_model_id"],
                "wire_format": task_lm_config["provider_type"],
            },
            "reflection_model": {
                "profile_id": reflection_lm_config["profile_id"],
                "model_id": reflection_lm_config["resolved_model_id"],
                "wire_format": reflection_lm_config["provider_type"],
            },
            "budget": request.budget.model_dump(mode="json"),
            "search": request.search.model_dump(mode="json"),
            "tracking": request.tracking.model_dump(mode="json"),
            "adapter": task_lm_config.get("adapter", "chat"),
        }
    )
    return run_spec, build_run_fingerprint(run_spec)


__all__ = [
    "build_optimization_run_spec",
    "effective_wall_clock_seconds",
    "require_metric_profile",
    "resolve_approved_dataset",
    "resolve_managed_target",
]
