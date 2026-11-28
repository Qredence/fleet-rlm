"""Manage each FastAPI worker’s optional connection to the local Docker MLflow tracker."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from concurrent.futures import Future
from contextlib import asynccontextmanager, suppress
from threading import Thread
from typing import Protocol

import mlflow
from mlflow.entities.trace_location import MlflowExperimentLocation
from mlflow.environment_variables import (
    MLFLOW_ENABLE_ASYNC_TRACE_LOGGING,
    MLFLOW_HTTP_REQUEST_MAX_RETRIES,
    MLFLOW_HTTP_REQUEST_TIMEOUT,
)

from .config import Settings
from .tracing import telemetry_operation


class TrackingRuntime(Protocol):
    @property
    def settings(self) -> Settings: ...

    mlflow_experiment_id: str | None
    mlflow_optimization_experiment_id: str | None
    tracking_pending: bool


_LOG = logging.getLogger(__name__)
_RETRY_SECONDS = 30
_SETUP_SECONDS = 6
_FLUSH_SECONDS = 3


def _background_call[T, **P](
    operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs
) -> asyncio.Future[T]:
    """A stuck optional SDK call must not delay asyncio's default executor shutdown."""
    result = Future[T]()

    def run() -> None:
        if result.set_running_or_notify_cancel():
            try:
                result.set_result(operation(*args, **kwargs))
            except BaseException as error:
                result.set_exception(error)

    Thread(target=run, name="fleet-mlflow", daemon=True).start()
    return asyncio.wrap_future(result)


def _experiment_id(settings: Settings, name: str) -> str:
    client = mlflow.MlflowClient(tracking_uri=settings.mlflow_tracking_uri)
    experiment = client.get_experiment_by_name(name)
    if experiment is None:
        return client.create_experiment(name)
    if experiment.lifecycle_stage != "active":
        raise ValueError("Tracking experiment is inactive")
    return experiment.experiment_id


async def _initialize(runtime: TrackingRuntime, optimizer: str) -> None:
    uri = runtime.settings.mlflow_tracking_uri
    if not uri:
        return
    await _initialize_experiment(runtime, uri, runtime.settings.mlflow_experiment, chat=True)
    await _initialize_experiment(runtime, uri, optimizer, chat=False)


async def _initialize_experiment(
    runtime: TrackingRuntime, uri: str, name: str, *, chat: bool
) -> None:
    degraded = False
    while True:
        # The lookup is scoped to its client. Only this task can publish its result.
        attempt = _background_call(_experiment_id, runtime.settings, name)
        try:
            async with asyncio.timeout(_SETUP_SECONDS):
                experiment = await asyncio.shield(attempt)
            if chat:
                mlflow.set_tracking_uri(uri)
                mlflow.tracing.set_destination(MlflowExperimentLocation(experiment))
                mlflow.dspy.autolog()
                runtime.mlflow_experiment_id = experiment
                runtime.tracking_pending = False
                _LOG.info("MLflow tracing ready")
            else:
                runtime.mlflow_optimization_experiment_id = experiment
            return
        except Exception as error:
            if not degraded:
                _LOG.warning(
                    "MLflow unavailable for experiment %s (%s)", name, type(error).__name__
                )
                degraded = True
            # A timeout does not kill a thread. Drain it before the next attempt.
            await asyncio.gather(attempt, return_exceptions=True)
            await asyncio.sleep(_RETRY_SECONDS)
        finally:
            if not attempt.done():
                attempt.cancel()


@asynccontextmanager
async def tracking_client(runtime: TrackingRuntime, optimizer: str) -> AsyncGenerator[None]:
    MLFLOW_HTTP_REQUEST_TIMEOUT.set(2)
    MLFLOW_HTTP_REQUEST_MAX_RETRIES.set(0)
    MLFLOW_ENABLE_ASYNC_TRACE_LOGGING.set(True)
    setup = asyncio.create_task(_initialize(runtime, optimizer))
    try:
        yield
    finally:
        setup.cancel()
        with suppress(asyncio.CancelledError):
            await setup
        if not runtime.tracking_pending:
            with telemetry_operation("flush"):
                await asyncio.wait_for(
                    _background_call(mlflow.flush_trace_async_logging), timeout=_FLUSH_SECONDS
                )
