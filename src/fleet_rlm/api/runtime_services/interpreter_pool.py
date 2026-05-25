"""Warm Daytona interpreter pool with pre-started sandboxes and brokers.

Maintains a configurable number of pre-warmed interpreters so incoming
requests avoid the full cold-start cost (sandbox creation + broker startup).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import ServerRuntimeConfig

logger = logging.getLogger(__name__)


class InterpreterPool:
    """Stateful warm interpreter pool with health monitoring.

    Pre-creates N interpreters at startup. Requests acquire a warm
    interpreter (O(ms)), use it, then release it back after a lightweight
    context reset. Overflow creates cold interpreters on demand.
    """

    def __init__(
        self,
        cfg: ServerRuntimeConfig,
        *,
        pool_size: int | None = None,
        overflow_max: int | None = None,
        acquire_timeout: float | None = None,
        health_interval: float | None = None,
    ) -> None:
        self._cfg = cfg
        self._pool_size = pool_size if pool_size is not None else cfg.interpreter_pool_size
        self._overflow_max = overflow_max if overflow_max is not None else cfg.interpreter_pool_overflow_max
        self._acquire_timeout = acquire_timeout if acquire_timeout is not None else cfg.interpreter_pool_acquire_timeout
        self._health_interval = health_interval if health_interval is not None else cfg.interpreter_pool_health_interval

        self._ready: asyncio.Queue[Any] = asyncio.Queue()
        self._in_use: set[int] = set()  # id() of in-use interpreters
        self._all: set[int] = set()  # id() of all managed interpreters
        self._all_refs: dict[int, Any] = {}  # id -> interpreter (prevent GC)

        self._health_task: asyncio.Task[None] | None = None
        self._replenish_event = asyncio.Event()
        self._replenish_task: asyncio.Task[None] | None = None
        self._draining = False
        self._started = False

    @property
    def size(self) -> int:
        """Number of interpreters currently managed (ready + in-use)."""
        return len(self._all)

    @property
    def available(self) -> int:
        """Number of interpreters ready for immediate acquire."""
        return self._ready.qsize()

    @property
    def in_use_count(self) -> int:
        """Number of interpreters currently checked out."""
        return len(self._in_use)

    async def start(self) -> None:
        """Pre-warm the pool by creating pool_size interpreters.

        Called during server lifespan startup. Non-fatal: logs warnings
        if some interpreters fail to create but continues with fewer.
        """
        if self._started:
            return
        self._started = True

        warmup_tasks = [
            asyncio.create_task(self._create_interpreter(), name=f"pool-warmup-{i}") for i in range(self._pool_size)
        ]
        results = await asyncio.gather(*warmup_tasks, return_exceptions=True)

        warmed = 0
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Pool warmup failed for one interpreter: %s", result)
            elif result is not None:
                self._register(result)
                await self._ready.put(result)
                warmed += 1

        logger.info(
            "Interpreter pool started: %d/%d warm interpreters ready",
            warmed,
            self._pool_size,
        )

        self._health_task = asyncio.create_task(self._health_check_loop(), name="pool-health")
        self._replenish_task = asyncio.create_task(self._replenish_loop(), name="pool-replenish")

    async def acquire(self, cfg: ServerRuntimeConfig | None = None) -> Any | None:
        """Get a ready interpreter from the pool.

        Tries in order:
        1. Return a pre-warmed interpreter from the ready queue (instant)
        2. Create an overflow interpreter if below overflow_max (cold start)
        3. Wait up to acquire_timeout for one to become available
        4. Create a cold interpreter as last resort

        Returns None if Daytona is not configured or all creation fails.
        """
        if self._draining:
            return None

        # 1. Try instant acquire from warm pool
        try:
            interp = self._ready.get_nowait()
            self._in_use.add(id(interp))
            return interp
        except asyncio.QueueEmpty:
            pass

        # 2. Create overflow if capacity allows
        if len(self._all) < self._overflow_max:
            interp = await self._create_interpreter()
            if interp is not None:
                self._register(interp)
                self._in_use.add(id(interp))
                return interp

        # 3. Wait for one to become available
        try:
            interp = await asyncio.wait_for(
                self._ready.get(),
                timeout=self._acquire_timeout,
            )
            self._in_use.add(id(interp))
            return interp
        except asyncio.TimeoutError:
            pass

        # 4. Last resort: create cold (even above overflow_max)
        logger.warning("Pool exhausted; creating cold interpreter above overflow_max")
        interp = await self._create_interpreter()
        if interp is not None:
            self._register(interp)
            self._in_use.add(id(interp))
        return interp

    async def release(self, interpreter: Any | None) -> None:
        """Return an interpreter to the pool after use.

        Performs a lightweight reset (clears REPL state, preserves sandbox
        and broker), then returns to the ready queue. If the interpreter
        is unhealthy or the pool is draining, destroys it instead.
        """
        if interpreter is None:
            return

        interp_id = id(interpreter)
        self._in_use.discard(interp_id)

        if self._draining:
            await self._destroy(interpreter)
            return

        try:
            areset = getattr(interpreter, "areset_for_pool", None)
            if callable(areset):
                await areset()
            await self._ready.put(interpreter)
        except Exception as exc:
            logger.warning("Pool release/reset failed, destroying interpreter: %s", exc)
            await self._destroy(interpreter)
            self._replenish_event.set()

    async def drain(self) -> None:
        """Graceful shutdown: wait for in-flight, then destroy all."""
        self._draining = True

        if self._health_task is not None:
            self._health_task.cancel()
        if self._replenish_task is not None:
            self._replenish_task.cancel()

        # Wait for in-flight interpreters (with timeout)
        drain_deadline = 60.0
        waited = 0.0
        while self._in_use and waited < drain_deadline:
            await asyncio.sleep(0.5)
            waited += 0.5

        if self._in_use:
            logger.warning("Drain timeout: %d interpreters still in use", len(self._in_use))

        # Destroy all remaining
        while not self._ready.empty():
            try:
                interp = self._ready.get_nowait()
                await self._destroy(interp)
            except asyncio.QueueEmpty:
                break

        # Force-destroy any still tracked
        for interp in list(self._all_refs.values()):
            await self._destroy(interp)

        self._all.clear()
        self._all_refs.clear()
        self._in_use.clear()
        logger.info("Interpreter pool drained")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _register(self, interpreter: Any) -> None:
        interp_id = id(interpreter)
        self._all.add(interp_id)
        self._all_refs[interp_id] = interpreter

    def _unregister(self, interpreter: Any) -> None:
        interp_id = id(interpreter)
        self._all.discard(interp_id)
        self._all_refs.pop(interp_id, None)
        self._in_use.discard(interp_id)

    async def _create_interpreter(self) -> Any | None:
        """Create a fresh DaytonaInterpreter with the pool's config."""
        cfg = self._cfg
        try:
            from fleet_rlm.integrations.daytona.config import DaytonaConfigError
            from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

            interpreter = DaytonaInterpreter(
                volume_name=cfg.volume_name,
                timeout=cfg.timeout,
                max_llm_calls=cfg.rlm_max_llm_calls,
                max_recursion_depth=cfg.rlm_max_depth,
                rlm_max_iterations=cfg.rlm_max_iterations,
                child_isolation_mode=cfg.rlm_child_isolation_mode,
                child_fork_fallback=cfg.rlm_child_fork_fallback,
                delegate_max_calls_per_turn=cfg.delegate_max_calls_per_turn,
                delegate_result_truncation_chars=cfg.delegate_result_truncation_chars,
                delegate_execution_timeout=cfg.delegate_execution_timeout,
                delegate_max_iterations=cfg.delegate_max_iterations,
                delegate_adapter=cfg.delegate_adapter,
                broker_health_timeout=cfg.daytona_broker_health_timeout,
                broker_tool_call_timeout=cfg.daytona_broker_tool_call_timeout,
                broker_start_retries=cfg.daytona_broker_start_retries,
                async_execute=cfg.interpreter_async_execute,
            )
            interpreter._host_repository = None
            interpreter._host_identity = None
            interpreter._host_run_id = None
            return interpreter
        except ImportError:
            return None
        except Exception as exc:
            logger.warning("Failed to create interpreter: %s", exc)
            return None

    async def _destroy(self, interpreter: Any) -> None:
        """Shutdown and unregister an interpreter."""
        self._unregister(interpreter)
        try:
            ashutdown = getattr(interpreter, "ashutdown", None)
            if callable(ashutdown):
                await ashutdown()
                return
            shutdown = getattr(interpreter, "shutdown", None)
            if callable(shutdown):
                await asyncio.to_thread(shutdown)
        except Exception as exc:
            logger.warning("Interpreter destroy failed: %s", exc)

    async def _is_healthy(self, interpreter: Any) -> bool:
        """Probe interpreter health via broker /health endpoint."""
        try:
            executor = getattr(interpreter, "_executor", None)
            if executor is None:
                return True  # Not yet used — sandbox should be alive
            bridge = getattr(executor, "_bridge", None)
            if bridge is None:
                return True  # No bridge yet — broker never started — OK
            broker_url = getattr(bridge, "_broker_url", None)
            if broker_url is None:
                return True  # Broker not initialized — fine
            return await asyncio.to_thread(bridge._check_health, broker_url)
        except Exception:
            return False

    async def _health_check_loop(self) -> None:
        """Periodically check idle interpreters and evict dead ones."""
        while not self._draining:
            try:
                await asyncio.sleep(self._health_interval)
            except asyncio.CancelledError:
                return
            if self._draining:
                return
            await self._run_health_check()

    async def _run_health_check(self) -> None:
        """Check all idle interpreters; evict unhealthy ones."""
        checked: list[Any] = []
        try:
            while not self._ready.empty():
                checked.append(self._ready.get_nowait())
        except asyncio.QueueEmpty:
            pass

        for interp in checked:
            if await self._is_healthy(interp):
                await self._ready.put(interp)
            else:
                logger.warning("Evicting unhealthy interpreter from pool")
                await self._destroy(interp)

        if len(self._all) < self._pool_size:
            self._replenish_event.set()

    async def _replenish_loop(self) -> None:
        """Background loop that replenishes the pool when below target size."""
        while not self._draining:
            try:
                await asyncio.wait_for(self._replenish_event.wait(), timeout=60.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if self._draining:
                    return
                continue
            self._replenish_event.clear()
            if self._draining:
                return
            while len(self._all) < self._pool_size and not self._draining:
                interp = await self._create_interpreter()
                if interp is None:
                    break
                self._register(interp)
                await self._ready.put(interp)
                logger.info("Pool replenished interpreter (now %d/%d)", len(self._all), self._pool_size)


__all__ = ["InterpreterPool"]
