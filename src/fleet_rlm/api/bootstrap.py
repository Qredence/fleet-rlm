"""Runtime bootstrap helpers for the FastAPI server."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from fleet_rlm.integrations.config.runtime_settings import resolve_env_path
from fleet_rlm.integrations.database import DatabaseManager, FleetRepository

from .auth import build_auth_provider
from .config import ServerRuntimeConfig
from .dependencies import ServerState
from .events import ExecutionEventEmitter
from .bootstrap_observability import (
    initialize_mlflow_runtime_service,
    initialize_posthog_runtime_service,
    set_optional_service_status,
    terminate_process,
)

logger = logging.getLogger(__name__)


def _runtime_config_helpers():
    from fleet_rlm.runtime.config import (
        configure_posthog_analytics_from_env,
        get_delegate_lm_from_env,
        get_planner_lm_from_env,
    )

    return (
        configure_posthog_analytics_from_env,
        get_planner_lm_from_env,
        get_delegate_lm_from_env,
    )


def get_planner_lm_from_env(*args, **kwargs):
    """Compatibility shim for lazy planner LM loading."""
    _, planner_loader, _ = _runtime_config_helpers()
    return planner_loader(*args, **kwargs)


def get_delegate_lm_from_env(*args, **kwargs):
    """Compatibility shim for lazy delegate LM loading."""
    _, _, delegate_loader = _runtime_config_helpers()
    return delegate_loader(*args, **kwargs)


def resolve_runtime_config(
    config: ServerRuntimeConfig | None = None,
) -> ServerRuntimeConfig:
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
    return ServerRuntimeConfig(env_path=env_path)


def prime_runtime_env(cfg: ServerRuntimeConfig) -> None:
    """Load configured .env into process env before runtime initialization."""
    load_dotenv(
        dotenv_path=str(cfg.env_path),
        override=cfg.app_env == "local",
    )


def build_server_state(cfg: ServerRuntimeConfig) -> ServerState:
    """Build initialized in-memory server state container."""
    state = ServerState(
        config=cfg,
        execution_event_emitter=ExecutionEventEmitter(
            max_queue=cfg.ws_execution_max_queue,
            drop_policy=cfg.ws_execution_drop_policy,
        ),
    )
    state.runtime_test_results = {}
    state.auth_provider = build_auth_provider(
        auth_mode=cfg.auth_mode,
        dev_jwt_secret=cfg.dev_jwt_secret,
        allow_debug_auth=cfg.allow_debug_auth,
        allow_query_auth_tokens=cfg.allow_query_auth_tokens,
        entra_jwks_url=cfg.entra_jwks_url,
        entra_issuer_template=cfg.entra_issuer_template,
        entra_audience=cfg.entra_audience,
    )
    state.db_manager = None
    state.repository = None
    return state


async def initialize_persistence(state: ServerState, cfg: ServerRuntimeConfig) -> None:
    """Initialize persistence paths based on runtime config."""
    if cfg.database_url:
        db_manager = DatabaseManager(cfg.database_url, echo=cfg.db_echo)
        if cfg.db_validate_on_startup or cfg.database_required:
            await db_manager.ping()
        state.db_manager = db_manager
        state.repository = FleetRepository(db_manager)
        return

    if cfg.database_required:
        raise RuntimeError("DATABASE_URL is required when database_required=true")

    logger.warning(
        "runtime_persistence_disabled",
        extra={
            "database_required": cfg.database_required,
            "app_env": cfg.app_env,
        },
    )


def initialize_lms(state: ServerState) -> None:
    """Load planner/delegate LMs into process state."""
    cfg = state.config
    configure_posthog, _, _ = _runtime_config_helpers()
    configure_posthog()
    model_name = cfg.agent_model
    if model_name is None:
        state.planner_lm = get_planner_lm_from_env(env_file=cfg.env_path)
    else:
        state.planner_lm = get_planner_lm_from_env(
            env_file=cfg.env_path,
            model_name=model_name,
        )
    state.delegate_lm = get_delegate_lm_from_env(
        env_file=cfg.env_path,
        model_name=cfg.agent_delegate_model,
        default_max_tokens=cfg.agent_delegate_max_tokens,
    )


async def ensure_runtime_models(
    state: ServerState,
) -> tuple[object | None, object | None]:
    """Initialize planner/delegate LMs on demand without blocking server startup."""
    if state.planner_lm is not None:
        return state.planner_lm, state.delegate_lm

    async with state.runtime_model_lock:
        if state.planner_lm is not None:
            return state.planner_lm, state.delegate_lm

        try:
            await asyncio.to_thread(initialize_lms, state)
        except Exception as exc:
            set_optional_service_status(
                state,
                "planner_lm",
                "degraded",
                error=str(exc),
            )
            set_optional_service_status(
                state,
                "delegate_lm",
                "degraded",
                error=str(exc),
            )
            raise

        set_optional_service_status(
            state,
            "planner_lm",
            "ready" if state.planner_lm is not None else "missing",
        )
        set_optional_service_status(
            state,
            "delegate_lm",
            "ready" if state.delegate_lm is not None else "missing",
        )
        return state.planner_lm, state.delegate_lm


async def _initialize_mlflow_runtime(
    state: ServerState,
) -> None:
    await initialize_mlflow_runtime_service(
        state,
        app_env=state.config.app_env,
    )


async def _initialize_posthog_runtime(
    state: ServerState,
) -> None:
    await initialize_posthog_runtime_service(
        state,
        app_env=state.config.app_env,
        auth_mode=state.config.auth_mode,
        database_required=state.config.database_required,
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
            set_optional_service_status(state, service_name, "degraded", error=str(exc))

    try:
        await ensure_runtime_models(state)
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
    state.optional_startup_task = task
    return task


async def cancel_optional_runtime_startup(state: ServerState) -> None:
    """Cancel the current optional startup task, if it is still running."""
    optional_task = state.optional_startup_task
    state.optional_startup_task = None
    if optional_task is None or optional_task.done():
        return
    optional_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await optional_task


async def startup_server_state(state: ServerState) -> None:
    """Run startup initialization for server state and runtime services."""
    cfg = state.config

    prime_runtime_env(cfg)

    await initialize_persistence(state, cfg)
    schedule_optional_runtime_startup(state)


async def shutdown_server_state(state: ServerState) -> None:
    """Tear down runtime services and persistence resources."""

    await cancel_optional_runtime_startup(state)

    state.planner_lm = None
    state.delegate_lm = None
    from fleet_rlm.integrations.observability.client import shutdown_posthog_client
    from fleet_rlm.integrations.observability.mlflow_runtime import shutdown_mlflow

    shutdown_mlflow()
    shutdown_posthog_client()

    mlflow_proc = getattr(state, "mlflow_server_process", None)
    if mlflow_proc is not None:
        # Clear the reference on state before attempting shutdown.
        setattr(state, "mlflow_server_process", None)
        logger.info(
            "Stopping MLflow tracking server (pid=%d)...",
            mlflow_proc.pid,
        )
        terminate_process(mlflow_proc)

    if state.db_manager is not None:
        await state.db_manager.dispose()
    state.db_manager = None
    state.repository = None
