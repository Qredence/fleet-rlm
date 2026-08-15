"""Session list/CRUD for fleet_rlm (/api/sessions)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Query

from fleet_rlm.api.dependencies import LocalScopeDep, SessionCatalogDep
from fleet_rlm.api.errors import http_error
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
from fleet_rlm.posthog_client import get_client, get_distinct_id
from fleet_rlm.sessions.catalog import SequenceCursor
from fleet_rlm.sessions.errors import SessionNotFoundError
from fleet_rlm.sessions.models import AssistantTurnRecord, SessionRecord

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


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
    "",
    response_model=SessionDetailResponse,
    status_code=201,
    operation_id="create_session",
)
async def create_session(
    body: SessionCreateRequest,
    identity: LocalScopeDep,
    repo: SessionCatalogDep,
) -> SessionDetailResponse:
    """
    Create a session for the authenticated user in the current workspace.

    Parameters:
        body (SessionCreateRequest): Session creation data, including the optional title.
        identity (LocalScopeDep): Authenticated user and workspace scope.
        repo (SessionCatalogDep): Session repository used to create the session.

    Returns:
        SessionDetailResponse: The newly created session details.
    """
    title = (body.title or "New Session").strip() or "New Session"
    record = await repo.create(
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        title=title[:255],
    )
    ph = get_client()
    if ph is not None:
        ph.capture(
            distinct_id=get_distinct_id(),
            event="session_created",
            properties={"workspace_id": str(identity.workspace_id)},
        )
    return SessionDetailResponse(
        id=record.id,
        title=record.title,
        status=_status(record.status),
        checkpoint_version=record.checkpoint_version,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


@router.get("", response_model=SessionListResponse, operation_id="list_sessions")
async def list_sessions(
    identity: LocalScopeDep,
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
    "/{session_id}",
    response_model=SessionDetailResponse,
    operation_id="get_session",
)
async def get_session(
    session_id: UUID,
    identity: LocalScopeDep,
    repo: SessionCatalogDep,
) -> SessionDetailResponse:
    try:
        record = await repo.get(
            session_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
    except SessionNotFoundError as exc:
        raise http_error(404, "session_not_found", "Session not found") from exc
    return SessionDetailResponse(
        id=record.id,
        title=record.title,
        status=_status(record.status),
        checkpoint_version=record.checkpoint_version,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


@router.patch(
    "/{session_id}",
    response_model=SessionDetailResponse,
    operation_id="update_session",
)
async def patch_session(
    session_id: UUID,
    body: SessionPatchRequest,
    identity: LocalScopeDep,
    repo: SessionCatalogDep,
) -> SessionDetailResponse:
    """
    Update the title or status of a session within the authenticated user's workspace.

    Parameters:
        body (SessionPatchRequest): Fields to update; at least one field is required.

    Returns:
        SessionDetailResponse: The updated session details.

    Raises:
        HTTPException: If no fields are provided, the title is blank, the status is invalid, or the
            session cannot be updated.
    """
    if body.title is None and body.status is None:
        raise http_error(422, "session_no_fields", "No fields to update")
    if body.title is not None and not body.title.strip():
        raise http_error(422, "session_title_empty", "Title must not be empty")
    if body.status is not None and body.status.strip().lower() not in {"active", "archived"}:
        raise http_error(422, "session_status_invalid", "Status must be active or archived")
    try:
        record = await repo.update(
            session_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            title=body.title,
            status=body.status,
        )
    except SessionNotFoundError as exc:
        raise http_error(404, "session_not_found", "Session not found") from exc
    except ValueError as exc:
        # Internal validation failures must not leak exception text into the
        # public contract; collapse them to the closed invalid_request code.
        raise http_error(422, "invalid_request", "Invalid request") from exc
    ph = get_client()
    if ph is not None:
        ph.capture(
            distinct_id=get_distinct_id(),
            event="session_updated",
            properties={
                "workspace_id": str(identity.workspace_id),
                "session_id": str(session_id),
                "title_changed": body.title is not None,
                "status_changed": body.status is not None,
                "new_status": body.status,
            },
        )
    return SessionDetailResponse(
        id=record.id,
        title=record.title,
        status=_status(record.status),
        checkpoint_version=record.checkpoint_version,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


@router.get(
    "/{session_id}/turns",
    response_model=SessionTurnPageResponse,
    operation_id="list_session_turns",
)
async def list_session_turns(
    session_id: UUID,
    identity: LocalScopeDep,
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
        raise http_error(404, "session_not_found", "Session not found") from exc
    messages = [
        assistant_turn_to_ui_message(item) if isinstance(item, AssistantTurnRecord) else user_turn_to_ui_message(item)
        for item in page.items
    ]
    return SessionTurnPageResponse(
        items=[UIMessageResponse.model_validate(message) for message in messages],
        next_after_sequence=page.next_after_sequence,
    )
