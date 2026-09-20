"""Session list/CRUD for fleet_rlm (/api/sessions)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from fleet_rlm.api.dependencies import (
    LocalScopeDep,
    MLflowRuntimeDep,
    RunLifecycleDep,
    SessionCatalogDep,
    SessionLifecycleDep,
    SessionPrewarmDep,
    SettingsDep,
    TraceFeedbackServiceDep,
)
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import (
    SessionCreateRequest,
    SessionDetailResponse,
    SessionListResponse,
    SessionPatchRequest,
    SessionSummaryResponse,
    SessionTurnPageResponse,
    TraceFeedbackRequest,
    TraceFeedbackResponse,
    UIMessageResponse,
)
from fleet_rlm.api.ui_message import assistant_turn_to_ui_message, user_turn_to_ui_message
from fleet_rlm.observability.feedback import (
    TraceFeedbackNotFoundError,
    TraceFeedbackUnavailableError,
)
from fleet_rlm.observability.posthog import capture
from fleet_rlm.sessions.catalog import SequenceCursor
from fleet_rlm.sessions.errors import SessionNotFoundError, SessionRetirementPendingError
from fleet_rlm.sessions.models import AssistantTurnRecord, SessionRecord, TurnAccess
from fleet_rlm.sessions.run_state import RunNotFoundError

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
sessions_router = router


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


def _to_detail(record: SessionRecord) -> SessionDetailResponse:
    return SessionDetailResponse(
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
    responses={503: {"description": "Service is not ready"}},
)
async def create_session(
    body: SessionCreateRequest,
    identity: LocalScopeDep,
    repo: SessionCatalogDep,
    prewarm: SessionPrewarmDep,
) -> SessionDetailResponse:
    """
    Create a session for the local user in the current workspace.

    Parameters:
        body (SessionCreateRequest): Session creation data, including the optional title.
        identity (LocalScopeDep): The deterministic local User and Workspace scope.
        repo (SessionCatalogDep): Session repository used to create the session.
        prewarm (SessionPrewarmDep): Optional background Sandbox pre-warm trigger.

    Returns:
        SessionDetailResponse: The newly created session details.
    """
    title = (body.title or "New Session").strip() or "New Session"
    record = await repo.create(
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        title=title[:255],
    )
    if prewarm is not None:
        # Fire-and-forget: the response does not wait for the Sandbox. A warm
        # binding makes the first Turn skip sandbox creation and layout; a
        # failed or absent pre-warm leaves the first Turn acquiring normally.
        prewarm(record.id, identity.user_id, identity.workspace_id)
    capture("session_created", properties={"workspace_id": str(identity.workspace_id)})
    return _to_detail(record)


@router.get(
    "",
    response_model=SessionListResponse,
    operation_id="list_sessions",
    responses={503: {"description": "Service is not ready"}},
)
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
    responses={
        404: {"description": "Session not found"},
        503: {"description": "Service is not ready"},
    },
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
    return _to_detail(record)


@router.patch(
    "/{session_id}",
    response_model=SessionDetailResponse,
    operation_id="update_session",
    responses={
        404: {"description": "Session not found"},
        422: {"description": "Session update is invalid"},
        503: {"description": "Service is not ready, or Session retirement is pending"},
    },
)
async def patch_session(
    session_id: UUID,
    body: SessionPatchRequest,
    identity: LocalScopeDep,
    lifecycle: SessionLifecycleDep,
) -> SessionDetailResponse:
    """
    Update the title or status of a session within the local user's workspace.

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
    normalized_status = body.status.strip().lower() if body.status is not None else None
    if normalized_status is not None and normalized_status not in {"active", "archived"}:
        raise http_error(422, "session_status_invalid", "Status must be active or archived")
    try:
        record = await lifecycle.update(
            session_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            title=body.title,
            status=normalized_status,
        )
    except SessionNotFoundError as exc:
        raise http_error(404, "session_not_found", "Session not found") from exc
    except ValueError as exc:
        # Internal validation failures must not leak exception text into the
        # public contract; collapse them to the closed invalid_request code.
        raise http_error(422, "invalid_request", "Invalid request") from exc
    except SessionRetirementPendingError as exc:
        raise http_error(
            503,
            "session_retirement_pending",
            "Session retirement is pending",
        ) from exc
    capture(
        "session_updated",
        properties={
            "workspace_id": str(identity.workspace_id),
            "session_id": str(session_id),
            "title_changed": body.title is not None,
            "status_changed": body.status is not None,
            "new_status": body.status,
        },
    )
    return _to_detail(record)


