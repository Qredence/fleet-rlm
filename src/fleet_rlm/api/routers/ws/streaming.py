"""Inner streaming loop and REPL hook management for WebSocket chat."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.features.analytics import (
    MlflowTraceRequestContext,
    merge_trace_result_metadata,
    mlflow_request_context,
)
from fleet_rlm.features.analytics.trace_context import runtime_telemetry_enabled_context
from fleet_rlm.infrastructure.database.models import RunStatus
from fleet_rlm.core.models import StreamEvent
from ...execution import ExecutionStepBuilder
from .contracts import ChatAgentProtocol, LocalPersistFn
from .helpers import _error_envelope, _sanitize_for_log, _try_send_json
from .lifecycle import ExecutionLifecycleManager, _classify_stream_failure
from .repl_hook import ReplHookBridge
from .runtime_options import DaytonaChatRequestOptions

logger = logging.getLogger(__name__)


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_text(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def _canonical_run_status(kind: str) -> str:
    if kind == "final":
        return "completed"
    if kind == "cancelled":
        return "cancelled"
    return "error"


def _build_execution_completion_summary(
    *,
    event: StreamEvent,
    request_message: str,
    run_id: str,
) -> dict[str, Any]:
    payload = _as_record(event.payload)
    runtime = _as_record(payload.get("runtime"))
    run_result = _as_record(payload.get("run_result"))
    summary_payload = _as_record(payload.get("summary"))
    runtime_mode = (
        _as_text(payload.get("runtime_mode"))
        or _as_text(runtime.get("runtime_mode"))
        or _as_text(run_result.get("runtime_mode"))
        or "modal_chat"
    )
    terminal_status = _canonical_run_status(event.kind)
    warnings = list(
        summary_payload.get("warnings") or payload.get("guardrail_warnings") or []
    )

    if run_result:
        normalized = dict(run_result)
        normalized.setdefault(
            "run_id", run_result.get("run_id") or runtime.get("run_id") or run_id
        )
        normalized.setdefault("runtime_mode", runtime_mode)
        normalized.setdefault("task", run_result.get("task") or request_message)
        normalized.setdefault("status", terminal_status)
        normalized.setdefault(
            "termination_reason",
            summary_payload.get("termination_reason") or event.kind,
        )
        normalized.setdefault("duration_ms", summary_payload.get("duration_ms"))
        normalized.setdefault("warnings", warnings)
        if summary_payload:
            nested_summary = dict(summary_payload)
            if warnings and "warnings" not in nested_summary:
                nested_summary["warnings"] = warnings
            normalized["summary"] = nested_summary
        return normalized

    error_text = event.text if event.kind == "error" else None
    final_artifact = None
    if event.kind == "final":
        final_artifact = {
            "kind": "assistant_response",
            "value": {
                "text": event.text,
                "final_markdown": event.text,
                "summary": event.text,
            },
            "finalization_mode": "RETURN",
        }

    return {
        "run_id": _as_text(runtime.get("run_id")) or run_id,
        "runtime_mode": runtime_mode,
        "task": request_message,
        "status": terminal_status,
        "termination_reason": summary_payload.get("termination_reason") or event.kind,
        "duration_ms": summary_payload.get("duration_ms"),
        "iterations": [],
        "callbacks": [],
        "prompts": [],
        "context_sources": [],
        "sources": list(payload.get("sources") or []),
        "attachments": list(payload.get("attachments") or []),
        "final_artifact": final_artifact,
        "summary": {
            "termination_reason": summary_payload.get("termination_reason")
            or event.kind,
            "duration_ms": summary_payload.get("duration_ms"),
            "warnings": warnings,
            "error": error_text,
        },
        "warnings": warnings,
    }


async def run_streaming_turn(
    *,
    websocket: WebSocket,
    agent: ChatAgentProtocol,
    message: str,
    docs_path: str | None,
    trace: bool,
    cancel_check: Callable[[], bool],
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    interpreter: object | None,
    last_loaded_docs_path: str | None,
    analytics_enabled: bool | None,
    persist_session_state: LocalPersistFn,
    mlflow_trace_context: MlflowTraceRequestContext | None = None,
    daytona_request: DaytonaChatRequestOptions | None = None,
) -> str | None:
    """Execute one streaming turn, emitting events and persisting lifecycle steps."""

    await lifecycle.emit_started()
    ws_loop = asyncio.get_running_loop()
    repl_hook_bridge = ReplHookBridge(
        ws_loop=ws_loop,
        lifecycle=lifecycle,
        step_builder=step_builder,
        interpreter=interpreter,
        enqueue_nonblocking=_enqueue_latest_nonblocking,
    )
    await repl_hook_bridge.start()

    if _should_reload_docs_path(last_loaded_docs_path, docs_path):
        agent.load_document(str(docs_path))
        last_loaded_docs_path = str(docs_path).strip()

    try:
        if mlflow_trace_context is None:
            await _stream_agent_events(
                websocket=websocket,
                agent=agent,
                request_message=message,
                message=message,
                docs_path=docs_path,
                trace=trace,
                cancel_check=cancel_check,
                lifecycle=lifecycle,
                step_builder=step_builder,
                analytics_enabled=analytics_enabled,
                persist_session_state=persist_session_state,
                daytona_request=daytona_request,
            )
        else:
            with mlflow_request_context(mlflow_trace_context):
                await _stream_agent_events(
                    websocket=websocket,
                    agent=agent,
                    request_message=message,
                    message=message,
                    docs_path=docs_path,
                    trace=trace,
                    cancel_check=cancel_check,
                    lifecycle=lifecycle,
                    step_builder=step_builder,
                    analytics_enabled=analytics_enabled,
                    persist_session_state=persist_session_state,
                    daytona_request=daytona_request,
                )
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        await _handle_stream_error(
            websocket=websocket,
            lifecycle=lifecycle,
            step_builder=step_builder,
            exc=exc,
            request_message=message,
        )
    finally:
        await repl_hook_bridge.stop()

    return last_loaded_docs_path


async def _stream_agent_events(
    *,
    websocket: WebSocket,
    agent: ChatAgentProtocol,
    request_message: str,
    message: str,
    docs_path: str | None,
    trace: bool,
    cancel_check: Callable[[], bool],
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    analytics_enabled: bool | None,
    persist_session_state: LocalPersistFn,
    daytona_request: DaytonaChatRequestOptions | None = None,
) -> None:
    with runtime_telemetry_enabled_context(analytics_enabled):
        if daytona_request is None:
            event_stream = agent.aiter_chat_turn_stream(
                message=message,
                trace=trace,
                cancel_check=cancel_check,
                docs_path=docs_path,
            )
        else:
            workspace_volume_name = daytona_request.workspace_id
            event_stream = agent.aiter_chat_turn_stream(
                message=message,
                trace=trace,
                cancel_check=cancel_check,
                docs_path=docs_path,
                repo_url=daytona_request.repo_url,
                repo_ref=daytona_request.repo_ref,
                context_paths=daytona_request.context_paths,
                batch_concurrency=daytona_request.batch_concurrency,
                volume_name=workspace_volume_name,
            )

        async for event in event_stream:
            await _emit_stream_event(
                websocket=websocket,
                lifecycle=lifecycle,
                step_builder=step_builder,
                event=event,
                persist_session_state=persist_session_state,
                request_message=request_message,
            )

    if not lifecycle.run_completed:
        lifecycle.raise_if_persistence_error()
        await lifecycle.complete_run(RunStatus.COMPLETED)


async def _emit_stream_event(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    event: StreamEvent,
    persist_session_state: LocalPersistFn,
    request_message: str,
) -> None:
    lifecycle.raise_if_persistence_error()
    payload = event.payload
    if event.kind == "final":
        payload = merge_trace_result_metadata(
            payload if isinstance(payload, dict) else None,
            response_preview=event.text,
        )
    event_dict = {
        "kind": event.kind,
        "text": event.text,
        "payload": payload,
        "timestamp": event.timestamp.isoformat(),
        "version": 2,
        "event_id": str(uuid.uuid4()),
    }
    is_terminal_event = event.kind in {"final", "cancelled", "error"}
    if not is_terminal_event:
        if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
            raise WebSocketDisconnect(code=1001)

    step = step_builder.from_stream_event(
        kind=event.kind,
        text=event.text,
        payload=payload,
        timestamp=event.timestamp.timestamp(),
    )
    if step is not None:
        await lifecycle.emit_step(step)
        await lifecycle.persist_step(step)
        lifecycle.raise_if_persistence_error()

    if event.kind == "final":
        await persist_session_state(include_volume_save=True)
        await lifecycle.complete_run(
            RunStatus.COMPLETED,
            step=step,
            summary=_build_execution_completion_summary(
                event=event,
                request_message=request_message,
                run_id=lifecycle.run_id,
            ),
        )
        if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
            raise WebSocketDisconnect(code=1001)
        return

    if event.kind in {"cancelled", "error"}:
        # Surface terminal Daytona/chat failures to the client before any
        # slower lifecycle bookkeeping so the UI does not sit in a spinner
        # when persistence or completion hooks stall.
        if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
            raise WebSocketDisconnect(code=1001)
        try:
            await persist_session_state(include_volume_save=True)
        except Exception:
            # Log and continue to ensure the run is still marked as completed/failed.
            logger.exception(
                "Failed to persist session state after %s event; completing run anyway",
                event.kind,
            )
        status = RunStatus.CANCELLED if event.kind == "cancelled" else RunStatus.FAILED
        error_json = (
            {"error": event.text, "kind": event.kind} if event.kind == "error" else None
        )
        await lifecycle.complete_run(
            status,
            step=step,
            error_json=error_json,
            summary=_build_execution_completion_summary(
                event=event,
                request_message=request_message,
                run_id=lifecycle.run_id,
            ),
        )


async def _handle_stream_error(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    exc: Exception,
    request_message: str,
) -> None:
    error_code = _classify_stream_failure(exc)
    logger.error(
        "Streaming error: %s",
        _sanitize_for_log(exc),
        exc_info=True,
        extra={
            "error_type": type(exc).__name__,
            "error_code": error_code,
        },
    )
    await _try_send_json(
        websocket,
        _error_envelope(
            code=error_code,
            message=f"Streaming error: {exc}",
            details={"error_type": type(exc).__name__},
        ),
    )
    if lifecycle.run_completed:
        return

    error_step = step_builder.from_stream_event(
        kind="error",
        text=f"Streaming error: {exc}",
        payload={
            "error_type": type(exc).__name__,
            "error_code": error_code,
        },
        timestamp=time.time(),
    )
    if error_step is not None:
        await lifecycle.emit_step(error_step)
    await lifecycle.complete_run(
        RunStatus.FAILED,
        step=error_step,
        error_json={
            "error": str(exc),
            "error_type": type(exc).__name__,
            "code": error_code,
        },
        summary=_build_execution_completion_summary(
            event=StreamEvent(
                kind="error",
                text=f"Streaming error: {exc}",
                payload={
                    "error_type": type(exc).__name__,
                    "error_code": error_code,
                },
            ),
            request_message=request_message,
            run_id=lifecycle.run_id,
        ),
    )


def _should_reload_docs_path(last_docs_path: str | None, docs_path: str | None) -> bool:
    """Return True when a docs path is provided and differs from the last loaded path."""
    candidate = (docs_path or "").strip()
    if not candidate:
        return False
    return candidate != (last_docs_path or "")


def _enqueue_latest_nonblocking(
    queue: asyncio.Queue[Any],
    item: Any,
) -> bool:
    """Enqueue without blocking, dropping the oldest item when the queue is full."""
    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        pass

    try:
        _ = queue.get_nowait()
    except asyncio.QueueEmpty:
        return False

    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        return False
