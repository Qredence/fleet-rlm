"""Chat websocket connection loop helpers."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.db.models import RunStatus

from .chat_runtime import _ChatSessionState, _PreparedChatRuntime
from .helpers import (
    _error_envelope,
    _get_execution_emitter,
    _sanitize_for_log,
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
from .streaming import run_streaming_turn
from .turn import handle_command_with_persist, initialize_turn_lifecycle

logger = logging.getLogger(__name__)


async def _process_chat_message(
    *,
    websocket: WebSocket,
    msg: Any,
    agent: Any,
    interpreter: Any,
    session: _ChatSessionState,
    local_persist: Any,
    runtime: _PreparedChatRuntime,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    execution_emitter: Any,
) -> str | None:
    """Process one ``message`` payload and return the loaded docs path."""
    message = str(msg.content or "").strip()
    if not message:
        await websocket.send_json(
            {"type": "error", "message": "Message content cannot be empty"}
        )
        return session.last_loaded_docs_path

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
    )


async def _chat_message_loop(
    *,
    websocket: WebSocket,
    state: Any,
    runtime: _PreparedChatRuntime,
    agent: Any,
    interpreter: Any,
    session: _ChatSessionState,
    local_persist: Any,
) -> None:
    execution_emitter = _get_execution_emitter(state)

    try:
        while True:
            raw_payload = await websocket.receive_json()
            msg = await parse_ws_message_or_send_error(
                websocket=websocket,
                raw_payload=raw_payload,
            )
            if msg is None:
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

            if msg.type != "message":
                await websocket.send_json(
                    {"type": "error", "message": f"Unknown message type: {msg.type}"}
                )
                continue

            session.last_loaded_docs_path = await _process_chat_message(
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
    except WebSocketDisconnect:
        session.cancel_flag["cancelled"] = True
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
        error_code = _classify_stream_failure(exc)
        await websocket.send_json(
            _error_envelope(
                code=error_code,
                message=f"Server error: {str(exc)}",
                details={"error_type": type(exc).__name__},
            )
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
