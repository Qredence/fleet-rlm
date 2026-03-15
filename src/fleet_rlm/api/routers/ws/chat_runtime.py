"""Chat websocket runtime bootstrap helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, cast

from fastapi import WebSocket

from fleet_rlm import runners
from fleet_rlm.core.execution.interpreter import ExecutionProfile
from fleet_rlm.infrastructure.database import FleetRepository
from fleet_rlm.infrastructure.database.types import IdentityUpsertResult

from ...config import ServerRuntimeConfig
from ...deps import ServerState
from ...auth import AuthError, resolve_admitted_identity
from ...auth import NormalizedIdentity
from .contracts import ChatAgentProtocol
from .helpers import (
    _close_websocket_safely,
    _error_envelope,
    _sanitize_id,
    _try_send_json,
)
from .lifecycle import ExecutionLifecycleManager, _classify_stream_failure


@dataclass(slots=True)
class _PreparedChatRuntime:
    cfg: ServerRuntimeConfig
    planner_lm: object
    delegate_lm: object | None
    repository: FleetRepository | None
    persistence_required: bool
    identity_rows: IdentityUpsertResult | None


@dataclass(slots=True)
class _ChatSessionState:
    canonical_workspace_id: str
    canonical_user_id: str
    cancel_flag: dict[str, bool]
    active_key: str | None = None
    active_manifest_path: str | None = None
    session_record: dict[str, Any] | None = None
    active_run_db_id: uuid.UUID | None = None
    lifecycle: ExecutionLifecycleManager | None = None
    last_loaded_docs_path: str | None = None


def _set_interpreter_default_profile(
    interpreter: object | None, cfg: ServerRuntimeConfig
) -> None:
    if interpreter is None:
        return
    try:
        setattr(
            interpreter,
            "default_execution_profile",
            ExecutionProfile(cfg.ws_default_execution_profile),
        )
    except ValueError:
        setattr(
            interpreter,
            "default_execution_profile",
            ExecutionProfile.ROOT_INTERLOCUTOR,
        )


async def _prepare_chat_runtime(
    *,
    websocket: WebSocket,
    state: ServerState,
    identity: NormalizedIdentity,
) -> _PreparedChatRuntime | None:
    cfg = state.config
    planner_lm = state.planner_lm
    delegate_lm = state.delegate_lm
    repository = state.repository
    persistence_required = cfg.database_required
    identity_rows = None

    if repository is not None:
        try:
            if cfg.auth_mode == "entra":
                identity_rows = await resolve_admitted_identity(repository, identity)
            else:
                identity_rows = await repository.upsert_identity(
                    entra_tenant_id=identity.tenant_claim,
                    entra_user_id=identity.user_claim,
                    email=identity.email,
                    full_name=identity.name,
                )
        except AuthError as exc:
            if await _try_send_json(
                websocket,
                _error_envelope(
                    code="tenant_forbidden"
                    if exc.status_code == 403
                    else "auth_failed",
                    message=exc.message,
                ),
            ):
                await _close_websocket_safely(websocket, code=1008)
            return None
    elif persistence_required:
        if await _try_send_json(
            websocket,
            _error_envelope(
                code="durable_state_unavailable",
                message="Database repository is required but unavailable",
            ),
        ):
            await _close_websocket_safely(websocket, code=1011)
        return None

    if planner_lm is None:
        if await _try_send_json(
            websocket,
            _error_envelope(
                code="planner_missing",
                message=(
                    "Planner LM not configured. "
                    "Check DSPY_LM_MODEL and DSPY_LLM_API_KEY env vars."
                ),
            ),
        ):
            await _close_websocket_safely(websocket)
        return None

    return _PreparedChatRuntime(
        cfg=cfg,
        planner_lm=planner_lm,
        delegate_lm=delegate_lm,
        repository=repository,
        persistence_required=persistence_required,
        identity_rows=identity_rows,
    )


def _build_chat_agent_context(
    runtime: _PreparedChatRuntime,
    *,
    runtime_mode: str = "modal_chat",
) -> ChatAgentProtocol:
    if runtime_mode == "daytona_pilot":
        return cast(
            ChatAgentProtocol,
            runners.build_daytona_workbench_chat_agent(
                timeout=runtime.cfg.timeout,
                max_depth=runtime.cfg.rlm_max_depth,
                planner_lm=runtime.planner_lm,
                delegate_lm=runtime.delegate_lm,
            ),
        )

    return cast(
        ChatAgentProtocol,
        runners.build_react_chat_agent(
            react_max_iters=runtime.cfg.react_max_iters,
            deep_react_max_iters=runtime.cfg.deep_react_max_iters,
            enable_adaptive_iters=runtime.cfg.enable_adaptive_iters,
            rlm_max_iterations=runtime.cfg.rlm_max_iterations,
            rlm_max_llm_calls=runtime.cfg.rlm_max_llm_calls,
            max_depth=runtime.cfg.rlm_max_depth,
            timeout=runtime.cfg.timeout,
            secret_name=runtime.cfg.secret_name,
            volume_name=runtime.cfg.volume_name,
            interpreter_async_execute=runtime.cfg.interpreter_async_execute,
            guardrail_mode=runtime.cfg.agent_guardrail_mode,
            max_output_chars=runtime.cfg.agent_max_output_chars,
            min_substantive_chars=runtime.cfg.agent_min_substantive_chars,
            planner_lm=runtime.planner_lm,
            delegate_lm=runtime.delegate_lm,
            delegate_max_calls_per_turn=runtime.cfg.delegate_max_calls_per_turn,
            delegate_result_truncation_chars=runtime.cfg.delegate_result_truncation_chars,
        ),
    )


def _new_chat_session_state(
    runtime: _PreparedChatRuntime, identity: NormalizedIdentity
) -> _ChatSessionState:
    return _ChatSessionState(
        canonical_workspace_id=_sanitize_id(
            identity.tenant_claim, runtime.cfg.ws_default_workspace_id
        ),
        canonical_user_id=_sanitize_id(
            identity.user_claim, runtime.cfg.ws_default_user_id
        ),
        cancel_flag={"cancelled": False},
    )


def _chat_startup_error_payload(exc: Exception) -> dict[str, object]:
    """Build a stable websocket error envelope for startup failures."""
    error_code = _classify_stream_failure(exc)
    lowered = str(exc).lower()

    if "token id is malformed" in lowered and "modal" in lowered:
        message = (
            "Modal authentication failed: Token ID is malformed. "
            "Update MODAL_TOKEN_ID / MODAL_TOKEN_SECRET or run `uv run modal token set`, "
            "then restart the server."
        )
    else:
        message = f"Server error: {str(exc)}"

    return _error_envelope(
        code=error_code,
        message=message,
        details={"error_type": type(exc).__name__},
    )
