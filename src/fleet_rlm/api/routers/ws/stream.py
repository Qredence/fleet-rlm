"""Inner streaming loop and REPL hook management for WebSocket chat."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.integrations.observability.mlflow_context import (
    merge_trace_result_metadata as _merge_trace_result_metadata,
)
from fleet_rlm.integrations.observability.trace_context import (
    runtime_telemetry_enabled_context,
)
from fleet_rlm.runtime.execution.streaming_events import is_terminal_stream_event_kind
from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log

from ...dependencies import DiagnosticsDeps, SessionCacheDeps
from ...events import (
    ExecutionEventEmitter,
    ExecutionStep,
    ExecutionStepBuilder,
    ExecutionSubscription,
)
from ...events.event_adapter import (
    adapt_stream_event,
    build_chat_event_payload,
    is_terminal_backend_event,
)
from ...runtime_services.chat_persistence import (
    ExecutionLifecycleManager,
    build_local_persist_fn,
    build_startup_status_event,
    build_workspace_task_request,
    classify_stream_failure,
    enqueue_latest_nonblocking,
    get_execution_emitter,
    handle_chat_disconnect,
    should_reload_docs_path,
)
from ...runtime_services.chat_runtime import (
    ChatAgentProtocol,
    LocalPersistFn,
    SessionContext,
    StreamEventLike,
    build_chat_agent_context,
    set_interpreter_default_profile,
)
from ...runtime_services.chat_runtime import (
    ChatSessionState as _ChatSessionState,
)
from ...runtime_services.chat_runtime import (
    PreparedChatRuntime as _PreparedChatRuntime,
)
from ...schemas import WSMessage
from .commands import handle_command_with_persist
from .repl_bridge import ReplHookBridge
from .session import (
    switch_session_if_needed,
)
from .transport import (
    _error_envelope,
    _try_send_json,
    handle_chat_loop_exception,
    parse_ws_message_or_send_error,
    resolve_session_identity,
)
from .turn_setup import PreparedStreamingTurn, prepare_chat_message_turn

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkspaceEvent:
    """Normalized event shape for websocket streaming."""

    kind: str
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    terminal: bool = False


@dataclass(slots=True)
class WorkspaceTaskRequest:
    """Input needed to execute one workspace task end-to-end."""

    agent: Any
    message: str
    execution_mode: str | None = None
    trace: bool = True
    docs_path: str | None = None
    repo_url: str | None = None
    repo_ref: str | None = None
    context_paths: list[str] | None = None
    batch_concurrency: int | None = None
    workspace_id: str | None = None
    cancel_check: Callable[[], bool] | None = None
    prepare: Callable[[], Awaitable[None]] | None = None


def merge_trace_result_metadata(
    payload: dict[str, Any] | None,
    *,
    response_preview: str | None = None,
    trace_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility shim for MLflow final-event metadata enrichment."""
    return _merge_trace_result_metadata(
        payload,
        response_preview=response_preview,
        trace_metadata=trace_metadata,
    )


def _runtime_trace_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    runtime_payload = payload.get("runtime")
    runtime = runtime_payload if isinstance(runtime_payload, dict) else {}

    metadata: dict[str, Any] = {}
    for key in (
        "runtime_degraded",
        "runtime_failure_category",
        "runtime_failure_phase",
        "runtime_fallback_used",
    ):
        value = payload.get(key, runtime.get(key))
        if value in (None, "", False):
            if key in {"runtime_degraded", "runtime_fallback_used"} and value is False:
                metadata[key] = False
            continue
        metadata[key] = value
    return metadata


def build_stream_event_dict(
    *,
    event: StreamEventLike,
    payload: Any,
) -> dict[str, Any]:
    """Serialize one stream event for websocket delivery."""
    backend_event = adapt_stream_event(
        kind=event.kind,
        text=event.text,
        payload=payload if isinstance(payload, dict) else None,
        timestamp=event.timestamp,
    )
    event_dict = build_chat_event_payload(backend_event)
    event_dict.setdefault("event_id", uuid.uuid4().hex)
    return event_dict


