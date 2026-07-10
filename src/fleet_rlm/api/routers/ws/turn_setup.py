"""Per-message websocket chat turn setup."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time as _time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from fleet_rlm.utils.sandbox_ownership import sandbox_owner_labels

from ...events import ExecutionEventEmitter, ExecutionStepBuilder
from ...runtime_services.chat_runtime import (
    ChatAgentProtocol,
    LocalPersistFn,
    PreStreamSetupFn,
)
from ...runtime_services.chat_runtime import (
    ChatSessionState as _ChatSessionState,
)
from ...runtime_services.chat_runtime import (
    PreparedChatRuntime as _PreparedChatRuntime,
)
from ...runtime_services.run_lifecycle import ExecutionLifecycleManager, initialize_turn_lifecycle
from ...schemas import WSMessage
from .transport import _try_send_json

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PreparedStreamingTurn:
    """Normalized websocket turn setup ready for worker-boundary execution."""

    message: str
    docs_path: str | None
    trace: bool
    execution_mode: str
    workspace_id: str
    repo_url: str | None
    repo_ref: str | None
    context_paths: list[str] | None
    batch_concurrency: int | None
    lifecycle: ExecutionLifecycleManager
    step_builder: ExecutionStepBuilder
    last_loaded_docs_path: str | None
    analytics_enabled: bool | None
    mlflow_trace_context: Any | None
    prepare_worker: PreStreamSetupFn


@dataclass(slots=True)
class DaytonaChatRequestOptions:
    """Normalized Daytona websocket options after schema validation."""

    repo_url: str | None
    repo_ref: str | None
    context_paths: list[str]
    batch_concurrency: int | None
    workspace_id: str
    sandbox_labels: dict[str, str]


def normalize_daytona_chat_request(
    msg: WSMessage,
    workspace_id: str,
    *,
    sandbox_labels: dict[str, str] | None = None,
) -> DaytonaChatRequestOptions:
    """Return a typed Daytona request payload for the canonical runtime."""

    repo_url = str(msg.repo_url or "").strip() or None
    repo_ref = str(msg.repo_ref or "").strip() or None
    context_paths = [str(item).strip() for item in (msg.context_paths or []) if str(item).strip()]
    return DaytonaChatRequestOptions(
        repo_url=repo_url,
        repo_ref=repo_ref,
        context_paths=context_paths,
        batch_concurrency=msg.batch_concurrency,
        workspace_id=workspace_id,
        sandbox_labels=dict(sandbox_labels or {}),
    )


def _normalize_context_paths(*groups: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
    return normalized


def _local_workspace_snapshot_path(*, user_request: str, repo_url: str | None) -> str | None:
    """Materialize a bounded host checkout snapshot for local code-review prompts."""

    if repo_url:
        return None
    from fleet_rlm.runtime.tools.rlm_delegate import _build_local_workspace_snapshot

    snapshot = _build_local_workspace_snapshot(query=user_request, context="")
    if not snapshot:
        return None

    digest = hashlib.sha256((user_request + "\n" + snapshot[:4096]).encode("utf-8")).hexdigest()[:16]
    snapshot_dir = Path.cwd() / ".codex" / "tmp" / "local-workspace-snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"local-workspace-snapshot-{digest}.md"
    snapshot_path.write_text(snapshot, encoding="utf-8")
    return str(snapshot_path)


def _augment_local_workspace_context(
    *,
    request: DaytonaChatRequestOptions,
    user_request: str | None,
    docs_path: str | None,
) -> DaytonaChatRequestOptions:
    if not user_request or docs_path or request.repo_url or request.context_paths:
        return request
    snapshot_path = _local_workspace_snapshot_path(
        user_request=user_request,
        repo_url=request.repo_url,
    )
    if not snapshot_path:
        return request
    return DaytonaChatRequestOptions(
        repo_url=request.repo_url,
        repo_ref=request.repo_ref,
        context_paths=[snapshot_path],
        batch_concurrency=request.batch_concurrency,
        workspace_id=request.workspace_id,
        sandbox_labels=request.sandbox_labels,
    )


async def prepare_daytona_workspace_for_turn(
    *,
    agent: ChatAgentProtocol,
    request: DaytonaChatRequestOptions,
    docs_path: str | None,
    user_request: str | None = None,
    execution_mode: str = "auto",
) -> None:
    """Apply Daytona workspace settings via the interpreter's native session API."""

    interpreter = getattr(agent, "interpreter", None)
    if interpreter is None:
        return

    configure_workspace = getattr(interpreter, "aconfigure_workspace", None)
    if not callable(configure_workspace):
        return

    snapshot: str | None = None
    if user_request:
        from fleet_rlm.integrations.daytona.runtime import resolve_snapshot_for_skills
        from fleet_rlm.runtime.modules.context_routing import build_turn_context_for_agent
        from fleet_rlm.runtime.modules.skill_selection import preview_skills_for_turn

        turn_context = build_turn_context_for_agent(
            agent,
            user_request=user_request,
            docs_path=docs_path,
            context_paths=list(request.context_paths),
        )
        routing_decision: str | None = None
        preview_routing = getattr(agent, "preview_routing", None)
        if callable(preview_routing):
            routing_payload = preview_routing(
                user_request=user_request,
                execution_mode=execution_mode,
                turn_context=turn_context,
            )
            if isinstance(routing_payload, dict):
                routing_decision = str(routing_payload.get("routing_decision") or "") or None
        turn_count = getattr(agent, "turn_count", None)
        if not isinstance(turn_count, int):
            agent_module = getattr(agent, "agent", None)
            turn_count = getattr(agent_module, "_turn_count", 0)
        preview_skills = preview_skills_for_turn(
            user_request,
            execution_mode=execution_mode,
            routing_decision=routing_decision,
            is_first_turn=int(turn_count or 0) == 0,
        )
        snapshot = resolve_snapshot_for_skills(preview_skills)

    raw_loaded_paths = getattr(agent, "loaded_document_paths", ())
    loaded_document_paths = (
        [str(item) for item in raw_loaded_paths] if isinstance(raw_loaded_paths, (list, tuple)) else []
    )
    docs_paths = [str(docs_path)] if docs_path is not None else []
    context_paths = _normalize_context_paths(
        loaded_document_paths,
        list(request.context_paths),
        docs_paths,
    )

    normalized_batch_concurrency = (
        max(1, int(request.batch_concurrency))
        if isinstance(request.batch_concurrency, int) and request.batch_concurrency > 0
        else None
    )
    setattr(agent, "batch_concurrency", normalized_batch_concurrency)

    await configure_workspace(
        repo_url=request.repo_url,
        repo_ref=request.repo_ref,
        context_paths=context_paths,
        volume_name=request.workspace_id,
        sandbox_labels=request.sandbox_labels,
        snapshot=snapshot,
    )

    if context_paths and hasattr(agent, "loaded_document_paths"):
        loaded = getattr(agent, "loaded_document_paths", None)
        if isinstance(loaded, list):
            for path in context_paths:
                if path not in loaded:
                    loaded.append(path)

    astart = getattr(interpreter, "astart", None)
    if not callable(astart):
        return

    prep_timeout = 120.0
    raw_timeout = getattr(interpreter, "timeout", None)
    if raw_timeout is not None:
        try:
            prep_timeout = float(raw_timeout)
        except (TypeError, ValueError):
            prep_timeout = 120.0
    if prep_timeout <= 0:
        prep_timeout = 120.0
    try:
        await asyncio.wait_for(astart(), timeout=prep_timeout)
    except asyncio.TimeoutError as exc:
        from fleet_rlm.integrations.daytona.errors import DaytonaDiagnosticError

        raise DaytonaDiagnosticError(
            f"Daytona workspace did not become ready within {prep_timeout:.0f}s. "
            "Verify DAYTONA_API_KEY, DAYTONA_API_URL, and sandbox capacity, then retry.",
            category="workspace_prep_timeout",
            phase="workspace_prepare",
        ) from exc


