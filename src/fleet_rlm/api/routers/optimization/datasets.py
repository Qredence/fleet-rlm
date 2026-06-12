"""Dataset endpoints for GEPA optimization."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi import (
    Path as ApiPath,
)

from fleet_rlm.integrations.database import DatasetFormat, DatasetSource
from fleet_rlm.integrations.database.repository_optimization import DatasetCreateRequest
from fleet_rlm.quality import module_registry

from ...dependencies import ConfigDepsDep, HTTPIdentityDep, PersistenceDep
from ...runtime_services.optimization_datasets import (
    build_transcript_dataset_rows,
    persist_jsonl_rows,
)
from ...schemas.optimization import (
    DatasetDetailResponse,
    DatasetListResponse,
    DatasetResponse,
)
from ...schemas.sessions import TranscriptDatasetRequest
from ._deps import (
    AUTH_ERROR_RESPONSES,
    OPTIMIZATION_DATA_ROOT,
    OpenAPIResponses,
    _dataset_row_from_example,
    _dataset_to_response,
    _extract_metadata_str,
    _parse_uuid_id,
    _require_workspace_id,
    _resolve_persisted_identity,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_\-]")


def _sanitize_filename(name: str) -> str:
    """Return a filesystem-safe version of *name*."""
    stem = Path(name).stem
    return _SAFE_NAME_RE.sub("_", stem)[:120]


def _parse_rows(content: bytes, fmt: str) -> list[Any]:
    """Parse uploaded file content into a list of JSON-decoded rows."""
    text = content.decode("utf-8")
    if fmt == "jsonl":
        rows: list[Any] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            rows.append(json.loads(stripped))
        return rows
    else:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        raise ValueError("JSON file must contain a top-level array of objects")


def _require_object_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Ensure every dataset row is a JSON object before persistence."""
    object_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise HTTPException(
                status_code=400,
                detail=f"Dataset row {index} must be a JSON object.",
            )
        object_rows.append(cast(dict[str, Any], row))
    return object_rows


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/transcript-datasets",
    response_model=DatasetResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            400: {"description": "Invalid transcript dataset payload."},
        },
    ),
)
@router.post(
    "/datasets/from-transcript",
    response_model=DatasetResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            400: {"description": "Invalid transcript dataset payload."},
        },
    ),
    include_in_schema=False,
)
async def create_dataset_from_transcript(
    request: TranscriptDatasetRequest,
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
) -> DatasetResponse:
    """Convert transcript turns into a GEPA dataset."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    try:
        rows, label = build_transcript_dataset_rows(
            module_slug=request.module_slug,
            turns=[(turn.user_message, turn.assistant_message) for turn in request.turns],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    title = request.title.strip() if request.title else "Transcript"
    dataset_name = f"{title} ({label})"

    dataset_path = await asyncio.to_thread(
        persist_jsonl_rows,
        root=Path(os.environ.get("FLEET_RLM_DATASET_ROOT", os.getcwd())),
        rows=rows,
        prefix="transcript-",
    )
    workspace_id = _require_workspace_id(persisted_identity)
    dataset = await persistence.create_dataset(
        DatasetCreateRequest(
            tenant_id=persisted_identity.tenant_id,
            workspace_id=workspace_id,
            created_by_user_id=persisted_identity.user_id,
            name=dataset_name,
            row_count=len(rows),
            format=DatasetFormat.JSONL,
            source=DatasetSource.TRANSCRIPT,
            module_slug=request.module_slug,
            uri=str(dataset_path),
        ),
        examples=rows,
    )
    return _dataset_to_response(dataset)


@router.post(
    "/datasets",
    response_model=DatasetResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            400: {"description": "Invalid dataset file."},
        },
    ),
)
async def upload_dataset(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    file: Annotated[UploadFile, File(description="Dataset file to upload in JSON or JSONL format.")],
    module_slug: Annotated[
        str | None,
        Form(description="Optional module slug used to validate required dataset keys."),
    ] = None,
) -> DatasetResponse:
    """Upload and register a dataset file (.json or .jsonl)."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    ext = Path(file.filename).suffix.lower()
    if ext not in (".json", ".jsonl"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{ext}'. Allowed: .json, .jsonl",
        )

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )

    fmt = ext.lstrip(".")  # "json" or "jsonl"
    try:
        rows = _parse_rows(content, fmt)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse dataset: {exc}")

    if not rows:
        raise HTTPException(status_code=400, detail="Dataset is empty.")
    object_rows = _require_object_rows(rows)

    # Validate ALL rows against module requirements if module_slug given.
    # No file or dataset record is written when any row fails validation.
    if module_slug:
        spec = module_registry.get_module_spec(module_slug)
        if spec is None:
            raise HTTPException(status_code=400, detail=f"Unknown module slug: {module_slug!r}")
        required_keys = set(spec.required_dataset_keys)
        for row_index, row in enumerate(object_rows):
            missing = required_keys - row.keys()
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Dataset row {row_index} is missing required keys for module "
                        f"'{module_slug}': {sorted(missing)}"
                    ),
                )

    # Save file to dataset root
    ds_root = Path(os.environ.get("FLEET_RLM_DATASET_ROOT", OPTIMIZATION_DATA_ROOT)).resolve()
    ds_root.mkdir(parents=True, exist_ok=True)
    # Generate a temp ID from timestamp for the filename
    import time

    ts_id = int(time.time() * 1000) % 10_000_000
    safe_name = _sanitize_filename(file.filename)
    dest = ds_root / f"{ts_id}_{safe_name}.{fmt}"
    await asyncio.to_thread(dest.write_bytes, content)

    workspace_id = _require_workspace_id(persisted_identity)
    ds = await persistence.create_dataset(
        DatasetCreateRequest(
            tenant_id=persisted_identity.tenant_id,
            workspace_id=workspace_id,
            created_by_user_id=persisted_identity.user_id,
            name=Path(file.filename).stem,
            row_count=len(object_rows),
            format=DatasetFormat(fmt),
            source=DatasetSource.UPLOAD,
            module_slug=module_slug,
            uri=str(dest),
        ),
        examples=object_rows,
    )
    return _dataset_to_response(ds)


