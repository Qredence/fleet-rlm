"""FastAPI dependency injection helpers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Security, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from fleet_rlm.integrations.database import DatabaseManager, FleetRepository
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.utils.identity import owner_fingerprint

from .auth import AuthError, AuthProvider, NormalizedIdentity, resolve_admitted_identity
from .config import ServerRuntimeConfig
from .events import ExecutionEventEmitter

logger = logging.getLogger(__name__)

http_bearer = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description=(
        "Bearer token used when HTTP authentication is enabled. "
        "When auth is optional, requests without a token fall back to the "
        "configured default server identity."
    ),
)

HTTPBearerCredentialsDep = Annotated[
    HTTPAuthorizationCredentials | None,
    Security(http_bearer),
]


# ---------------------------------------------------------------------------
# Focused dependency types (decomposed from the former monolithic ServerState)
# ---------------------------------------------------------------------------


@dataclass
class ConfigDeps:
    """Runtime configuration dependency slice."""

    config: ServerRuntimeConfig = field(default_factory=ServerRuntimeConfig)


@dataclass
class LmDeps:
    """Language-model dependency slice."""

    planner_lm: Any | None = None
    delegate_lm: Any | None = None
    runtime_model_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class AuthDeps:
    """Authentication provider dependency slice."""

    auth_provider: AuthProvider | None = None


@dataclass
class SessionCacheDeps:
    """In-memory session cache dependency slice."""

    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class PersistenceDeps:
    """Database persistence dependency slice."""

    db_manager: DatabaseManager | None = None
    repository: FleetRepository | None = None


@dataclass
class DiagnosticsDeps:
    """Runtime diagnostics / observability dependency slice."""

    events_event_emitter: ExecutionEventEmitter = field(
        default_factory=ExecutionEventEmitter
    )
    runtime_test_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    optional_service_status: dict[str, str] = field(
        default_factory=lambda: {
            "mlflow": "pending",
            "posthog": "pending",
            "planner_lm": "pending",
            "delegate_lm": "pending",
        }
    )
    optional_service_errors: dict[str, str] = field(default_factory=dict)
    mlflow_server_process: Any | None = None
    optional_startup_task: asyncio.Task[None] | None = None


# ---------------------------------------------------------------------------
# Backward-compatible ServerState container
# ---------------------------------------------------------------------------


class ServerState:
    """Shared server state, set during lifespan.

    This class is kept as a backward-compatible container that composes the
    focused dependency slices.  New code should import the focused types
    directly; legacy code and tests can continue to use the flat attribute
    API provided by the property accessors below.
    """

    def __init__(
        self,
        *,
        config: ServerRuntimeConfig | None = None,
        execution_event_emitter: ExecutionEventEmitter | None = None,
    ) -> None:
        self.config_deps = ConfigDeps(config=config or ServerRuntimeConfig())
        self.lm_deps = LmDeps()
        self.auth_deps = AuthDeps()
        self.session_cache_deps = SessionCacheDeps()
        self.persistence_deps = PersistenceDeps()
        self.diagnostics_deps = DiagnosticsDeps(
            events_event_emitter=execution_event_emitter or ExecutionEventEmitter(),
        )

    # -- ConfigDeps proxies --

    @property
    def config(self) -> ServerRuntimeConfig:
        return self.config_deps.config

    @config.setter
    def config(self, value: ServerRuntimeConfig) -> None:
        self.config_deps.config = value

    # -- LmDeps proxies --

    @property
    def planner_lm(self) -> Any | None:
        return self.lm_deps.planner_lm

    @planner_lm.setter
    def planner_lm(self, value: Any | None) -> None:
        self.lm_deps.planner_lm = value

    @property
    def delegate_lm(self) -> Any | None:
        return self.lm_deps.delegate_lm

    @delegate_lm.setter
    def delegate_lm(self, value: Any | None) -> None:
        self.lm_deps.delegate_lm = value

    @property
    def runtime_model_lock(self) -> asyncio.Lock:
        return self.lm_deps.runtime_model_lock

    # -- AuthDeps proxies --

    @property
    def auth_provider(self) -> AuthProvider | None:
        return self.auth_deps.auth_provider

    @auth_provider.setter
    def auth_provider(self, value: AuthProvider | None) -> None:
        self.auth_deps.auth_provider = value

    # -- SessionCacheDeps proxies --

    @property
    def sessions(self) -> dict[str, dict[str, Any]]:
        return self.session_cache_deps.sessions

    @sessions.setter
    def sessions(self, value: dict[str, dict[str, Any]]) -> None:
        self.session_cache_deps.sessions = value

    # -- PersistenceDeps proxies --

    @property
    def db_manager(self) -> DatabaseManager | None:
        return self.persistence_deps.db_manager

    @db_manager.setter
    def db_manager(self, value: DatabaseManager | None) -> None:
        self.persistence_deps.db_manager = value

    @property
    def repository(self) -> FleetRepository | None:
        return self.persistence_deps.repository

    @repository.setter
    def repository(self, value: FleetRepository | None) -> None:
        self.persistence_deps.repository = value

    # -- DiagnosticsDeps proxies --

    @property
    def events_event_emitter(self) -> ExecutionEventEmitter:
        return self.diagnostics_deps.events_event_emitter

    @events_event_emitter.setter
    def events_event_emitter(self, value: ExecutionEventEmitter) -> None:
        self.diagnostics_deps.events_event_emitter = value

    @property
    def runtime_test_results(self) -> dict[str, dict[str, Any]]:
        return self.diagnostics_deps.runtime_test_results

    @runtime_test_results.setter
    def runtime_test_results(self, value: dict[str, dict[str, Any]]) -> None:
        self.diagnostics_deps.runtime_test_results = value

    @property
    def optional_service_status(self) -> dict[str, str]:
        return self.diagnostics_deps.optional_service_status

    @optional_service_status.setter
    def optional_service_status(self, value: dict[str, str]) -> None:
        self.diagnostics_deps.optional_service_status = value

    @property
    def optional_service_errors(self) -> dict[str, str]:
        return self.diagnostics_deps.optional_service_errors

    @optional_service_errors.setter
    def optional_service_errors(self, value: dict[str, str]) -> None:
        self.diagnostics_deps.optional_service_errors = value

    @property
    def mlflow_server_process(self) -> Any | None:
        return self.diagnostics_deps.mlflow_server_process

    @mlflow_server_process.setter
    def mlflow_server_process(self, value: Any | None) -> None:
        self.diagnostics_deps.mlflow_server_process = value

    @property
    def optional_startup_task(self) -> asyncio.Task[None] | None:
        return self.diagnostics_deps.optional_startup_task

    @optional_startup_task.setter
    def optional_startup_task(self, value: asyncio.Task[None] | None) -> None:
        self.diagnostics_deps.optional_startup_task = value

    @property
    def is_ready(self) -> bool:
        """Return whether critical server dependencies are ready to serve requests."""
        db_ready = not self.config.database_required or self.repository is not None
        planner_ready = (
            self.planner_lm is not None
            or self.optional_service_status.get("planner_lm") == "ready"
        )
        return db_ready and planner_ready


# ---------------------------------------------------------------------------
# Legacy server-state accessors (used by tests and internal ws/ code)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Focused dependency getters (preferred for new router code)
# ---------------------------------------------------------------------------


def _require_dep(app: Any, attr: str) -> Any:
    candidate = getattr(getattr(app, "state", None), attr, None)
    if candidate is not None:
        return candidate
    # Fallback: try to extract from legacy server_state
    server_state = getattr(getattr(app, "state", None), "server_state", None)
    if isinstance(server_state, ServerState):
        mapping = {
            "config_deps": server_state.config_deps,
            "lm_deps": server_state.lm_deps,
            "auth_deps": server_state.auth_deps,
            "session_cache_deps": server_state.session_cache_deps,
            "persistence_deps": server_state.persistence_deps,
            "diagnostics_deps": server_state.diagnostics_deps,
        }
        dep = mapping.get(attr)
        if dep is not None:
            return dep
    raise RuntimeError(
        f"Server dependency '{attr}' is not initialized. "
        "Ensure FastAPI lifespan startup has completed."
    )


def get_config_deps(request: Request) -> ConfigDeps:
    """Resolve runtime configuration dependencies."""
    return _require_dep(request.app, "config_deps")


ConfigDepsDep = Annotated[ConfigDeps, Depends(get_config_deps)]


def get_lm_deps(request: Request) -> LmDeps:
    """Resolve language-model dependencies."""
    return _require_dep(request.app, "lm_deps")


LmDepsDep = Annotated[LmDeps, Depends(get_lm_deps)]


def get_auth_deps(request: Request) -> AuthDeps:
    """Resolve authentication dependencies."""
    return _require_dep(request.app, "auth_deps")


AuthDepsDep = Annotated[AuthDeps, Depends(get_auth_deps)]


def get_session_cache_deps(request: Request) -> SessionCacheDeps:
    """Resolve in-memory session cache dependencies."""
    return _require_dep(request.app, "session_cache_deps")


SessionCacheDepsDep = Annotated[SessionCacheDeps, Depends(get_session_cache_deps)]


def get_persistence_deps(request: Request) -> PersistenceDeps:
    """Resolve database persistence dependencies."""
    return _require_dep(request.app, "persistence_deps")


PersistenceDepsDep = Annotated[PersistenceDeps, Depends(get_persistence_deps)]


def get_diagnostics_deps(request: Request) -> DiagnosticsDeps:
    """Resolve runtime diagnostics dependencies."""
    return _require_dep(request.app, "diagnostics_deps")


DiagnosticsDepsDep = Annotated[DiagnosticsDeps, Depends(get_diagnostics_deps)]


# WebSocket variants


def get_config_deps_from_websocket(websocket: WebSocket) -> ConfigDeps:
    return _require_dep(websocket.app, "config_deps")


def get_lm_deps_from_websocket(websocket: WebSocket) -> LmDeps:
    return _require_dep(websocket.app, "lm_deps")


def get_auth_deps_from_websocket(websocket: WebSocket) -> AuthDeps:
    return _require_dep(websocket.app, "auth_deps")


def get_session_cache_deps_from_websocket(websocket: WebSocket) -> SessionCacheDeps:
    return _require_dep(websocket.app, "session_cache_deps")


def get_persistence_deps_from_websocket(websocket: WebSocket) -> PersistenceDeps:
    return _require_dep(websocket.app, "persistence_deps")


def get_diagnostics_deps_from_websocket(websocket: WebSocket) -> DiagnosticsDeps:
    return _require_dep(websocket.app, "diagnostics_deps")


# ---------------------------------------------------------------------------
# Legacy convenience getters (still used by some runtime_services)
# ---------------------------------------------------------------------------


def get_db_manager(request: Request) -> DatabaseManager | None:
    """Return the configured database manager, if persistence is enabled."""
    return get_persistence_deps(request).db_manager


def get_repository(request: Request) -> FleetRepository | None:
    """Return the configured repository facade, if persistence is enabled."""
    return get_persistence_deps(request).repository


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


async def require_http_identity(
    request: Request,
    credentials: HTTPBearerCredentialsDep,
) -> NormalizedIdentity:
    """Authenticate an HTTP request or fall back to the configured dev identity."""
    _ = credentials
    config_deps = get_config_deps(request)
    auth_deps = get_auth_deps(request)
    provider = auth_deps.auth_provider
    cfg = config_deps.config
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


async def resolve_persisted_identity(
    config_deps: ConfigDepsDep,
    repository: RepositoryDep,
    identity: HTTPIdentityDep,
) -> IdentityUpsertResult | None:
    """Resolve the caller's persisted identity, or None if DB is unavailable."""
    if repository is None:
        return None
    if config_deps.config.auth_mode == "entra":
        try:
            return await resolve_admitted_identity(repository, identity)
        except AuthError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.message
            ) from exc
    return await repository.upsert_identity(
        entra_tenant_id=identity.tenant_claim,
        entra_user_id=identity.user_claim,
        email=identity.email,
        full_name=identity.name,
    )


