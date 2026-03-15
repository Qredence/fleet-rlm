"""Chat websocket connection loop helpers."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.features.analytics import MlflowTraceRequestContext, new_client_request_id
from fleet_rlm.infrastructure.database.models import RunStatus

from ...deps import ServerState
from ...execution import ExecutionEventEmitter
from ...schemas import WSMessage

from .chat_runtime import _ChatSessionState, _PreparedChatRuntime
from .contracts import ChatAgentProtocol, LocalPersistFn
from .helpers import (
    _error_envelope,
    _get_execution_emitter,
    _sanitize_for_log,
    _try_send_json,
)
from .lifecycle import (
    PersistenceRequiredError,
    _classify_stream_failure,
)
from .message_loop import (
    parse_ws_message_or_send_error,
    resolve_session_identity,
    switch_session_if_needed,
)
from .runtime_options import normalize_daytona_chat_request
from .streaming import run_streaming_turn
from .turn import handle_command_with_persist, initialize_turn_lifecycle

logger = logging.getLogger(__name__)


def _cancelled_event_payload(message: str = "Request cancelled.") -> dict[str, Any]:
    return {
        "type": "event",
        "data": {
            "kind": "cancelled",
            "text": message,
            "payload": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": 2,
            "event_id": str(uuid.uuid4()),
        },
    }


async def _cancel_task(task: asyncio.Task[object] | None) -> None:
    """Cancel an in-flight task and swallow expected shutdown exceptions."""
    if task is None or task.done():
        return

    task.cancel()
    outcomes = await asyncio.gather(task, return_exceptions=True)
    if not outcomes:
        return

    outcome = outcomes[0]
    if isinstance(outcome, (asyncio.CancelledError, WebSocketDisconnect)):
        return
    if isinstance(outcome, BaseException):
        raise outcome


async def _process_chat_message(
    *,
    websocket: WebSocket,
    msg: WSMessage,
    agent: ChatAgentProtocol,
    interpreter: object | None,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
    runtime: _PreparedChatRuntime,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    execution_emitter: ExecutionEventEmitter,
) -> str | None:
    """Process one ``message`` payload and return the loaded docs path."""
    message = str(msg.content or "").strip()
    if not message:
        await _try_send_json(
            websocket, {"type": "error", "message": "Message content cannot be empty"}
        )
        return session.last_loaded_docs_path

    runtime_mode = msg.runtime_mode
    execution_mode = msg.execution_mode
    agent.set_execution_mode(execution_mode)
    daytona_request = normalize_daytona_chat_request(msg)

    await local_persist(include_volume_save=True, latest_user_message=message)
    session.cancel_flag["cancelled"] = False
    turn_index = agent.history_turns() + 1
    (
        session.lifecycle,
        step_builder,
        _run_id,
        session.active_run_db_id,
    ) = await initialize_turn_lifecycle(
        planner_lm=runtime.planner_lm,
        cfg=runtime.cfg,
        repository=runtime.repository,
        identity_rows=runtime.identity_rows,
        persistence_required=runtime.persistence_required,
        execution_emitter=execution_emitter,
        workspace_id=workspace_id,
        user_id=user_id,
        sess_id=sess_id,
        turn_index=turn_index,
        session_record=session.session_record,
        sandbox_provider="daytona" if daytona_request is not None else None,
    )
    trace_context = MlflowTraceRequestContext(
        client_request_id=new_client_request_id(prefix="chat"),
        session_id=f"{workspace_id}:{user_id}:{sess_id}",
        user_id=user_id,
        app_env=runtime.cfg.app_env,
        request_preview=message,
        metadata={
            "fleet_rlm.workspace_id": workspace_id,
            "fleet_rlm.turn_index": str(turn_index),
            "fleet_rlm.runtime_mode": runtime_mode,
            "fleet_rlm.execution_mode": execution_mode,
        },
    )

    def cancel_check() -> bool:
        return session.cancel_flag["cancelled"]

    return await run_streaming_turn(
        websocket=websocket,
        agent=agent,
        message=message,
        docs_path=msg.docs_path,
        trace=bool(msg.trace),
        cancel_check=cancel_check,
        lifecycle=session.lifecycle,
        step_builder=step_builder,
        interpreter=interpreter,
        last_loaded_docs_path=session.last_loaded_docs_path,
        analytics_enabled=getattr(msg, "analytics_enabled", None),
        persist_session_state=local_persist,
        mlflow_trace_context=trace_context,
        daytona_request=daytona_request,
    )


async def _chat_message_loop(
    *,
    websocket: WebSocket,
    state: ServerState,
    runtime: _PreparedChatRuntime,
    agent: ChatAgentProtocol,
    interpreter: object | None,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
    initial_message: WSMessage | None = None,
) -> None:
    execution_emitter = _get_execution_emitter(state)
    stream_task: asyncio.Task[str | None] | None = None
    pending_receive_task: asyncio.Task[object] | None = None
    pending_message = initial_message

    try:
        while True:
            if stream_task is not None:
                if pending_receive_task is None:
                    pending_receive_task = asyncio.create_task(websocket.receive_json())

                done, _pending = await asyncio.wait(
                    {stream_task, pending_receive_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if stream_task in done:
                    session.last_loaded_docs_path = await stream_task
                    stream_task = None
                    continue

                assert pending_receive_task in done
                raw_payload = await pending_receive_task
                pending_receive_task = None
                msg = await parse_ws_message_or_send_error(
                    websocket=websocket,
                    raw_payload=raw_payload,
                )
                if msg is None:
                    continue

                if msg.type == "cancel":
                    session.cancel_flag["cancelled"] = True
                    continue

                if msg.type == "command":
                    await handle_command_with_persist(
                        websocket=websocket,
                        agent=agent,
                        payload=msg.model_dump(),
                        session_record=session.session_record,
                        repository=runtime.repository,
                        identity_rows=runtime.identity_rows,
                        persistence_required=runtime.persistence_required,
                        local_persist=local_persist,
                    )
                    continue

                await _try_send_json(
                    websocket,
                    {
                        "type": "error",
                        "message": (
                            "A run is already in progress. Cancel it or wait for "
                            "completion before sending another message."
                        ),
                    },
                )
                continue

            if pending_message is not None:
                msg = pending_message
                pending_message = None
            elif pending_receive_task is not None:
                raw_payload = await pending_receive_task
                pending_receive_task = None
                msg = await parse_ws_message_or_send_error(
                    websocket=websocket,
                    raw_payload=raw_payload,
                )
            else:
                raw_payload = await websocket.receive_json()
                msg = await parse_ws_message_or_send_error(
                    websocket=websocket,
                    raw_payload=raw_payload,
                )
            if msg is None:
                continue

            if msg.type == "cancel":
                session.cancel_flag["cancelled"] = True
                await _try_send_json(websocket, _cancelled_event_payload())
                continue

            workspace_id, user_id, sess_id = resolve_session_identity(
                msg=msg,
                workspace_id=session.canonical_workspace_id,
                user_id=session.canonical_user_id,
            )
            (
                session.active_key,
                session.active_manifest_path,
                session.session_record,
                session.last_loaded_docs_path,
            ) = await switch_session_if_needed(
                state=state,
                agent=agent,
                interpreter=interpreter,
                workspace_id=workspace_id,
                user_id=user_id,
                sess_id=sess_id,
                active_key=session.active_key,
                session_record=session.session_record,
                last_loaded_docs_path=session.last_loaded_docs_path,
                local_persist=local_persist,
            )

            if msg.type == "command":
                await handle_command_with_persist(
                    websocket=websocket,
                    agent=agent,
                    payload=msg.model_dump(),
                    session_record=session.session_record,
                    repository=runtime.repository,
                    identity_rows=runtime.identity_rows,
                    persistence_required=runtime.persistence_required,
                    local_persist=local_persist,
                )
                continue

            if msg.type != "message":
                await _try_send_json(
                    websocket,
                    {"type": "error", "message": f"Unknown message type: {msg.type}"},
                )
                continue

            stream_task = asyncio.create_task(
                _process_chat_message(
                    websocket=websocket,
                    msg=msg,
                    agent=agent,
                    interpreter=interpreter,
                    session=session,
                    local_persist=local_persist,
                    runtime=runtime,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    sess_id=sess_id,
                    execution_emitter=execution_emitter,
                )
            )
    except WebSocketDisconnect:
        session.cancel_flag["cancelled"] = True
        await _cancel_task(pending_receive_task)
        await _cancel_task(stream_task)
        try:
            await local_persist(include_volume_save=True)
        except PersistenceRequiredError as exc:
            logger.warning(
                "Session persistence failed during disconnect: %s",
                _sanitize_for_log(exc),
            )
            if session.lifecycle is not None and not session.lifecycle.run_completed:
                await session.lifecycle.complete_run(
                    RunStatus.FAILED,
                    error_json={
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "code": exc.code,
                    },
                )
            return
        if session.lifecycle is not None:
            await session.lifecycle.complete_run(RunStatus.CANCELLED)
    except Exception as exc:
        await _cancel_task(pending_receive_task)
        await _cancel_task(stream_task)
        error_code = _classify_stream_failure(exc)
        await _try_send_json(
            websocket,
            _error_envelope(
                code=error_code,
                message=f"Server error: {str(exc)}",
                details={"error_type": type(exc).__name__},
            ),
        )
        try:
            await local_persist(include_volume_save=True)
        except PersistenceRequiredError as persist_exc:
            logger.warning(
                "Session persistence failed after stream error: %s",
                _sanitize_for_log(persist_exc),
            )
        if session.lifecycle is not None:
            await session.lifecycle.complete_run(
                RunStatus.FAILED,
                error_json={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "code": error_code,
                },
            )