@router.get(
    "/datasets",
    response_model=DatasetListResponse,
    responses=AUTH_ERROR_RESPONSES,
)
async def list_datasets_endpoint(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    module_slug: Annotated[str | None, Query(description="Filter by module slug")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Maximum number of datasets to return.")] = 50,
    offset: Annotated[int, Query(ge=0, description="Pagination offset into the dataset list.")] = 0,
) -> DatasetListResponse:
    """List registered datasets with optional module filter."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    items, total = await persistence.list_datasets(
        tenant_id=persisted_identity.tenant_id,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
        module_slug=module_slug,
        limit=limit,
        offset=offset,
    )
    return DatasetListResponse(
        items=[_dataset_to_response(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
        has_more=(offset + limit) < total,
    )


@router.get(
    "/datasets/{dataset_id}",
    response_model=DatasetDetailResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            404: {"description": "Dataset not found."},
        },
    ),
)
async def get_dataset_detail(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    dataset_id: Annotated[str, ApiPath(description="Identifier of the dataset to inspect.")],
) -> DatasetDetailResponse:
    """Return dataset metadata with the first 10 rows as preview."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    ds = await persistence.get_dataset(
        tenant_id=persisted_identity.tenant_id,
        dataset_id=_parse_uuid_id(dataset_id, detail=f"Dataset {dataset_id} not found."),
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
    )
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found.")
    examples, _total = await persistence.list_dataset_examples(
        tenant_id=persisted_identity.tenant_id,
        dataset_id=ds.id,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
        limit=10,
        offset=0,
    )
    metadata = getattr(ds, "metadata_json", {}) or {}
    output_key = _extract_metadata_str(metadata, "output_key")
    sample_rows = [_dataset_row_from_example(example, output_key) for example in examples]
    response = _dataset_to_response(ds)
    return DatasetDetailResponse(
        **response.model_dump(),
        sample_rows=sample_rows,
        uri=ds.uri or "",
    )
