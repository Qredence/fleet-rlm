"""FastAPI dependency injection helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, WebSocket

from fleet_rlm.integrations.database import DatabaseManager, FleetRepository

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
        self.mlflow_server_process: Any | None = None
        self.optional_startup_task: asyncio.Task[None] | None = None
        self.runtime_model_lock: asyncio.Lock = asyncio.Lock()
        self.optional_service_status: dict[str, str] = {
            "mlflow": "pending",
            "posthog": "pending",
            "planner_lm": "pending",
            "delegate_lm": "pending",
        }
        self.optional_service_errors: dict[str, str] = {}

    @property
    def is_ready(self) -> bool:
        """Return whether critical server dependencies are ready to serve requests."""
        db_ready = not self.config.database_required or self.repository is not None
        planner_ready = (
            self.planner_lm is not None
            or self.optional_service_status.get("planner_lm") == "ready"
        )
        return db_ready and planner_ready


def _require_server_state(app: Any) -> ServerState:
    candidate = getattr(getattr(app, "state", None), "server_state", None)
    if isinstance(candidate, ServerState):
        return candidate
    raise RuntimeError(
        "Server state is not initialized. Ensure FastAPI lifespan startup has completed."
    )


def get_server_state(request: Request) -> ServerState:
    """Resolve initialized server state for HTTP request handlers."""
    try:
        return _require_server_state(request.app)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def get_server_state_from_websocket(websocket: WebSocket) -> ServerState:
    """Resolve initialized server state for websocket handlers."""
    return _require_server_state(websocket.app)


ServerStateDep = Annotated[ServerState, Depends(get_server_state)]


def get_db_manager(request: Request) -> DatabaseManager | None:
    """Return the configured database manager, if persistence is enabled."""
    return get_server_state(request).db_manager


def get_repository(request: Request) -> FleetRepository | None:
    """Return the configured repository facade, if persistence is enabled."""
    return get_server_state(request).repository


RepositoryDep = Annotated[FleetRepository | None, Depends(get_repository)]


def build_unauthenticated_identity(
    config: ServerRuntimeConfig | None = None,
) -> NormalizedIdentity:
    """Create the fallback development identity used when auth is optional."""
    cfg = config or ServerRuntimeConfig()
    return NormalizedIdentity(
        tenant_claim=cfg.ws_default_workspace_id,
        user_claim=cfg.ws_default_user_id,
        name="Dev Anonymous",
        raw_claims={"auth": "disabled"},
    )


async def require_http_identity(request: Request) -> NormalizedIdentity:
    """Authenticate an HTTP request or fall back to the configured dev identity."""
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
    """Read the resolved identity cached on the request state, if present."""
    identity = getattr(request.state, "identity", None)
    if isinstance(identity, NormalizedIdentity):
        return identity
    return None


def session_key(workspace_id: str, user_id: str, session_id: str | None = None) -> str:
    """Build a stable in-memory key for a stateful user/workspace session."""
    resolved_session_id = (session_id or "").strip() or "__default__"
    return f"{workspace_id}:{user_id}:{resolved_session_id}"
