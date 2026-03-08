"""FastAPI dependency injection helpers."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, WebSocket

from fleet_rlm.db import DatabaseManager, FleetRepository

from .auth import AuthError, AuthProvider, NormalizedIdentity
from .config import ServerRuntimeConfig
from .execution import ExecutionEventEmitter

logger = logging.getLogger(__name__)


class ServerState:
    """Shared server state, set during lifespan."""

    def __init__(
        self,
        *,
        config: ServerRuntimeConfig | None = None,
        execution_event_emitter: ExecutionEventEmitter | None = None,
    ) -> None:
        self.config = config or ServerRuntimeConfig()
        self.planner_lm: Any | None = None
        self.delegate_lm: Any | None = None
        self.sessions: dict[str, dict[str, Any]] = {}
        self.runtime_test_results: dict[str, dict[str, Any]] = {}
        self.execution_event_emitter = (
            execution_event_emitter or ExecutionEventEmitter()
        )
        self.db_manager: DatabaseManager | None = None
        self.repository: FleetRepository | None = None
        self.auth_provider: AuthProvider | None = None

    @property
    def is_ready(self) -> bool:
        planner_ready = self.planner_lm is not None
        db_ready = not self.config.database_required or self.repository is not None
        return planner_ready and db_ready


def _require_server_state(app: Any) -> ServerState:
    candidate = getattr(getattr(app, "state", None), "server_state", None)
    if isinstance(candidate, ServerState):
        return candidate
    raise RuntimeError(
        "Server state is not initialized. Ensure FastAPI lifespan startup has completed."
    )


def get_server_state(request: Request) -> ServerState:
    try:
        return _require_server_state(request.app)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def get_server_state_from_websocket(websocket: WebSocket) -> ServerState:
    return _require_server_state(websocket.app)


def get_config(request: Request) -> ServerRuntimeConfig:
    return get_server_state(request).config


def get_server_config(request: Request) -> ServerRuntimeConfig:
    """Compatibility alias for FastAPI DI callers that expect a request-bound getter."""
    return get_config(request)


def get_planner_lm(request: Request) -> Any:
    return get_server_state(request).planner_lm


def get_delegate_lm(request: Request) -> Any:
    return get_server_state(request).delegate_lm


ServerStateDep = Annotated[ServerState, Depends(get_server_state)]
RequestConfigDep = Annotated[ServerRuntimeConfig, Depends(get_config)]
ServerConfigDep = Annotated[ServerRuntimeConfig, Depends(get_server_config)]
PlannerLMDep = Annotated[Any, Depends(get_planner_lm)]
DelegateLMDep = Annotated[Any, Depends(get_delegate_lm)]


def get_db_manager(request: Request) -> DatabaseManager | None:
    return get_server_state(request).db_manager


def get_repository(request: Request) -> FleetRepository | None:
    return get_server_state(request).repository


RepositoryDep = Annotated[FleetRepository | None, Depends(get_repository)]


def get_auth_provider(request: Request) -> AuthProvider | None:
    return get_server_state(request).auth_provider


def build_unauthenticated_identity(
    config: ServerRuntimeConfig | None = None,
) -> NormalizedIdentity:
    cfg = config or ServerRuntimeConfig()
    return NormalizedIdentity(
        tenant_claim=cfg.ws_default_workspace_id,
        user_claim=cfg.ws_default_user_id,
        name="Dev Anonymous",
        raw_claims={"auth": "disabled"},
    )


def require_repository(request: Request) -> FleetRepository:
    repository = get_repository(request)
    if repository is None:
        raise HTTPException(
            status_code=503,
            detail="Database repository unavailable. Configure DATABASE_URL for server runtime.",
        )
    return repository


async def require_http_identity(request: Request) -> NormalizedIdentity:
    state = get_server_state(request)
    provider = state.auth_provider
    cfg = state.config
    if provider is None:
        if cfg.auth_required:
            raise HTTPException(
                status_code=503, detail="Auth provider is not configured"
            )
        identity = build_unauthenticated_identity(cfg)
        request.state.identity = identity
        return identity
    try:
        identity = await provider.authenticate_http(request)
    except AuthError as exc:
        if cfg.auth_required:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.message
            ) from exc
        logger.debug("HTTP auth optional; continuing without auth: %s", exc.message)
        identity = build_unauthenticated_identity(cfg)
    request.state.identity = identity
    return identity


HTTPIdentityDep = Annotated[NormalizedIdentity, Depends(require_http_identity)]


def get_request_identity(request: Request) -> NormalizedIdentity | None:
    identity = getattr(request.state, "identity", None)
    if isinstance(identity, NormalizedIdentity):
        return identity
    return None


def session_key(workspace_id: str, user_id: str, session_id: str | None = None) -> str:
    """Build a stable in-memory key for a stateful user/workspace session."""
    resolved_session_id = (session_id or "").strip() or "__default__"
    return f"{workspace_id}:{user_id}:{resolved_session_id}"
