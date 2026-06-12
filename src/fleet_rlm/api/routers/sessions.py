"""Router for session state management."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query

from ..dependencies import (
    HTTPIdentityDep,
    PersistedIdentityDep,
    PersistenceDep,
    SessionCacheDepsDep,
)
from ..runtime_services.session_service import (
    _TRANSCRIPT_EXPORT_MAX_TURNS,  # noqa: F401
    SessionService,
)
from ..schemas.optimization import DatasetResponse
from ..schemas.sessions import (
    SessionDeleteResponse,
    SessionDetailResponse,
    SessionExportRequest,
    SessionListResponse,
    SessionPatchRequest,
    SessionRestoreResponse,
    SessionStateResponse,
    SessionStatsResponse,
    SessionTraceExportRequest,
    SessionTraceExportResponse,
    SessionTraceListResponse,
    TurnListResponse,
)
from ._types import OpenAPIResponses

router = APIRouter(prefix="/sessions", tags=["sessions"])

SESSIONS_ERROR_RESPONSES: OpenAPIResponses = {
    401: {"description": "Authentication is required or the provided token is invalid."},
    403: {"description": "The caller does not have permission to access this resource."},
    503: {"description": "Session services are unavailable because server startup is incomplete."},
}

SESSION_DETAIL_RESPONSES: OpenAPIResponses = {
    **SESSIONS_ERROR_RESPONSES,
    404: {"description": "Session not found."},
}


@router.get(
    "/state",
    response_model=SessionStateResponse,
    responses={
        401: {"description": "Authentication is required or the provided token is invalid."},
        503: {"description": "Session state is unavailable because server startup is incomplete."},
    },
)
def list_session_state(
    session_cache: SessionCacheDepsDep,
    identity: HTTPIdentityDep,
) -> SessionStateResponse:
    """Return lightweight summaries of active/restored in-memory session state."""
    return SessionService(persistence=None).list_session_state(
        session_cache=session_cache.sessions,
        identity=identity,
    )


# ---------------------------------------------------------------------------
# Session history (durable transcript store)
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=SessionListResponse,
    responses=SESSIONS_ERROR_RESPONSES,
    summary="List session history",
    description="Paginated list of durable session transcripts with search and status filters.",
)
async def list_sessions_endpoint(
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    search: Annotated[str | None, Query(description="Full-text search on title")] = None,
    status: Annotated[str | None, Query(description="Filter by status (active, archived)")] = None,
    created_after: Annotated[
        datetime | None,
        Query(description="Filter sessions created on or after this date (ISO 8601)"),
    ] = None,
    created_before: Annotated[
        datetime | None,
        Query(description="Filter sessions created on or before this date (ISO 8601)"),
    ] = None,
    model_name: Annotated[str | None, Query(description="Filter by exact model name")] = None,
    model_provider: Annotated[str | None, Query(description="Filter by exact model provider")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Page size")] = 20,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> SessionListResponse:
    """Return paginated session history filtered by the caller's ownership."""
    return await SessionService(persistence).list_sessions(
        persisted_identity=persisted_identity,
        search=search,
        status=status,
        created_after=created_after,
        created_before=created_before,
        model_name=model_name,
        model_provider=model_provider,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{session_id}",
    response_model=SessionDetailResponse,
    responses=SESSION_DETAIL_RESPONSES,
    summary="Get session detail",
    description="Return session metadata and turn count for a specific session.",
)
async def get_session_detail(
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    session_id: Annotated[str, Path(description="Identifier of the session to inspect.")],
) -> SessionDetailResponse:
    """Return full session detail with turn count."""
    return await SessionService(persistence).get_session_detail(
        persisted_identity=persisted_identity,
        session_id=session_id,
    )


@router.patch(
    "/{session_id}",
    response_model=SessionDetailResponse,
    responses=SESSION_DETAIL_RESPONSES,
    summary="Patch session metadata",
    description="Update session title and/or metadata_json. Returns the updated session snapshot.",
)
async def patch_session_endpoint(
    body: SessionPatchRequest,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    session_id: Annotated[str, Path(description="Identifier of the session to update.")],
) -> SessionDetailResponse:
    """Update session title and/or metadata."""
    return await SessionService(persistence).patch_session(
        persisted_identity=persisted_identity,
        session_id=session_id,
        body=body,
    )


