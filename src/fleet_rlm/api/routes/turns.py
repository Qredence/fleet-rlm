"""Session-scoped Turn creation with prepare-before-headers streaming."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.responses import StreamingResponse

from fleet_rlm.api.dependencies import TurnCoordinatorDep
from fleet_rlm.api.identity import RequestIdentity, get_request_identity
from fleet_rlm.api.schemas import CreateTurnRequest
from fleet_rlm.api.sse import AI_SDK_UI_STREAM_HEADERS, AISDKUIProjector, SSEProjector, _json_default
from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.turn_lifecycle import (
    TurnIdempotencyMismatchError,
    TurnInProgressError,
    TurnLifecycleUnavailable,
    TurnNotFoundError,
)
from fleet_rlm.chat.turn_preparation import TurnPreparationTimeout, TurnPreparationUnavailable
from fleet_rlm.sessions.models import TurnAccess, TurnInput

router = APIRouter(tags=["turns"])


def _http_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.post(
    "/api/sessions/{session_id}/turns",
    operation_id="create_turn",
    responses={
        200: {
            "description": "AI SDK UI v1 UIMessage SSE stream",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        404: {"description": "Session not found"},
        409: {"description": "Turn conflict"},
        422: {"description": "Invalid request"},
        503: {"description": "Turn unavailable"},
        504: {"description": "Turn preparation timed out"},
    },
)
async def create_turn(
    session_id: UUID,
    body: CreateTurnRequest,
    request: Request,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    coordinator: TurnCoordinatorDep,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
) -> StreamingResponse:
    """Open the Turn fully, then construct its public SSE response."""
    try:
        command = OpenTurnCommand(
            TurnAccess(identity.user_id, identity.workspace_id),
            session_id,
            TurnInput(body.text, tuple(body.attachment_ids)),
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
    except TurnPreparationTimeout as exc:
        raise _http_error(504, "turn_preparation_timeout", "Turn preparation timed out") from exc
    except (TurnLifecycleUnavailable, TurnPreparationUnavailable) as exc:
        raise _http_error(503, "turn_unavailable", "Turn is unavailable") from exc
    except ValueError as exc:
        raise _http_error(422, "invalid_request", "Invalid request") from exc

    projector = AISDKUIProjector()

    async def events() -> AsyncIterator[str]:
        try:
            async for event in opened:
                if await request.is_disconnected():
                    break
                for chunk in projector.project(event):
                    yield f"data: {json.dumps(chunk, default=_json_default)}\n\n"
            if not await request.is_disconnected():
                yield "data: [DONE]\n\n"
        finally:
            await opened.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "connection": AI_SDK_UI_STREAM_HEADERS["connection"],
            "x-vercel-ai-ui-message-stream": AI_SDK_UI_STREAM_HEADERS["x-vercel-ai-ui-message-stream"],
        },
    )


__all__ = ["SSEProjector", "router"]