def _terminal_run_status(event: StreamEventLike) -> RunStatus:
    """Return the authoritative terminal run status for one event."""
    if event.kind == "done" and (isinstance(event.payload, dict) and event.payload.get("cancelled")):
        return RunStatus.CANCELLED
    if event.kind == "done":
        payload = event.payload if isinstance(event.payload, dict) else {}
        return RunStatus.FAILED if final_event_failed(payload) else RunStatus.COMPLETED
    return RunStatus.FAILED


async def handle_terminal_stream_event(
    *,
    websocket: WebSocket | None,
    lifecycle: ExecutionLifecycleManager,
    event: StreamEventLike,
    event_dict: dict[str, Any],
    step: ExecutionStep | None,
    persist_session_state: LocalPersistFn,
    request_message: str,
    orchestration_session: SessionContext | None = None,
) -> None:
    """Handle terminal websocket events: persist, complete lifecycle, send.

    ``orchestration_session`` is retained for API compatibility but the
    simplified architecture has no HITL/checkpoint logic.
    """
    summary = build_execution_completion_summary(
        event=event,
        request_message=request_message,
        run_id=lifecycle.run_id,
    )

    if event.kind == "done":
        try:
            await persist_session_state(include_volume_save=True, release_idle_session=True)
        except Exception:
            logger.debug(
                "Failed to persist session state before final event; continuing",
                exc_info=True,
            )
        await lifecycle.complete_run(
            _terminal_run_status(event),
            step=step,
            summary=summary,
        )
        return

    try:
        await persist_session_state(include_volume_save=True, release_idle_session=True)
    except Exception:
        logger.debug(
            "Failed to persist session state after %s event; completing run anyway",
            event.kind,
            exc_info=True,
        )

    error_json: dict[str, Any] | None = {"error": event.text, "kind": event.kind} if event.kind == "error" else None
    await lifecycle.complete_run(
        _terminal_run_status(event),
        step=step,
        error_json=error_json,
        summary=summary,
    )


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_text(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def _normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_as_text(entry) for entry in value) if item is not None]


def final_event_failed(payload: dict[str, Any]) -> bool:
    runtime = _as_record(payload.get("runtime"))
    runtime_degraded = bool(payload.get("runtime_degraded", runtime.get("runtime_degraded", False)))
    category = _as_text(payload.get("runtime_failure_category") or runtime.get("runtime_failure_category"))
    return runtime_degraded and category == "tool_execution_error"


def _extract_human_review_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = _as_record(payload.get("human_review"))
    if raw:
        required = raw.get("required")
        if required is False:
            return None
        return {
            "required": True,
            "reason": _as_text(raw.get("reason")) or "Recursive repair requested human review before continuing.",
            "repair_mode": _as_text(raw.get("repair_mode")),
            "repair_target": _as_text(raw.get("repair_target")),
            "repair_steps": _normalize_text_list(raw.get("repair_steps")),
        }

    recursive_repair = _as_record(payload.get("recursive_repair"))
    if _as_text(recursive_repair.get("repair_mode")) != "needs_human_review":
        return None

    normalized_steps = _normalize_text_list(recursive_repair.get("repair_steps"))
    return {
        "required": True,
        "reason": _as_text(payload.get("final_reasoning"))
        or _as_text(recursive_repair.get("repair_rationale"))
        or _as_text(recursive_repair.get("repair_target"))
        or "Recursive repair requested human review before continuing.",
        "repair_mode": "needs_human_review",
        "repair_target": _as_text(recursive_repair.get("repair_target")),
        "repair_steps": normalized_steps,
    }


def _canonical_run_status(
    kind: str,
    payload: dict[str, Any],
    *,
    human_review_required: bool,
) -> str:
    if kind == "done":
        # A "done" event with payload["cancelled"]=True is a cancelled turn.
        if isinstance(payload, dict) and payload.get("cancelled"):
            return "cancelled"
        if human_review_required:
            return "needs_human_review"
        return "error" if final_event_failed(payload) else "completed"
    return "error"