PersistedIdentityDep = Annotated[
    IdentityUpsertResult | None, Depends(resolve_persisted_identity)
]


def get_request_identity(request: Request) -> NormalizedIdentity | None:
    """Read the resolved identity cached on the request state, if present."""
    identity = getattr(request.state, "identity", None)
    if isinstance(identity, NormalizedIdentity):
        return identity
    return None


def compose_server_state(
    config_deps: ConfigDeps,
    lm_deps: LmDeps,
    auth_deps: AuthDeps,
    session_cache_deps: SessionCacheDeps,
    persistence_deps: PersistenceDeps,
    diagnostics_deps: DiagnosticsDeps,
) -> ServerState:
    """Assemble a backward-compatible ServerState from focused dependency slices."""
    state = ServerState.__new__(ServerState)
    state.config_deps = config_deps
    state.lm_deps = lm_deps
    state.auth_deps = auth_deps
    state.session_cache_deps = session_cache_deps
    state.persistence_deps = persistence_deps
    state.diagnostics_deps = diagnostics_deps
    return state


def session_key(
    tenant_claim: str,
    user_claim: str,
    session_id: str | None = None,
) -> str:
    """Build a stable in-memory key for a stateful user/workspace session."""
    resolved_session_id = (session_id or "").strip() or "__default__"
    owner_id = owner_fingerprint(tenant_claim, user_claim)
    return f"owner:{owner_id}:{resolved_session_id}"
