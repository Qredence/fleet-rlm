"""Session list/CRUD for fleet_rlm (/api/sessions)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from fleet_rlm.api.dependencies import SessionCatalogDep
from fleet_rlm.api.local_scope import LocalScope, get_local_scope
from fleet_rlm.api.schemas import (
    SessionCreateRequest,
    SessionDetailResponse,
    SessionListResponse,
    SessionPatchRequest,
    SessionSummaryResponse,
    SessionTurnPageResponse,
    UIMessageResponse,
)
from fleet_rlm.api.ui_message import assistant_turn_to_ui_message, user_turn_to_ui_message
from fleet_rlm.sessions.catalog import SequenceCursor
from fleet_rlm.sessions.errors import SessionNotFoundError
from fleet_rlm.sessions.models import AssistantTurnRecord, SessionRecord

router = APIRouter(tags=["sessions"])


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _status(value: str) -> Literal["active", "archived"]:
    return cast(Literal["active", "archived"], value)


def _to_summary(record: SessionRecord) -> SessionSummaryResponse:
    return SessionSummaryResponse(
        id=record.id,
        title=record.title,
        status=_status(record.status),
        checkpoint_version=record.checkpoint_version,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


@router.post(
    "/api/sessions",
    response_model=SessionDetailResponse,
    status_code=201,
    operation_id="create_session",
)
async def create_session(
    body: SessionCreateRequest,
    identity: Annotated[LocalScope, Depends(get_local_scope)],
    repo: SessionCatalogDep,
) -> SessionDetailResponse:
    title = (body.title or "New Session").strip() or "New Session"
    record = await repo.create(
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        title=title[:255],
    )
    return SessionDetailResponse(
        id=record.id,
        title=record.title,
        status=_status(record.status),
        checkpoint_version=record.checkpoint_version,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


@router.get("/api/sessions", response_model=SessionListResponse, operation_id="list_sessions")
async def list_sessions(
    identity: Annotated[LocalScope, Depends(get_local_scope)],
    repo: SessionCatalogDep,
    status: Annotated[Literal["active", "archived"] | None, Query()] = None,
    search: Annotated[str | None, Query(description="Title contains")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionListResponse:
    page = await repo.list(
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )
    return SessionListResponse(
        items=[_to_summary(r) for r in page.items],
        total=page.total,
        offset=offset,
        limit=limit,
        has_more=offset + len(page.items) < page.total,
    )


@router.get(
    "/api/sessions/{session_id}",
    response_model=SessionDetailResponse,
    operation_id="get_session",
)
async def get_session(
    session_id: UUID,
    identity: Annotated[LocalScope, Depends(get_local_scope)],
    repo: SessionCatalogDep,
) -> SessionDetailResponse:
    try:
        record = await repo.get(
            session_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return SessionDetailResponse(
        id=record.id,
        title=record.title,
        status=_status(record.status),
        checkpoint_version=record.checkpoint_version,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


@router.patch(
    "/api/sessions/{session_id}",
    response_model=SessionDetailResponse,
    operation_id="update_session",
)
async def patch_session(
    session_id: UUID,
    body: SessionPatchRequest,
    identity: Annotated[LocalScope, Depends(get_local_scope)],
    repo: SessionCatalogDep,
) -> SessionDetailResponse:
    if body.title is None and body.status is None:
        raise HTTPException(status_code=422, detail="no fields to update")
    if body.title is not None and not body.title.strip():
        raise HTTPException(status_code=422, detail="title must not be empty")
    if body.status is not None and body.status.strip().lower() not in {"active", "archived"}:
        raise HTTPException(status_code=422, detail="status must be active or archived")
    try:
        record = await repo.update(
            session_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            title=body.title,
            status=body.status,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)[:200]) from exc
    return SessionDetailResponse(
        id=record.id,
        title=record.title,
        status=_status(record.status),
        checkpoint_version=record.checkpoint_version,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


@router.get(
    "/api/sessions/{session_id}/turns",
    response_model=SessionTurnPageResponse,
    operation_id="list_session_turns",
)
async def list_session_turns(
    session_id: UUID,
    identity: Annotated[LocalScope, Depends(get_local_scope)],
    repo: SessionCatalogDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    after_sequence: Annotated[int | None, Query(ge=0)] = None,
) -> SessionTurnPageResponse:
    try:
        page = await repo.turns(
            session_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            cursor=SequenceCursor(after_sequence),
            limit=limit,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    messages = [
        assistant_turn_to_ui_message(item) if isinstance(item, AssistantTurnRecord) else user_turn_to_ui_message(item)
        for item in page.items
    ]
    return SessionTurnPageResponse(
        items=[UIMessageResponse.model_validate(message) for message in messages],
        next_after_sequence=page.next_after_sequence,
    )
