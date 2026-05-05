"""Router for session state management."""

import asyncio
import os
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path as FsPath
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Path, Query

from fleet_rlm.integrations.database import ChatSessionStatus, ChatTurn
from fleet_rlm.utils.identity import sanitize_id as _sanitize_id

from ..dependencies import (
    HTTPIdentityDep,
    PersistedIdentityDep,
    PersistenceDep,
    SessionCacheDepsDep,
)
from ..runtime_services.session_helpers import (
    optional_string as _optional_string,
)
from ..runtime_services.session_helpers import (
    parse_legacy_session_key_owner as _parse_legacy_session_key_owner,
)
from ..runtime_services.session_helpers import (
    parse_session_uuid as _parse_session_uuid,
)
from ..runtime_services.session_helpers import (
    session_external_id as _session_external_id,
)
from ..runtime_services.session_helpers import (
    string_or_default as _string_or_default,
)
from ..schemas.optimization import DatasetResponse
from ..schemas.sessions import (
    SessionDeleteResponse,
    SessionDetailResponse,
    SessionExportRequest,
    SessionListItem,
    SessionListResponse,
    SessionPatchRequest,
    SessionRestoreResponse,
    SessionStateResponse,
    SessionStateSummary,
    SessionStatsResponse,
    TurnItem,
    TurnListResponse,
)
from ._types import OpenAPIResponses

router = APIRouter(prefix="/sessions", tags=["sessions"])
_TURN_COUNT_QUERY_LIMIT = 1
_TRANSCRIPT_EXPORT_MAX_TURNS = 10_000


SESSIONS_ERROR_RESPONSES: OpenAPIResponses = {
    401: {
        "description": "Authentication is required or the provided token is invalid."
    },
    403: {
        "description": "The caller does not have permission to access this resource."
    },
    503: {
        "description": "Session services are unavailable because server startup is incomplete."
    },
}

SESSION_DETAIL_RESPONSES: OpenAPIResponses = {
    **SESSIONS_ERROR_RESPONSES,
    404: {"description": "Session not found."},
}


async def _load_turns_for_export(
    *,
    persistence,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    user_id: uuid.UUID | None,
    workspace_id: uuid.UUID | None,
) -> list[ChatTurn]:
    """Load turns for export, capped at ``_TRANSCRIPT_EXPORT_MAX_TURNS``.

    Raises HTTP 413 if the session exceeds the cap so the server does not
    attempt to materialize unbounded transcripts.
    """

    turns, total = await persistence.list_chat_turns(
        tenant_id=tenant_id,
        session_id=session_id,
        user_id=user_id,
        workspace_id=workspace_id,
        limit=_TRANSCRIPT_EXPORT_MAX_TURNS,
        offset=0,
    )
    if total > _TRANSCRIPT_EXPORT_MAX_TURNS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Session has {total} turns; export is limited to "
                f"{_TRANSCRIPT_EXPORT_MAX_TURNS} turns."
            ),
        )
    return list(turns)


def _turn_item_from_repo(turn: ChatTurn) -> TurnItem:
    return TurnItem(
        id=str(turn.id),
        turn_index=turn.turn_index,
        user_message=turn.user_message,
        assistant_message=turn.assistant_message,
        created_at=turn.created_at.isoformat(),
    )


