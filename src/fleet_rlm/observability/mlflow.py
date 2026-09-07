"""Application-owned MLflow tracing lifecycle.

`tracing.py` owns configuration, sanitization, and MLflow API details. This
small lifecycle object owns one FastAPI lifespan's startup outcome, bounded
flush, and same-owner teardown of process-global tracing hooks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
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
    _reset: Callable[[], None] | None = None
    _state: MLflowRuntimeState = MLflowRuntimeState.INACTIVE
    _flush_future: Future[None] | None = field(default=None, init=False, repr=False)
    _owner_executor: ThreadPoolExecutor | None = field(default=None, init=False, repr=False)
    _reset_required: bool = field(default=False, init=False, repr=False)
    _reset_scheduled: bool = field(default=False, init=False, repr=False)

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

    async def _run_on_owner(self, function: Callable[..., object], *args: object) -> object:
        """Run MLflow/DSPy global-state operations on one stable owner thread."""
        if self._owner_executor is None:
            self._owner_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fleet-mlflow-owner")
        future = self._owner_executor.submit(function, *args)
        return await asyncio.wrap_future(future)

    def _shutdown_owner_executor(self) -> None:
        """Release the owner executor without waiting on unrelated exporter work."""
        executor = self._owner_executor
        self._owner_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _reset_after_flush_on_owner(self) -> None:
        """Remove Fleet's process-global tracing hooks after export has drained."""
        if not self._reset_required or self.flush_pending:
            return
        if self._reset is not None:
            reset = self._reset
        elif self._configure is None:
            from fleet_rlm.observability.tracing import reset_tracing

            reset = reset_tracing
        else:
            # Injected configuration functions in unit/contract tests do not
            # own the process-global MLflow/DSPy state.
            reset = None
        if reset is not None:
            reset_result = reset()
            if reset_result is False:
                raise RuntimeError("MLflow tracing cleanup was incomplete")
        self._reset_required = False

    async def _reset_after_flush(self) -> None:
        """Run process-global reset on the same owner as configuration."""
        await self._run_on_owner(self._reset_after_flush_on_owner)

    def _reset_completed(self, future: Future[None]) -> None:
        """Consume deferred reset errors and release the owner when cleanup finishes."""
        try:
            future.result()
        except Exception:
            logger.warning("MLflow tracing reset failed; retaining cleanup ownership")
        finally:
            self._reset_scheduled = False
            if not self._reset_required:
                self._shutdown_owner_executor()

    def _schedule_reset_after_flush(self) -> None:
        """Ensure a timed-out flush resets tracing once it later completes."""
        if not self._reset_required or self.flush_pending:
            return
        executor = self._owner_executor
        if executor is None:
            return
        self._reset_scheduled = True
        reset_future = executor.submit(self._reset_after_flush_on_owner)
        reset_future.add_done_callback(self._reset_completed)

    def _flush_completed(self, future: Future[None]) -> None:
        """Schedule same-owner teardown after retained background export finishes."""
        if not future.cancelled():
            with contextlib.suppress(Exception):
                future.exception()
        self._schedule_reset_after_flush()

    async def start(self) -> None:
        """Attempt tracing configuration once for this application lifespan."""
        if self._state in {MLflowRuntimeState.ACTIVE, MLflowRuntimeState.STARTING}:
            return
        if self.flush_pending:
            self._state = MLflowRuntimeState.UNAVAILABLE
            return
        if self._reset_required:
            self._state = MLflowRuntimeState.UNAVAILABLE
            return
        self._flush_future = None
        self._reset_scheduled = False
        self._state = MLflowRuntimeState.STARTING
        try:
            if self._configure is None:
                from fleet_rlm.observability.tracing import configure_tracing

                configure = configure_tracing
            else:
                configure = self._configure
            active = bool(await self._run_on_owner(configure, self._settings))
        except FleetConfigurationError:
            self._state = MLflowRuntimeState.UNAVAILABLE
            raise
        except Exception:
            self._state = MLflowRuntimeState.UNAVAILABLE
            logger.warning("MLflow tracing startup failed; continuing without traces")
            return
        self._state = MLflowRuntimeState.ACTIVE if active else MLflowRuntimeState.UNAVAILABLE
        self._reset_required = active

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
            # Normal shutdown performs reset inline after waiting. Only a
            # bounded timeout needs a retained completion callback.
            if self._flush_future is not None:
                self._reset_scheduled = True
                self._flush_future.add_done_callback(self._flush_completed)
        except Exception:
            logger.warning("MLflow tracing shutdown failed; continuing FastAPI shutdown")
        finally:
            if not self.flush_pending and self._reset_required and not self._reset_scheduled:
                try:
                    await self._reset_after_flush()
                except Exception:
                    logger.warning("MLflow tracing reset failed; retaining cleanup ownership")
            self._state = MLflowRuntimeState.CLOSED
            if self.flush_pending:
                # The completion callback retains ownership and will reset on
                # the configuration thread once the exporter actually drains.
                pass
            elif not self._reset_required:
                self._shutdown_owner_executor()
