"""Application-owned MLflow tracing lifecycle.

`tracing.py` owns configuration, sanitization, and MLflow API details. This
small lifecycle object owns one FastAPI lifespan's startup outcome and shutdown
without relying on a sticky process-global "configured" flag.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Thread

from fleet_rlm.config.settings import FleetConfigurationError, Settings

logger = logging.getLogger(__name__)


class MLflowRuntimeState(StrEnum):
    """Explicit startup lifecycle for one application lifespan."""

    INACTIVE = "inactive"
    STARTING = "starting"
    ACTIVE = "active"
    UNAVAILABLE = "unavailable"
    CLOSED = "closed"


@dataclass(slots=True)
class MLflowRuntime:
    """Own one application lifespan's MLflow startup and shutdown."""

    _settings: Settings
    _configure: Callable[[Settings], bool] | None = None
    _flush: Callable[[], None] | None = None
    _state: MLflowRuntimeState = MLflowRuntimeState.INACTIVE
    _flush_future: Future[None] | None = field(default=None, init=False, repr=False)

    @property
    def flush_pending(self) -> bool:
        """A bounded close may leave SDK export work retained by this owner."""
        return self._flush_future is not None and not self._flush_future.done()

    @property
    def state(self) -> MLflowRuntimeState:
        return self._state

    @property
    def active(self) -> bool:
        return self._state == MLflowRuntimeState.ACTIVE

    async def start(self) -> None:
        """Attempt tracing configuration once for this application lifespan."""
        if self._state in {MLflowRuntimeState.ACTIVE, MLflowRuntimeState.STARTING}:
            return
        if self.flush_pending:
            self._state = MLflowRuntimeState.UNAVAILABLE
            return
        self._flush_future = None
        self._state = MLflowRuntimeState.STARTING
        try:
            if self._configure is None:
                from fleet_rlm.observability.tracing import configure_tracing

                configure = configure_tracing
            else:
                configure = self._configure
            active = await asyncio.to_thread(configure, self._settings)
        except FleetConfigurationError:
            self._state = MLflowRuntimeState.UNAVAILABLE
            raise
        except Exception:
            self._state = MLflowRuntimeState.UNAVAILABLE
            logger.warning("MLflow tracing startup failed; continuing without traces", exc_info=True)
            return
        self._state = MLflowRuntimeState.ACTIVE if active else MLflowRuntimeState.UNAVAILABLE

    async def close(self) -> None:
        """Flush tracing only after a successful startup, then release the lifespan."""
        try:
            if self._state == MLflowRuntimeState.ACTIVE and self._flush_future is None:
                if self._flush is None:
                    from fleet_rlm.observability.tracing import flush_tracing

                    flush = flush_tracing
                else:
                    flush = self._flush
                future: Future[None] = Future()
                self._flush_future = future

                def run_flush() -> None:
                    try:
                        flush()
                    except BaseException:
                        # Retain only a safe outcome, never SDK exception text.
                        future.set_exception(RuntimeError("MLflow trace flush failed"))
                    else:
                        future.set_result(None)

                # The default asyncio executor is joined at loop shutdown and
                # would defeat the wait bound for a stalled exporter.
                Thread(target=run_flush, name="fleet-mlflow-flush", daemon=True).start()
            if self._flush_future is not None:
                waiter = asyncio.wrap_future(self._flush_future)
                waiter.add_done_callback(lambda done: None if done.cancelled() else done.exception())
                await asyncio.wait_for(
                    asyncio.shield(waiter),
                    timeout=self._settings.mlflow_trace_shutdown_seconds,
                )
        except TimeoutError:
            logger.warning("MLflow trace flush remains pending after bounded shutdown wait")
        except Exception:
            logger.warning("MLflow tracing shutdown failed; continuing FastAPI shutdown")
        finally:
            self._state = MLflowRuntimeState.CLOSED
