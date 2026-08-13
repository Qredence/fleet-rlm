"""Execute one already-prepared DSPy RLM and stream typed observations."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import replace
from typing import Any, Protocol, Self, cast

import dspy
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.observability.failure_diagnostics import normalize_turn_failure, trace_failure_category
from fleet_rlm.observability.turn_tracing import turn_phase_span
from fleet_rlm.rlm.context import RLMExecutionContext, RLMExecutionSpec
from fleet_rlm.rlm.dspy_contract import (
    PredictionOutputError,
    _RLMTraceCallback,
    bind_native_rlm_observer,
    empty_rlm_usage,
    normalize_prediction_trajectory,
    observed_usage,
    prediction_result,
)
from fleet_rlm.rlm.errors import (
    RLMConfigError,
    RunCancelledError,
    RunIntegrityFailureError,
    RunTerminalError,
)
from fleet_rlm.rlm.events import (
    AttachmentRead,
    EventRecorder,
    RLMReasoning,
    RunStarted,
    RuntimeEvent,
    RuntimeEventDetail,
    SkillActivated,
    SkillLoaded,
    Status,
    StepFinished,
    StepStarted,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    WarningEvent,
)
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.inputs import build_rlm_input_kwargs
from fleet_rlm.rlm.outcome import ExecutionDetail, RLMOutcome, TerminalStatus
from fleet_rlm.rlm.recursive_calls import RecursiveCallSummary, RecursiveRLMExecutor
from fleet_rlm.rlm.sanitize import truncate_public_text
from fleet_rlm.rlm.signature import root_signature_for_recursion
from fleet_rlm.rlm.tool_guards import RunToolGuards, workspace_obligations
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool
from fleet_rlm.rlm.trajectory_projection import has_reasoning, reconcile_trajectory

logger = logging.getLogger(__name__)
_MAX_DETAIL_EVENTS = 1024


def _recursive_summary(executor: RecursiveRLMExecutor | None, metrics: Any | None = None) -> RecursiveCallSummary:
    """
    Return recursive execution metrics, or zero-valued metrics when recursion is disabled.

    Parameters:
        executor (RecursiveRLMExecutor | None): The recursive executor whose metrics should be summarized.

    Returns:
        RecursiveCallSummary: The executor's recursive call metrics or an empty summary.
    """
    if executor is not None:
        return executor.summary()
    if metrics is not None and callable(getattr(metrics, "snapshot", None)):
        snapshot = metrics.snapshot()
        return RecursiveCallSummary(
            0,
            0,
            0,
            0,
            snapshot.depth_fallback_calls,
            (),
            recursive_batch_calls=snapshot.recursive_batch_calls,
            recursive_children_started=snapshot.recursive_children_started,
            recursive_children_completed=snapshot.recursive_children_completed,
            peak_child_concurrency=snapshot.peak_child_concurrency,
            delegation_metrics=snapshot,
        )
    return RecursiveCallSummary(0, 0, 0, 0, 0, ())


class RLMFactoryLike(Protocol):
    def create(
        self,
        *,
        models: Any,
        options: Any,
        tools: Sequence[dspy.Tool] | None = None,
        signature: Any = None,
        verbose: bool = True,
    ) -> Any:
        """
        Constructs an RLM with the specified models, execution options, tools, and signature.

        Parameters:
            models (Any): Models used by the RLM.
            options (Any): Execution options for the RLM.
            tools (Sequence[dspy.Tool] | None): Optional tools available to the RLM.
            signature (Any): Optional signature defining the RLM interface.
            verbose (bool): Whether to enable verbose execution output.

        Returns:
            Any: The configured RLM instance.
        """
        ...


class _WorkerOwnership:
    def __init__(self) -> None:
        self.task: asyncio.Task[Any] | None = None
        self._blocking_waiters: list[Callable[[], None]] = []

    def add_blocking_waiter(self, waiter: Callable[[], None]) -> None:
        """Register synchronous resource ownership that outlives the RLM task."""
        self._blocking_waiters.append(waiter)

    async def wait(self) -> None:
        task = self.task
        if task is not None:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            if task.done() and not task.cancelled():
                with contextlib.suppress(BaseException):
                    task.exception()

        # Recursive batch workers run in a separate ThreadPoolExecutor.  A
        # Root task can finish after a batch has failed while those workers
        # still own child leases, so wait for each ownership callback off the
        # event loop before Run resources are released.
        waiter_errors: list[BaseException] = []
        for waiter in tuple(self._blocking_waiters):
            owned = asyncio.create_task(asyncio.to_thread(waiter))
            while not owned.done():
                try:
                    await asyncio.shield(owned)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            if owned.done() and not owned.cancelled():
                try:
                    owned.result()
                except BaseException as exc:
                    waiter_errors.append(exc)
        if waiter_errors:
            raise waiter_errors[0]


class RunEventStream:
    """Async observation iterator with its measured outcome after completion."""

    def __init__(
        self,
        agen: AsyncIterator[RuntimeEvent],
        outcome_factory: Callable[[], RLMOutcome],
        ownership: _WorkerOwnership,
    ) -> None:
        self._agen = agen.__aiter__()
        self._outcome_factory = outcome_factory
        self._outcome: RLMOutcome | None = None
        self._finished = False
        self._ownership = ownership

    @property
    def outcome(self) -> RLMOutcome | None:
        return self._outcome

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> RuntimeEvent:
        try:
            return await self._agen.__anext__()
        except StopAsyncIteration:
            self._finish()
            raise

    async def aclose(self) -> None:
        if not self._finished:
            close = getattr(self._agen, "aclose", None)
            if close is not None:
                await close()
            self._finish()

    async def wait_owned(self) -> None:
        """Wait for a detached non-cancellable worker under process ownership."""
        await self._ownership.wait()

    def _finish(self) -> None:
        if not self._finished:
            self._finished = True
            self._outcome = self._outcome_factory()


class _DetailRelay:
    def __init__(self, *, maxsize: int = _MAX_DETAIL_EVENTS) -> None:
        self._loop = asyncio.get_running_loop()
        # Step and Tool lifecycle are durable protocol signals, not optional
        # diagnostic detail. Keep them even while normal observation traffic is capped.
        self._queue: asyncio.Queue[RuntimeEventDetail] = asyncio.Queue()
        self._maxsize = max(0, maxsize)
        self._ordinary_count = 0
        self.overflowed = False

    def publish(self, detail: RuntimeEventDetail) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is self._loop:
            self._put(detail)
        else:
            self._loop.call_soon_threadsafe(self._put, detail)

    def _put(self, detail: RuntimeEventDetail) -> None:
        if isinstance(
            detail, (SkillActivated, SkillLoaded, StepStarted, StepFinished, ToolStarted, ToolCompleted, ToolFailed)
        ):
            self._queue.put_nowait(detail)
            return
        if self._ordinary_count >= self._maxsize:
            self.overflowed = True
            return
        self._ordinary_count += 1
        self._queue.put_nowait(detail)

    @staticmethod
    def _is_retained(detail: RuntimeEventDetail) -> bool:
        return isinstance(
            detail, (SkillActivated, SkillLoaded, StepStarted, StepFinished, ToolStarted, ToolCompleted, ToolFailed)
        )

    async def get(self) -> RuntimeEventDetail:
        detail = await self._queue.get()
        if not self._is_retained(detail):
            self._ordinary_count -= 1
        return detail

    def drain(self) -> list[RuntimeEventDetail]:
        values: list[RuntimeEventDetail] = []
        while True:
            try:
                detail = self._queue.get_nowait()
                if not self._is_retained(detail):
                    self._ordinary_count -= 1
                values.append(detail)
            except asyncio.QueueEmpty:
                return values


def _terminal_status(exc: BaseException) -> TerminalStatus:
    if isinstance(exc, RunTerminalError):
        return cast(TerminalStatus, exc.status)
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "failed"


def _public_failure_message(exc: BaseException) -> str:
    # Read the instance attribute so a parametrized ``RunTerminalError("...")``
    # override is honored, matching ``sanitize_public_error``. Class-attr
    # defaults (currently all raise sites) fall through the same lookup.
    if isinstance(exc, PredictionOutputError):
        return str(getattr(exc, "public_message", "Turn output is invalid"))
    if isinstance(exc, RunTerminalError):
        return str(getattr(exc, "public_message", "Turn failed"))
    if isinstance(exc, AdapterParseError):
        return "The model produced a response that could not be parsed into the expected fields."
    return "Turn failed"


async def _settle_worker(task: asyncio.Task[Any]) -> bool:
    """Wait through repeated caller cancellation until the owned worker exits."""
    cancellation_requested = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_requested = True
        except BaseException:
            break
    return cancellation_requested


class _WorkerMonitor:
    """Bound polling/cancellation policy for one non-cancellable RLM worker."""

    def __init__(
        self,
        task: asyncio.Task[Any],
        relay: _DetailRelay,
        context: RLMExecutionContext,
        drain_capabilities: Callable[[], tuple[ExecutionDetail, ...]],
    ) -> None:
        self.task = task
        self.relay = relay
        self.context = context
        self.drain_capabilities = drain_capabilities
        self.intended_stop: BaseException | None = None
        self.caller_cancelled = False

    async def stream(self) -> AsyncIterator[RuntimeEventDetail]:
        pending: asyncio.Task[RuntimeEventDetail] | None = None
        try:
            while not self.task.done():
                if await self.context.execution.cancellation_requested():
                    self.intended_stop = RunCancelledError()
                    break
                remaining = self.context.execution.deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    self.intended_stop = TimeoutError()
                    break
                pending = asyncio.create_task(self.relay.get())
                done, _ = await asyncio.wait(
                    {self.task, pending},
                    timeout=min(remaining, 0.25),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if pending in done:
                    yield pending.result()
                else:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                pending = None
                for detail in self.drain_capabilities():
                    yield detail
        except (GeneratorExit, asyncio.CancelledError):
            self.caller_cancelled = True
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                self.caller_cancelled |= await _settle_worker(pending)
            if self.intended_stop is None and not self.caller_cancelled:
                self.caller_cancelled |= await _settle_worker(self.task)

    def raise_if_stopped(self) -> None:
        if self.caller_cancelled:
            if self.task.done() and not self.task.cancelled():
                self.task.exception()
            raise asyncio.CancelledError
        if self.intended_stop is not None:
            if self.task.done() and not self.task.cancelled():
                self.task.exception()
            raise self.intended_stop


class _ObservationBuffer:
    """Own durable execution details and their matching Runtime Event records."""

    def __init__(self, recorder: EventRecorder) -> None:
        self.recorder = recorder
        self.details: list[ExecutionDetail] = []

    def record(self, detail: RuntimeEventDetail) -> RuntimeEvent:
        if not isinstance(detail, Status):
            self.details.append(cast(ExecutionDetail, detail))
        return self.recorder.record(detail)


class RLMRunner:
    """Consume only an immutable prepared context and emit no terminal detail."""

    def __init__(self, *, factory: RLMFactoryLike | None = None) -> None:
        self._factory = factory or RLMFactory()

    def stream(self, context: RLMExecutionContext) -> RunEventStream:
        outcome: list[RLMOutcome] = []
        ownership = _WorkerOwnership()
        events = self._generate(context, outcome, ownership)
        return RunEventStream(
            events,
            # A stream closed before the generator body ever runs leaves the
            # outcome cell empty; synthesize a cancelled outcome (matching the
            # GeneratorExit path in ``_generate``) instead of raising IndexError.
            lambda: (
                outcome[-1]
                if outcome
                else RLMOutcome(
                    terminal_status="cancelled",
                    usage=empty_rlm_usage(),
                    public_error_message="Turn cancelled",
                    duration_ms=0,
                )
            ),
            ownership,
        )

    async def _generate(
        self,
        context: RLMExecutionContext,
        outcome: list[RLMOutcome],
        ownership: _WorkerOwnership,
    ) -> AsyncIterator[RuntimeEvent]:
        started = time.perf_counter()
        prediction: list[Any] = []
        try:
            async for event in self._run_success(context, outcome, ownership, prediction, started):
                yield event
        except (GeneratorExit, asyncio.CancelledError):
            duration_ms = int((time.perf_counter() - started) * 1000)
            outcome.append(
                RLMOutcome(
                    terminal_status="cancelled",
                    usage=observed_usage(prediction[-1] if prediction else None, duration_ms=duration_ms),
                    public_error_message="Turn cancelled",
                    duration_ms=duration_ms,
                )
            )
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            diagnostic = normalize_turn_failure(exc)
            logger.warning(
                "RLM execution failed (%s) cause_type=%s provider_status_category=%s message=%s",
                type(exc).__name__,
                diagnostic.cause_type,
                diagnostic.provider_status_category,
                diagnostic.message,
            )
            outcome.append(
                RLMOutcome(
                    terminal_status=_terminal_status(exc),
                    usage=observed_usage(prediction[-1] if prediction else None, duration_ms=duration_ms),
                    public_error_message=_public_failure_message(exc),
                    duration_ms=duration_ms,
                )
            )
        finally:
            if outcome and outcome[-1].terminal_status != "completed":
                context.capabilities.drain_memory_candidates()
            if not outcome:
                context.capabilities.drain_memory_candidates()
                outcome.append(RLMOutcome(terminal_status="failed", public_error_message="Turn failed"))

    async def _run_success(
        self,
        context: RLMExecutionContext,
        outcome: list[RLMOutcome],
        ownership: _WorkerOwnership,
        prediction: list[Any],
        started: float,
    ) -> AsyncIterator[RuntimeEvent]:
        observations = _ObservationBuffer(EventRecorder(context.identity.run_id, context.identity.session_id))
        async for event in self._initial_events(context, observations):
            yield event
        spec = context.capabilities.spec
        spec, relay, guards, task, recursive_executor = self._start_worker(context)
        ownership.task = task
        if recursive_executor is not None:
            ownership.add_blocking_waiter(recursive_executor.wait_owned)
        monitor = _WorkerMonitor(task, relay, context, lambda: self._drain_capability_details(context))
        async for event in self._worker_events(context, observations, relay, monitor):
            yield event
        prediction.append(task.result())
        if guards.integrity.unresolved:
            raise RunIntegrityFailureError
        async for event in self._prediction_events(context, observations, prediction[-1]):
            yield event
        duration_ms = int((time.perf_counter() - started) * 1000)
        result = prediction_result(
            prediction[-1],
            spec.signature,
            schema_id=spec.output_schema_id,
            schema_version=spec.output_schema_version,
            max_output_chars=context.execution.options.max_output_chars,
        )
        outcome.append(
            RLMOutcome(
                terminal_status="completed",
                prediction=result,
                usage=observed_usage(prediction[-1], duration_ms=duration_ms),
                artifact_candidates=context.capabilities.drain_artifact_candidates(),
                memory_candidates=context.capabilities.drain_memory_candidates(),
                execution_details=tuple(observations.details),
                duration_ms=duration_ms,
            )
        )

    async def _initial_events(
        self,
        context: RLMExecutionContext,
        observations: _ObservationBuffer,
    ) -> AsyncIterator[RuntimeEvent]:
        yield observations.recorder.record(RunStarted(delivery="live"))
        yield observations.recorder.record(Status("execution", "running"))
        for notice in context.session.preparation_notices:
            yield observations.recorder.record(WarningEvent(notice.message, notice.code))
        for item in self._drain_capability_details(context):
            yield observations.record(item)
        if await context.execution.cancellation_requested():
            raise RunCancelledError

    def _start_worker(
        self, context: RLMExecutionContext
    ) -> tuple[RLMExecutionSpec, _DetailRelay, RunToolGuards, asyncio.Task[Any], RecursiveRLMExecutor | None]:
        """
        Prepare the RLM worker and supporting execution state for a turn.

        Parameters:
            context (RLMExecutionContext): Execution context containing the request, capabilities,
                runtime options, and authorization state.

        Returns:
            tuple: The execution specification, detail relay, tool guards, and worker task.

        Raises:
            RLMConfigError: If recursive execution requires a child runtime factory that is unavailable.
        """
        spec = context.capabilities.spec
        relay = _DetailRelay(maxsize=_MAX_DETAIL_EVENTS)
        guards = RunToolGuards(required_targets=workspace_obligations(context.session.request))
        self._bind_observer(context.execution.interpreter, relay, context.execution.options.max_output_chars)
        self._bind_context_capsule(context)
        recursive_executor = None
        if context.delegation.recursive_options.enabled:
            if context.delegation.child_runtime_factory is None:
                raise RLMConfigError("recursive child runtime is unavailable")
            recursive_executor = RecursiveRLMExecutor(
                models=context.execution.models,
                options=context.delegation.recursive_options,
                child_runtime_factory=context.delegation.child_runtime_factory,
                deadline=context.execution.deadline,
                metrics=context.delegation.metrics,
                observer=relay.publish,
                is_authorized=lambda: not context.identity.authority.revoked,
            )
        spec = replace(
            spec,
            signature=root_signature_for_recursion(
                spec.signature,
                recursion_enabled=context.delegation.recursive_options.enabled,
                skill_instructions=spec.skill_instructions,
            ),
        )

        def relay_capability_details(_result: Any) -> None:
            """Relay pending capability details to the event stream."""
            for detail in self._drain_capability_details(context):
                relay.publish(detail)

        observed_tools = tuple(
            observe_tool(
                tool,
                relay.publish,
                spec.tool_event_views.get(str(tool.name), ToolEventView.metadata_only()),
                after_result=(relay_capability_details if str(tool.name) == "load_skill" else None),
                is_authorized=lambda: not context.identity.authority.revoked,
                guards=guards,
            )
            for tool in spec.tools
        )
        recursive_tools = (
            (recursive_executor.tool, recursive_executor.batched_tool) if recursive_executor is not None else ()
        )
        all_tools = (*observed_tools, *recursive_tools)
        rlm = self._factory.create(
            models=context.execution.models,
            options=context.execution.options,
            tools=all_tools or None,
            signature=spec.signature,
        )
        self._bind_observer(
            rlm,
            relay,
            context.execution.options.max_output_chars,
            emit_reasoning=type(rlm) is not dspy.RLM,
        )
        return (
            spec,
            relay,
            guards,
            asyncio.create_task(self._execute_rlm_in_worker(rlm, context, spec, recursive_executor, relay)),
            recursive_executor,
        )

    async def _worker_events(
        self,
        context: RLMExecutionContext,
        observations: _ObservationBuffer,
        relay: _DetailRelay,
        monitor: _WorkerMonitor,
    ) -> AsyncIterator[RuntimeEvent]:
        async for item in monitor.stream():
            yield observations.record(item)
        for item in (*self._drain_capability_details(context), *relay.drain()):
            yield observations.record(item)
        if relay.overflowed:
            warning = WarningEvent("some detailed execution events were omitted")
            yield observations.record(warning)
        monitor.raise_if_stopped()

    async def _prediction_events(
        self,
        context: RLMExecutionContext,
        observations: _ObservationBuffer,
        prediction: Any,
    ) -> AsyncIterator[RuntimeEvent]:
        trajectory = normalize_prediction_trajectory(prediction)
        for item in reconcile_trajectory(
            observations.details, trajectory, max_chars=context.execution.options.max_output_chars
        ):
            yield observations.recorder.record(item)
        final_reasoning = getattr(prediction, "final_reasoning", None)
        if isinstance(final_reasoning, str) and final_reasoning.strip():
            public_reasoning = truncate_public_text(final_reasoning, max_len=context.execution.options.max_output_chars)
            if not has_reasoning(observations.details, public_reasoning, context.execution.options.max_output_chars):
                item = RLMReasoning(public_reasoning)
                yield observations.record(item)
        for item in self._drain_capability_details(context):
            yield observations.record(item)

    @staticmethod
    def _bind_context_capsule(context: RLMExecutionContext) -> None:
        """Bind the prepared Volume context to the interpreter before the RLM starts."""
        if context.session.attachment_context is None:
            return
        interpreter = context.execution.interpreter
        if interpreter is None:
            return
        interpreter.bind_context_capsule(context.session.attachment_context)

    @staticmethod
    def _bind_observer(
        target: Any,
        relay: _DetailRelay,
        max_chars: int,
        *,
        emit_reasoning: bool = True,
    ) -> None:
        if type(target) is dspy.RLM:
            bind_native_rlm_observer(
                target,
                relay.publish if emit_reasoning else None,
                max_chars=max_chars,
            )
            return
        bind = getattr(target, "bind_observer", None)
        if callable(bind):
            bind(relay.publish, max_chars=max_chars)

    @staticmethod
    def _native_call_args(rlm: Any, context: RLMExecutionContext) -> tuple[Any, ...]:
        if type(rlm) is not dspy.RLM:
            return ()
        if context.execution.interpreter is None:
            raise RLMConfigError("native RLM execution requires a caller-owned interpreter")
        return (context.execution.interpreter,)

    async def _invoke_rlm(
        self,
        rlm: Any,
        context: RLMExecutionContext,
        kwargs: dict[str, Any],
        relay: _DetailRelay,
    ) -> Any:
        # Fleet performs one standard dspy.RLM completion per action. Token-level
        # streaming (dspy.streamify + chunk projection) is intentionally removed:
        # it added a custom SSE delta grammar with measurable producer cost while
        # the canonical trajectory reconciliation already publishes complete
        # per-iteration reasoning/code/output events.
        del relay
        native_call_args = self._native_call_args(rlm, context)
        return await rlm.acall(*native_call_args, **kwargs)

    @staticmethod
    def _record_attachment_accesses(context: RLMExecutionContext) -> None:
        interpreter = context.execution.interpreter
        if interpreter is None:
            return
        context.capabilities.record_attachment_accesses(tuple(interpreter.drain_context_accesses()))

    @staticmethod
    def _record_phase_failure(
        phase: Any,
        started: float,
        recursive_executor: RecursiveRLMExecutor | None,
        metrics: Any,
        exc: BaseException,
    ) -> None:
        recursive_summary = _recursive_summary(recursive_executor, metrics)
        phase.set_outputs(
            {
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "request_status": "failed",
                "failure_category": trace_failure_category(exc),
                "recursive_call_count": recursive_summary.call_count,
                "recursive_prompt_chars": recursive_summary.delegated_prompt_chars,
                "recursive_depth_fallback_count": recursive_summary.depth_fallback_count,
                "delegation_metrics": recursive_summary.delegation_metrics.as_dict(),
            }
        )

    @staticmethod
    def _record_phase_success(
        phase: Any,
        prediction: Any,
        started: float,
        recursive_executor: RecursiveRLMExecutor | None,
        metrics: Any,
    ) -> Any:
        final_reasoning = getattr(prediction, "final_reasoning", None)
        termination_mode = (
            "native_extraction_fallback" if final_reasoning == "Extract forced final output" else "typed_submit"
        )
        usage = observed_usage(prediction, duration_ms=int((time.perf_counter() - started) * 1000))
        recursive_summary = _recursive_summary(recursive_executor, metrics)
        phase.set_outputs(
            {
                "iterations": usage["iterations"],
                "observed_lm_usage": usage["observed_lm_usage"],
                "termination_mode": termination_mode,
                "elapsed_ms": usage["duration_ms"],
                "request_status": "completed",
                "recursive_call_count": recursive_summary.call_count,
                "recursive_prompt_chars": recursive_summary.delegated_prompt_chars,
                "recursive_depth_fallback_count": recursive_summary.depth_fallback_count,
                "delegation_metrics": recursive_summary.delegation_metrics.as_dict(),
            }
        )
        return prediction

    async def _execute_rlm(
        self,
        rlm: Any,
        context: RLMExecutionContext,
        spec: RLMExecutionSpec,
        recursive_executor: RecursiveRLMExecutor | None,
        relay: _DetailRelay,
    ) -> Any:
        """
        Execute the RLM with the prepared request context and return its prediction.

        The execution records tracing and usage metadata. Per-iteration reasoning
        observations come from the action observer callback; the canonical
        trajectory reconciliation after completion publishes any missing
        reasoning/code/output values. Cleanup failures from recursive execution
        are surfaced as execution failures.
        """
        kwargs = build_rlm_input_kwargs(
            request=context.session.request,
            session_context=context.session.session_context,
            skill_cards=spec.skill_cards,
            attachments=context.session.attachments,
            attachment_context=context.session.attachment_context,
            workspace=spec.workspace,
            workspace_memory_digest=context.session.workspace_memory_digest,
        )
        started = time.perf_counter()
        with (
            turn_phase_span(
                "RLM.execute",
                inputs={
                    "max_iterations": context.execution.options.max_iterations,
                    "max_llm_calls": context.execution.options.max_llm_calls,
                    "max_output_chars": context.execution.options.max_output_chars,
                },
            ) as phase,
            dspy.context(
                lm=context.execution.models.root_lm,
                # DSPy 3.3.x combines context callbacks with instance
                # callbacks around LM requests (dspy/utils/callback.py:258-288).
                callbacks=[
                    _RLMTraceCallback(
                        root_lm=context.execution.models.root_lm,
                        sub_lm=context.execution.models.sub_lm,
                        metrics=context.delegation.metrics,
                    )
                ],
                # Keep the pinned DSPy JSON action protocol authoritative. A
                # provider-native token stream is an adapter failure, not a
                # second grammar that Fleet should reinterpret.
                adapter=dspy.JSONAdapter(),
                track_usage=True,
            ),
        ):
            try:
                prediction = await self._invoke_rlm(rlm, context, kwargs, relay)
                if recursive_executor is not None:
                    recursive_executor.raise_if_cleanup_failed()
            except BaseException as exc:
                self._record_phase_failure(phase, started, recursive_executor, context.delegation.metrics, exc)
                raise
            finally:
                self._record_attachment_accesses(context)
            return self._record_phase_success(
                phase,
                prediction,
                started,
                recursive_executor,
                context.delegation.metrics,
            )

    async def _execute_rlm_in_worker(
        self,
        rlm: Any,
        context: RLMExecutionContext,
        spec: RLMExecutionSpec,
        recursive_executor: RecursiveRLMExecutor | None,
        relay: _DetailRelay,
    ) -> Any:
        """
        Execute the RLM in a worker thread with its own asynchronous event loop.

        Parameters:
            rlm (Any): The RLM instance to execute.
            context (RLMExecutionContext): Runtime context for the execution.
            spec (RLMExecutionSpec): Execution configuration.
            recursive_executor (RecursiveRLMExecutor | None): Optional executor for recursive calls.
            relay (_DetailRelay): Relay for execution details.

        Returns:
            Any: The RLM execution result.
        """

        def run() -> Any:
            return asyncio.run(self._execute_rlm(rlm, context, spec, recursive_executor, relay))

        return await asyncio.to_thread(run)

    @staticmethod
    def _drain_capability_details(context: RLMExecutionContext) -> tuple[ExecutionDetail, ...]:
        values = context.capabilities.drain_public_details()
        if not all(isinstance(item, (AttachmentRead, SkillActivated, SkillLoaded, WarningEvent)) for item in values):
            raise TypeError("capability host returned an unsupported public detail")
        return cast(tuple[ExecutionDetail, ...], values)
