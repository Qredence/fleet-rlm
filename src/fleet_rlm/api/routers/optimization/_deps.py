"""Shared dependencies, helpers, and converters for optimization sub-routers."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, TypeAlias, cast

from fastapi import HTTPException

from fleet_rlm.integrations.database.types import IdentityUpsertResult

from ...auth import AuthError, resolve_admitted_identity
from ...dependencies import (
    HTTPIdentityDep,
    RepositoryDep,
    ServerStateDep,
)
from ...schemas.core import (
    DatasetResponse,
    OptimizationRunResponse,
)

logger = logging.getLogger(__name__)

OpenAPIResponses: TypeAlias = dict[int | str, dict[str, Any]]

AUTH_ERROR_RESPONSES: OpenAPIResponses = {
    401: {
        "description": "Authentication is required or the provided token is invalid."
    },
}

OPTIMIZATION_TIMEOUT_SECONDS = 900

OPTIMIZATION_DATA_ROOT = Path(
    os.environ.get("FLEET_RLM_OPTIMIZATION_DATA_ROOT", os.getcwd())
).resolve()


# ---------------------------------------------------------------------------
# OptimizationContext dataclass
# ---------------------------------------------------------------------------


@dataclass
class OptimizationContext:
    """Resolved persistence context for optimization endpoints."""

    repository: Any | None  # FleetRepository | None
    identity: IdentityUpsertResult | None
    local_store_path: Path

    @property
    def has_db(self) -> bool:
        return self.repository is not None and self.identity is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_uuid_id(value: str, *, detail: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=detail) from exc


async def _resolve_persisted_identity(
    *,
    state: ServerStateDep,
    repository: RepositoryDep,
    identity: HTTPIdentityDep,
) -> IdentityUpsertResult | None:
    if repository is None:
        return None
    if state.config.auth_mode == "entra":
        try:
            return await resolve_admitted_identity(repository, identity)
        except AuthError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            ) from exc
    return await repository.upsert_identity(
        entra_tenant_id=identity.tenant_claim,
        entra_user_id=identity.user_claim,
        email=identity.email,
        full_name=identity.name,
    )


def _extract_metadata_str(metadata: object, key: str) -> str | None:
    """Unified metadata extraction helper.

    Replaces the former ``_dataset_module_slug``, ``_run_module_slug``,
    ``_dataset_output_key``, and ``_run_dataset_path`` helpers.
    """
    if isinstance(metadata, dict):
        val = metadata.get(key)
        return str(val) if val is not None else None
    return None


def _dataset_row_from_example(example: Any, output_key: str | None) -> dict[str, Any]:
    row = dict(example.input_json)
    if output_key and example.expected_output is not None:
        row[output_key] = example.expected_output
    return row


def _require_workspace_id(identity: IdentityUpsertResult) -> uuid.UUID:
    workspace_id = identity.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=503, detail="Workspace persistence is unavailable."
        )
    return workspace_id


def _resolve_relative_dataset_lookup(relative_path: str) -> str:
    """Normalize a user-provided dataset lookup into a safe relative key."""
    normalized = PurePosixPath(relative_path.replace("\\", "/"))
    if normalized.is_absolute():
        raise HTTPException(
            status_code=400,
            detail="Absolute paths are not allowed. Use a relative path.",
        )

    parts = normalized.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(
            status_code=400, detail="Path escapes the allowed data directory."
        )
    return normalized.as_posix()


def _find_dataset_under_root(root: Path, relative_path: str) -> Path | None:
    """Return a dataset file under ``root`` that matches the relative lookup key."""
    root_resolved = root.resolve()
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue
        try:
            candidate_relative = candidate.relative_to(root).as_posix()
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        if candidate_relative == relative_path:
            return resolved_candidate
    return None


def _check_gepa_available() -> bool:
    """Return True if the GEPA teleprompt module is importable."""
    try:
        from dspy.teleprompt import GEPA  # noqa: F401

        return True
    except Exception:
        return False


def _get_mlflow_status() -> tuple[bool, bool]:
    """Return whether MLflow is configured and whether it is reachable."""
    try:
        from fleet_rlm.integrations.observability.config import MlflowConfig
        from fleet_rlm.integrations.observability.mlflow_runtime import (
            initialize_mlflow,
        )

        config = MlflowConfig.from_env()
        if not config.enabled:
            return False, False
        return True, initialize_mlflow(config)
    except Exception:
        return False, False


def configure_planner_from_env(*, env_file: Path | None = None) -> bool:
    """Lazily configure the planner LM from environment settings."""
    from fleet_rlm.runtime.config import (
        configure_planner_from_env as _configure_planner_from_env,
    )

    return _configure_planner_from_env(env_file=env_file)


async def _resolve_dataset_request(
    request: Any,
    *,
    repository: RepositoryDep = None,
    persisted_identity: IdentityUpsertResult | None = None,
) -> tuple[Path, str]:
    """Resolve a dataset request into an executable path and stored reference."""
    if request.dataset_id is not None:
        if repository is not None and persisted_identity is not None:
            dataset_row = await repository.get_dataset(
                tenant_id=persisted_identity.tenant_id,
                dataset_id=_parse_uuid_id(
                    request.dataset_id,
                    detail=f"Dataset {request.dataset_id} not found.",
                ),
                workspace_id=persisted_identity.workspace_id,
                created_by_user_id=persisted_identity.user_id,
            )
            if dataset_row is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Dataset {request.dataset_id} not found.",
                )
            if not dataset_row.uri:
                raise HTTPException(
                    status_code=400,
                    detail=f"Dataset file not found for dataset {request.dataset_id}.",
                )
            dataset = Path(dataset_row.uri).resolve()
            if not dataset.exists():
                raise HTTPException(
                    status_code=400,
                    detail=f"Dataset file not found for dataset {request.dataset_id}.",
                )
            return dataset, dataset_row.uri

        from fleet_rlm.integrations.local_store import get_dataset

        try:
            legacy_dataset_id = int(request.dataset_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Dataset {request.dataset_id} not found.",
            ) from exc
        dataset_row = await asyncio.to_thread(get_dataset, legacy_dataset_id)
        if dataset_row is None:
            raise HTTPException(
                status_code=400,
                detail=f"Dataset {request.dataset_id} not found.",
            )
        dataset = Path(dataset_row.uri).resolve()
        if not dataset.exists():
            raise HTTPException(
                status_code=400,
                detail=f"Dataset file not found for dataset {request.dataset_id}.",
            )
        return dataset, str(dataset)

    dataset_path = (request.dataset_path or "").strip()
    dataset_lookup = _resolve_relative_dataset_lookup(dataset_path)
    dataset = _find_dataset_under_root(OPTIMIZATION_DATA_ROOT, dataset_lookup)
    if dataset is None:
        raise HTTPException(
            status_code=400, detail=f"Dataset file not found: {dataset_path}"
        )
    return dataset, dataset_lookup


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def _db_run_to_response(row: Any) -> OptimizationRunResponse:
    """Convert an OptimizationRun SQLModel row to an API response."""
    metadata = getattr(row, "metadata_json", {}) or {}
    return OptimizationRunResponse(
        id=str(row.id),
        status=row.status.value if hasattr(row.status, "value") else str(row.status),
        module_slug=getattr(row, "module_slug", None)
        or _extract_metadata_str(metadata, "module_slug"),
        program_spec=row.program_spec,
        optimizer=row.optimizer.value
        if hasattr(row.optimizer, "value")
        else str(row.optimizer),
        auto=row.auto,
        train_ratio=row.train_ratio,
        dataset_path=getattr(row, "dataset_path", None)
        or _extract_metadata_str(metadata, "dataset_path"),
        train_examples=row.train_examples,
        validation_examples=row.validation_examples,
        validation_score=row.validation_score,
        output_path=row.output_path,
        manifest_path=getattr(row, "manifest_path", None),
        error=row.error,
        phase=getattr(row, "phase", None),
        started_at=row.started_at.isoformat() if row.started_at else "",
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
    )


def _dataset_to_response(row: Any) -> DatasetResponse:
    metadata = getattr(row, "metadata_json", {}) or {}
    return DatasetResponse(
        id=str(row.id),
        name=row.name,
        row_count=row.row_count or 0,
        format=row.format.value
        if hasattr(row.format, "value")
        else str(row.format or ""),
        module_slug=getattr(row, "module_slug", None)
        or _extract_metadata_str(metadata, "module_slug"),
        created_at=row.created_at.isoformat(),
    )
