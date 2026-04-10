"""Transport-facing chat runtime preparation helpers for websocket execution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from fastapi import WebSocket

from fleet_rlm.integrations.database import FleetRepository
from fleet_rlm.integrations.database.types import IdentityUpsertResult
from fleet_rlm.runtime.execution.profiles import ExecutionProfile
from fleet_rlm.runtime.factory import build_chat_agent

from ..auth import AuthError, NormalizedIdentity, resolve_admitted_identity
from ..config import ServerRuntimeConfig
from ..dependencies import ServerState
from ..server_utils import sanitize_id as _sanitize_id

if TYPE_CHECKING:
    from fleet_rlm.orchestration_app import OrchestrationSessionContext


@dataclass(slots=True)
class PreparedChatRuntime:
    cfg: ServerRuntimeConfig
    planner_lm: object
    delegate_lm: object | None
    repository: FleetRepository | None
    persistence_required: bool
    identity_rows: IdentityUpsertResult | None


@dataclass(slots=True)
class ChatSessionState:
    canonical_workspace_id: str
    canonical_user_id: str
    owner_tenant_claim: str
    owner_user_claim: str
    cancel_flag: dict[str, bool]
    active_key: str | None = None
    active_manifest_path: str | None = None
    session_record: dict[str, object] | None = None
    active_run_db_id: uuid.UUID | None = None
    lifecycle: Any | None = None
    last_loaded_docs_path: str | None = None
    orchestration_session: OrchestrationSessionContext | None = None


def set_interpreter_default_profile(
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


async def _ensure_runtime_models(state: ServerState) -> tuple[Any | None, Any | None]:
    from ..bootstrap import ensure_runtime_models

    return await ensure_runtime_models(state)


async def _resolve_persisted_identity(
    *,
    cfg: ServerRuntimeConfig,
    repository: FleetRepository,
    identity: NormalizedIdentity,
) -> IdentityUpsertResult:
    if cfg.auth_mode == "entra":
        return await resolve_admitted_identity(repository, identity)
    return await repository.upsert_identity(
        entra_tenant_id=identity.tenant_claim,
        entra_user_id=identity.user_claim,
        email=identity.email,
        full_name=identity.name,
    )


async def prepare_chat_runtime(
    *,
    websocket: WebSocket,
    state: ServerState,
    identity: NormalizedIdentity,
    send_error,
    close_websocket,
) -> PreparedChatRuntime | None:
    cfg = state.config
    try:
        planner_lm, delegate_lm = await _ensure_runtime_models(state)
    except Exception as exc:
        if await send_error(
            websocket,
            code="planner_initialization_failed",
            message=f"Planner initialization failed: {exc}",
        ):
            await close_websocket(websocket, code=1011)
        return None

    repository = state.repository
    persistence_required = cfg.database_required
    identity_rows = None

    if repository is not None:
        try:
            identity_rows = await _resolve_persisted_identity(
                cfg=cfg,
                repository=repository,
                identity=identity,
            )
        except AuthError as exc:
            if await send_error(
                websocket,
                code="tenant_forbidden" if exc.status_code == 403 else "auth_failed",
                message=exc.message,
            ):
                await close_websocket(websocket, code=1008)
            return None
    elif persistence_required:
        if await send_error(
            websocket,
            code="durable_state_unavailable",
            message="Database repository is required but unavailable",
        ):
            await close_websocket(websocket, code=1011)
        return None

    if planner_lm is None:
        if await send_error(
            websocket,
            code="planner_missing",
            message=(
                "Planner LM not configured. "
                "Check DSPY_LM_MODEL and DSPY_LLM_API_KEY env vars."
            ),
        ):
            await close_websocket(websocket)
        return None

    return PreparedChatRuntime(
        cfg=cfg,
        planner_lm=planner_lm,
        delegate_lm=delegate_lm,
        repository=repository,
        persistence_required=persistence_required,
        identity_rows=identity_rows,
    )


def _chat_agent_builder_kwargs(runtime: PreparedChatRuntime) -> dict[str, Any]:
    return {
        "react_max_iters": runtime.cfg.react_max_iters,
        "deep_react_max_iters": runtime.cfg.deep_react_max_iters,
        "enable_adaptive_iters": runtime.cfg.enable_adaptive_iters,
        "rlm_max_iterations": runtime.cfg.rlm_max_iterations,
        "rlm_max_llm_calls": runtime.cfg.rlm_max_llm_calls,
        "max_depth": runtime.cfg.rlm_max_depth,
        "timeout": runtime.cfg.timeout,
        "secret_name": runtime.cfg.secret_name,
        "volume_name": runtime.cfg.volume_name,
        "interpreter_async_execute": runtime.cfg.interpreter_async_execute,
        "guardrail_mode": runtime.cfg.agent_guardrail_mode,
        "max_output_chars": runtime.cfg.agent_max_output_chars,
        "min_substantive_chars": runtime.cfg.agent_min_substantive_chars,
        "planner_lm": runtime.planner_lm,
        "delegate_lm": runtime.delegate_lm,
        "delegate_max_calls_per_turn": runtime.cfg.delegate_max_calls_per_turn,
        "delegate_result_truncation_chars": runtime.cfg.delegate_result_truncation_chars,
    }


def build_chat_agent_context(runtime: PreparedChatRuntime):
    return cast(Any, build_chat_agent(**_chat_agent_builder_kwargs(runtime)))


def new_chat_session_state(
    runtime: PreparedChatRuntime, identity: NormalizedIdentity
) -> ChatSessionState:
    return ChatSessionState(
        canonical_workspace_id=_sanitize_id(
            identity.tenant_claim, runtime.cfg.ws_default_workspace_id
        ),
        canonical_user_id=_sanitize_id(
            identity.user_claim, runtime.cfg.ws_default_user_id
        ),
        owner_tenant_claim=identity.tenant_claim,
        owner_user_claim=identity.user_claim,
        cancel_flag={"cancelled": False},
    )


__all__ = [
    "ChatSessionState",
    "PreparedChatRuntime",
    "build_chat_agent_context",
    "new_chat_session_state",
    "prepare_chat_runtime",
    "set_interpreter_default_profile",
]
