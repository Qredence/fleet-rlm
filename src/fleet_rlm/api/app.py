"""The FastAPI application: lifespan resources, routers, and the health route."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import mlflow
from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fleet_rlm.api.routes import experiments, sessions, setup, turns, workspace
from fleet_rlm.api.schemas import Health
from fleet_rlm.config import FleetSettings, get_settings
from fleet_rlm.experiments import ExperimentManager
from fleet_rlm.launcher import mlflow_is_healthy
from fleet_rlm.observability import configure_mlflow_observability
from fleet_rlm.session import SessionRuntimeRegistry
from fleet_rlm.store import Store

VERSION = version("fleet-rlm")


def create_app(
    *,
    settings: FleetSettings | None = None,
    initialize_db: bool = True,
    configure_observability: bool = True,
    store: Store | None = None,
    setup_mode: bool = False,
    setup_token: str = "",
    project_dir: Path | None = None,
    setup_backend: Literal["local", "databricks"] = "local",
) -> FastAPI:
    """Build the API. External resources are acquired by the lifespan, not at construction."""
    resolved = None if setup_mode else settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
        """Yield with API resources available, releasing acquired resources on exit or startup failure.

        Join experiment workers before closing the Turn executor, Session registry, and
        owned Store. Setup mode yields without acquiring runtime resources; an injected
        Store remains owned by the caller. Startup and cleanup errors propagate.
        """
        async with AsyncExitStack() as resources:
            if application.state.setup_service is not None:
                resources.push_async_callback(asyncio.to_thread, application.state.setup_service.close)
            if setup_mode:
                yield
                return
            assert resolved is not None

            async def flush_traces() -> None:
                """Flush pending traces when tracing was initialized, suppressing flush errors."""
                if application.state.mlflow_experiment_id:
                    with suppress(Exception):
                        await asyncio.to_thread(mlflow.flush_trace_async_logging)

            resources.push_async_callback(flush_traces)
            # Acquire in dependency order; register each release before the next operation can fail.
            application.state.store = store or (
                Store.open(
                    resolved.fleet_database_url,
                    lakebase_endpoint=resolved.fleet_lakebase_endpoint,
                    migration_dsn=resolved.fleet_database_migration_url,
                    schema=resolved.fleet_database_schema,
                )
                if initialize_db
                else None
            )
            if application.state.store is not None and store is None:
                resources.push_async_callback(asyncio.to_thread, application.state.store.close)
            if initialize_db and application.state.store is not None:
                try:
                    await asyncio.to_thread(application.state.store.initialize)
                finally:
                    await asyncio.to_thread(application.state.store.close_migration_pool)
            application.state.mlflow_experiment_id = (
                await asyncio.to_thread(_configure_tracing, resolved) if configure_observability else None
            )
            application.state.session_registry = SessionRuntimeRegistry(resolved, application.state.store)
            resources.push_async_callback(asyncio.to_thread, application.state.session_registry.close)
            application.state.turn_executor = turns.create_turn_executor()
            resources.callback(application.state.turn_executor.shutdown, wait=False, cancel_futures=True)
            if application.state.store is not None:
                application.state.experiments = ExperimentManager(resolved, application.state.store)
                resources.push_async_callback(application.state.experiments.close)
                if initialize_db:
                    await application.state.experiments.initialize()
            yield

    app = FastAPI(title="Fleet-RLM Lite API", version=VERSION, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        """Do not echo setup credential input in validation error responses."""
        if request.url.path.startswith("/setup/"):
            return JSONResponse(
                status_code=422, content={"detail": "Invalid setup request; check the submitted fields."}
            )
        return await request_validation_exception_handler(request, exc)

    app.state.settings = resolved
    app.state.store = store
    app.state.session_registry = None
    app.state.turn_executor = None
    app.state.mlflow_experiment_id = None
    app.state.experiments = None
    app.state.setup_token = setup_token
    app.state.setup_applying = False
    app.state.setup_apply_lock = asyncio.Lock()
    app.state.setup_service = None
    if setup_token:
        from fleet_rlm.setup.service import SetupService

        app.state.setup_service = SetupService(project_dir or Path.cwd(), setup_backend)
    app.include_router(setup.router)

    @app.get("/health", tags=["health"])
    def health() -> Health:
        """Return the API version and setup status or MLflow configuration.

        MLflow readiness reflects whether an experiment ID was initialized.
        Databricks workspace identity errors propagate as ValueError.
        """
        if setup_mode:
            return Health(status="setup", version=VERSION, mlflow=None)
        assert resolved is not None
        return Health(
            status="ok",
            version=VERSION,
            mlflow={
                **resolved.mlflow_descriptor(),
                "experiment_id": app.state.mlflow_experiment_id,
                "ready": app.state.mlflow_experiment_id is not None,
            },
        )

    for router in (
        ()
        if setup_mode
        else (
            sessions.router,
            workspace.router,
            sessions.memory_router,
            sessions.skills_router,
            turns.router,
            experiments.router,
        )
    ):
        app.include_router(router)
    return app


def _configure_tracing(settings: FleetSettings) -> str:
    target = settings.mlflow_target()
    settings.prepare_mlflow_auth()
    if target.mode == "local" and not mlflow_is_healthy(target.tracking_uri):
        raise RuntimeError(
            f"Local MLflow is not reachable at {target.tracking_uri}; start it with `fleet cli` "
            "or start an MLflow server separately before using `fleet serve`."
        )
    try:
        experiment_id = configure_mlflow_observability(
            target.experiment_name,
            target.tracking_uri,
            trace_catalog=settings.mlflow_trace_catalog,
            trace_schema=settings.mlflow_trace_schema,
            trace_table_prefix=settings.mlflow_trace_table_prefix,
            tracing_sql_warehouse_id=settings.mlflow_tracing_sql_warehouse_id,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Could not initialize {target.mode} MLflow experiment {target.experiment_name!r} "
            f"({type(exc).__name__}); check tracking access and experiment configuration."
        ) from None
    if not experiment_id:
        raise RuntimeError("MLflow initialization did not return an experiment ID")
    return experiment_id
