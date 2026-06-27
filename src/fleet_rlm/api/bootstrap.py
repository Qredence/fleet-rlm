"""Runtime bootstrap helpers for the FastAPI server."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from fleet_rlm.integrations.config.runtime_settings import resolve_env_path
from fleet_rlm.integrations.database import DatabaseManager, FleetRepository

from .auth import build_auth_provider
from .bootstrap_observability import (
    initialize_mlflow_runtime_service,
    initialize_posthog_runtime_service,
    set_optional_service_status,
    terminate_process,
)
from .config import AppConfig
from .dependencies import (
    AuthDeps,
    ConfigDeps,
    DiagnosticsDeps,
    LmDeps,
    PersistenceDeps,
    ServerState,
    SessionCacheDeps,
    WebSocketTicketDeps,
)
from .events import ExecutionEventEmitter

logger = logging.getLogger(__name__)

_LLM_MODEL_ENV_KEYS = (
    "DSPY_LM_MODEL",
    "DSPY_DELEGATE_LM_MODEL",
    "DSPY_DELEGATE_LM_SMALL_MODEL",
    "DSPY_DELEGATE_LM_MAX_TOKENS",
)


def _sync_llm_model_config_from_env(cfg: AppConfig) -> None:
    """Align in-memory runtime config with current process env model settings."""
    normalized = {key: os.environ[key] for key in _LLM_MODEL_ENV_KEYS if key in os.environ}
    if not normalized:
        return

    from fleet_rlm.api.runtime_services.settings import apply_runtime_settings_to_config

    apply_runtime_settings_to_config(config=cfg, normalized=normalized)


def _runtime_config_helpers():
    from fleet_rlm.runtime.config import (
        configure_posthog_analytics_from_env,
        get_delegate_lm_from_env,
        get_delegate_small_lm_from_env,
        get_planner_lm_from_env,
    )

    return (
        configure_posthog_analytics_from_env,
        get_planner_lm_from_env,
        get_delegate_lm_from_env,
        get_delegate_small_lm_from_env,
    )


def get_planner_lm_from_env(*args, **kwargs):
    """Compatibility shim for lazy planner LM loading."""
    _, planner_loader, _, _ = _runtime_config_helpers()
    return planner_loader(*args, **kwargs)


def get_delegate_lm_from_env(*args, **kwargs):
    """Compatibility shim for lazy delegate LM loading."""
    _, _, delegate_loader, _ = _runtime_config_helpers()
    return delegate_loader(*args, **kwargs)


def get_delegate_small_lm_from_env(*args, **kwargs):
    """Compatibility shim for lazy small delegate LM loading."""
    _, _, _, delegate_small_loader = _runtime_config_helpers()
    return delegate_small_loader(*args, **kwargs)


def resolve_runtime_config(
    config: AppConfig | None = None,
) -> AppConfig:
    """Resolve the runtime config, loading `.env` when needed."""
    if config is not None:
        return config

    env_path = resolve_env_path(
        start_paths=[
            Path(__file__).resolve().parent,
            Path.cwd(),
        ]
    )
    app_env = (os.getenv("APP_ENV") or "local").strip().lower()
    load_dotenv(dotenv_path=str(env_path), override=app_env == "local")
    return AppConfig(env_path=env_path)


def prime_runtime_env(cfg: AppConfig) -> None:
    """Load configured .env into process env before runtime initialization."""
    load_dotenv(
        dotenv_path=str(cfg.env_path),
        override=cfg.app_env == "local",
    )


def build_server_state(cfg: AppConfig) -> ServerState:
    """Build initialized in-memory server state container.

    Constructs focused dependency slices individually and composes them into
    the backward-compatible ServerState wrapper.
    """
    from .dependencies import InterpreterPoolDeps
    from .runtime_services.interpreter_pool import InterpreterPool

    config_deps = ConfigDeps(config=cfg)
    lm_deps = LmDeps()
    auth_deps = AuthDeps(
        auth_provider=build_auth_provider(
            auth_mode="neon" if cfg.auth_required else "dev",
            dev_jwt_secret=cfg.dev_jwt_secret,
            neon_tenant_claim=cfg.neon_tenant_claim,
        ),
    )
    ws_ticket_deps = WebSocketTicketDeps()
    session_cache_deps = SessionCacheDeps()
    persistence_deps = PersistenceDeps()
    diagnostics_deps = DiagnosticsDeps(
        events_event_emitter=ExecutionEventEmitter(
            max_queue=cfg.ws_execution_max_queue,
            drop_policy=cfg.ws_execution_drop_policy,
        ),
    )
    interpreter_pool_deps = InterpreterPoolDeps(pool=InterpreterPool(cfg))

    # Compose into the backward-compatible ServerState wrapper.
    state = ServerState.__new__(ServerState)
    state.config_deps = config_deps
    state.lm_deps = lm_deps
    state.auth_deps = auth_deps
    state.ws_ticket_deps = ws_ticket_deps
    state.session_cache_deps = session_cache_deps
    state.persistence_deps = persistence_deps
    state.diagnostics_deps = diagnostics_deps
    state.interpreter_pool_deps = interpreter_pool_deps
    return state


def attach_server_state(app: FastAPI, state: ServerState) -> None:
    """Attach server state and focused dependency slices to a FastAPI app."""
    app.state.server_state = state
    app.state.config_deps = state.config_deps
    app.state.lm_deps = state.lm_deps
    app.state.auth_deps = state.auth_deps
    app.state.ws_ticket_deps = state.ws_ticket_deps
    app.state.session_cache_deps = state.session_cache_deps
    app.state.persistence_deps = state.persistence_deps
    app.state.diagnostics_deps = state.diagnostics_deps
    app.state.interpreter_pool_deps = state.interpreter_pool_deps


async def initialize_persistence(persistence_deps: PersistenceDeps, cfg: AppConfig) -> None:
    """Initialize persistence paths based on runtime config."""
    from fleet_rlm.integrations.local_store import LocalStore

    if cfg.database_url:
        db_manager = DatabaseManager(cfg.database_url, echo=cfg.db_echo)
        if cfg.db_validate_on_startup or cfg.database_required:
            try:
                await db_manager.ping()
            except (TimeoutError, OSError, Exception) as exc:
                if cfg.database_required:
                    raise RuntimeError(
                        f"DATABASE_URL is set and database_required=true, but the database is unreachable: {exc}"
                    ) from exc
                # Graceful fallback for local development
                logger.warning(
                    "Database ping failed (%s: %s); falling back to local-only "
                    "persistence. Unset DATABASE_URL or fix connectivity to silence "
                    "this warning.",
                    type(exc).__name__,
                    exc,
                )
                await db_manager.dispose()
                persistence_deps.local_store = LocalStore()
                return
        persistence_deps.db_manager = db_manager
        persistence_deps.repository = FleetRepository(db_manager)

        # Auto-seed the NEON_TENANT_CLAIM on startup
        if cfg.neon_tenant_claim:
            try:
                tenant = await persistence_deps.repository.resolve_tenant_by_entra_claim(
                    entra_tenant_id=cfg.neon_tenant_claim
                )
                if tenant is None:
                    logger.info(
                        "Auto-seeding tenant '%s' for Neon auth...",
                        cfg.neon_tenant_claim,
                    )
                    await persistence_deps.repository.upsert_tenant(
                        entra_tenant_id=cfg.neon_tenant_claim,
                        slug=cfg.neon_tenant_claim.lower(),
                        display_name=f"{cfg.neon_tenant_claim.title()} Tenant",
                    )
            except Exception as exc:
                logger.warning(
                    "Could not auto-seed tenant '%s' on startup: %s",
                    cfg.neon_tenant_claim,
                    exc,
                )

        persistence_deps.local_store = LocalStore()
        return

    if cfg.database_required:
        raise RuntimeError("DATABASE_URL is required when database_required=true")

    persistence_deps.local_store = LocalStore()
    logger.info(
        "runtime_persistence_local",
        extra={
            "database_required": cfg.database_required,
            "app_env": cfg.app_env,
        },
    )


def initialize_lms(lm_deps: LmDeps, config_deps: ConfigDeps) -> None:
    """Load planner/delegate LMs into process state."""
    cfg = config_deps.config
    _sync_llm_model_config_from_env(cfg)
    configure_posthog, _, _, _ = _runtime_config_helpers()
    configure_posthog()
    model_name = cfg.agent_model
    if model_name is None:
        lm_deps.planner_lm = get_planner_lm_from_env(env_file=cfg.env_path)
    else:
        lm_deps.planner_lm = get_planner_lm_from_env(
            env_file=cfg.env_path,
            model_name=model_name,
        )
    lm_deps.delegate_lm = get_delegate_lm_from_env(
        env_file=cfg.env_path,
        model_name=cfg.agent_delegate_model,
        default_max_tokens=cfg.agent_delegate_max_tokens,
    )
    lm_deps.delegate_small_lm = get_delegate_small_lm_from_env(
        env_file=cfg.env_path,
        model_name=cfg.agent_delegate_small_model,
        default_max_tokens=cfg.agent_delegate_max_tokens,
    )


async def ensure_runtime_models(
    lm_deps: LmDeps,
    config_deps: ConfigDeps,
    diagnostics_deps: DiagnosticsDeps,
) -> tuple[object | None, object | None]:
    """Initialize planner/delegate LMs on demand without blocking server startup."""
    if lm_deps.planner_lm is not None:
        return lm_deps.planner_lm, lm_deps.delegate_lm

    async with lm_deps.runtime_model_lock:
        if lm_deps.planner_lm is not None:
            return lm_deps.planner_lm, lm_deps.delegate_lm

        try:
            await asyncio.to_thread(initialize_lms, lm_deps, config_deps)
        except Exception as exc:
            set_optional_service_status(
                diagnostics_deps,
                "planner_lm",
                "degraded",
                error=str(exc),
            )
            set_optional_service_status(
                diagnostics_deps,
                "delegate_lm",
                "degraded",
                error=str(exc),
            )
            raise

        set_optional_service_status(
            diagnostics_deps,
            "planner_lm",
            "ready" if lm_deps.planner_lm is not None else "missing",
        )
        set_optional_service_status(
            diagnostics_deps,
            "delegate_lm",
            "ready" if lm_deps.delegate_lm is not None else "missing",
        )
        return lm_deps.planner_lm, lm_deps.delegate_lm


async def _initialize_mlflow_runtime(
    state: ServerState,
) -> None:
    await initialize_mlflow_runtime_service(
        state.diagnostics_deps,
        app_env=state.config_deps.config.app_env,
    )


async def _initialize_posthog_runtime(
    state: ServerState,
) -> None:
    await initialize_posthog_runtime_service(
        state.diagnostics_deps,
        app_env=state.config_deps.config.app_env,
        database_required=state.config_deps.config.database_required,
    )


async def _warm_optional_runtime_services(
    state: ServerState,
) -> None:
    for service_name, initializer in (
        ("mlflow", _initialize_mlflow_runtime),
        ("posthog", _initialize_posthog_runtime),
    ):
        try:
            await initializer(state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("%s optional startup failed", service_name, exc_info=True)
            set_optional_service_status(state.diagnostics_deps, service_name, "degraded", error=str(exc))

    try:
        await ensure_runtime_models(
            state.lm_deps,
            state.config_deps,
            state.diagnostics_deps,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Runtime model warmup failed", exc_info=True)


def schedule_optional_runtime_startup(
    state: ServerState,
) -> asyncio.Task[None]:
    """Start optional runtime warmup in the background and keep the task on state."""
    task = asyncio.create_task(
        _warm_optional_runtime_services(state),
        name="fleet-optional-startup",
    )
    state.diagnostics_deps.optional_startup_task = task
    return task


async def cancel_optional_runtime_startup(state: ServerState) -> None:
    """Cancel the current optional startup task, if it is still running."""
    optional_task = state.diagnostics_deps.optional_startup_task
    state.diagnostics_deps.optional_startup_task = None
    if optional_task is None or optional_task.done():
        return
    optional_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await optional_task


async def startup_server_state(state: ServerState) -> None:
    """Run startup initialization for server state and runtime services."""
    cfg = state.config_deps.config

    prime_runtime_env(cfg)
    _sync_llm_model_config_from_env(cfg)

    await initialize_persistence(state.persistence_deps, cfg)

    if state.persistence_deps.db_manager is None:
        from fleet_rlm.integrations.llm_profiles.maintenance import repair_persisted_llm_profiles
        from fleet_rlm.integrations.llm_profiles.store import resolve_profile_store

        try:
            await repair_persisted_llm_profiles(
                resolve_profile_store(state.persistence_deps.db_manager),
                env_path=cfg.env_path,
            )
            _sync_llm_model_config_from_env(cfg)
        except Exception:
            logger.warning("LLM profile repair failed during startup", exc_info=True)

    # Start the warm interpreter pool (non-fatal if Daytona is unconfigured).
    pool = state.interpreter_pool_deps.pool
    if pool is not None:
        try:
            await pool.start()
        except Exception:
            logger.warning("Interpreter pool startup failed; requests will cold-start", exc_info=True)

    schedule_optional_runtime_startup(state)


async def recover_stale_optimization_runs(state: ServerState) -> None:
    """Mark optimization runs orphaned by a prior server restart as stale."""
    try:
        if state.persistence_deps.repository is not None:
            recovered = await state.persistence_deps.repository.recover_stale_optimization_runs()
        else:
            from fleet_rlm.integrations.local_store import (
                recover_stale_optimization_runs as recover_local_stale_runs,
            )

            recovered = recover_local_stale_runs()
        if recovered:
            logger.info("Recovered %d stale optimization run(s) on startup", recovered)
    except Exception:
        logger.warning(
            "Stale optimization run recovery failed; some runs may remain in 'running' state",
            exc_info=True,
        )


async def shutdown_server_state(state: ServerState) -> None:
    """Tear down runtime services and persistence resources."""

    # Drain the interpreter pool before tearing down other services.
    pool = state.interpreter_pool_deps.pool
    if pool is not None:
        try:
            await pool.drain()
        except Exception:
            logger.warning("Interpreter pool drain failed", exc_info=True)

    await cancel_optional_runtime_startup(state)

    state.lm_deps.planner_lm = None
    state.lm_deps.delegate_lm = None
    state.lm_deps.delegate_small_lm = None
    from fleet_rlm.integrations.observability.client import shutdown_posthog_client
    from fleet_rlm.integrations.observability.mlflow_runtime import shutdown_mlflow

    shutdown_mlflow()
    shutdown_posthog_client()

    mlflow_proc = state.diagnostics_deps.mlflow_server_process
    if mlflow_proc is not None:
        # Clear the reference on state before attempting shutdown.
        state.diagnostics_deps.mlflow_server_process = None
        logger.info(
            "Stopping MLflow tracking server (pid=%d)...",
            mlflow_proc.pid,
        )
        terminate_process(mlflow_proc)

    if state.persistence_deps.db_manager is not None:
        await state.persistence_deps.db_manager.dispose()
    state.persistence_deps.db_manager = None
    state.persistence_deps.repository = None