@router.get(
    "/{session_id}/turns",
    response_model=SessionTurnPageResponse,
    operation_id="list_session_turns",
    responses={
        404: {"description": "Session not found"},
        503: {"description": "Service is not ready"},
    },
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


# ---------------------------------------------------------------------------
# Traces Router (/api/sessions/{session_id}/traces/feedback)
# ---------------------------------------------------------------------------

traces_router = APIRouter(prefix="/api/sessions", tags=["traces"])


@traces_router.post(
    "/{session_id}/traces/feedback",
    response_model=TraceFeedbackResponse,
    operation_id="submit_trace_feedback",
    responses={
        404: {"description": "Trace not found"},
        503: {"description": "Trace feedback is unavailable"},
    },
)
async def submit_trace_feedback(
    session_id: UUID,
    body: TraceFeedbackRequest,
    identity: LocalScopeDep,
    repo: SessionCatalogDep,
    settings: SettingsDep,
    service: TraceFeedbackServiceDep,
    mlflow_runtime: MLflowRuntimeDep,
) -> TraceFeedbackResponse:
    """Record human feedback for an execution trace owned by this Session."""
    try:
        await repo.get(
            session_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
    except SessionNotFoundError as exc:
        raise http_error(404, "feedback_trace_not_found", "Trace not found") from exc

    try:
        result = await mlflow_runtime.run_operation(
            service.submit,
            session_id=session_id,
            trace_id=body.trace_id,
            value=body.value,
            comment=body.comment,
            content_enabled=settings.mlflow_trace_content_enabled,
        )
    except TraceFeedbackNotFoundError as exc:
        raise http_error(404, "feedback_trace_not_found", "Trace not found") from exc
    except TraceFeedbackUnavailableError as exc:
        raise http_error(503, "trace_feedback_unavailable", "Trace feedback is unavailable") from exc
    except RuntimeError as exc:
        raise http_error(503, "trace_feedback_unavailable", "Trace feedback is unavailable") from exc

    return TraceFeedbackResponse(
        trace_id=result.trace_id,
        value=result.value,
        assessment_id=result.assessment_id,
    )


# ---------------------------------------------------------------------------
# Runs Router (/api/runs)
# ---------------------------------------------------------------------------

runs_router = APIRouter(prefix="/api/runs", tags=["runs"])


class CancellationResponse(BaseModel):
    run_id: UUID
    state: Literal["requested", "already_requested", "already_terminal"]


@runs_router.put(
    "/{run_id}/cancellation",
    response_model=CancellationResponse,
    operation_id="request_run_cancellation",
    responses={
        404: {"description": "Run not found"},
        503: {"description": "Service is not ready"},
    },
)
async def request_run_cancellation(
    run_id: UUID,
    identity: LocalScopeDep,
    lifecycle: RunLifecycleDep,
) -> CancellationResponse:
    """Request cancellation for a run."""
    try:
        status = await lifecycle.request_cancel(TurnAccess(identity.user_id, identity.workspace_id), run_id)
    except RunNotFoundError as exc:
        raise http_error(404, "run_not_found", "Run not found") from exc
    capture(
        "run_cancellation_requested",
        properties={
            "workspace_id": str(identity.workspace_id),
            "run_id": str(run_id),
            "cancellation_state": status,
        },
    )
    return CancellationResponse(run_id=run_id, state=status)


__all__ = [
    "CancellationResponse",
    "router",
    "runs_router",
    "sessions_router",
    "traces_router",
]