@router.get(
    "/state",
    response_model=SessionStateResponse,
    responses={
        401: {
            "description": "Authentication is required or the provided token is invalid."
        },
        503: {
            "description": "Session state is unavailable because server startup is incomplete."
        },
    },
)
def list_session_state(
    session_cache: SessionCacheDepsDep,
    identity: HTTPIdentityDep,
) -> SessionStateResponse:
    """Return lightweight summaries of active/restored in-memory session state."""
    summaries: list[SessionStateSummary] = []
    expected_workspace_id = _sanitize_id(identity.tenant_claim, "default")
    expected_user_id = _sanitize_id(identity.user_claim, "anonymous")
    for key, payload in session_cache.sessions.items():
        if not isinstance(payload, Mapping):
            continue
        payload_dict = payload
        owner_tenant_claim = _optional_string(payload_dict.get("owner_tenant_claim"))
        owner_user_claim = _optional_string(payload_dict.get("owner_user_claim"))
        if owner_tenant_claim is not None and owner_user_claim is not None:
            if (
                owner_tenant_claim != identity.tenant_claim
                or owner_user_claim != identity.user_claim
            ):
                continue
        else:
            key_workspace_id, key_user_id = _parse_legacy_session_key_owner(key)
            workspace_id_fallback = _optional_string(payload_dict.get("workspace_id"))
            user_id_fallback = _optional_string(payload_dict.get("user_id"))
            legacy_workspace_id = workspace_id_fallback or key_workspace_id
            legacy_user_id = user_id_fallback or key_user_id
            if legacy_workspace_id is None or legacy_user_id is None:
                continue
            if (
                legacy_workspace_id != expected_workspace_id
                or legacy_user_id != expected_user_id
            ):
                continue

        workspace_id = _string_or_default(payload_dict.get("workspace_id"), "default")
        user_id = _string_or_default(payload_dict.get("user_id"), "anonymous")
        manifest = payload_dict.get("manifest", {})
        session = payload_dict.get("session", {})
        session_state = session.get("state", {}) if isinstance(session, Mapping) else {}
        history = (
            session_state.get("history", [])
            if isinstance(session_state, Mapping)
            else []
        )
        documents = (
            session_state.get("documents", {})
            if isinstance(session_state, Mapping)
            else {}
        )
        memory = manifest.get("memory", []) if isinstance(manifest, Mapping) else []
        logs = manifest.get("logs", []) if isinstance(manifest, Mapping) else []
        artifacts = (
            manifest.get("artifacts", []) if isinstance(manifest, Mapping) else []
        )
        metadata = manifest.get("metadata", {}) if isinstance(manifest, Mapping) else {}
        summaries.append(
            SessionStateSummary(
                key=str(key),
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=_optional_string(payload_dict.get("session_id")),
                history_turns=len(history) if isinstance(history, list) else 0,
                document_count=len(documents) if isinstance(documents, dict) else 0,
                memory_count=len(memory) if isinstance(memory, list) else 0,
                log_count=len(logs) if isinstance(logs, list) else 0,
                artifact_count=len(artifacts) if isinstance(artifacts, list) else 0,
                updated_at=_optional_string(metadata.get("updated_at"))
                if isinstance(metadata, Mapping)
                else None,
            )
        )
    return SessionStateResponse(ok=True, sessions=summaries)


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
    search: Annotated[
        str | None, Query(description="Full-text search on title")
    ] = None,
    status: Annotated[
        str | None, Query(description="Filter by status (active, archived)")
    ] = None,
    created_after: Annotated[
        datetime | None,
        Query(description="Filter sessions created on or after this date (ISO 8601)"),
    ] = None,
    created_before: Annotated[
        datetime | None,
        Query(description="Filter sessions created on or before this date (ISO 8601)"),
    ] = None,
    model_name: Annotated[
        str | None, Query(description="Filter by exact model name")
    ] = None,
    model_provider: Annotated[
        str | None, Query(description="Filter by exact model provider")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Page size")] = 20,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> SessionListResponse:
    """Return paginated session history filtered by the caller's ownership."""
    status_filter = None
    if status:
        try:
            status_filter = ChatSessionStatus(status)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {status}",
            ) from exc
    items, total = await persistence.list_chat_sessions(
        tenant_id=persisted_identity.tenant_id,
        user_id=persisted_identity.user_id,
        workspace_id=persisted_identity.workspace_id,
        search=search,
        status=status_filter,
        created_after=created_after,
        created_before=created_before,
        model_name=model_name,
        model_provider=model_provider,
        limit=limit,
        offset=offset,
    )
    return SessionListResponse(
        items=[
            SessionListItem(
                id=str(s.id),
                title=s.title,
                status=s.status.value if hasattr(s.status, "value") else str(s.status),
                model_name=s.model_name,
                external_session_id=_session_external_id(
                    getattr(s, "metadata_json", None)
                ),
                created_at=s.created_at.isoformat(),
                updated_at=s.updated_at.isoformat(),
            )
            for s in items
        ],
        total=total,
        offset=offset,
        limit=limit,
        has_more=(offset + limit) < total,
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
    session_id: Annotated[
        str, Path(description="Identifier of the session to inspect.")
    ],
) -> SessionDetailResponse:
    """Return full session detail with turn count."""
    session_uuid = _parse_session_uuid(session_id)
    session = await persistence.get_chat_session(
        tenant_id=persisted_identity.tenant_id,
        session_id=session_uuid,
        user_id=persisted_identity.user_id,
        workspace_id=persisted_identity.workspace_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    _turns, turn_count = await persistence.list_chat_turns(
        tenant_id=persisted_identity.tenant_id,
        session_id=session_uuid,
        user_id=persisted_identity.user_id,
        workspace_id=persisted_identity.workspace_id,
        limit=_TURN_COUNT_QUERY_LIMIT,
        offset=0,
    )
    return SessionDetailResponse(
        id=str(session.id),
        title=session.title,
        status=session.status.value
        if hasattr(session.status, "value")
        else str(session.status),
        model_name=session.model_name,
        external_session_id=_session_external_id(
            getattr(session, "metadata_json", None)
        ),
        workspace_id=str(session.workspace_id),
        turn_count=turn_count,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
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
    session_id: Annotated[
        str, Path(description="Identifier of the session to update.")
    ],
) -> SessionDetailResponse:
    """Update session title and/or metadata."""
    session_uuid = _parse_session_uuid(session_id)
    session = await persistence.update_chat_session(
        tenant_id=persisted_identity.tenant_id,
        session_id=session_uuid,
        user_id=persisted_identity.user_id,
        workspace_id=persisted_identity.workspace_id,
        title=body.title,
        metadata_json=body.metadata_json,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    _turns, turn_count = await persistence.list_chat_turns(
        tenant_id=persisted_identity.tenant_id,
        session_id=session_uuid,
        user_id=persisted_identity.user_id,
        workspace_id=persisted_identity.workspace_id,
        limit=_TURN_COUNT_QUERY_LIMIT,
        offset=0,
    )
    return SessionDetailResponse(
        id=str(session.id),
        title=session.title,
        status=session.status.value
        if hasattr(session.status, "value")
        else str(session.status),
        model_name=session.model_name,
        external_session_id=_session_external_id(
            getattr(session, "metadata_json", None)
        ),
        workspace_id=str(session.workspace_id),
        turn_count=turn_count,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
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
    session_id: Annotated[
        str, Path(description="Identifier of the session whose turns to list.")
    ],
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> TurnListResponse:
    """Return paginated turns for a session."""
    session_uuid = _parse_session_uuid(session_id)
    session = await persistence.get_chat_session(
        tenant_id=persisted_identity.tenant_id,
        session_id=session_uuid,
        user_id=persisted_identity.user_id,
        workspace_id=persisted_identity.workspace_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    items, total = await persistence.list_chat_turns(
        tenant_id=persisted_identity.tenant_id,
        session_id=session_uuid,
        user_id=persisted_identity.user_id,
        workspace_id=persisted_identity.workspace_id,
        limit=limit,
        offset=offset,
    )
    return TurnListResponse(
        items=[_turn_item_from_repo(turn) for turn in items],
        total=total,
        offset=offset,
        limit=limit,
        has_more=(offset + limit) < total,
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
    session_id: Annotated[
        str, Path(description="Identifier of the session whose stats to retrieve.")
    ],
) -> SessionStatsResponse:
    """Return aggregated usage stats for a session."""
    session_uuid = _parse_session_uuid(session_id)
    stats = await persistence.get_session_stats(
        tenant_id=persisted_identity.tenant_id,
        session_id=session_uuid,
        user_id=persisted_identity.user_id,
        workspace_id=persisted_identity.workspace_id,
    )
    if stats is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionStatsResponse(
        total_tokens_in=int(cast(int, stats.get("total_tokens_in", 0))),
        total_tokens_out=int(cast(int, stats.get("total_tokens_out", 0))),
        total_latency_ms=int(cast(int, stats.get("total_latency_ms", 0))),
        model_breakdown=dict(cast(dict[str, int], stats.get("model_breakdown") or {})),
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
    session_id: Annotated[
        str, Path(description="Identifier of the session to archive.")
    ],
) -> SessionDeleteResponse:
    """Archive a session (soft delete)."""
    archived = await persistence.archive_chat_session(
        tenant_id=persisted_identity.tenant_id,
        session_id=_parse_session_uuid(session_id),
        user_id=persisted_identity.user_id,
        workspace_id=persisted_identity.workspace_id,
    )
    if not archived:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionDeleteResponse()


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
    session_id: Annotated[
        str, Path(description="Identifier of the session to restore.")
    ],
) -> SessionRestoreResponse:
    """Restore an archived session to active status."""
    session_uuid = _parse_session_uuid(session_id)
    session = await persistence.get_chat_session(
        tenant_id=persisted_identity.tenant_id,
        session_id=session_uuid,
        user_id=persisted_identity.user_id,
        workspace_id=persisted_identity.workspace_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if (
        hasattr(session.status, "value")
        and session.status.value == ChatSessionStatus.ACTIVE.value
    ) or str(session.status) == ChatSessionStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail="Session is already active")
    restored = await persistence.restore_chat_session(
        tenant_id=persisted_identity.tenant_id,
        session_id=session_uuid,
        user_id=persisted_identity.user_id,
        workspace_id=persisted_identity.workspace_id,
    )
    if not restored:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionRestoreResponse()


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
    session_id: Annotated[
        str, Path(description="Identifier of the session to export as a dataset.")
    ],
) -> DatasetResponse:
    """Export a session as a GEPA dataset."""
    from fleet_rlm.api.runtime_services.optimization_datasets import (
        build_transcript_dataset_rows,
        persist_jsonl_rows,
    )
    from fleet_rlm.integrations.database import DatasetFormat, DatasetSource
    from fleet_rlm.integrations.database.repository_optimization import (
        DatasetCreateRequest,
    )

    workspace_id = persisted_identity.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=503,
            detail="Workspace persistence is unavailable.",
        )

    session_uuid = _parse_session_uuid(session_id)
    session = await persistence.get_chat_session(
        tenant_id=persisted_identity.tenant_id,
        session_id=session_uuid,
        user_id=persisted_identity.user_id,
        workspace_id=workspace_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    turns = await _load_turns_for_export(
        persistence=persistence,
        tenant_id=persisted_identity.tenant_id,
        session_id=session_uuid,
        user_id=persisted_identity.user_id,
        workspace_id=workspace_id,
    )
    transcript_turns: list[tuple[str | None, str | None]] = [
        (turn.user_message, turn.assistant_message) for turn in turns
    ]
    try:
        rows, label = build_transcript_dataset_rows(
            module_slug=body.module_slug,
            turns=transcript_turns,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dataset_path = await asyncio.to_thread(
        persist_jsonl_rows,
        root=FsPath(os.environ.get("FLEET_RLM_DATASET_ROOT", os.getcwd())),
        rows=rows,
        prefix="transcript-",
    )
    dataset = await persistence.create_dataset(
        DatasetCreateRequest(
            tenant_id=persisted_identity.tenant_id,
            workspace_id=workspace_id,
            created_by_user_id=persisted_identity.user_id,
            name=f"{session.title} ({label})",
            row_count=len(rows),
            format=DatasetFormat.JSONL,
            source=DatasetSource.TRANSCRIPT,
            module_slug=body.module_slug,
            uri=str(dataset_path),
        ),
        examples=rows,
    )
    return DatasetResponse(
        id=str(dataset.id),
        name=dataset.name,
        row_count=dataset.row_count or 0,
        format=dataset.format.value
        if hasattr(dataset.format, "value")
        else str(dataset.format or "jsonl"),
        module_slug=body.module_slug,
        created_at=dataset.created_at.isoformat(),
    )
