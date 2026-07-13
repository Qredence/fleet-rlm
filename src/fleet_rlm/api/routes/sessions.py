"""Session list/CRUD for fleet_rlm (/api/sessions)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from fleet_rlm.api.dependencies import SessionRepositoryDep
from fleet_rlm.api.identity import RequestIdentity, get_request_identity
from fleet_rlm.api.schemas import (
    SessionCreateRequest,
    SessionDetailResponse,
    SessionListResponse,
    SessionPatchRequest,
    SessionSummaryResponse,
    TurnListResponse,
    TurnResponse,
)
from fleet_rlm.api.ui_message import detail_parts_to_ui_parts
from fleet_rlm.sessions.errors import SessionNotFoundError
from fleet_rlm.sessions.models import SessionRecord, TurnRecord

router = APIRouter(tags=["sessions"])


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _to_summary(record: SessionRecord) -> SessionSummaryResponse:
    return SessionSummaryResponse(
        id=record.id,
        title=record.title,
        status=record.status,
        checkpoint_version=record.checkpoint_version,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


def _to_turn(record: TurnRecord) -> TurnResponse:
    metadata: dict[str, object] = {}
    if record.run_id is not None:
        metadata["runId"] = str(record.run_id)
    if record.structured_output is not None:
        metadata["structuredResult"] = {
            "schemaId": record.result_schema_id,
            "schemaVersion": record.result_schema_version,
            "value": record.structured_output,
        }
    return TurnResponse(
        id=record.id,
        sequence=record.sequence,
        role=record.role,
        content=record.content,
        status=record.status,
        run_id=record.run_id,
        parts=detail_parts_to_ui_parts(
            record.detail_parts,
            answer_text=record.content,
            structured_result=(
                {
                    "schemaId": record.result_schema_id,
                    "schemaVersion": record.result_schema_version,
                    "value": record.structured_output,
                }
                if record.structured_output is not None
                else None
            ),
        ),
        metadata=metadata or None,
    )


@router.post("/api/sessions", response_model=SessionDetailResponse, status_code=201)
async def create_session(
    body: SessionCreateRequest,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    repo: SessionRepositoryDep,
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
        status=record.status,
        checkpoint_version=record.checkpoint_version,
        turn_count=0,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


@router.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    repo: SessionRepositoryDep,
    status: Annotated[str | None, Query(description="active | archived")] = None,
    search: Annotated[str | None, Query(description="Title contains")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionListResponse:
    items, total = await repo.list(
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )
    return SessionListResponse(
        items=[_to_summary(r) for r in items],
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + len(items) < total,
    )


@router.get("/api/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    repo: SessionRepositoryDep,
) -> SessionDetailResponse:
    try:
        record = await repo.get_owned(
            session_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
        count = await repo.turn_count(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return SessionDetailResponse(
        id=record.id,
        title=record.title,
        status=record.status,
        checkpoint_version=record.checkpoint_version,
        turn_count=count,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


@router.patch("/api/sessions/{session_id}", response_model=SessionDetailResponse)
async def patch_session(
    session_id: UUID,
    body: SessionPatchRequest,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    repo: SessionRepositoryDep,
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
        count = await repo.turn_count(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)[:200]) from exc
    return SessionDetailResponse(
        id=record.id,
        title=record.title,
        status=record.status,
        checkpoint_version=record.checkpoint_version,
        turn_count=count,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


@router.delete("/api/sessions/{session_id}", response_model=SessionDetailResponse)
async def delete_session(
    session_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    repo: SessionRepositoryDep,
) -> SessionDetailResponse:
    """Soft-delete: archive the session."""
    try:
        record = await repo.archive(
            session_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
        count = await repo.turn_count(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return SessionDetailResponse(
        id=record.id,
        title=record.title,
        status=record.status,
        checkpoint_version=record.checkpoint_version,
        turn_count=count,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


@router.get("/api/sessions/{session_id}/turns", response_model=TurnListResponse)
async def list_session_turns(
    session_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    repo: SessionRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TurnListResponse:
    try:
        items, total = await repo.list_turns(
            session_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            limit=limit,
            offset=offset,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return TurnListResponse(
        items=[_to_turn(t) for t in items],
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + len(items) < total,
    )