async def _reject_empty_message(
    websocket: WebSocket | None,
    *,
    message: str,
) -> bool:
    if message:
        return False
    if websocket is not None:
        await _try_send_json(
            websocket,
            {"type": "error", "message": "Message content cannot be empty"},
        )
    return True


def _optional_context_paths(
    raw_context_paths: list[str] | None,
    normalized_context_paths: list[str],
) -> list[str] | None:
    """Preserve the distinction between unspecified and explicitly empty paths."""

    if raw_context_paths is None:
        return list(normalized_context_paths) if normalized_context_paths else None
    return list(normalized_context_paths)


def _build_prepare_stream(
    *,
    agent: ChatAgentProtocol,
    msg: WSMessage,
    workspace_id: str,
    owner_tenant_claim: str,
    owner_user_claim: str,
    sess_id: str,
) -> tuple[DaytonaChatRequestOptions, PreStreamSetupFn]:
    daytona_request = normalize_daytona_chat_request(
        msg,
        workspace_id=workspace_id,
        sandbox_labels=sandbox_owner_labels(
            tenant_claim=owner_tenant_claim,
            user_claim=owner_user_claim,
            session_id=sess_id,
        ),
    )
    daytona_request = _augment_local_workspace_context(
        request=daytona_request,
        user_request=msg.content,
        docs_path=msg.docs_path,
    )

    async def _prepare_stream() -> None:
        t_ws_prep = _time.monotonic()
        await prepare_daytona_workspace_for_turn(
            agent=agent,
            request=daytona_request,
            docs_path=msg.docs_path,
            user_request=msg.content,
            execution_mode=msg.execution_mode or "auto",
        )
        t_ws_prep_ms = (_time.monotonic() - t_ws_prep) * 1000
        logger.debug("turn_setup: prepare_daytona_workspace completed in %.0fms", t_ws_prep_ms)

    return daytona_request, _prepare_stream


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
    run_id: str | None,
    message: str,
    execution_mode: str,
):
    from fleet_rlm.integrations.observability.mlflow_context import build_chat_trace_context

    # Check if delegate LM (sub_lm) is configured for cost tracking. Use
    # getattr so test/mocked runtimes without a delegate_lm attribute don't crash.
    sub_lm_configured = getattr(runtime, "delegate_lm", None) is not None
    return build_chat_trace_context(
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=sess_id,
        turn_index=turn_index,
        run_id=run_id,
        message=message,
        execution_mode=execution_mode,
        app_env=getattr(runtime.cfg, "app_env", None),
        sub_lm_configured=sub_lm_configured,
    )


