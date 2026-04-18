"""Router for session state management."""

import asyncio
from collections.abc import Mapping
import os
from pathlib import Path as FsPath
from typing import cast
import uuid

from fastapi import APIRouter, HTTPException, Path, Query

from fleet_rlm.integrations.database import ChatSessionStatus, ChatTurn
from fleet_rlm.integrations.database.types import IdentityUpsertResult

from ..auth import AuthError, resolve_admitted_identity
from ..dependencies import HTTPIdentityDep, RepositoryDep, ServerStateDep
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


def _parse_session_uuid(session_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


def _parse_legacy_session_id(session_id: str) -> int:
    try:
        return int(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


def _session_external_id(metadata: object) -> str | None:
    if not isinstance(metadata, dict):
        return None
    metadata_dict = cast(dict[str, object], metadata)
    return _optional_string(metadata_dict.get("external_session_id"))


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


def _turn_item_from_repo(turn: ChatTurn) -> TurnItem:
    return TurnItem(
        id=str(turn.id),
        turn_index=turn.turn_index,
        user_message=turn.user_message,
        assistant_message=turn.assistant_message,
        created_at=turn.created_at.isoformat(),
    )


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
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
    search: str | None = Query(default=None, description="Full-text search on title"),
    status: str | None = Query(
        default=None, description="Filter by status (active, archived)"
    ),
    limit: int = Query(default=20, ge=1, le=100, description="Page size"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> SessionListResponse:
    """Return paginated session history filtered by the caller's ownership."""
    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )
    if repository is not None and persisted_identity is not None:
        status_filter = None
        if status:
            try:
                status_filter = ChatSessionStatus(status)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status: {status}",
                ) from exc
        items, total = await repository.list_chat_sessions(
            tenant_id=persisted_identity.tenant_id,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
            search=search,
            status=status_filter,
            limit=limit,
            offset=offset,
        )
        return SessionListResponse(
            items=[
                SessionListItem(
                    id=str(s.id),
                    title=s.title,
                    status=s.status.value
                    if hasattr(s.status, "value")
                    else str(s.status),
                    model_name=s.model_name,
                    external_session_id=_session_external_id(s.metadata_json),
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

    from fleet_rlm.integrations.local_store import SessionStatus, list_sessions

    status_filter = None
    if status:
        try:
            status_filter = SessionStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    items, total = await asyncio.to_thread(
        list_sessions,
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
                id=str(s.id),
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
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
    session_id: str = Path(description="Identifier of the session to inspect."),
) -> SessionDetailResponse:
    """Return full session detail with turn count."""
    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )
    if repository is not None and persisted_identity is not None:
        session_uuid = _parse_session_uuid(session_id)
        session = await repository.get_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        _turns, turn_count = await repository.list_chat_turns(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
            limit=0,
            offset=0,
        )
        return SessionDetailResponse(
            id=str(session.id),
            title=session.title,
            status=session.status.value
            if hasattr(session.status, "value")
            else str(session.status),
            model_name=session.model_name,
            external_session_id=_session_external_id(session.metadata_json),
            workspace_id=str(session.workspace_id),
            turn_count=turn_count,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
        )

    from fleet_rlm.integrations.local_store import get_chat_session, get_turns_paginated

    local_session_id = _parse_legacy_session_id(session_id)
    session = await asyncio.to_thread(
        get_chat_session,
        local_session_id,
        owner_tenant=identity.tenant_claim,
        owner_user=identity.user_claim,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    _turns, turn_count = await asyncio.to_thread(
        get_turns_paginated, local_session_id, limit=0, offset=0
    )
    return SessionDetailResponse(
        id=str(session.id),
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
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
    session_id: str = Path(
        description="Identifier of the session whose turns to list."
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Page size"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> TurnListResponse:
    """Return paginated turns for a session."""
    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )
    if repository is not None and persisted_identity is not None:
        session_uuid = _parse_session_uuid(session_id)
        session = await repository.get_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        items, total = await repository.list_chat_turns(
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

    from fleet_rlm.integrations.local_store import get_chat_session, get_turns_paginated

    local_session_id = _parse_legacy_session_id(session_id)
    session = await asyncio.to_thread(
        get_chat_session,
        local_session_id,
        owner_tenant=identity.tenant_claim,
        owner_user=identity.user_claim,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    items, total = await asyncio.to_thread(
        get_turns_paginated, local_session_id, limit=limit, offset=offset
    )
    return TurnListResponse(
        items=[
            TurnItem(
                id=str(t.id),
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
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
    session_id: str = Path(description="Identifier of the session to archive."),
) -> SessionDeleteResponse:
    """Archive a session (soft delete)."""
    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )
    if repository is not None and persisted_identity is not None:
        archived = await repository.archive_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=_parse_session_uuid(session_id),
            user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
        if not archived:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionDeleteResponse()

    from fleet_rlm.integrations.local_store import archive_session

    local_session_id = _parse_legacy_session_id(session_id)
    archived = await asyncio.to_thread(
        archive_session,
        local_session_id,
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
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
    session_id: str = Path(
        description="Identifier of the session to export as a dataset."
    ),
) -> DatasetResponse:
    """Export a session as a GEPA dataset."""
    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )
    if repository is not None and persisted_identity is not None:
        from fleet_rlm.api.runtime_services.optimization_datasets import (
            build_transcript_dataset_rows,
            persist_jsonl_rows,
        )
        from fleet_rlm.integrations.database import DatasetFormat, DatasetSource
        from fleet_rlm.integrations.database.types import DatasetCreateRequest

        workspace_id = persisted_identity.workspace_id
        if workspace_id is None:
            raise HTTPException(
                status_code=503,
                detail="Workspace persistence is unavailable.",
            )

        session_uuid = _parse_session_uuid(session_id)
        session = await repository.get_chat_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=workspace_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        turns, _total = await repository.list_chat_turns(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            user_id=persisted_identity.user_id,
            workspace_id=workspace_id,
            limit=10_000,
            offset=0,
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
        dataset = await repository.create_dataset(
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

    from fleet_rlm.integrations.local_store import (
        export_session_as_dataset,
        get_chat_session,
    )

    local_session_id = _parse_legacy_session_id(session_id)
    session = await asyncio.to_thread(
        get_chat_session,
        local_session_id,
        owner_tenant=identity.tenant_claim,
        owner_user=identity.user_claim,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        dataset = await asyncio.to_thread(
            export_session_as_dataset, local_session_id, body.module_slug
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return DatasetResponse(
        id=str(dataset.id or 0),
        name=dataset.name,
        row_count=dataset.row_count or 0,
        format=dataset.format or "jsonl",
        module_slug=dataset.module_slug,
        created_at=dataset.created_at.isoformat(),
    )