def _build_fallback_final_artifact(event: StreamEventLike) -> dict[str, Any] | None:
    if event.kind != "done":
        return None
    return {
        "kind": "assistant_response",
        "value": {
            "text": event.text,
            "final_markdown": event.text,
            "summary": event.text,
        },
        "finalization_mode": "RETURN",
    }


def _build_minimum_summary(
    *,
    event: StreamEventLike,
    summary_payload: dict[str, Any],
    warnings: list[Any],
    human_review: dict[str, Any] | None,
    termination_reason: str,
) -> dict[str, Any]:
    error_text = event.text if event.kind == "error" else None
    summary = {
        "termination_reason": termination_reason,
        "duration_ms": summary_payload.get("duration_ms"),
        "warnings": warnings,
        "error": error_text,
    }
    if human_review is not None:
        summary["human_review"] = human_review
    return summary


def _resolve_terminal_status(
    *,
    existing_status: Any,
    terminal_status: str,
) -> str:
    normalized = _as_text(existing_status)
    if terminal_status in {"needs_human_review", "error", "cancelled"}:
        return terminal_status
    return normalized or terminal_status


def _resolve_termination_reason(
    *,
    existing_reason: Any,
    event_kind: str,
    human_review_required: bool,
) -> str:
    normalized = _as_text(existing_reason)
    if human_review_required and normalized in {None, "", "done", "completed"}:
        return "needs_human_review"
    return normalized or event_kind


def build_execution_completion_summary(
    *,
    event: StreamEventLike,
    request_message: str,
    run_id: str,
) -> dict[str, Any]:
    """Build the canonical execution summary payload from a terminal event."""
    payload = _as_record(event.payload)
    runtime = _as_record(payload.get("runtime"))
    run_result = _as_record(payload.get("run_result"))
    summary_payload = _as_record(payload.get("summary"))
    payload_final_artifact = _as_record(payload.get("final_artifact"))
    human_review = _extract_human_review_payload(payload)
    runtime_mode = (
        _as_text(payload.get("runtime_mode"))
        or _as_text(runtime.get("runtime_mode"))
        or _as_text(run_result.get("runtime_mode"))
        or "daytona_pilot"
    )
    terminal_status = _canonical_run_status(
        event.kind,
        payload,
        human_review_required=human_review is not None,
    )
    resolved_termination_reason = _resolve_termination_reason(
        existing_reason=run_result.get("termination_reason") or summary_payload.get("termination_reason"),
        event_kind=event.kind,
        human_review_required=human_review is not None,
    )
    warnings = list(summary_payload.get("warnings") or payload.get("guardrail_warnings") or [])
    minimum_summary = _build_minimum_summary(
        event=event,
        summary_payload=summary_payload,
        warnings=warnings,
        human_review=human_review,
        termination_reason=resolved_termination_reason,
    )

    if run_result:
        normalized = dict(run_result)
        normalized.setdefault("run_id", run_result.get("run_id") or runtime.get("run_id") or run_id)
        normalized.setdefault("runtime_mode", runtime_mode)
        normalized.setdefault("task", run_result.get("task") or request_message)
        normalized["status"] = _resolve_terminal_status(
            existing_status=run_result.get("status"),
            terminal_status=terminal_status,
        )
        normalized["termination_reason"] = resolved_termination_reason
        normalized.setdefault("duration_ms", summary_payload.get("duration_ms"))
        normalized.setdefault("warnings", warnings)
        nested_summary = _as_record(normalized.get("summary"))
        nested_summary = {**minimum_summary, **nested_summary}
        if summary_payload:
            nested_summary = {**nested_summary, **summary_payload}
        nested_summary["termination_reason"] = resolved_termination_reason
        if warnings and not nested_summary.get("warnings"):
            nested_summary["warnings"] = warnings
        if human_review is not None:
            normalized["human_review"] = human_review
            nested_summary["human_review"] = human_review
        normalized["summary"] = nested_summary
        normalized.setdefault(
            "final_artifact",
            payload_final_artifact or _build_fallback_final_artifact(event),
        )
        return normalized

    final_artifact = payload_final_artifact or _build_fallback_final_artifact(event)

    return {
        "run_id": _as_text(runtime.get("run_id")) or run_id,
        "runtime_mode": runtime_mode,
        "task": request_message,
        "status": terminal_status,
        "termination_reason": resolved_termination_reason,
        "duration_ms": summary_payload.get("duration_ms"),
        "iterations": [],
        "callbacks": [],
        "prompts": [],
        "context_sources": [],
        "sources": list(payload.get("sources") or []),
        "attachments": list(payload.get("attachments") or []),
        "final_artifact": final_artifact,
        "summary": minimum_summary,
        "warnings": warnings,
        **({"human_review": human_review} if human_review is not None else {}),
    }


