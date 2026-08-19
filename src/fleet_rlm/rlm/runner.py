"""Execute one already-prepared DSPy RLM and stream typed observations."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import replace
from typing import Any, Protocol, Self, cast

import dspy
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.observability.failure_diagnostics import normalize_turn_failure
from fleet_rlm.rlm.context import RLMExecutionContext, RLMExecutionSpec
from fleet_rlm.rlm.dspy_contract import (
    PredictionOutputError,
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
    RLMReasoning,
    RunStarted,
    RuntimeEvent,
    RuntimeEventDetail,
    SkillActivated,
    SkillLoaded,
    Status,
    WarningEvent,
)
from fleet_rlm.rlm.execution_trace import ExecutionTraceAssembler
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.inputs import build_rlm_input_kwargs
from fleet_rlm.rlm.observation import ObservationSession
from fleet_rlm.rlm.outcome import ExecutionDetail, RLMOutcome, TerminalStatus
from fleet_rlm.rlm.recursive_calls import RecursiveRLMExecutor
from fleet_rlm.rlm.sanitize import truncate_public_text
from fleet_rlm.rlm.signature import root_signature_for_recursion
from fleet_rlm.rlm.tool_guards import RunToolGuards, workspace_obligations
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool
from fleet_rlm.rlm.trajectory_projection import has_reasoning, reconcile_trajectory
from fleet_rlm.rlm.worker_execution import RLMWorkerHandle, WorkerOwnership, start_rlm_worker

logger = logging.getLogger(__name__)


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


class RunEventStream:
    """Async observation iterator with its measured outcome after completion."""

    def __init__(
        self,
        agen: AsyncIterator[RuntimeEvent],
        outcome_factory: Callable[[], RLMOutcome],
        ownership: WorkerOwnership,
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
        await self._ownership.wait_owned()

    def _finish(self) -> None:
        if not self._finished:
            self._finished = True
            self._outcome = self._outcome_factory()


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


class RLMRunner:
    """Consume only an immutable prepared context and emit no terminal detail."""

    def __init__(self, *, factory: RLMFactoryLike | None = None) -> None:
        self._factory = factory or RLMFactory()

    def stream(self, context: RLMExecutionContext) -> RunEventStream:
        outcome: list[RLMOutcome] = []
        ownership = WorkerOwnership()
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
        ownership: WorkerOwnership,
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
        ownership: WorkerOwnership,
        prediction: list[Any],
        started: float,
    ) -> AsyncIterator[RuntimeEvent]:
        observations = ObservationSession(context.identity.run_id, context.identity.session_id)
        async for event in self._initial_events(context, observations):
            yield event
        spec, guards, worker, recursive_executor = self._start_worker(context, ownership, observations)
        if recursive_executor is not None:
            ownership.add_blocking_waiter(recursive_executor.wait_owned)
        async for event in self._worker_events(context, observations, worker):
            yield event
        prediction.append(worker.result())
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
        observations: ObservationSession,
    ) -> AsyncIterator[RuntimeEvent]:
        yield observations.record_event(RunStarted(delivery="live"))
        yield observations.record_event(Status("execution", "running"))
        for notice in context.session.preparation_notices:
            yield observations.record(WarningEvent(notice.message, notice.code))
        for item in self._drain_capability_details(context):
            yield observations.record(item)
        if await context.execution.cancellation_requested():
            raise RunCancelledError

    def _start_worker(
        self,
        context: RLMExecutionContext,
        ownership: WorkerOwnership,
        observations: ObservationSession,
    ) -> tuple[RLMExecutionSpec, RunToolGuards, RLMWorkerHandle[Any], RecursiveRLMExecutor | None]:
        """
        Prepare the RLM worker and supporting execution state for a turn.

        Parameters:
            context (RLMExecutionContext): Execution context containing the request, capabilities,
                runtime options, and authorization state.
            ownership (WorkerOwnership): Owner of the started worker and its blocking resource waiters.
            observations (ObservationSession): Recorder and publish seam for worker-thread details.

        Returns:
            tuple: The execution specification, tool guards, typed worker handle, and recursive executor.

        Raises:
            RLMConfigError: If recursive execution requires a child runtime factory that is unavailable.
        """
        spec = context.capabilities.spec
        guards = RunToolGuards(required_targets=workspace_obligations(context.session.request))
        self._bind_observer(
            context.execution.interpreter,
            observations.publish,
            context.execution.options.max_output_chars,
        )
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
                observer=observations.publish,
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
                observations.publish(detail)

        observed_tools = tuple(
            observe_tool(
                tool,
                observations.publish,
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
            observations.publish,
            context.execution.options.max_output_chars,
            emit_reasoning=type(rlm) is not dspy.RLM,
        )
        kwargs = build_rlm_input_kwargs(
            request=context.session.request,
            session_context=context.session.session_context,
            skill_cards=spec.skill_cards,
            attachments=context.session.attachments,
            attachment_context=context.session.attachment_context,
            workspace=spec.workspace,
            workspace_memory_digest=context.session.workspace_memory_digest,
        )
        trace = ExecutionTraceAssembler(recursive_executor)
        worker = start_rlm_worker(
            rlm=rlm,
            context=context,
            kwargs=kwargs,
            ownership=ownership,
            execute=trace.execute,
        )
        return (
            spec,
            guards,
            worker,
            recursive_executor,
        )

    async def _worker_events(
        self,
        context: RLMExecutionContext,
        observations: ObservationSession,
        worker: RLMWorkerHandle[Any],
    ) -> AsyncIterator[RuntimeEvent]:
        async for event in observations.stream_worker(
            worker,
            context,
            lambda: self._drain_capability_details(context),
        ):
            yield event

    async def _prediction_events(
        self,
        context: RLMExecutionContext,
        observations: ObservationSession,
        prediction: Any,
    ) -> AsyncIterator[RuntimeEvent]:
        trajectory = normalize_prediction_trajectory(prediction)
        for item in reconcile_trajectory(
            observations.details, trajectory, max_chars=context.execution.options.max_output_chars
        ):
            # ``reconcile_trajectory`` appends the canonical details to the
            # observation list; emit them without recording them a second time.
            yield observations.record_event(item)
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
        bind = getattr(context.execution.interpreter, "bind_context_capsule", None)
        if callable(bind):
            bind(context.session.attachment_context)

    @staticmethod
    def _bind_observer(
        target: Any,
        publish: Callable[[RuntimeEventDetail], None],
        max_chars: int,
        *,
        emit_reasoning: bool = True,
    ) -> None:
        if type(target) is dspy.RLM:
            bind_native_rlm_observer(
                target,
                publish if emit_reasoning else None,
                max_chars=max_chars,
            )
            return
        bind = getattr(target, "bind_observer", None)
        if callable(bind):
            bind(publish, max_chars=max_chars)

    @staticmethod
    def _drain_capability_details(context: RLMExecutionContext) -> tuple[ExecutionDetail, ...]:
        values = context.capabilities.drain_public_details()
        if not all(isinstance(item, (AttachmentRead, SkillActivated, SkillLoaded, WarningEvent)) for item in values):
            raise TypeError("capability host returned an unsupported public detail")
        return cast(tuple[ExecutionDetail, ...], values)
