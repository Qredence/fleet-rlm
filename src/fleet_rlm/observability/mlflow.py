"""Application-owned MLflow tracing lifecycle.

`tracing.py` owns configuration, sanitization, and MLflow API details. This
small lifecycle object owns one FastAPI lifespan's startup outcome and shutdown
without relying on a sticky process-global "configured" flag.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

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
            if self._state == MLflowRuntimeState.ACTIVE:
                if self._flush is None:
                    from fleet_rlm.observability.tracing import flush_tracing

                    flush = flush_tracing
                else:
                    flush = self._flush
                await asyncio.to_thread(flush)
        except Exception:
            logger.warning("MLflow tracing shutdown failed; continuing FastAPI shutdown", exc_info=True)
        finally:
            self._state = MLflowRuntimeState.CLOSED
