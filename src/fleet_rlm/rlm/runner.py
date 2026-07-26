"""Execute one already-prepared DSPy RLM and stream typed observations."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any, Protocol, Self, cast

import dspy

from fleet_rlm.rlm.context import RLMExecutionContext, RLMExecutionSpec
from fleet_rlm.rlm.dspy_contract import (
    PredictionOutputError,
    TrajectoryStep,
    bind_native_rlm_observer,
    normalize_prediction_trajectory,
    observed_usage,
    prediction_result,
)
from fleet_rlm.rlm.errors import (
    TurnCancelled,
    TurnIntegrityFailure,
    TurnTerminalError,
)
from fleet_rlm.rlm.events import (
    AttachmentRead,
    EventRecorder,
    ObservationDetail,
    RLMCode,
    RLMOutput,
    RLMReasoning,
    RunStarted,
    RuntimeEvent,
    SkillActivated,
    SkillLoaded,
    Status,
    StepFinished,
    StepStarted,
    WarningEvent,
)
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.inputs import build_rlm_input_kwargs
from fleet_rlm.rlm.outcome import ExecutionDetail, RLMOutcome, TerminalStatus
from fleet_rlm.rlm.sanitize import truncate_public_text
from fleet_rlm.rlm.tool_guards import TurnToolGuards, workspace_obligations
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool

logger = logging.getLogger(__name__)


class RLMFactoryLike(Protocol):
    def create(
        self,
        *,
        models: Any,
        options: Any,
        interpreter: Any,
        tools: Sequence[dspy.Tool] | None = None,
        signature: Any = None,
        verbose: bool = True,
    ) -> Any: ...


class _WorkerOwnership:
    def __init__(self) -> None:
        self.task: asyncio.Task[Any] | None = None

    async def wait(self) -> None:
        task = self.task
        if task is None:
            return
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if task.done() and not task.cancelled():
            try:
                task.exception()
            except BaseException:
                pass


class TurnEventStream:
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
    def __init__(self, *, maxsize: int = 256) -> None:
        self._loop = asyncio.get_running_loop()
        # Lifecycle is a durable protocol signal, not optional diagnostic
        # detail. Keep it even while normal observation traffic is capped.
        self._queue: asyncio.Queue[ExecutionDetail] = asyncio.Queue()
        self._maxsize = max(0, maxsize)
        self._ordinary_count = 0
        self.overflowed = False

    def publish(self, detail: ExecutionDetail) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is self._loop:
            self._put(detail)
        else:
            self._loop.call_soon_threadsafe(self._put, detail)

    def _put(self, detail: ExecutionDetail) -> None:
        if not isinstance(detail, (SkillActivated, SkillLoaded)) and self._ordinary_count >= self._maxsize:
            self.overflowed = True
            return
        if not isinstance(detail, (SkillActivated, SkillLoaded)):
            self._ordinary_count += 1
        self._queue.put_nowait(detail)

    async def get(self) -> ExecutionDetail:
        detail = await self._queue.get()
        if not isinstance(detail, (SkillActivated, SkillLoaded)):
            self._ordinary_count -= 1
        return detail

    def drain(self) -> list[ExecutionDetail]:
        values: list[ExecutionDetail] = []
        while True:
            try:
                detail = self._queue.get_nowait()
                if not isinstance(detail, (SkillActivated, SkillLoaded)):
                    self._ordinary_count -= 1
                values.append(detail)
            except asyncio.QueueEmpty:
                return values


def _trajectory_details(steps: Sequence[TrajectoryStep], *, max_chars: int) -> list[ObservationDetail]:
    """Project strictly normalized DSPy trajectory steps into public details."""
    details: list[ObservationDetail] = []
    for step in steps:
        output = step.output
        if output.startswith("FINAL:"):
            output = "FINAL submitted"
        details.extend(
            (
                StepStarted(step.index),
                RLMReasoning(truncate_public_text(step.reasoning, max_len=max_chars), step.index),
                RLMCode(truncate_public_text(step.code, max_len=max_chars), step.index),
                RLMOutput(truncate_public_text(output, max_len=max_chars), step.index),
                StepFinished(step.index),
            )
        )
    return details


def _detail_position(details: Sequence[ExecutionDetail], detail_type: type[object], step: int) -> int | None:
    return next(
        (
            index
            for index, detail in enumerate(details)
            if isinstance(detail, detail_type) and getattr(detail, "step", None) == step
        ),
        None,
    )


def _outside_reasoning_position(details: Sequence[ExecutionDetail], target: ObservationDetail, step: int) -> int | None:
    if not isinstance(target, RLMReasoning):
        return None
    return _detail_position(details, RLMReasoning, step)


def _trajectory_insertion(details: Sequence[ExecutionDetail], target: ObservationDetail, step: int, finish: int) -> int:
    if isinstance(target, RLMReasoning):
        start = _detail_position(details, StepStarted, step)
        assert start is not None
        return start + 1
    if isinstance(target, RLMCode):
        reasoning = _detail_position(details, RLMReasoning, step)
        if reasoning is not None:
            return reasoning + 1
        start = _detail_position(details, StepStarted, step)
        assert start is not None
        return start + 1
    return finish


def _reconcile_trajectory(
    details: list[ExecutionDetail],
    trajectory: Sequence[TrajectoryStep],
    *,
    max_chars: int,
) -> list[ObservationDetail]:
    """Make native trajectory the durable source while retaining live timing.

    Existing equal observations stay put. A differing same-step RLM detail is
    replaced in the durable list and re-emitted with the same stable step ID so
    live TUI projection upserts it rather than appending a second card.
    """
    emissions: list[ObservationDetail] = []
    for trajectory_step in trajectory:
        step = trajectory_step.index
        step_details = _trajectory_details((trajectory_step,), max_chars=max_chars)
        start = _detail_position(details, StepStarted, step)
        finish = _detail_position(details, StepFinished, step)
        if start is None or finish is None or start >= finish:
            details.extend(step_details)
            emissions.extend(step_details)
            continue

        canonical = step_details[1:-1]
        for target in canonical:
            target_type = type(target)
            existing_positions = [
                index
                for index in range(start + 1, finish)
                if isinstance(details[index], target_type) and getattr(details[index], "step", None) == step
            ]
            if existing_positions:
                first = existing_positions[0]
                if details[first] != target:
                    details[first] = target
                    emissions.append(target)
                for duplicate in reversed(existing_positions[1:]):
                    del details[duplicate]
                start = _detail_position(details, StepStarted, step)
                finish = _detail_position(details, StepFinished, step)
                assert start is not None and finish is not None
                continue

            # Live observation may publish reasoning before interpreter StepStarted.
            outside = _outside_reasoning_position(details, target, step)
            if outside is not None:
                if details[outside] != target:
                    details[outside] = target
                    emissions.append(target)
                continue
            insertion = _trajectory_insertion(details, target, step, finish)
            details.insert(insertion, target)
            emissions.append(target)
            start = _detail_position(details, StepStarted, step)
            finish = _detail_position(details, StepFinished, step)
            assert start is not None and finish is not None
    return emissions


def _terminal_status(exc: BaseException) -> TerminalStatus:
    if isinstance(exc, TurnTerminalError):
        return cast(TerminalStatus, exc.status)
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "failed"


def _public_failure_message(exc: BaseException) -> str:
    if isinstance(exc, PredictionOutputError):
        return str(type(exc).public_message)
    if isinstance(exc, TurnTerminalError):
        return str(type(exc).public_message)
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

    async def stream(self) -> AsyncIterator[ExecutionDetail]:
        pending: asyncio.Task[ExecutionDetail] | None = None
        try:
            while not self.task.done():
                if await self.context.cancellation_requested():
                    self.intended_stop = TurnCancelled()
                    break
                remaining = self.context.deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    self.intended_stop = asyncio.TimeoutError()
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

    def record(self, detail: ExecutionDetail) -> RuntimeEvent:
        self.details.append(detail)
        return self.recorder.record(detail)


class RLMRunner:
    """Consume only an immutable prepared context and emit no terminal detail."""

    def __init__(self, *, factory: RLMFactoryLike | None = None) -> None:
        self._factory = factory or RLMFactory()

    def stream(self, context: RLMExecutionContext) -> TurnEventStream:
        outcome: list[RLMOutcome] = []
        ownership = _WorkerOwnership()
        events = self._generate(context, outcome, ownership)
        return TurnEventStream(events, lambda: outcome[-1], ownership)

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
            logger.warning("RLM execution failed (%s)", type(exc).__name__)
            outcome.append(
                RLMOutcome(
                    terminal_status=_terminal_status(exc),
                    usage=observed_usage(prediction[-1] if prediction else None, duration_ms=duration_ms),
                    public_error_message=_public_failure_message(exc),
                    duration_ms=duration_ms,
                )
            )
        finally:
            if not outcome:
                outcome.append(RLMOutcome(terminal_status="failed", public_error_message="Turn failed"))

    async def _run_success(
        self,
        context: RLMExecutionContext,
        outcome: list[RLMOutcome],
        ownership: _WorkerOwnership,
        prediction: list[Any],
        started: float,
    ) -> AsyncIterator[RuntimeEvent]:
        observations = _ObservationBuffer(EventRecorder(context.run_id, context.session_id))
        async for event in self._initial_events(context, observations):
            yield event
        spec, relay, guards, task = self._start_worker(context)
        ownership.task = task
        monitor = _WorkerMonitor(task, relay, context, lambda: self._drain_capability_details(context))
        async for event in self._worker_events(context, observations, relay, monitor):
            yield event
        prediction.append(task.result())
        if guards.integrity.unresolved:
            raise TurnIntegrityFailure
        async for event in self._prediction_events(context, observations, prediction[-1]):
            yield event
        duration_ms = int((time.perf_counter() - started) * 1000)
        result = prediction_result(
            prediction[-1],
            spec.signature,
            schema_id=spec.output_schema_id,
            schema_version=spec.output_schema_version,
            max_output_chars=context.options.max_output_chars,
        )
        outcome.append(
            RLMOutcome(
                terminal_status="completed",
                prediction=result,
                usage=observed_usage(prediction[-1], duration_ms=duration_ms),
                artifact_candidates=context.capabilities.drain_artifact_candidates(),
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
        for notice in context.preparation_notices:
            yield observations.recorder.record(WarningEvent(notice.message, notice.code))
        for item in self._drain_capability_details(context):
            yield observations.record(item)
        if await context.cancellation_requested():
            raise TurnCancelled

    def _start_worker(
        self, context: RLMExecutionContext
    ) -> tuple[RLMExecutionSpec, _DetailRelay, TurnToolGuards, asyncio.Task[Any]]:
        spec = context.capabilities.spec
        relay = _DetailRelay()
        guards = TurnToolGuards(required_targets=workspace_obligations(context.request))
        self._bind_observer(context.interpreter, relay, context.options.max_output_chars)

        def relay_capability_details(_result: Any) -> None:
            for detail in self._drain_capability_details(context):
                relay.publish(detail)

        observed_tools = tuple(
            observe_tool(
                tool,
                relay.publish,
                spec.tool_event_views.get(str(tool.name), ToolEventView.metadata_only()),
                after_result=(relay_capability_details if str(tool.name) == "load_skill" else None),
                is_authorized=lambda: not context.authority.revoked,
                guards=guards,
            )
            for tool in spec.tools
        )
        rlm = self._factory.create(
            models=context.models,
            options=context.options,
            interpreter=context.interpreter,
            tools=observed_tools or None,
            signature=spec.signature,
        )
        self._bind_observer(rlm, relay, context.options.max_output_chars)
        return spec, relay, guards, asyncio.create_task(self._execute_rlm_in_worker(rlm, context, spec))

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
        for item in _reconcile_trajectory(observations.details, trajectory, max_chars=context.options.max_output_chars):
            yield observations.recorder.record(item)
        final_reasoning = getattr(prediction, "final_reasoning", None)
        if isinstance(final_reasoning, str) and final_reasoning.strip():
            public_reasoning = truncate_public_text(final_reasoning, max_len=context.options.max_output_chars)
            if not self._has_reasoning(observations.details, public_reasoning, context.options.max_output_chars):
                item = RLMReasoning(public_reasoning)
                yield observations.record(item)
        for item in self._drain_capability_details(context):
            yield observations.record(item)

    @staticmethod
    def _bind_observer(target: Any, relay: _DetailRelay, max_chars: int) -> None:
        if type(target) is dspy.RLM:
            bind_native_rlm_observer(target, relay.publish, max_chars=max_chars)
            return
        bind = getattr(target, "bind_observer", None)
        if callable(bind):
            bind(relay.publish, max_chars=max_chars)

    @staticmethod
    def _has_reasoning(details: Sequence[ExecutionDetail], text: str, max_chars: int) -> bool:
        return any(
            isinstance(detail, RLMReasoning) and truncate_public_text(detail.text, max_len=max_chars) == text
            for detail in details
        )

    async def _execute_rlm(
        self,
        rlm: Any,
        context: RLMExecutionContext,
        spec: RLMExecutionSpec,
    ) -> Any:
        kwargs = build_rlm_input_kwargs(
            request=context.request,
            session_context=context.session_context,
            skill_cards=spec.skill_cards,
            attachments=context.attachments,
            workspace=spec.workspace,
        )
        with dspy.context(
            lm=context.models.root_lm,
            adapter=dspy.JSONAdapter(use_native_function_calling=True),
            track_usage=True,
        ):
            return await rlm.acall(**kwargs)

    async def _execute_rlm_in_worker(
        self,
        rlm: Any,
        context: RLMExecutionContext,
        spec: RLMExecutionSpec,
    ) -> Any:
        def run() -> Any:
            return asyncio.run(self._execute_rlm(rlm, context, spec))

        return await asyncio.to_thread(run)

    @staticmethod
    def _drain_capability_details(context: RLMExecutionContext) -> tuple[ExecutionDetail, ...]:
        values = context.capabilities.drain_public_details()
        if not all(isinstance(item, (AttachmentRead, SkillActivated, SkillLoaded, WarningEvent)) for item in values):
            raise TypeError("capability host returned an unsupported public detail")
        return cast(tuple[ExecutionDetail, ...], values)