async def prepare_chat_message_turn(
    *,
    websocket: WebSocket | None,
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
    t_setup_start = _time.monotonic()
    message = str(msg.content or "").strip()
    if await _reject_empty_message(websocket, message=message):
        return None

    execution_mode = msg.execution_mode
    daytona_request, prepare_worker = _build_prepare_stream(
        agent=agent,
        msg=msg,
        workspace_id=workspace_id,
        owner_tenant_claim=session.owner_tenant_claim,
        owner_user_claim=session.owner_user_claim,
        sess_id=sess_id,
    )
    sandbox_provider = "daytona"

    t_persist_start = _time.monotonic()
    await local_persist(include_volume_save=False, latest_user_message=message)
    t_persist_ms = (_time.monotonic() - t_persist_start) * 1000
    logger.debug("turn_setup: local_persist completed in %.0fms", t_persist_ms)

    session.cancel_flag["cancelled"] = False
    turn_index = agent.history_turns() + 1

    t_lifecycle_start = _time.monotonic()
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
    t_lifecycle_ms = (_time.monotonic() - t_lifecycle_start) * 1000
    logger.debug("turn_setup: lifecycle init completed in %.0fms", t_lifecycle_ms)

    trace_context = _build_trace_context(
        runtime=runtime,
        workspace_id=workspace_id,
        user_id=user_id,
        sess_id=sess_id,
        turn_index=turn_index,
        run_id=_run_id,
        message=message,
        execution_mode=execution_mode,
    )
    if session.lifecycle is None:
        raise RuntimeError("Turn lifecycle initialization returned no lifecycle manager")

    context_paths = _optional_context_paths(
        msg.context_paths,
        daytona_request.context_paths,
    )

    t_setup_total_ms = (_time.monotonic() - t_setup_start) * 1000
    logger.debug("turn_setup: total prepare_chat_message_turn completed in %.0fms", t_setup_total_ms)

    return PreparedStreamingTurn(
        message=message,
        docs_path=msg.docs_path,
        trace=bool(msg.trace),
        execution_mode=execution_mode,
        workspace_id=workspace_id,
        repo_url=daytona_request.repo_url,
        repo_ref=daytona_request.repo_ref,
        context_paths=context_paths,
        batch_concurrency=daytona_request.batch_concurrency,
        lifecycle=session.lifecycle,
        step_builder=step_builder,
        last_loaded_docs_path=session.last_loaded_docs_path,
        analytics_enabled=getattr(msg, "analytics_enabled", None),
        mlflow_trace_context=trace_context,
        prepare_worker=prepare_worker,
    )
