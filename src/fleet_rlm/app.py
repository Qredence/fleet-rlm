"""FastAPI application factory for the canonical Fleet RLM backend."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Protocol

from fastapi import FastAPI

from fleet_rlm.observability.posthog import init_posthog, shutdown_posthog

from . import __version__
from .config.loader import configure_logging, load_runtime_settings, reject_retired_environment_variables
from .config.settings import Settings

if TYPE_CHECKING:
    from fleet_rlm.app_services import RuntimeInventory


class _ServicesBuilder(Protocol):
    def __call__(self, app: FastAPI, settings: Settings) -> AbstractAsyncContextManager[RuntimeInventory]: ...


def create_app(
    *,
    settings: Settings | None = None,
    _services_builder: _ServicesBuilder | None = None,
) -> FastAPI:
    """
    Create and configure the Fleet RLM FastAPI application.

    Parameters:
        settings (Settings | None): Optional runtime settings. When omitted, settings are loaded from the environment.
        _services_builder: Private credential-free services builder used by tests.

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
                from fleet_rlm.app_services import clear_runtime_inventory, install_runtime_inventory

                if _services_builder is None:
                    if settings_obj.run_environment != "daytona":
                        raise RuntimeError("Fleet only supports the Daytona runtime")
                    from fleet_rlm.app_lifecycle import daytona_services

                    builder = daytona_services
                else:
                    builder = _services_builder
                async with builder(app, settings_obj) as services:
                    install_runtime_inventory(app, services)
                    try:
                        yield
                    finally:
                        clear_runtime_inventory(app)
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
    app.state.route_services = None
    from fleet_rlm.observability.feedback import TraceFeedbackService
    from fleet_rlm.observability.mlflow import MLflowRuntime

    app.state.mlflow_runtime = MLflowRuntime(resolved)
    app.state.trace_feedback_service = TraceFeedbackService()

    from fleet_rlm.api.errors import install_error_handlers
    from fleet_rlm.api.openapi import install_openapi_contract

    install_error_handlers(app)
    install_openapi_contract(app)

    from fleet_rlm.api.routes.files import (
        artifacts_router,
        attachments_router,
        volume_router,
        workspace_files_router,
    )
    from fleet_rlm.api.routes.health import (
        health_router,
        settings_router,
        skills_router,
    )
    from fleet_rlm.api.routes.sessions import (
        runs_router,
        sessions_router,
        traces_router,
    )
    from fleet_rlm.api.routes.turns import router as turns_router

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