async def handle_stream_error(
    *,
    websocket: WebSocket | None,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    exc: Exception,
    request_message: str,
) -> None:
    """Log, emit, and persist a failed websocket streaming turn."""
    error_code = classify_stream_failure(exc)
    logger.error(
        "Streaming error: %s",
        _sanitize_for_log(exc),
        exc_info=True,
        extra={
            "error_type": type(exc).__name__,
            "error_code": error_code,
        },
    )
    if websocket is not None:
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

    error_text = f"Streaming error: {exc}"
    error_payload = {
        "error_type": type(exc).__name__,
        "error_code": error_code,
    }
    error_step = step_builder.from_stream_event(
        kind="error",
        text=error_text,
        payload=error_payload,
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
        summary=build_execution_completion_summary(
            event=WorkspaceEvent(
                kind="error",
                text=error_text,
                payload=error_payload,
                terminal=True,
            ),
            request_message=request_message,
            run_id=lifecycle.run_id,
        ),
    )


def _is_terminal_transport_event(event: StreamEventLike) -> bool:
    """Return websocket-terminal semantics for worker and legacy runtime events."""

    backend_event = adapt_stream_event(
        kind=event.kind,
        text=event.text,
        payload=event.payload if isinstance(event.payload, dict) else None,
        timestamp=event.timestamp,
    )
    return bool(getattr(event, "terminal", False)) or is_terminal_backend_event(backend_event)


def _build_agent_stream_kwargs(request: WorkspaceTaskRequest) -> dict[str, Any]:
    """Build canonical runtime stream kwargs from a workspace task request."""
    kwargs: dict[str, Any] = {
        "message": request.message,
        "trace": request.trace,
        "cancel_check": request.cancel_check,
        "docs_path": request.docs_path,
    }
    if request.repo_url is not None:
        kwargs["repo_url"] = request.repo_url
    if request.repo_ref is not None:
        kwargs["repo_ref"] = request.repo_ref
    if request.context_paths is not None:
        kwargs["context_paths"] = list(request.context_paths)
    if request.batch_concurrency is not None:
        kwargs["batch_concurrency"] = request.batch_concurrency
    if request.workspace_id is not None:
        kwargs["volume_name"] = request.workspace_id
    return kwargs


def _to_workspace_event(event: Any) -> WorkspaceEvent:
    """Normalize a runtime-style stream event into a workspace event."""
    raw_ts = getattr(event, "timestamp", None)
    timestamp = raw_ts if isinstance(raw_ts, datetime) else datetime.now(timezone.utc)
    return WorkspaceEvent(
        kind=str(getattr(event, "kind", "status")),
        text=str(getattr(event, "text", "") or ""),
        payload=dict(getattr(event, "payload", {}) or {}),
        timestamp=timestamp,
        terminal=is_terminal_stream_event_kind(str(getattr(event, "kind", ""))),
    )


async def stream_agent_turn(
    request: WorkspaceTaskRequest,
) -> AsyncIterator[WorkspaceEvent]:
    """Stream one workspace task directly through the agent without HITL wrapper."""
    if request.execution_mode is not None:
        request.agent.set_execution_mode(request.execution_mode)
    if request.prepare is not None:
        await request.prepare()
    async for runtime_event in request.agent.aiter_chat_turn_stream(**_build_agent_stream_kwargs(request)):
        yield _to_workspace_event(runtime_event)


