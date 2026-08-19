"""Owned worker execution for one prepared native DSPy RLM."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine, Mapping
from typing import Any, Generic, TypeVar, cast

import dspy

from fleet_rlm.rlm.context import RLMExecutionContext
from fleet_rlm.rlm.errors import RLMConfigError
from fleet_rlm.runtime.owned_effect import OwnedEffect

T = TypeVar("T")

RLMWorkerExecution = Callable[[Any, RLMExecutionContext, Mapping[str, Any]], Coroutine[Any, Any, T]]


class WorkerOwnership:
    """Keep one started worker and its blocking resource waiters owned."""

    def __init__(self) -> None:
        self._effect: OwnedEffect[Any] | None = None
        self._blocking_waiters: list[Callable[[], None]] = []

    def attach(self, effect: OwnedEffect[Any]) -> None:
        """Attach the owned effect without exposing task mechanics."""
        self._effect = effect

    def add_blocking_waiter(self, waiter: Callable[[], None]) -> None:
        """Register synchronous resource ownership that outlives the RLM task."""
        self._blocking_waiters.append(waiter)

    async def wait_owned(self) -> None:
        """Wait for the worker and all blocking resource owners to settle."""
        if self._effect is not None:
            with contextlib.suppress(BaseException):
                await self._effect.settle()

        # Recursive batch workers run in a separate ThreadPoolExecutor. A
        # Root task can finish after a batch has failed while those workers
        # still own child leases, so wait for each ownership callback off the
        # event loop before Run resources are released.
        waiter_errors: list[BaseException] = []
        for waiter in tuple(self._blocking_waiters):
            owned = OwnedEffect.start(asyncio.to_thread(waiter))
            try:
                await owned.settle()
            except BaseException as exc:
                waiter_errors.append(exc)
        if waiter_errors:
            raise waiter_errors[0]


class RLMWorkerHandle(Generic[T]):
    """Typed access to an owned RLM result without exposing task mechanics."""

    def __init__(self, effect: OwnedEffect[T]) -> None:
        self._effect = effect

    def done(self) -> bool:
        """Return whether the worker has reached a terminal task state."""
        return self._effect.done()

    def result(self) -> T:
        """Return the worker result, preserving its original exception."""
        return self._effect.result()

    def consume_exception(self) -> None:
        """Mark a completed worker exception as observed without changing its result."""
        self._effect.consume_exception()

    async def wait_until_done(self) -> None:
        """Wait for completion as an observation signal without raising its error."""
        try:
            await self._effect.observe_completion()
        except BaseException:
            self.consume_exception()

    async def settle_after_caller_cancellation(self) -> bool:
        """Settle the owned worker and report whether the waiter was cancelled."""
        try:
            await self._effect.settle()
        except BaseException:
            self.consume_exception()
        return self._effect.caller_cancelled


async def invoke_native_rlm(
    rlm: Any,
    context: RLMExecutionContext,
    kwargs: Mapping[str, Any],
) -> Any:
    """Invoke the supported native DSPy surface with the caller-owned interpreter."""
    native_call_args: tuple[Any, ...] = ()
    if type(rlm) is dspy.RLM:
        if context.execution.interpreter is None:
            raise RLMConfigError("native RLM execution requires a caller-owned interpreter")
        native_call_args = (context.execution.interpreter,)
    return await rlm.acall(*native_call_args, **dict(kwargs))


def start_rlm_worker(
    *,
    rlm: Any,
    context: RLMExecutionContext,
    kwargs: Mapping[str, Any],
    ownership: WorkerOwnership,
    execute: RLMWorkerExecution[T],
) -> RLMWorkerHandle[T]:
    """Start one non-cancellable RLM worker on a private event loop."""
    effect = OwnedEffect.start(_run_in_worker(rlm, context, kwargs, execute))
    ownership.attach(effect)
    return RLMWorkerHandle(effect)


async def _run_in_worker(
    rlm: Any,
    context: RLMExecutionContext,
    kwargs: Mapping[str, Any],
    execute: RLMWorkerExecution[T],
) -> T:
    """Propagate context into a thread and run the async RLM call there."""
    return cast(T, await asyncio.to_thread(_run_private_event_loop, rlm, context, kwargs, execute))


def _run_private_event_loop(
    rlm: Any,
    context: RLMExecutionContext,
    kwargs: Mapping[str, Any],
    execute: RLMWorkerExecution[T],
) -> T:
    """Create and close the worker's event loop around one execution."""
    return asyncio.run(execute(rlm, context, kwargs))
