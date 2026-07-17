"""Execute one already-prepared DSPy RLM and stream typed observations."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any, Protocol, Self, cast

import dspy

from fleet_rlm.rlm.context import RLMExecutionContext
from fleet_rlm.rlm.dspy_contract import PredictionOutputError, observed_usage, prediction_result
from fleet_rlm.rlm.errors import TurnCancelled, TurnTerminalError
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
from fleet_rlm.rlm.inputs import build_rlm_input_kwargs, skill_card_metadata
from fleet_rlm.rlm.outcome import ExecutionDetail, RLMOutcome, TerminalStatus
from fleet_rlm.rlm.sanitize import truncate_public_text
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool
from fleet_rlm.skills.capabilities import DEFAULT_TASK_CONTRACT, TurnCapabilityBlueprint


class RLMFactoryLike(Protocol):
    def create(
        self,
        *,
        models: Any,
        options: Any,
        interpreter: Any,
        tools: Sequence[dspy.Tool] | None = None,
        signature: Any = None,
        verbose: bool = False,
    ) -> Any: ...


class TurnEventStream:
    """Async observation iterator with its measured outcome after completion."""

    def __init__(self, agen: AsyncIterator[RuntimeEvent], outcome_factory: Callable[[], RLMOutcome]) -> None:
        self._agen = agen.__aiter__()
        self._outcome_factory = outcome_factory
        self._outcome: RLMOutcome | None = None
        self._finished = False

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


def _observation_key(value: ExecutionDetail) -> tuple[type[object], int | None] | None:
    if isinstance(value, (StepStarted, StepFinished, RLMReasoning, RLMCode, RLMOutput)):
        return type(value), value.step
    return None


def _trajectory_details(prediction: Any, *, max_chars: int) -> list[ObservationDetail]:
    trajectory = getattr(prediction, "trajectory", None)
    if not isinstance(trajectory, Sequence) or isinstance(trajectory, (str, bytes, bytearray)):
        return []
    details: list[ObservationDetail] = []
    for step, raw in enumerate(trajectory, start=1):
        if not isinstance(raw, Mapping):
            continue
        details.append(StepStarted(step))
        if "reasoning" in raw:
            details.append(RLMReasoning(truncate_public_text(str(raw.get("reasoning") or ""), max_len=max_chars), step))
        if "code" in raw:
            details.append(RLMCode(truncate_public_text(str(raw.get("code") or ""), max_len=max_chars), step))
        if "output" in raw:
            output = str(raw.get("output") or "")
            if output.startswith("FINAL:"):
                output = "FINAL submitted"
            details.append(RLMOutput(truncate_public_text(output, max_len=max_chars), step))
        details.append(StepFinished(step))
    return details


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
        return PredictionOutputError.public_message
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


class RLMRunner:
    """Consume only an immutable prepared context and emit no terminal detail."""

    def __init__(self, *, factory: RLMFactoryLike | None = None, turn_exporter: Any | None = None) -> None:
        self._factory = factory or RLMFactory()
        self._turn_exporter = turn_exporter

    def stream(self, context: RLMExecutionContext) -> TurnEventStream:
        outcome: list[RLMOutcome] = []

        async def generate() -> AsyncIterator[RuntimeEvent]:
            recorder = EventRecorder(context.run_id, context.session_id)
            started = time.perf_counter()
            details: list[ExecutionDetail] = []
            rlm: Any = None
            prediction: Any | None = None
            try:
                yield recorder.record(RunStarted(delivery="live"))
                yield recorder.record(Status("execution", "running"))
                for notice in context.preparation_notices:
                    yield recorder.record(WarningEvent(notice.message, notice.code))
                for item in self._drain_capability_details(context):
                    details.append(item)
                    yield recorder.record(item)
                if await context.cancellation_requested():
                    raise TurnCancelled

                blueprint = cast(TurnCapabilityBlueprint, context.capabilities.blueprint)
                relay = _DetailRelay()
                bind_observer = getattr(context.interpreter, "bind_observer", None)
                if callable(bind_observer):
                    bind_observer(relay.publish, max_chars=context.options.max_output_chars)

                def relay_capability_details(_result: Any) -> None:
                    for detail in self._drain_capability_details(context):
                        relay.publish(detail)

                observed_tools = tuple(
                    observe_tool(
                        tool,
                        relay.publish,
                        blueprint.tool_event_views.get(str(tool.name), ToolEventView.metadata_only()),
                        after_result=(relay_capability_details if str(tool.name) == "load_skill" else None),
                    )
                    for tool in blueprint.tools
                )
                rlm = self._factory.create(
                    models=context.models,
                    options=context.options,
                    interpreter=context.interpreter,
                    tools=observed_tools or None,
                    signature=blueprint.signature,
                )
                task = asyncio.create_task(self._execute_rlm_in_worker(rlm, context, blueprint))
                pending: asyncio.Task[ExecutionDetail] | None = None
                intended_stop: BaseException | None = None
                caller_cancelled = False
                try:
                    while not task.done():
                        if await context.cancellation_requested():
                            intended_stop = TurnCancelled()
                            break
                        remaining = context.deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            intended_stop = asyncio.TimeoutError()
                            break
                        pending = asyncio.create_task(relay.get())
                        done, _ = await asyncio.wait(
                            {task, pending}, timeout=min(remaining, 0.25), return_when=asyncio.FIRST_COMPLETED
                        )
                        if pending in done:
                            item = pending.result()
                            details.append(item)
                            yield recorder.record(item)
                        else:
                            pending.cancel()
                            await asyncio.gather(pending, return_exceptions=True)
                        pending = None
                        for item in self._drain_capability_details(context):
                            details.append(item)
                            yield recorder.record(item)
                except asyncio.CancelledError:
                    caller_cancelled = True
                finally:
                    if pending is not None and not pending.done():
                        pending.cancel()
                        caller_cancelled |= await _settle_worker(pending)
                    caller_cancelled |= await _settle_worker(task)

                for item in self._drain_capability_details(context):
                    details.append(item)
                    yield recorder.record(item)
                for observed in relay.drain():
                    details.append(observed)
                    yield recorder.record(observed)
                if relay.overflowed:
                    warning = WarningEvent("some detailed execution events were omitted")
                    details.append(warning)
                    yield recorder.record(warning)

                if caller_cancelled:
                    if task.done() and not task.cancelled():
                        task.exception()
                    raise asyncio.CancelledError
                if intended_stop is not None:
                    if task.done() and not task.cancelled():
                        task.exception()
                    raise intended_stop

                prediction = task.result()

                seen = {key for item in details if (key := _observation_key(item)) is not None}
                for item in _trajectory_details(prediction, max_chars=context.options.max_output_chars):
                    key = _observation_key(item)
                    if key is not None and key in seen:
                        continue
                    if key is not None:
                        seen.add(key)
                    details.append(item)
                    yield recorder.record(item)
                final_reasoning = getattr(prediction, "final_reasoning", None)
                if isinstance(final_reasoning, str) and final_reasoning.strip():
                    item = RLMReasoning(truncate_public_text(final_reasoning, max_len=context.options.max_output_chars))
                    details.append(item)
                    yield recorder.record(item)

                for item in self._drain_capability_details(context):
                    details.append(item)
                    yield recorder.record(item)

                duration_ms = int((time.perf_counter() - started) * 1000)
                usage = observed_usage(prediction, duration_ms=duration_ms)
                result = prediction_result(
                    prediction,
                    blueprint.task_contract or DEFAULT_TASK_CONTRACT,
                    max_output_chars=context.options.max_output_chars,
                )
                for validator in blueprint.validators:
                    try:
                        validator(result.outputs)
                    except Exception:
                        raise PredictionOutputError from None
                outcome.append(
                    RLMOutcome(
                        terminal_status="completed",
                        prediction=result,
                        usage=usage,
                        artifact_candidates=context.capabilities.drain_artifact_candidates(),
                        execution_details=tuple(details),
                        duration_ms=duration_ms,
                    )
                )
            except (GeneratorExit, asyncio.CancelledError):
                duration_ms = int((time.perf_counter() - started) * 1000)
                outcome.append(
                    RLMOutcome(
                        terminal_status="cancelled",
                        usage=observed_usage(prediction, duration_ms=duration_ms),
                        public_error_message="Turn cancelled",
                        duration_ms=duration_ms,
                    )
                )
                raise
            except Exception as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                outcome.append(
                    RLMOutcome(
                        terminal_status=_terminal_status(exc),
                        usage=observed_usage(prediction, duration_ms=duration_ms),
                        public_error_message=_public_failure_message(exc),
                        duration_ms=duration_ms,
                    )
                )
            finally:
                if not outcome:
                    outcome.append(RLMOutcome(terminal_status="failed", public_error_message="Turn failed"))

        return TurnEventStream(generate(), lambda: outcome[-1])

    async def _execute_rlm(
        self,
        rlm: Any,
        context: RLMExecutionContext,
        blueprint: TurnCapabilityBlueprint,
    ) -> Any:
        if blueprint.task_contract is not None:
            kwargs = dict(blueprint.input_values)
            fields = getattr(blueprint.signature, "fields", {})
            if "skill_cards" in fields:
                kwargs["skill_cards"] = [skill_card_metadata(card) for card in blueprint.skill_cards]
            if blueprint.knowledge and "capability_knowledge" in fields:
                kwargs["capability_knowledge"] = list(blueprint.knowledge)
        else:
            kwargs = build_rlm_input_kwargs(
                request=context.request,
                session_context=context.session_context,
                skill_cards=blueprint.skill_cards,
                attachments=context.attachments,
                workspace=blueprint.workspace,
            )
            if blueprint.knowledge:
                kwargs["capability_knowledge"] = list(blueprint.knowledge)
            kwargs.update(blueprint.input_values)
        with dspy.context(lm=context.models.root_lm, track_usage=True):
            return await rlm.acall(**kwargs)

    async def _execute_rlm_in_worker(
        self,
        rlm: Any,
        context: RLMExecutionContext,
        blueprint: TurnCapabilityBlueprint,
    ) -> Any:
        def run() -> Any:
            return asyncio.run(self._execute_rlm(rlm, context, blueprint))

        return await asyncio.to_thread(run)

    @staticmethod
    def _drain_capability_details(context: RLMExecutionContext) -> tuple[ExecutionDetail, ...]:
        values = context.capabilities.drain_public_details()
        if not all(isinstance(item, (AttachmentRead, SkillActivated, SkillLoaded, WarningEvent)) for item in values):
            raise TypeError("capability host returned an unsupported public detail")
        return cast(tuple[ExecutionDetail, ...], values)
