"""Session-scoped Turn creation with prepare-before-headers streaming."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

from fleet_rlm.api.dependencies import TurnCoordinatorDep
from fleet_rlm.api.local_scope import LocalScope, get_local_scope
from fleet_rlm.api.schemas import CreateTurnRequest
from fleet_rlm.api.sse import AISDKUIProjector
from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.turn_coordinator import OpenedTurnStream
from fleet_rlm.chat.turn_lifecycle import (
    TurnIdempotencyMismatchError,
    TurnInProgressError,
    TurnLifecycleUnavailableError,
    TurnNotFoundError,
)
from fleet_rlm.chat.turn_preparation import TurnPreparationTimeoutError, TurnPreparationUnavailableError
from fleet_rlm.observability.failure_diagnostics import normalize_turn_failure
from fleet_rlm.sessions.models import TurnAccess, TurnInput
from fleet_rlm.skills.errors import InvalidSkillSelectionError
from fleet_rlm.skills.models import SkillSelectionRef

router = APIRouter(tags=["turns"])
logger = logging.getLogger(__name__)


def _http_error(
    status: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message}, headers=headers)


def _correlation_id(request: Request) -> str:
    for header in ("x-request-id", "x-correlation-id"):
        value = request.headers.get(header)
        if value and value.strip():
            return value.strip()
    return str(uuid4())


def _preparation_unavailable(request: Request, exc: BaseException) -> HTTPException:
    correlation_id = _correlation_id(request)
    diagnostic = normalize_turn_failure(exc)
    logger.warning(
        "turn_preparation_failure correlation_id=%s cause_type=%s provider_status_category=%s message=%s",
        correlation_id,
        diagnostic.cause_type,
        diagnostic.provider_status_category,
        diagnostic.message,
    )
    return _http_error(
        503,
        "turn_unavailable",
        "Turn is unavailable",
        headers={
            "X-Request-ID": correlation_id,
            "X-Correlation-ID": correlation_id,
        },
    )


async def _open_turn(
    session_id: UUID,
    body: CreateTurnRequest,
    request: Request,
    response: Response,
    identity: Annotated[LocalScope, Depends(get_local_scope)],
    coordinator: TurnCoordinatorDep,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
) -> OpenedTurnStream:
    """Prepare a Turn before FastAPI commits the native SSE response."""
    try:
        command = OpenTurnCommand(
            TurnAccess(identity.user_id, identity.workspace_id),
            session_id,
            TurnInput(
                body.text,
                tuple(body.attachment_ids),
                tuple(
                    SkillSelectionRef(selection.id, selection.expected_version) for selection in body.skill_selections
                ),
            ),
            idempotency_key,
            uuid4(),
        )
        opened = await coordinator.open(command)
    except TurnNotFoundError as exc:
        raise _http_error(404, "session_not_found", "Session not found") from exc
    except TurnInProgressError as exc:
        raise _http_error(409, "turn_in_progress", "A Turn is already running") from exc
    except TurnIdempotencyMismatchError as exc:
        raise _http_error(409, "idempotency_mismatch", "Idempotency key input mismatch") from exc
    except InvalidSkillSelectionError as exc:
        raise _http_error(422, "invalid_skill_selection", "Invalid Skill selection") from exc
    except TurnPreparationTimeoutError as exc:
        raise _http_error(504, "turn_preparation_timeout", "Turn preparation timed out") from exc
    except (TurnLifecycleUnavailableError, TurnPreparationUnavailableError) as exc:
        raise _preparation_unavailable(request, exc) from exc
    except ValueError as exc:
        raise _http_error(422, "invalid_request", "Invalid request") from exc

    response.headers["x-vercel-ai-ui-message-stream"] = "v1"
    response.headers["X-Fleet-Run-ID"] = str(opened.run_id)
    return opened


OpenedTurnDep = Annotated[OpenedTurnStream, Depends(_open_turn)]


@router.post(
    "/api/sessions/{session_id}/turns",
    operation_id="create_turn",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "AI SDK UI v1 UIMessage SSE stream",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
            "headers": {
                "Cache-Control": {"schema": {"type": "string"}},
                "X-Accel-Buffering": {"schema": {"type": "string"}},
                "x-vercel-ai-ui-message-stream": {"schema": {"type": "string"}},
                "X-Fleet-Run-ID": {"schema": {"type": "string", "format": "uuid"}},
            },
        },
        404: {"description": "Session not found"},
        409: {"description": "Turn conflict"},
        422: {"description": "Invalid request"},
        503: {"description": "Turn unavailable"},
        504: {"description": "Turn preparation timed out"},
    },
)
async def create_turn(opened: OpenedTurnDep) -> AsyncIterator[ServerSentEvent]:
    """Project one prepared Turn through FastAPI's native SSE transport."""
    projector = AISDKUIProjector()
    try:
        async for event in opened:
            for chunk in projector.project(event):
                yield ServerSentEvent(data=chunk)
        yield ServerSentEvent(raw_data="[DONE]")
    finally:
        await opened.aclose()


__all__ = ["OpenedTurnDep", "router"]
