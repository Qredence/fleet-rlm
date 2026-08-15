"""Session-scoped Turn creation with preparation heartbeats inside the SSE stream."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any, Final
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

from fleet_rlm.api.dependencies import LocalScopeDep, SettingsDep, TurnCoordinatorDep
from fleet_rlm.api.schemas import CreateTurnRequest
from fleet_rlm.api.sse import AISDKUIProjector
from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.run_lifecycle import (
    RunIdempotencyMismatchError,
    RunInProgressError,
    RunLifecycleUnavailableError,
    RunNotFoundError,
)
from fleet_rlm.chat.run_preparation import (
    RunPreparationCancelledError,
    RunPreparationTimeoutError,
    RunPreparationUnavailableError,
)
from fleet_rlm.chat.turn_coordinator import OpenedTurnStream
from fleet_rlm.observability.failure_diagnostics import normalize_turn_failure
from fleet_rlm.posthog_client import get_client, get_distinct_id
from fleet_rlm.sessions.models import TurnAccess, TurnInput
from fleet_rlm.skills.errors import InvalidSkillSelectionError
from fleet_rlm.skills.models import SkillSelectionRef

router = APIRouter(prefix="/api/sessions", tags=["turns"])
logger = logging.getLogger(__name__)

# Transient client-facing pre-run heartbeat emitted until coordinator.open()
# resolves; it never enters the durable event log and may repeat.
_PREPARATION_PRELUDE_CHUNK: Final[dict[str, Any]] = {
    "type": "data-status",
    "data": {"phase": "preparation", "status": "running", "message": None},
    "transient": True,
}


def _preparation_prelude() -> ServerSentEvent:
    chunk: dict[str, Any] = dict(_PREPARATION_PRELUDE_CHUNK)
    chunk["data"] = dict(_PREPARATION_PRELUDE_CHUNK["data"])
    return ServerSentEvent(data=chunk)


def _correlation_id(request: Request) -> str:
    for header in ("x-request-id", "x-correlation-id"):
        value = request.headers.get(header)
        if value and value.strip():
            return value.strip()
    return str(uuid4())


def _log_preparation_unavailable(correlation_id: str, exc: BaseException) -> None:
    diagnostic = normalize_turn_failure(exc)
    logger.warning(
        "turn_preparation_failure correlation_id=%s cause_type=%s provider_status_category=%s message=%s",
        correlation_id,
        diagnostic.cause_type,
        diagnostic.provider_status_category,
        diagnostic.message,
    )


def _open_failure_message(exc: BaseException) -> str | None:
    """Map one open failure to its closed public message, preserving _open_turn semantics."""
    if isinstance(exc, RunNotFoundError):
        return "Session not found"
    if isinstance(exc, RunInProgressError):
        return "A Turn is already running"
    if isinstance(exc, RunIdempotencyMismatchError):
        return "Idempotency key input mismatch"
    if isinstance(exc, InvalidSkillSelectionError):
        return "Invalid Skill selection"
    if isinstance(exc, RunPreparationTimeoutError):
        return "Turn preparation timed out"
    if isinstance(exc, (RunLifecycleUnavailableError, RunPreparationUnavailableError)):
        return "Turn is unavailable"
    if isinstance(exc, ValueError):
        return "Invalid request"
    return None


def _open_failure_frames(message: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project an open failure exactly like the RunFailed terminal chunk pair."""
    return (
        {"type": "error", "errorText": message},
        {"type": "finish", "finishReason": "error"},
    )


def _command(
    session_id: UUID,
    body: CreateTurnRequest,
    identity: LocalScopeDep,
    idempotency_key: str,
) -> OpenTurnCommand:
    return OpenTurnCommand(
        TurnAccess(identity.user_id, identity.workspace_id),
        session_id,
        TurnInput(
            body.text,
            tuple(body.attachment_ids),
            tuple(SkillSelectionRef(selection.id, selection.expected_version) for selection in body.skill_selections),
        ),
        idempotency_key,
        uuid4(),
    )


async def _settle_open_after_disconnect(open_task: asyncio.Task[OpenedTurnStream]) -> OpenedTurnStream | None:
    """Wait through repeated client-disconnect cancellation until open resolves.

    ``coordinator.open`` owns claim settlement and preparation cleanup;
    cancelling it midway would strand the durable claim, so a disconnecting
    client never cancels the task. The route waits the open out, then closes
    the resulting stream the same way the transport would have.
    """
    while not open_task.done():
        try:
            await asyncio.shield(open_task)
        except asyncio.CancelledError:
            continue
    try:
        return open_task.result()
    except asyncio.CancelledError:  # pragma: no cover - only foreign task owners cancel
        return None
    except Exception:
        logger.warning("turn open failed after the client disconnected", exc_info=True)
        return None


