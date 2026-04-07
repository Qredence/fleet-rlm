"""Per-message websocket chat turn setup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from ...runtime_services.chat_persistence import initialize_turn_lifecycle
from ...events import ExecutionEventEmitter, ExecutionStepBuilder
from ...schemas import WSMessage
from .helpers import _try_send_json
from .lifecycle import ExecutionLifecycleManager
from .runtime import _ChatSessionState, _PreparedChatRuntime
from .types import (
    ChatAgentProtocol,
    LocalPersistFn,
    PreStreamSetupFn,
    normalize_daytona_chat_request,
    prepare_daytona_workspace_for_turn,
)


@dataclass(slots=True)
class PreparedStreamingTurn:
    """Normalized websocket turn setup ready for stream execution."""

    message: str
    docs_path: str | None
    trace: bool
    lifecycle: ExecutionLifecycleManager
    step_builder: ExecutionStepBuilder
    last_loaded_docs_path: str | None
    analytics_enabled: bool | None
    mlflow_trace_context: Any | None
    prepare_stream: PreStreamSetupFn


async def _reject_empty_message(
    websocket: WebSocket,
    *,
    message: str,
) -> bool:
    if message:
        return False
    await _try_send_json(
        websocket,
        {"type": "error", "message": "Message content cannot be empty"},
    )
    return True


def _build_prepare_stream(
    *,
    agent: ChatAgentProtocol,
    msg: WSMessage,
    workspace_id: str,
) -> PreStreamSetupFn:
    daytona_request = normalize_daytona_chat_request(msg, workspace_id=workspace_id)

    async def _prepare_stream() -> None:
        await prepare_daytona_workspace_for_turn(
            agent=agent,
            request=daytona_request,
            docs_path=msg.docs_path,
        )

    return _prepare_stream


async def _initialize_turn_components(
    *,
    runtime: _PreparedChatRuntime,
    session: _ChatSessionState,
    execution_emitter: ExecutionEventEmitter,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    turn_index: int,
    sandbox_provider: str | None,
) -> tuple[ExecutionLifecycleManager, ExecutionStepBuilder, Any | None, Any | None]:
    return await initialize_turn_lifecycle(
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
        sandbox_provider=sandbox_provider,
    )


def _build_trace_context(
    *,
    runtime: _PreparedChatRuntime,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    turn_index: int,
    message: str,
    execution_mode: str,
):
    from fleet_rlm.integrations.observability.mlflow_runtime import (
        MlflowTraceRequestContext,
        new_client_request_id,
    )

    return MlflowTraceRequestContext(
        client_request_id=new_client_request_id(prefix="chat"),
        session_id=f"{workspace_id}:{user_id}:{sess_id}",
        user_id=user_id,
        app_env=runtime.cfg.app_env,
        request_preview=message,
        metadata={
            "fleet_rlm.workspace_id": workspace_id,
            "fleet_rlm.turn_index": str(turn_index),
            "fleet_rlm.runtime_mode": "daytona_pilot",
            "fleet_rlm.events_mode": execution_mode,
        },
    )


async def prepare_chat_message_turn(
    *,
    websocket: WebSocket,
    msg: WSMessage,
    agent: ChatAgentProtocol,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
    runtime: _PreparedChatRuntime,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    execution_emitter: ExecutionEventEmitter,
) -> PreparedStreamingTurn | None:
    """Prepare lifecycle and trace metadata for one websocket chat message."""
    message = str(msg.content or "").strip()
    if await _reject_empty_message(websocket, message=message):
        return None

    execution_mode = msg.execution_mode
    agent.set_execution_mode(execution_mode)
    prepare_stream = _build_prepare_stream(
        agent=agent,
        msg=msg,
        workspace_id=workspace_id,
    )
    sandbox_provider = "daytona"

    await local_persist(include_volume_save=True, latest_user_message=message)
    session.cancel_flag["cancelled"] = False
    turn_index = agent.history_turns() + 1
    (
        session.lifecycle,
        step_builder,
        _run_id,
        session.active_run_db_id,
    ) = await _initialize_turn_components(
        runtime=runtime,
        session=session,
        execution_emitter=execution_emitter,
        workspace_id=workspace_id,
        user_id=user_id,
        sess_id=sess_id,
        turn_index=turn_index,
        sandbox_provider=sandbox_provider,
    )
    trace_context = _build_trace_context(
        runtime=runtime,
        workspace_id=workspace_id,
        user_id=user_id,
        sess_id=sess_id,
        turn_index=turn_index,
        message=message,
        execution_mode=execution_mode,
    )
    if session.lifecycle is None:
        raise RuntimeError(
            "Turn lifecycle initialization returned no lifecycle manager"
        )

    return PreparedStreamingTurn(
        message=message,
        docs_path=msg.docs_path,
        trace=bool(msg.trace),
        lifecycle=session.lifecycle,
        step_builder=step_builder,
        last_loaded_docs_path=session.last_loaded_docs_path,
        analytics_enabled=getattr(msg, "analytics_enabled", None),
        mlflow_trace_context=trace_context,
        prepare_stream=prepare_stream,
    )
