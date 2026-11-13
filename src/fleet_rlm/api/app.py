"""The FastAPI application: lifespan resources, routers, and the health route."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from importlib.metadata import version
from typing import Any

import mlflow
from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fleet_rlm.api.routes import improve, sessions, turns, workspace
from fleet_rlm.api.schemas import Health
from fleet_rlm.config import FleetSettings, get_settings
from fleet_rlm.experiments import ExperimentManager
from fleet_rlm.launcher import mlflow_is_healthy
from fleet_rlm.observability import MlflowTraceDestinationMismatchError, configure_mlflow_observability
from fleet_rlm.session import SessionRuntimeRegistry
from fleet_rlm.store import Store

VERSION = version("fleet-rlm")


def create_app(
    *,
    settings: FleetSettings | None = None,
    initialize_db: bool = True,
    configure_observability: bool = True,
    store: Store | None = None,
) -> FastAPI:
    """Build the API. External resources are acquired by the lifespan, not at construction."""
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
        """Yield with API resources available, releasing acquired resources on exit or startup failure.

        Join experiment workers before closing the Turn executor, Session registry, and
        owned Store. An injected Store remains owned by the caller. Startup and cleanup errors
        propagate.
        """
        async with AsyncExitStack() as resources:

            async def flush_traces() -> None:
                """Flush pending traces when tracing was initialized, suppressing flush errors."""
                if application.state.mlflow_experiment_id:
                    with suppress(Exception):
                        await asyncio.to_thread(mlflow.flush_trace_async_logging)

            resources.push_async_callback(flush_traces)
            # Acquire in dependency order; register each release before the next operation can fail.
            application.state.store = store or (
                await asyncio.to_thread(
                    Store.open,
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
            heartbeat = getattr(application.state.store, "heartbeat", None)
            if heartbeat is not None:
                heartbeat.register_idle_callback(application.state.session_registry.evict_idle)
                resources.callback(heartbeat.unregister_idle_callback, application.state.session_registry.evict_idle)
            application.state.turn_executor = turns.create_turn_executor()
            resources.callback(application.state.turn_executor.shutdown, wait=False, cancel_futures=True)
            if application.state.store is not None:
                application.state.experiments = ExperimentManager(resolved, application.state.store)
                resources.push_async_callback(application.state.experiments.close)
                if initialize_db:
                    await application.state.experiments.initialize()
            yield

    app = FastAPI(title="Fleet-RLM Lite API", version=VERSION, lifespan=lifespan)
    native_openapi = app.openapi

    def openapi_contract() -> dict[str, Any]:
        """Keep native SSE documentation valid under the declared OpenAPI 3.1 version."""
        schema = native_openapi()
        # FastAPI 0.162 adds the OpenAPI 3.2 Media Type itemSchema to SSE routes.
        # Only correct these media declarations; retain native components and framing.
        path = schema.get("paths", {}).get("/sessions/{session_id}/turn", {})
        for operation in path.values():
            if not isinstance(operation, dict):
                continue
            for response in operation.get("responses", {}).values():
                media = response.get("content", {}).get("text/event-stream")
                if media is not None:
                    media.pop("itemSchema", None)
                    media["schema"] = {"type": "string"}
        return schema

    app.openapi = openapi_contract  # ty: ignore[invalid-assignment] -- FastAPI's supported OpenAPI hook.

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        """Keep workspace content out of request validation responses."""
        if "/workspace/files/" in request.url.path:
            errors = exc.errors()
            if any(error.get("type") == "workspace_content_xor" for error in errors):
                # Preserve the existing XOR response without echoing file content.
                return JSONResponse(
                    status_code=422, content={"detail": "Supply exactly one of content or content_base64"}
                )
            safe = [{key: error[key] for key in ("type", "loc", "msg") if key in error} for error in errors]
            return JSONResponse(status_code=422, content={"detail": safe})
        return await request_validation_exception_handler(request, exc)

    app.state.settings = resolved
    app.state.store = store
    app.state.session_registry = None
    app.state.turn_executor = None
    app.state.mlflow_experiment_id = None
    app.state.experiments = None

    @app.get("/health", tags=["health"])
    def health() -> Health:
        """Return the API version and initialized MLflow configuration.

        MLflow readiness reflects whether an experiment ID was initialized.
        Databricks workspace identity errors propagate as ValueError.
        """
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
        sessions.router,
        workspace.router,
        sessions.memory_router,
        sessions.skills_router,
        turns.router,
        improve.router,
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
    except MlflowTraceDestinationMismatchError:
        raise RuntimeError(
            "Databricks MLflow trace destination mismatch: the experiment is already linked to a "
            "different Unity Catalog destination. Check FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG, "
            "FLEET_MLFLOW_DATABRICKS_TRACE_SCHEMA, and FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX, "
            "including .env overrides, and align them with the experiment's existing binding."
        ) from None
    except Exception as exc:
        raise RuntimeError(
            f"Could not initialize {target.mode} MLflow experiment {target.experiment_name!r} "
            f"({type(exc).__name__}); check tracking access and experiment configuration."
        ) from None
    if not experiment_id:
        raise RuntimeError("MLflow initialization did not return an experiment ID")
    return experiment_id