def _stream_headers(response: Response) -> None:
    response.headers["x-vercel-ai-ui-message-stream"] = "v1"


@router.post(
    "/{session_id}/turns",
    operation_id="create_turn",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": (
                "AI SDK UI v1 UIMessage SSE stream. It opens immediately with a transient"
                " data-status prelude (phase=preparation) that repeats every runtime heartbeat"
                " until the Turn is claimed and prepared. Claim or preparation failures no"
                " longer change the HTTP status: they project closed error + finish chunks"
                " inside the stream, and cancellation projects one abort chunk."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
            "headers": {
                "Cache-Control": {"schema": {"type": "string"}},
                "X-Accel-Buffering": {"schema": {"type": "string"}},
                "x-vercel-ai-ui-message-stream": {"schema": {"type": "string"}},
            },
        },
        422: {"description": "Invalid request"},
        503: {"description": "Turn unavailable while composition installs dependencies"},
    },
)
async def create_turn(
    session_id: UUID,
    body: CreateTurnRequest,
    request: Request,
    identity: LocalScopeDep,
    coordinator: TurnCoordinatorDep,
    settings: SettingsDep,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
    _headers: Annotated[None, Depends(_stream_headers)],
) -> AsyncIterator[ServerSentEvent]:
    """Stream one Turn, opening claim and preparation inside the SSE generator."""
    ph = get_client()
    yield _preparation_prelude()
    heartbeat_seconds = float(settings.run_heartbeat_seconds)
    open_task: asyncio.Task[OpenedTurnStream] | None = None
    opened: OpenedTurnStream | None = None
    try:
        open_task = asyncio.create_task(
            coordinator.open(_command(session_id, body, identity, idempotency_key)),
            name="fleet-turn-open",
        )
        while True:
            done, _pending = await asyncio.wait({open_task}, timeout=heartbeat_seconds)
            if open_task in done:
                opened = open_task.result()
                break
            yield _preparation_prelude()
    except RunPreparationCancelledError:
        yield ServerSentEvent(data={"type": "abort", "reason": "Turn cancelled"})
        yield ServerSentEvent(raw_data="[DONE]")
        return
    except (asyncio.CancelledError, GeneratorExit):
        if open_task is not None:
            opened = await _settle_open_after_disconnect(open_task)
            if opened is not None:
                with contextlib.suppress(BaseException):
                    await opened.__anext__()
                with contextlib.suppress(BaseException):
                    await opened.aclose()
        raise
    except BaseException as exc:
        message = _open_failure_message(exc)
        if message is None:
            raise
        if message == "Turn is unavailable":
            _log_preparation_unavailable(_correlation_id(request), exc)
        if ph is not None:
            ph.capture(
                distinct_id=get_distinct_id(),
                event="turn_failed",
                properties={
                    "workspace_id": str(identity.workspace_id),
                    "session_id": str(session_id),
                    "failure_phase": "open",
                    "failure_message": message,
                },
            )
        for chunk in _open_failure_frames(message):
            yield ServerSentEvent(data=chunk)
        yield ServerSentEvent(raw_data="[DONE]")
        return

    skill_count = len(body.skill_selections)
    if ph is not None:
        ph.capture(
            distinct_id=get_distinct_id(),
            event="turn_created",
            properties={
                "workspace_id": str(identity.workspace_id),
                "session_id": str(session_id),
                "skill_count": skill_count,
                "has_attachments": len(body.attachment_ids) > 0,
                "attachment_count": len(body.attachment_ids),
            },
        )

    projector = AISDKUIProjector()
    try:
        async for event in opened:
            for chunk in projector.project(event):
                yield ServerSentEvent(data=chunk)
        yield ServerSentEvent(raw_data="[DONE]")
    except (asyncio.CancelledError, GeneratorExit):
        # Client disconnect is not a turn failure; never capture it.
        raise
    except BaseException as exc:
        if ph is not None:
            ph.capture(
                distinct_id=get_distinct_id(),
                event="turn_failed",
                properties={
                    "workspace_id": str(identity.workspace_id),
                    "session_id": str(session_id),
                    "failure_phase": "stream",
                    "failure_message": normalize_turn_failure(exc).message,
                },
            )
        raise
    finally:
        await opened.aclose()


__all__ = ["router"]
