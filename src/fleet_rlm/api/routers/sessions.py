"""Router for session state management."""

from collections.abc import Mapping

from fastapi import APIRouter, HTTPException, Path, Query

from ..dependencies import HTTPIdentityDep, ServerStateDep
from ..schemas.core import (
    DatasetResponse,
    SessionDeleteResponse,
    SessionDetailResponse,
    SessionExportRequest,
    SessionListItem,
    SessionListResponse,
    SessionStateResponse,
    SessionStateSummary,
    TurnItem,
    TurnListResponse,
)
from ..server_utils import sanitize_id as _sanitize_id

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _string_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_legacy_session_key_owner(key: object) -> tuple[str | None, str | None]:
    if not isinstance(key, str):
        return None, None
    if key.startswith("owner:"):
        return None, None
    workspace_id, separator, remainder = key.partition(":")
    if not separator:
        return None, None
    user_id, separator, _session_id = remainder.partition(":")
    if not separator:
        return None, None
    return (
        workspace_id or None,
        user_id or None,
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
async def list_session_state(
    state: ServerStateDep,
    identity: HTTPIdentityDep,
) -> SessionStateResponse:
    """Return lightweight summaries of active/restored in-memory session state."""
    summaries: list[SessionStateSummary] = []
    expected_workspace_id = _sanitize_id(identity.tenant_claim, "default")
    expected_user_id = _sanitize_id(identity.user_claim, "anonymous")
    for key, payload in state.sessions.items():
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
    summary="List session history",
    description="Paginated list of durable session transcripts with search and status filters.",
)
async def list_sessions_endpoint(
    identity: HTTPIdentityDep,
    search: str | None = Query(default=None, description="Full-text search on title"),
    status: str | None = Query(
        default=None, description="Filter by status (active, archived)"
    ),
    limit: int = Query(default=20, ge=1, le=100, description="Page size"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> SessionListResponse:
    """Return paginated session history filtered by the caller's ownership."""
    from fleet_rlm.integrations.local_store import SessionStatus, list_sessions

    status_filter = None
    if status:
        try:
            status_filter = SessionStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    items, total = list_sessions(
        owner_tenant=identity.tenant_claim,
        owner_user=identity.user_claim,
        search=search,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return SessionListResponse(
        items=[
            SessionListItem(
                id=s.id,  # type: ignore
                title=s.title,
                status=s.status.value if hasattr(s.status, "value") else str(s.status),
                model_name=s.model_name,
                external_session_id=s.external_session_id,
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
    summary="Get session detail",
    description="Return session metadata and turn count for a specific session.",
)
async def get_session_detail(
    identity: HTTPIdentityDep,
    session_id: int = Path(description="Identifier of the session to inspect."),
) -> SessionDetailResponse:
    """Return full session detail with turn count."""
    from fleet_rlm.integrations.local_store import get_chat_session, get_turns_paginated

    session = get_chat_session(
        session_id,
        owner_tenant=identity.tenant_claim,
        owner_user=identity.user_claim,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    _turns, turn_count = get_turns_paginated(session_id, limit=0, offset=0)
    return SessionDetailResponse(
        id=session.id,  # type: ignore
        title=session.title,
        status=session.status.value
        if hasattr(session.status, "value")
        else str(session.status),
        model_name=session.model_name,
        external_session_id=session.external_session_id,
        workspace_id=session.workspace_id,
        turn_count=turn_count,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


@router.get(
    "/{session_id}/turns",
    response_model=TurnListResponse,
    summary="Get session turns",
    description="Paginated turn-by-turn transcript for a session.",
)
async def get_session_turns(
    identity: HTTPIdentityDep,
    session_id: int = Path(
        description="Identifier of the session whose turns to list."
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Page size"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> TurnListResponse:
    """Return paginated turns for a session."""
    from fleet_rlm.integrations.local_store import get_chat_session, get_turns_paginated

    session = get_chat_session(
        session_id,
        owner_tenant=identity.tenant_claim,
        owner_user=identity.user_claim,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    items, total = get_turns_paginated(session_id, limit=limit, offset=offset)
    return TurnListResponse(
        items=[
            TurnItem(
                id=t.id,  # type: ignore
                turn_index=t.turn_index,
                user_message=t.user_message,
                assistant_message=t.assistant_message,
                created_at=t.created_at.isoformat(),
            )
            for t in items
        ],
        total=total,
        offset=offset,
        limit=limit,
        has_more=(offset + limit) < total,
    )


@router.delete(
    "/{session_id}",
    response_model=SessionDeleteResponse,
    summary="Archive session",
    description="Soft-delete (archive) a session. Returns success when archived, 404 if not found or not owned.",
)
async def delete_session_endpoint(
    identity: HTTPIdentityDep,
    session_id: int = Path(description="Identifier of the session to archive."),
) -> SessionDeleteResponse:
    """Archive a session (soft delete)."""
    from fleet_rlm.integrations.local_store import archive_session

    archived = archive_session(
        session_id,
        owner_tenant=identity.tenant_claim,
        owner_user=identity.user_claim,
    )
    if not archived:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionDeleteResponse()


@router.post(
    "/{session_id}/export",
    response_model=DatasetResponse,
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
    session_id: int = Path(
        description="Identifier of the session to export as a dataset."
    ),
) -> DatasetResponse:
    """Export a session as a GEPA dataset."""
    from fleet_rlm.integrations.local_store import (
        export_session_as_dataset,
        get_chat_session,
    )

    session = get_chat_session(
        session_id,
        owner_tenant=identity.tenant_claim,
        owner_user=identity.user_claim,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        dataset = export_session_as_dataset(session_id, body.module_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return DatasetResponse(
        id=dataset.id or 0,
        name=dataset.name,
        row_count=dataset.row_count or 0,
        format=dataset.format or "jsonl",
        module_slug=dataset.module_slug,
        created_at=dataset.created_at.isoformat(),
    )