async def run_streaming_turn(
    *,
    websocket: WebSocket | None,
    agent: ChatAgentProtocol,
    prepared_turn: PreparedStreamingTurn,
    orchestration_session: SessionContext | None,
    cancel_check: Callable[[], bool],
    interpreter: object | None,
    persist_session_state: LocalPersistFn,
    execution_emitter: ExecutionEventEmitter,
) -> str | None:
    """Execute one streaming turn, emitting events and persisting lifecycle steps."""

    lifecycle = prepared_turn.lifecycle
    step_builder = prepared_turn.step_builder
    if interpreter is not None and hasattr(lifecycle, "active_run_db_id"):
        # interpreter is typed as `object | None` here; use setattr so ty
        # accepts the dynamic attribute (declared on DaytonaInterpreter).
        setattr(interpreter, "_host_run_id", lifecycle.active_run_db_id)
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
                execution_emitter=execution_emitter,
            )

        await _run_prepared_stream(
            mlflow_trace_context=prepared_turn.mlflow_trace_context,
            stream_body=_stream_body,
        )
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        try:
            await persist_session_state(
                include_volume_save=True,
                allow_volume_session_create=False,
                release_idle_session=True,
            )
        except Exception:
            logger.debug("Failed to persist session state after stream exception", exc_info=True)
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
    websocket: WebSocket | None,
    agent: ChatAgentProtocol,
    prepared_turn: PreparedStreamingTurn,
    orchestration_session: SessionContext | None,
    cancel_check: Callable[[], bool],
    lifecycle: ExecutionLifecycleManager,
    hosted_repl_bridge: ReplHookBridge | None,
    step_builder: ExecutionStepBuilder,
    analytics_enabled: bool | None,
    persist_session_state: LocalPersistFn,
    execution_emitter: ExecutionEventEmitter,
) -> None:
    worker_request = build_workspace_task_request(
        agent=agent,
        prepared_turn=prepared_turn,
        cancel_check=cancel_check,
    )

    bridge_started = False
    try:
        if hosted_repl_bridge is not None:
            hosted_repl_bridge.start()
            bridge_started = True

        with runtime_telemetry_enabled_context(analytics_enabled):
            async for worker_event in stream_agent_turn(worker_request):
                await _emit_stream_event(
                    websocket=websocket,
                    lifecycle=lifecycle,
                    step_builder=step_builder,
                    event=worker_event,
                    orchestration_session=orchestration_session,
                    persist_session_state=persist_session_state,
                    request_message=prepared_turn.message,
                    execution_emitter=execution_emitter,
                )
    finally:
        if hosted_repl_bridge is not None and bridge_started:
            try:
                await hosted_repl_bridge.stop()
            except Exception:
                pass

    if not lifecycle.run_completed:
        lifecycle.raise_if_persistence_error()
        await lifecycle.complete_run(RunStatus.COMPLETED)


async def _emit_stream_event(
    *,
    websocket: WebSocket | None,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    event: WorkspaceEvent | StreamEventLike,
    orchestration_session: SessionContext | None = None,
    persist_session_state: LocalPersistFn,
    request_message: str,
    execution_emitter: ExecutionEventEmitter,
) -> None:
    lifecycle.raise_if_persistence_error()
    payload = event.payload
    if event.kind == "done":
        payload = merge_trace_result_metadata(
            payload if isinstance(payload, dict) else None,
            response_preview=event.text,
            trace_metadata=_runtime_trace_metadata(payload if isinstance(payload, dict) else None),
        )
    event_dict = build_stream_event_dict(event=event, payload=payload)
    is_terminal_event = _is_terminal_transport_event(event)

    # We NO LONGER send raw event_dicts via the websocket directly.
    # Instead, we rely entirely on the ExecutionEventEmitter (via lifecycle)
    # which emits typed ExecutionEvent payloads.

    event_timestamp = event.timestamp.timestamp()
    step = step_builder.from_stream_event(
        kind=event.kind,
        text=event.text,
        payload=payload,
        timestamp=event_timestamp,
    )
    if step is not None:
        if event.kind == "text":
            await lifecycle.emit_step(step)
        else:
            await asyncio.gather(
                lifecycle.emit_step(step),
                lifecycle.persist_step(step),
            )
        lifecycle.raise_if_persistence_error()

    if is_terminal_event:
        await handle_terminal_stream_event(
            websocket=websocket,
            lifecycle=lifecycle,
            event=event,
            event_dict=event_dict,
            step=step,
            orchestration_session=orchestration_session,
            persist_session_state=persist_session_state,
            request_message=request_message,
        )