@router.get(
    "/{session_id}/turns",
    response_model=TurnListResponse,
    responses=SESSION_DETAIL_RESPONSES,
    summary="Get session turns",
    description="Paginated turn-by-turn transcript for a session.",
)
async def get_session_turns(
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    session_id: Annotated[str, Path(description="Identifier of the session whose turns to list.")],
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> TurnListResponse:
    """Return paginated turns for a session."""
    return await SessionService(persistence).get_session_turns(
        persisted_identity=persisted_identity,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{session_id}/traces",
    response_model=SessionTraceListResponse,
    responses=SESSION_DETAIL_RESPONSES,
    summary="List session traces",
    description="Paginated external traces (for example MLflow child delegations) linked to a session.",
)
async def get_session_traces(
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    session_id: Annotated[str, Path(description="Identifier of the session whose traces to list.")],
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> SessionTraceListResponse:
    """Return paginated external traces for a session."""
    return await SessionService(persistence).get_session_traces(
        persisted_identity=persisted_identity,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{session_id}/trace-export",
    response_model=SessionTraceExportResponse,
    responses=SESSION_DETAIL_RESPONSES,
    summary="Export session MLflow traces",
    description="Write full MLflow trace JSON/JSONL artifacts and a distilled GEPA evidence bundle.",
)
async def export_session_traces_endpoint(
    body: SessionTraceExportRequest,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    session_id: Annotated[str, Path(description="Identifier of the session whose traces to export.")],
) -> SessionTraceExportResponse:
    """Export linked MLflow traces for offline GEPA optimization."""
    return await SessionService(persistence).export_session_traces(
        persisted_identity=persisted_identity,
        session_id=session_id,
        body=body,
    )


@router.get(
    "/{session_id}/stats",
    response_model=SessionStatsResponse,
    responses=SESSION_DETAIL_RESPONSES,
    summary="Get session usage stats",
    description="Aggregated token counts, latency, and model breakdown for all turns in a session.",
)
async def get_session_stats(
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    session_id: Annotated[str, Path(description="Identifier of the session whose stats to retrieve.")],
) -> SessionStatsResponse:
    """Return aggregated usage stats for a session."""
    return await SessionService(persistence).get_session_stats(
        persisted_identity=persisted_identity,
        session_id=session_id,
    )


@router.delete(
    "/{session_id}",
    response_model=SessionDeleteResponse,
    responses=SESSION_DETAIL_RESPONSES,
    summary="Archive session",
    description="Soft-delete (archive) a session. Returns success when archived, 404 if not found or not owned.",
)
async def delete_session_endpoint(
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    session_id: Annotated[str, Path(description="Identifier of the session to archive.")],
) -> SessionDeleteResponse:
    """Archive a session (soft delete)."""
    return await SessionService(persistence).delete_session(
        persisted_identity=persisted_identity,
        session_id=session_id,
    )


SESSION_RESTORE_RESPONSES: OpenAPIResponses = {
    **SESSIONS_ERROR_RESPONSES,
    404: {"description": "Session not found."},
    409: {"description": "Session is already active."},
}


@router.post(
    "/{session_id}/restore",
    response_model=SessionRestoreResponse,
    responses=SESSION_RESTORE_RESPONSES,
    summary="Restore session",
    description="Unarchive (restore) a soft-deleted session. Returns success when restored, 404 if not found, 409 if already active.",
)
async def restore_session_endpoint(
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    session_id: Annotated[str, Path(description="Identifier of the session to restore.")],
) -> SessionRestoreResponse:
    """Restore an archived session to active status."""
    return await SessionService(persistence).restore_session(
        persisted_identity=persisted_identity,
        session_id=session_id,
    )


@router.post(
    "/{session_id}/export",
    response_model=DatasetResponse,
    responses={
        **SESSION_DETAIL_RESPONSES,
        400: {"description": "Invalid export parameters."},
    },
    summary="Export session as GEPA dataset",
    description=(
        "Convert a session's turn history into a JSONL dataset suitable for "
        "GEPA optimization. Requires a target module slug to determine the "
        "column mapping."
    ),
)
async def export_session_endpoint(
    body: SessionExportRequest,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    session_id: Annotated[str, Path(description="Identifier of the session to export as a dataset.")],
) -> DatasetResponse:
    """Export a session as a GEPA dataset."""
    return await SessionService(persistence).export_session(
        persisted_identity=persisted_identity,
        session_id=session_id,
        body=body,
    )
