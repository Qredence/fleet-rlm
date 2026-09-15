"""FastAPI application factory for the canonical Fleet RLM backend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol

from fastapi import FastAPI

from fleet_rlm.observability.posthog import init_posthog, shutdown_posthog

from . import __version__
from .config.loader import configure_logging, load_runtime_settings, reject_retired_environment_variables
from .config.settings import Settings

if TYPE_CHECKING:
    from fleet_rlm.composition.inventory import RuntimeDatabaseLifecycle, RuntimeInventory


class _CompositionInstaller(Protocol):
    def __call__(
        self,
        app: FastAPI,
        settings: Settings,
        *,
        database: RuntimeDatabaseLifecycle,
    ) -> RuntimeInventory: ...


@asynccontextmanager
async def _local_db_lifespan(
    app: FastAPI,
    settings_obj: Settings,
    install_fn: _CompositionInstaller,
) -> AsyncIterator[None]:
    from fleet_rlm.composition.inventory import RuntimeDatabaseLifecycle, clear_runtime_inventory

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
            session_factory = create_session_factory(engine)
            if is_sqlite_url(settings_obj.database_url):
                await create_tables(engine)
        database = RuntimeDatabaseLifecycle(engine=engine, session_factory=session_factory)
        inventory = install_fn(app, settings_obj, database=database)
        run_state = inventory.run_state_store
        reconcile = getattr(run_state, "reconcile_settling", None)
        if callable(reconcile):
            from fleet_rlm.composition.inventory import no_provider_recovery_fence

            await reconcile(no_provider_recovery_fence)
        yield
    finally:
        detached = clear_runtime_inventory(app)
        from fleet_rlm.composition.inventory import close_inventory_services

        shutdown_error: BaseException | None = None
        service_close = await close_inventory_services(detached, drain_seconds=30)
        if service_close.first_error is not None:
            shutdown_error = service_close.first_error

        if detached is not None:
            try:
                await detached.database.aclose()
            except BaseException as exc:
                if shutdown_error is None:
                    shutdown_error = exc
        if engine is not None and (detached is None or detached.database.engine is not engine):
            try:
                await engine.dispose()
            except BaseException as exc:
                if shutdown_error is None:
                    shutdown_error = exc
        if shutdown_error is not None:
            raise shutdown_error


def create_app(
    *,
    settings: Settings | None = None,
    _composition_installer: _CompositionInstaller | None = None,
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
    # The certified-DSPy runtime guard runs before any other startup work so a
    # rejected runtime can never reach provider, database, or Daytona resource
    # construction, nor bind a public listener.
    from fleet_rlm.rlm.compat_3_3_1 import assert_dspy_version

    assert_dspy_version()
    reject_retired_environment_variables()
    resolved = settings if settings is not None else load_runtime_settings()
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """
        Manage application resources for the duration of a FastAPI application lifespan.

        Parameters:
            app (FastAPI): Application instance whose runtime state and composition are initialized.

        Raises:
            RuntimeError: If the configured runtime environment is unsupported.
        """
        from fleet_rlm.observability.mlflow import MLflowRuntime

        settings_obj: Settings = app.state.settings
        mlflow_runtime = app.state.mlflow_runtime
        if not isinstance(mlflow_runtime, MLflowRuntime):
            mlflow_runtime = MLflowRuntime(settings_obj)
            app.state.mlflow_runtime = mlflow_runtime

        try:
            init_posthog(settings_obj)
            await mlflow_runtime.start()
            try:
                if _composition_installer is not None:
                    async with _local_db_lifespan(app, settings_obj, _composition_installer):
                        yield
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
                        if installed:
                            await dispose_daytona_composition(app)
                    return

                raise RuntimeError("Fleet only supports the Daytona runtime")
            finally:
                await mlflow_runtime.close()
        finally:
            shutdown_posthog()

    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.composition_ready = False
    app.state.runtime_inventory = None
    from fleet_rlm.observability.feedback import TraceFeedbackService
    from fleet_rlm.observability.mlflow import MLflowRuntime

    app.state.mlflow_runtime = MLflowRuntime(resolved)
    app.state.trace_feedback_service = TraceFeedbackService()

    from fleet_rlm.api.errors import install_error_handlers
    from fleet_rlm.api.openapi import install_openapi_contract

    install_error_handlers(app)
    install_openapi_contract(app)

    from fleet_rlm.api.routes.artifacts import router as artifacts_router
    from fleet_rlm.api.routes.attachments import router as attachments_router
    from fleet_rlm.api.routes.health import router as health_router
    from fleet_rlm.api.routes.runs import router as runs_router
    from fleet_rlm.api.routes.sessions import router as sessions_router
    from fleet_rlm.api.routes.settings import router as settings_router
    from fleet_rlm.api.routes.skills import router as skills_router
    from fleet_rlm.api.routes.traces import router as traces_router
    from fleet_rlm.api.routes.turns import router as turns_router
    from fleet_rlm.api.routes.volume import router as volume_router
    from fleet_rlm.api.routes.workspace_files import router as workspace_files_router

    app.include_router(turns_router)
    app.include_router(traces_router)
    app.include_router(sessions_router)
    app.include_router(attachments_router)
    app.include_router(artifacts_router)
    app.include_router(skills_router)
    app.include_router(runs_router)
    app.include_router(settings_router)
    app.include_router(workspace_files_router)
    app.include_router(volume_router)
    app.include_router(health_router)

    from fleet_rlm.skills.catalog import build_bundled_skill_catalog

    app.state.skill_catalog = build_bundled_skill_catalog()
    return app
