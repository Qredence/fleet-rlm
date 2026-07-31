"""FastAPI application factory for the canonical Fleet RLM backend."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from . import __version__
from .config import Settings, configure_logging, load_runtime_settings

_RETIRED_ENVIRONMENT_VARIABLES = frozenset(
    {
        "FLEET_LIVE_KERNEL",
        "FLEET_UPLOAD_ROOT",
        "FLEET_ARTIFACT_ROOT",
        "FLEET_MAX_TURN_WALL_SECONDS",
        "FLEET_BUDGET_MAX_ITERATIONS",
        "FLEET_BUDGET_MAX_LLM_CALLS",
        "FLEET_BUDGET_MAX_OUTPUT_CHARS",
        "FLEET_BUDGET_MAX_WALL_SECONDS",
        "FLEET_BUDGET_MAX_SUB_LM_CONCURRENCY",
        "FLEET_BUDGET_MAX_TOOL_CALLS",
        "FLEET_BUDGET_MAX_SKILL_LOADS",
    }
)


def _reject_retired_environment_variables() -> None:
    configured = set(_RETIRED_ENVIRONMENT_VARIABLES.intersection(os.environ))
    env_file = Path(".env")
    if env_file.is_file():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name = line.split("=", 1)[0].removeprefix("export ").strip()
            if name in _RETIRED_ENVIRONMENT_VARIABLES:
                configured.add(name)
    if configured:
        names = ", ".join(sorted(configured))
        raise ValueError(f"retired Fleet environment variable(s): {names}")


@asynccontextmanager
async def _local_db_lifespan(
    app: FastAPI,
    settings_obj: Settings,
    install_fn: Callable[..., Any],
) -> AsyncIterator[None]:
    from fleet_rlm.composition import clear_composition_state

    engine = None
    session_factory = None
    try:
        if settings_obj.database_url:
            from fleet_rlm.persistence.database import (
                create_async_engine_from_url,
                create_session_factory,
                create_tables,
                is_sqlite_url,
            )

            engine = create_async_engine_from_url(settings_obj.database_url)
            app.state.db_engine = engine
            session_factory = create_session_factory(engine)
            if is_sqlite_url(settings_obj.database_url):
                await create_tables(engine)
        handles = install_fn(app, settings_obj, session_factory=session_factory)
        turn_state = getattr(app.state, "turn_state_store", None)
        reconcile = getattr(turn_state, "reconcile_settling", None)
        if callable(reconcile):
            await reconcile()
        yield
    finally:
        cleanup = getattr(locals().get("handles", None), "turn_cleanup_supervisor", None)
        if cleanup is not None:
            await cleanup.shutdown(drain_seconds=30)
        clear_composition_state(app)
        try:
            if engine is not None:
                await engine.dispose()
        finally:
            app.state.db_engine = None


def create_app(
    *,
    settings: Settings | None = None,
    _composition_installer: Callable[..., Any] | None = None,
) -> FastAPI:
    """
    Create and configure the Fleet RLM FastAPI application.

    Parameters:
        settings (Settings | None): Optional runtime settings. When omitted, settings are loaded from the environment.
        _composition_installer (Callable[..., Any] | None): Optional composition installer used for local
            database-backed application lifecycles.

    Returns:
        FastAPI: The configured application instance.
    """
    _reject_retired_environment_variables()
    resolved = settings if settings is not None else load_runtime_settings()
    configure_logging(resolved)

    from fleet_rlm.observability.tracing import configure_tracing

    configure_tracing(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """
        Manage application startup and shutdown for the configured execution environment.

        Initializes the selected composition before yielding control to the application and
        performs composition cleanup and tracing flushes during shutdown.
        """
        settings_obj: Settings = app.state.settings
        if _composition_installer is not None:
            try:
                async with _local_db_lifespan(app, settings_obj, _composition_installer):
                    yield
            finally:
                from fleet_rlm.observability.tracing import flush_tracing

                flush_tracing()
            return

        if settings_obj.run_environment == "daytona":
            from fleet_rlm.composition import (
                dispose_daytona_composition,
                install_daytona_composition,
                require_daytona_settings,
            )

            require_daytona_settings(settings_obj)
            installed = False
            try:
                await install_daytona_composition(app, settings_obj)
                installed = True
                yield
            finally:
                try:
                    if installed:
                        await dispose_daytona_composition(app)
                finally:
                    from fleet_rlm.observability.tracing import flush_tracing

                    flush_tracing()
            return

        if settings_obj.run_environment == "deno":
            from fleet_rlm.composition import install_deno_composition, require_deno_settings

            require_deno_settings(settings_obj)
            try:
                async with _local_db_lifespan(app, settings_obj, install_deno_composition):
                    yield
            finally:
                from fleet_rlm.observability.tracing import flush_tracing

                flush_tracing()
            return

    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.composition_ready = False
    app.state.db_engine = None
    from fleet_rlm.composition.common import COMPOSITION_STATE_FIELDS

    for name in COMPOSITION_STATE_FIELDS:
        setattr(app.state, name, None)

    from fleet_rlm.api.errors import install_error_handlers
    from fleet_rlm.api.openapi import install_openapi_contract

    install_error_handlers(app)
    install_openapi_contract(app)

    from fleet_rlm.api.routes.artifacts import router as artifacts_router
    from fleet_rlm.api.routes.attachments import router as attachments_router
    from fleet_rlm.api.routes.runs import router as runs_router
    from fleet_rlm.api.routes.sessions import router as sessions_router
    from fleet_rlm.api.routes.settings import router as settings_router
    from fleet_rlm.api.routes.skills import router as skills_router
    from fleet_rlm.api.routes.turns import router as turns_router
    from fleet_rlm.api.routes.volume import router as volume_router
    from fleet_rlm.api.routes.workspace_files import router as workspace_files_router

    app.include_router(turns_router)
    app.include_router(sessions_router)
    app.include_router(attachments_router)
    app.include_router(artifacts_router)
    app.include_router(skills_router)
    app.include_router(runs_router)
    app.include_router(settings_router)
    app.include_router(workspace_files_router)
    app.include_router(volume_router)

    from fleet_rlm.skills.catalog import build_bundled_skill_catalog

    app.state.skill_catalog = build_bundled_skill_catalog()
    return app
