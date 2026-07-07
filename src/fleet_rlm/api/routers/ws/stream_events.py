"""Event dataclasses and serialization for WebSocket streaming."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from fleet_rlm.api.events.project_chat import project_chat
from fleet_rlm.api.runtime_services.chat_context import (
    ChatExecutionContext,
    TurnControls,
)
from fleet_rlm.api.runtime_services.stream_turn import stream_turn
from fleet_rlm.runtime.events import RuntimeEvent

logger = logging.getLogger(__name__)


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
    prepare: Callable[[], Any] | None = None
    # Transport-neutral context fields (set when refactored path is active)
    prepared_runtime: Any | None = None
    identity: Any | None = None
    session_id: str | None = None
    canonical_workspace_id: str | None = None
    canonical_user_id: str | None = None
    owner_tenant_claim: str | None = None
    owner_user_claim: str | None = None
    cancel_flag: dict[str, bool] | None = None
    selected_skill_ids: list[str] | None = None
    trace_mode: str | None = None


def build_stream_event_dict(
    *,
    event: RuntimeEvent,
    payload: Any = None,
    sequence: int = 0,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Serialize one runtime stream event for websocket delivery."""
    payload_override = payload if isinstance(payload, dict) and payload is not event.payload else None
    return project_chat(
        event,
        sequence=sequence,
        run_id=run_id,
        payload_override=payload_override,
    )


def _is_terminal_transport_event(event: RuntimeEvent) -> bool:
    """Return websocket-terminal semantics for one runtime event."""
    return event.kind.is_terminal()


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


@contextmanager
def _safe_stream_span(name: str, *, attributes: dict[str, Any] | None = None):
    from fleet_rlm.integrations.observability.mlflow_context import mlflow_child_span

    manager = None
    span = None
    try:
        manager = mlflow_child_span(
            name,
            span_type="CHAIN",
            attributes=attributes or {"fleet_rlm.execution_origin": "stream_agent_turn"},
        )
        span = manager.__enter__()
    except Exception:
        logger.debug("MLflow websocket stream span skipped: %s", name, exc_info=True)
        manager = None

    try:
        yield span
    except BaseException as exc:
        if manager is not None:
            try:
                manager.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                logger.debug("MLflow websocket stream span exit skipped after error: %s", name, exc_info=True)
        raise
    else:
        if manager is not None:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                logger.debug("MLflow websocket stream span exit skipped: %s", name, exc_info=True)


async def stream_agent_turn(
    request: WorkspaceTaskRequest,
) -> AsyncIterator[RuntimeEvent]:
    """Stream one workspace task through the agent.

    When ``request.prepared_runtime`` is set (production path), builds a
    ``ChatExecutionContext`` and delegates to the transport-neutral
    ``stream_turn()``.  Falls back to the direct ``aiter_chat_turn_stream``
    path when context fields are unavailable (legacy/test path).
    """
    from fleet_rlm.integrations.observability.mlflow_context import set_mlflow_span_outputs

    if request.execution_mode is not None:
        request.agent.set_execution_mode(request.execution_mode)
    if request.prepare is not None:
        with _safe_stream_span(
            "fleet_rlm.ws_prepare_worker",
            attributes={"fleet_rlm.execution_origin": "stream_agent_turn"},
        ) as span:
            await request.prepare()
            set_mlflow_span_outputs(span, {"status": "ok"})

    # ── Refactored path: build ChatExecutionContext and delegate to stream_turn() ──
    if request.prepared_runtime is not None:
        ctx = ChatExecutionContext(
            prepared=request.prepared_runtime,
            identity=request.identity,  # type: ignore
            session_id=request.session_id,
            canonical_workspace_id=request.canonical_workspace_id,
            canonical_user_id=request.canonical_user_id,
            owner_tenant_claim=request.owner_tenant_claim,
            owner_user_claim=request.owner_user_claim,
            cancel_flag=request.cancel_flag or {"cancelled": False},
            controls=TurnControls(
                execution_mode=request.execution_mode,
                repo_url=request.repo_url,
                repo_ref=request.repo_ref,
                context_paths=list(request.context_paths) if request.context_paths else [],
                batch_concurrency=request.batch_concurrency,
                docs_path=request.docs_path,
                trace=request.trace,
                trace_mode=request.trace_mode,
                selected_skill_ids=list(request.selected_skill_ids) if request.selected_skill_ids else [],
            ),
        )
        event_count = 0
        stream = stream_turn(ctx, request.message)
        try:
            while True:
                with _safe_stream_span(
                    "fleet_rlm.ws_agent_stream",
                    attributes={"fleet_rlm.execution_origin": "stream_agent_turn"},
                ) as span:
                    try:
                        runtime_event = await anext(stream)
                    except StopAsyncIteration:
                        set_mlflow_span_outputs(
                            span, {"status": "ok", "event_count": event_count, "stream_done": True}
                        )
                        break
                    event_count += 1
                    set_mlflow_span_outputs(
                        span,
                        {
                            "status": "ok",
                            "event_count": event_count,
                            "runtime_event_kind": runtime_event.kind.value,
                        },
                    )
                yield runtime_event
        finally:
            aclose = getattr(stream, "aclose", None)
            if callable(aclose):
                await aclose()
        return

    # ── Legacy path (used by tests without context fields) ──
    stream = request.agent.aiter_chat_turn_stream(**_build_agent_stream_kwargs(request))
    event_count = 0
    try:
        while True:
            with _safe_stream_span(
                "fleet_rlm.ws_agent_stream",
                attributes={"fleet_rlm.execution_origin": "stream_agent_turn"},
            ) as span:
                try:
                    runtime_event = await anext(stream)
                except StopAsyncIteration:
                    set_mlflow_span_outputs(span, {"status": "ok", "event_count": event_count, "stream_done": True})
                    break
                event_count += 1
                set_mlflow_span_outputs(
                    span,
                    {
                        "status": "ok",
                        "event_count": event_count,
                        "runtime_event_kind": runtime_event.kind.value,
                    },
                )
            yield runtime_event
    finally:
        aclose = getattr(stream, "aclose", None)
        if callable(aclose):
            await aclose()


__all__ = [
    "WorkspaceTaskRequest",
    "build_stream_event_dict",
    "_is_terminal_transport_event",
    "_build_agent_stream_kwargs",
    "stream_agent_turn",
]