async def _process_chat_message(
    *,
    websocket: WebSocket | None,
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
    prepared_turn = await prepare_chat_message_turn(
        websocket=websocket,
        msg=msg,
        agent=agent,
        session=session,
        local_persist=local_persist,
        runtime=runtime,
        workspace_id=workspace_id,
        user_id=user_id,
        sess_id=sess_id,
        execution_emitter=execution_emitter,
    )
    if prepared_turn is None:
        return session.last_loaded_docs_path

    def cancel_check() -> bool:
        return session.cancel_flag["cancelled"]

    orchestration_session = session.orchestration_session or SessionContext(
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=sess_id,
        session_record=session.session_record,
    )
    session.orchestration_session = orchestration_session

    return await run_streaming_turn(
        websocket=websocket,
        agent=agent,
        prepared_turn=prepared_turn,
        orchestration_session=orchestration_session,
        cancel_check=cancel_check,
        interpreter=interpreter,
        persist_session_state=local_persist,
        execution_emitter=execution_emitter,
    )


def _ensure_pending_receive_task(
    *,
    websocket: WebSocket,
    pending_receive_task: asyncio.Task[object] | None,
) -> asyncio.Task[object]:
    if pending_receive_task is not None:
        return pending_receive_task
    return asyncio.create_task(websocket.receive_json())


async def _await_message_while_streaming(
    *,
    websocket: WebSocket,
    stream_task: asyncio.Task[str | None],
    pending_receive_task: asyncio.Task[object] | None,
    session: _ChatSessionState,
) -> tuple[WSMessage | None, asyncio.Task[str | None] | None, asyncio.Task[object] | None]:
    pending_receive_task = _ensure_pending_receive_task(
        websocket=websocket,
        pending_receive_task=pending_receive_task,
    )
    done, _pending = await asyncio.wait(
        {stream_task, pending_receive_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if stream_task in done:
        session.last_loaded_docs_path = await stream_task
        return None, None, pending_receive_task

    raw_payload = await pending_receive_task
    msg = await parse_ws_message_or_send_error(
        websocket=websocket,
        raw_payload=raw_payload,
    )
    return msg, stream_task, None


async def _background_execution_task(
    *,
    msg: WSMessage,
    session_cache: SessionCacheDeps,
    runtime: _PreparedChatRuntime,
    session: _ChatSessionState,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    execution_emitter: ExecutionEventEmitter,
) -> str | None:
    """Run execution in the background with its own agent context."""
    agent_context = await build_chat_agent_context(runtime)
    async with agent_context as agent:
        interpreter = getattr(agent, "interpreter", None)
        set_interpreter_default_profile(interpreter, runtime.cfg)

        async def _noop_persist(
            *,
            include_volume_save: bool = True,
            latest_user_message: str = "",
        ) -> None:
            _ = include_volume_save, latest_user_message

        (
            session.active_key,
            session.active_manifest_path,
            session.session_record,
            session.last_loaded_docs_path,
            session.orchestration_session,
        ) = await switch_session_if_needed(
            session_cache=session_cache,
            agent=agent,
            interpreter=interpreter,
            workspace_id=workspace_id,
            user_id=user_id,
            sess_id=sess_id,
            owner_tenant_claim=session.owner_tenant_claim,
            owner_user_claim=session.owner_user_claim,
            active_key=None,
            session_record=session.session_record,
            last_loaded_docs_path=session.last_loaded_docs_path,
            local_persist=_noop_persist,
            persistence=runtime.persistence,
            identity_rows=runtime.identity_rows,
        )

        agent._db_session_id = (session.session_record or {}).get("db_session_id")
        agent._identity_rows = runtime.identity_rows
        if agent.interpreter is not None:
            agent.interpreter._host_repository = runtime.persistence
            agent.interpreter._host_identity = runtime.identity_rows
            agent.interpreter._host_run_id = None
        local_persist = build_local_persist_fn(
            session_cache=session_cache,
            runtime=runtime,
            agent=agent,
            interpreter=interpreter,
            session=session,
        )

        # Execute
        return await _process_chat_message(
            websocket=None,  # Decoupled
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


async def _handle_message_while_streaming(
    *,
    websocket: WebSocket,
    msg: WSMessage,
    agent: ChatAgentProtocol,
    runtime: _PreparedChatRuntime,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
) -> bool:
    if msg.type == "cancel":
        session.cancel_flag["cancelled"] = True
        return True

    if msg.type == "command":
        await handle_command_with_persist(
            websocket=websocket,
            agent=agent,
            payload=msg.model_dump(),
            session_record=session.session_record,
            persistence=runtime.persistence,
            identity_rows=runtime.identity_rows,
            persistence_required=runtime.persistence_required,
            local_persist=local_persist,
        )
        return True

    if session.lifecycle is not None and session.lifecycle.run_completed:
        return False

    await _try_send_json(
        websocket,
        {
            "type": "error",
            "message": (
                "A run is already in progress. Cancel it or wait for completion before sending another message."
            ),
        },
    )
    return True


async def _receive_next_chat_message(
    *,
    websocket: WebSocket,
    pending_message: WSMessage | None,
    pending_receive_task: asyncio.Task[object] | None,
) -> tuple[WSMessage | None, asyncio.Task[object] | None]:
    if pending_message is not None:
        return pending_message, pending_receive_task

    if pending_receive_task is not None:
        raw_payload = await pending_receive_task
        pending_receive_task = None
    else:
        raw_payload = await websocket.receive_json()

    msg = await parse_ws_message_or_send_error(
        websocket=websocket,
        raw_payload=raw_payload,
    )
    return msg, pending_receive_task


async def _handle_idle_non_turn_message(
    *,
    websocket: WebSocket,
    msg: WSMessage,
    agent: ChatAgentProtocol,
    runtime: _PreparedChatRuntime,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
) -> bool:
    if msg.type == "cancel":
        session.cancel_flag["cancelled"] = True
        await _try_send_json(
            websocket,
            _error_envelope(
                code="no_active_run",
                message="No active websocket run is available to cancel.",
            ),
        )
        return True

    if msg.type == "command":
        await handle_command_with_persist(
            websocket=websocket,
            agent=agent,
            payload=msg.model_dump(),
            session_record=session.session_record,
            persistence=runtime.persistence,
            identity_rows=runtime.identity_rows,
            persistence_required=runtime.persistence_required,
            local_persist=local_persist,
        )
        return True

    if msg.type != "message":
        await _try_send_json(
            websocket,
            {"type": "error", "message": f"Unknown message type: {msg.type}"},
        )
        return True

    return False


class _ExecutionConnectionLoop:
    """Connection-scoped websocket message loop for one execution socket."""

    def __init__(
        self,
        *,
        websocket: WebSocket,
        session_cache: SessionCacheDeps,
        diagnostics_deps: DiagnosticsDeps,
        runtime: _PreparedChatRuntime,
        agent: ChatAgentProtocol,
        interpreter: object | None,
        session: _ChatSessionState,
        local_persist: LocalPersistFn,
        initial_message: WSMessage | None = None,
    ) -> None:
        self.websocket = websocket
        self.session_cache = session_cache
        self.diagnostics_deps = diagnostics_deps
        self.runtime = runtime
        self.agent = agent
        self.interpreter = interpreter
        self.session = session
        self.local_persist = local_persist
        self.execution_emitter = get_execution_emitter(diagnostics_deps)
        self.stream_task: asyncio.Task[str | None] | asyncio.Task[None] | None = None
        self.pending_receive_task: asyncio.Task[object] | None = None
        self.pending_message = initial_message

    async def run(self) -> None:
        try:
            while True:
                if self.stream_task is not None:
                    (
                        msg,
                        self.stream_task,
                        self.pending_receive_task,
                    ) = await _await_message_while_streaming(
                        websocket=self.websocket,
                        stream_task=self.stream_task,
                        pending_receive_task=self.pending_receive_task,
                        session=self.session,
                    )
                    if msg is None:
                        continue
                    if self.stream_task is None:
                        self.pending_message = msg
                        continue

                    if await _handle_message_while_streaming(
                        websocket=self.websocket,
                        msg=msg,
                        agent=self.agent,
                        runtime=self.runtime,
                        session=self.session,
                        local_persist=self.local_persist,
                    ):
                        continue
                    continue

                (
                    self.pending_message,
                    self.pending_receive_task,
                ) = await _receive_next_chat_message(
                    websocket=self.websocket,
                    pending_message=self.pending_message,
                    pending_receive_task=self.pending_receive_task,
                )
                msg = self.pending_message
                self.pending_message = None
                if msg is None:
                    continue

                if await _handle_idle_non_turn_message(
                    websocket=self.websocket,
                    msg=msg,
                    agent=self.agent,
                    runtime=self.runtime,
                    session=self.session,
                    local_persist=self.local_persist,
                ):
                    continue

                if not str(msg.content or "").strip():
                    await _try_send_json(
                        self.websocket,
                        {"type": "error", "message": "Message content cannot be empty"},
                    )
                    continue

                workspace_id, user_id, sess_id = resolve_session_identity(
                    msg=msg,
                    workspace_id=self.session.canonical_workspace_id,
                    user_id=self.session.canonical_user_id,
                )
                await self.execution_emitter.update_subscription(
                    self.websocket,
                    ExecutionSubscription(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        session_id=sess_id,
                    ),
                )
                startup_event = build_startup_status_event()
                await _try_send_json(
                    self.websocket,
                    {
                        "type": "event",
                        "data": build_stream_event_dict(
                            event=startup_event,
                            payload=startup_event.payload,
                        ),
                    },
                )
                self.stream_task = asyncio.create_task(
                    _background_execution_task(
                        msg=msg,
                        session_cache=self.session_cache,
                        runtime=self.runtime,
                        session=self.session,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        sess_id=sess_id,
                        execution_emitter=self.execution_emitter,
                    )
                )
        except (asyncio.CancelledError, WebSocketDisconnect):
            await handle_chat_disconnect(
                pending_receive_task=self.pending_receive_task,
                stream_task=self.stream_task,
                cancel_flag=self.session.cancel_flag,
                local_persist=self.local_persist,
                lifecycle=self.session.lifecycle,
            )
        except Exception as exc:
            await handle_chat_loop_exception(
                websocket=self.websocket,
                exc=exc,
                pending_receive_task=self.pending_receive_task,
                stream_task=self.stream_task,
                local_persist=self.local_persist,
                lifecycle=self.session.lifecycle,
            )


async def _chat_message_loop(
    *,
    websocket: WebSocket,
    session_cache: SessionCacheDeps,
    diagnostics_deps: DiagnosticsDeps,
    runtime: _PreparedChatRuntime,
    agent: ChatAgentProtocol,
    interpreter: object | None,
    session: _ChatSessionState,
    local_persist: LocalPersistFn,
    initial_message: WSMessage | None = None,
) -> None:
    loop = _ExecutionConnectionLoop(
        websocket=websocket,
        session_cache=session_cache,
        diagnostics_deps=diagnostics_deps,
        runtime=runtime,
        agent=agent,
        interpreter=interpreter,
        session=session,
        local_persist=local_persist,
        initial_message=initial_message,
    )
    await loop.run()
