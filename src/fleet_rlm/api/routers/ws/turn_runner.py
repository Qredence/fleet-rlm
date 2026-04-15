"""Turn execution helpers for one streaming chat turn."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.agent_host import (
    OrchestrationSessionContext,
    ReplHookBridge,
    stream_hosted_workspace_task,
)
from fleet_rlm.integrations.observability.trace_context import (
    runtime_telemetry_enabled_context,
)

from ...events import ExecutionStepBuilder
from .errors import handle_stream_error
from .lifecycle import ExecutionLifecycleManager
from .task_control import enqueue_latest_nonblocking, should_reload_docs_path
from .turn_persistence import _emit_stream_event, complete_run_if_needed
from .turn_setup import PreparedStreamingTurn
from .types import ChatAgentProtocol, LocalPersistFn
from .worker_request import build_workspace_task_request


async def run_streaming_turn(
    *,
    websocket: WebSocket,
    agent: ChatAgentProtocol,
    prepared_turn: PreparedStreamingTurn,
    orchestration_session: OrchestrationSessionContext | None,
    cancel_check: Callable[[], bool],
    interpreter: object | None,
    persist_session_state: LocalPersistFn,
) -> str | None:
    """Execute one streaming turn, emitting events and persisting lifecycle steps."""

    lifecycle = prepared_turn.lifecycle
    step_builder = prepared_turn.step_builder
    await lifecycle.emit_started()
    ws_loop = asyncio.get_running_loop()
    repl_hook_bridge = ReplHookBridge(
        ws_loop=ws_loop,
        lifecycle=lifecycle,
        step_builder=step_builder,
        interpreter=interpreter,
        enqueue_nonblocking=enqueue_latest_nonblocking,
    )

    last_loaded_docs_path = prepared_turn.last_loaded_docs_path
    if should_reload_docs_path(last_loaded_docs_path, prepared_turn.docs_path):
        agent.load_document(str(prepared_turn.docs_path))
        last_loaded_docs_path = str(prepared_turn.docs_path).strip()

    try:

        async def _stream_body() -> None:
            await _stream_agent_events(
                websocket=websocket,
                agent=agent,
                prepared_turn=prepared_turn,
                orchestration_session=orchestration_session,
                cancel_check=cancel_check,
                lifecycle=lifecycle,
                hosted_repl_bridge=repl_hook_bridge,
                step_builder=step_builder,
                analytics_enabled=prepared_turn.analytics_enabled,
                persist_session_state=persist_session_state,
            )

        await _run_prepared_stream(
            mlflow_trace_context=prepared_turn.mlflow_trace_context,
            stream_body=_stream_body,
        )
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        await handle_stream_error(
            websocket=websocket,
            lifecycle=lifecycle,
            step_builder=step_builder,
            exc=exc,
            request_message=prepared_turn.message,
        )

    return last_loaded_docs_path


async def _run_prepared_stream(
    *,
    mlflow_trace_context: Any | None,
    stream_body: Callable[[], Awaitable[None]],
) -> None:
    if mlflow_trace_context is None:
        await stream_body()
        return

    from fleet_rlm.integrations.observability.mlflow_runtime import (
        mlflow_request_context,
    )

    with mlflow_request_context(mlflow_trace_context):
        await stream_body()


async def _stream_agent_events(
    *,
    websocket: WebSocket,
    agent: ChatAgentProtocol,
    prepared_turn: PreparedStreamingTurn,
    orchestration_session: OrchestrationSessionContext | None,
    cancel_check: Callable[[], bool],
    lifecycle: ExecutionLifecycleManager,
    hosted_repl_bridge: ReplHookBridge | None,
    step_builder: ExecutionStepBuilder,
    analytics_enabled: bool | None,
    persist_session_state: LocalPersistFn,
) -> None:
    worker_request = build_workspace_task_request(
        agent=agent,
        prepared_turn=prepared_turn,
        cancel_check=cancel_check,
    )

    with runtime_telemetry_enabled_context(analytics_enabled):
        # The Agent Framework outer host owns hosted HITL plus hosted
        # continuation/session policy while still preserving the worker seam.
        async for worker_event in stream_hosted_workspace_task(
            request=worker_request,
            session=orchestration_session,
            hosted_repl_bridge=hosted_repl_bridge,
        ):
            await _emit_stream_event(
                websocket=websocket,
                lifecycle=lifecycle,
                step_builder=step_builder,
                event=worker_event,
                orchestration_session=orchestration_session,
                persist_session_state=persist_session_state,
                request_message=prepared_turn.message,
            )

    await complete_run_if_needed(lifecycle)
