"""Execute one already-prepared DSPy RLM and stream typed observations."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any, Protocol, Self, cast

import dspy

from fleet_rlm.rlm.context import RLMExecutionContext
from fleet_rlm.rlm.errors import RunBudgetError, TurnTerminalError
from fleet_rlm.rlm.events import (
    AttachmentRead,
    EventRecorder,
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
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    WarningEvent,
)
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.observable import DetailObserver, RLMDetail, RLMDetailKind
from fleet_rlm.rlm.outcome import ExecutionDetail, RLMOutcome, TerminalStatus
from fleet_rlm.rlm.sanitize import sanitize_public_error, sanitize_public_text, sanitize_public_value
from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint


class RLMFactoryLike(Protocol):
    def create(
        self,
        *,
        models: Any,
        budget: Any,
        interpreter: Any,
        tools: Sequence[Any] | None = None,
        signature: Any = None,
        verbose: bool = False,
        observer: DetailObserver | None = None,
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
        self._queue: asyncio.Queue[RLMDetail] = asyncio.Queue(maxsize=maxsize)
        self.overflowed = False

    def publish(self, detail: RLMDetail) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is self._loop:
            self._put(detail)
        else:
            self._loop.call_soon_threadsafe(self._put, detail)

    def _put(self, detail: RLMDetail) -> None:
        try:
            self._queue.put_nowait(detail)
        except asyncio.QueueFull:
            self.overflowed = True

    async def get(self) -> RLMDetail:
        return await self._queue.get()

    def drain(self) -> list[RLMDetail]:
        values: list[RLMDetail] = []
        while True:
            try:
                values.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                return values


def _json(value: Any) -> Any:
    return sanitize_public_value(value, max_len=3_000)


def _detail(value: RLMDetail) -> ExecutionDetail:
    data = value.payload
    step = data.get("step")
    normalized_step = int(step) if step is not None else None
    if value.kind is RLMDetailKind.STEP_STARTED:
        return StepStarted(step=int(data["step"]))
    if value.kind is RLMDetailKind.STEP_FINISHED:
        duration = data.get("duration_ms")
        return StepFinished(
            step=int(data["step"]),
            duration_ms=int(duration) if duration is not None else None,
        )
    if value.kind is RLMDetailKind.REASONING:
        return RLMReasoning(text=str(data.get("text") or ""), step=normalized_step)
    if value.kind is RLMDetailKind.CODE:
        return RLMCode(code=str(data.get("code") or ""), step=normalized_step)
    if value.kind is RLMDetailKind.OUTPUT:
        return RLMOutput(output=str(data.get("output") or ""), step=normalized_step)
    if value.kind is RLMDetailKind.TOOL_STARTED:
        return ToolStarted(
            tool_call_id=str(data.get("tool_call_id") or ""),
            tool_name=str(data.get("tool_name") or ""),
            input=_json(data.get("input")),
        )
    if value.kind is RLMDetailKind.TOOL_COMPLETED:
        return ToolCompleted(
            tool_call_id=str(data.get("tool_call_id") or ""),
            tool_name=str(data.get("tool_name") or ""),
            output=_json(data.get("output")),
        )
    if value.kind is RLMDetailKind.TOOL_FAILED:
        return ToolFailed(
            tool_call_id=str(data.get("tool_call_id") or ""),
            tool_name=str(data.get("tool_name") or ""),
            error=str(data.get("error") or "Tool failed"),
        )
    raise AssertionError(f"unhandled RLM detail: {value.kind}")


def _prediction_text(prediction: Any) -> str:
    if prediction is None:
        return ""
    if hasattr(prediction, "answer"):
        return str(prediction.answer)
    if isinstance(prediction, Mapping) and "answer" in prediction:
        return str(prediction["answer"])
    return str(prediction)


def _terminal_status(exc: BaseException) -> TerminalStatus:
    if isinstance(exc, TurnTerminalError):
        return cast(TerminalStatus, exc.status)
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, RunBudgetError):
        return "budget_exhausted"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "failed"


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
            try:
                yield recorder.record(RunStarted(delivery="live"))
                yield recorder.record(Status("execution", "running"))
                for notice in context.preparation_notices:
                    yield recorder.record(WarningEvent(notice.message, notice.code))
                if await context.cancellation_requested():
                    raise asyncio.CancelledError

                blueprint = cast(TurnCapabilityBlueprint, context.capabilities.blueprint)
                for card in blueprint.activated_skills:
                    item = SkillActivated(str(card.id), card.name, card.version, card.trust, tuple(card.affordances))
                    details.append(item)
                    yield recorder.record(item)

                relay = _DetailRelay()
                rlm = self._factory.create(
                    models=context.models,
                    budget=context.budget.budget,
                    interpreter=context.interpreter,
                    tools=blueprint.tools or None,
                    signature=blueprint.signature,
                    observer=relay.publish,
                )
                task = asyncio.create_task(self._execute_rlm(rlm, context, blueprint))
                try:
                    while not task.done():
                        if await context.cancellation_requested():
                            task.cancel()
                            raise asyncio.CancelledError
                        remaining = context.deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            task.cancel()
                            raise asyncio.TimeoutError
                        pending = asyncio.create_task(relay.get())
                        done, _ = await asyncio.wait(
                            {task, pending}, timeout=min(remaining, 0.25), return_when=asyncio.FIRST_COMPLETED
                        )
                        if pending in done:
                            item = _detail(pending.result())
                            details.append(item)
                            yield recorder.record(item)
                        else:
                            pending.cancel()
                            await asyncio.gather(pending, return_exceptions=True)
                    prediction = await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

                for observed in relay.drain():
                    item = _detail(observed)
                    details.append(item)
                    yield recorder.record(item)
                if relay.overflowed:
                    warning = WarningEvent("some detailed execution events were omitted")
                    details.append(warning)
                    yield recorder.record(warning)

                for item in context.capabilities.drain_public_details():
                    if not isinstance(item, (AttachmentRead, SkillLoaded, WarningEvent)):
                        raise TypeError("capability host returned an unsupported public detail")
                    details.append(item)
                    yield recorder.record(item)

                text = sanitize_public_text(
                    _prediction_text(prediction), max_len=context.budget.budget.max_output_chars
                )
                context.budget.consume_output_chars(max(1, len(text)))
                usage = context.budget.snapshot()
                usage.update(
                    {
                        "tool_calls": int(getattr(rlm, "tool_calls_used", usage["tool_calls"])),
                        "llm_calls": int(getattr(rlm, "sub_lm_calls_used", usage["llm_calls"])),
                    }
                )
                structured = None
                schema_id = schema_version = None
                if blueprint.task_contract is not None:
                    structured = _json(blueprint.task_contract.serialize(prediction))
                    for validator in blueprint.validators:
                        validator(structured)
                    schema_id = blueprint.task_contract.id
                    schema_version = blueprint.task_contract.schema_version
                outcome.append(
                    RLMOutcome(
                        terminal_status="completed",
                        text=text,
                        usage=usage,
                        artifact_candidates=context.capabilities.drain_artifact_candidates(),
                        execution_details=tuple(details),
                        structured_output=structured,
                        result_schema_id=schema_id,
                        result_schema_version=schema_version,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                )
            except (GeneratorExit, asyncio.CancelledError):
                outcome.append(
                    RLMOutcome(
                        terminal_status="cancelled",
                        usage=context.budget.snapshot(),
                        public_error_message="Turn cancelled",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                )
                raise
            except Exception as exc:
                outcome.append(
                    RLMOutcome(
                        terminal_status=_terminal_status(exc),
                        usage=context.budget.snapshot(),
                        public_error_message=sanitize_public_error(exc),
                        duration_ms=int((time.perf_counter() - started) * 1000),
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
            if blueprint.knowledge and "capability_knowledge" in fields:
                kwargs["capability_knowledge"] = list(blueprint.knowledge)
        else:
            kwargs = {
                "request": context.request,
                "history": [{"role": message.role, "content": message.content} for message in context.history],
                "session_summary": "",
                "skill_cards": [],
                "attachments": [
                    {
                        "id": str(item.attachment_id),
                        "filename": item.filename,
                        "content_type": item.content_type,
                        "byte_size": item.byte_size,
                        "checksum_sha256": item.checksum_sha256,
                    }
                    for item in context.attachments
                ],
            }
            if blueprint.knowledge:
                kwargs["capability_knowledge"] = list(blueprint.knowledge)
            kwargs.update(blueprint.input_values)
        with dspy.context(lm=context.models.root_lm):
            return await rlm.acall(**kwargs)
