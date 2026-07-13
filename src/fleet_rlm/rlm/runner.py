"""Execute one recursive DSPy turn and stream non-terminal RuntimeEvents."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, Self

import dspy

from fleet_rlm.observability.exporters import safe_export
from fleet_rlm.observability.record import TurnTrace, apply_event_to_trace
from fleet_rlm.rlm.cancel import get_run_cancel_registry
from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.errors import (
    RLMBudgetError,
    TurnBudgetExhausted,
    TurnCancelled,
    TurnTerminalError,
    TurnTimeout,
)
from fleet_rlm.rlm.events import EventRecorder, RuntimeEvent, RuntimeEventKind
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.inputs import build_rlm_input_kwargs
from fleet_rlm.rlm.observable import DetailObserver, RLMDetail, RLMDetailKind
from fleet_rlm.rlm.outcome import TerminalStatus, TurnExecutionOutcome
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
    """Async iterator of non-terminal events; ``outcome`` after the stream ends."""

    def __init__(
        self,
        agen: AsyncIterator[RuntimeEvent],
        *,
        outcome: TurnExecutionOutcome | None = None,
        outcome_factory: Callable[[], TurnExecutionOutcome] | None = None,
    ) -> None:
        self._agen = agen.__aiter__()
        self._outcome = outcome
        self._outcome_factory = outcome_factory
        self._finished = False

    @property
    def outcome(self) -> TurnExecutionOutcome | None:
        return self._outcome

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> RuntimeEvent:
        try:
            return await self._agen.__anext__()
        except StopAsyncIteration:
            if not self._finished:
                self._finished = True
                if self._outcome is None and self._outcome_factory is not None:
                    self._outcome = self._outcome_factory()
            raise

    async def aclose(self) -> None:
        """Close the wrapped runner generator and finalize its measured outcome."""
        if self._finished:
            return
        close = getattr(self._agen, "aclose", None)
        if callable(close):
            await close()
        self._finished = True
        if self._outcome is None and self._outcome_factory is not None:
            self._outcome = self._outcome_factory()


def _prediction_text(prediction: Any) -> str:
    if prediction is None:
        return ""
    answer = getattr(prediction, "answer", None)
    if answer is not None:
        return str(answer)
    if isinstance(prediction, dict) and "answer" in prediction:
        return str(prediction["answer"])
    return str(prediction)


def _usage_payload(prediction: Any) -> dict[str, Any]:
    getter = getattr(prediction, "get_lm_usage", None)
    if callable(getter):
        try:
            usage = getter()
        except Exception:  # noqa: BLE001 - usage is best-effort for public events
            usage = None
        if usage:
            return dict(usage) if isinstance(usage, dict) else {"usage": usage}
    return {}


def _model_profile(lm: Any) -> str:
    value = getattr(lm, "model", None) or getattr(lm, "model_name", None) or type(lm).__name__
    return sanitize_public_text(str(value), max_len=120)


def _runtime_usage(
    usage: dict[str, Any],
    *,
    rlm: Any,
    context: RLMTurnContext,
    details: list[dict[str, Any]],
) -> dict[str, Any]:
    result = dict(usage)
    result.update(
        {
            "root_model_profile": _model_profile(context.models.root_lm),
            "sub_model_profile": _model_profile(context.models.sub_lm),
            "iterations": sum(1 for item in details if item.get("kind") == RuntimeEventKind.STEP_STARTED.value),
            "tool_calls": int(getattr(rlm, "tool_calls_used", 0) or 0),
            "sub_lm_calls": int(getattr(rlm, "sub_lm_calls_used", 0) or 0),
            "iteration_limit": context.budget.max_iterations,
            "tool_call_limit": context.budget.max_tool_calls,
            "sub_lm_call_limit": context.budget.max_llm_calls,
            "sub_lm_concurrency_limit": context.budget.max_sub_lm_concurrency,
            "estimated_cost": result.get("estimated_cost") or result.get("cost"),
        }
    )
    return result


_DETAIL_KIND_MAP: dict[RLMDetailKind, RuntimeEventKind] = {
    RLMDetailKind.STEP_STARTED: RuntimeEventKind.STEP_STARTED,
    RLMDetailKind.STEP_FINISHED: RuntimeEventKind.STEP_FINISHED,
    RLMDetailKind.REASONING: RuntimeEventKind.RLM_REASONING,
    RLMDetailKind.CODE: RuntimeEventKind.RLM_CODE,
    RLMDetailKind.OUTPUT: RuntimeEventKind.RLM_OUTPUT,
    RLMDetailKind.TOOL_STARTED: RuntimeEventKind.TOOL_STARTED,
    RLMDetailKind.TOOL_COMPLETED: RuntimeEventKind.TOOL_COMPLETED,
    RLMDetailKind.TOOL_FAILED: RuntimeEventKind.TOOL_FAILED,
}


class _DetailRelay:
    """Thread-safe, nonblocking detail relay with one overflow signal."""

    def __init__(self, *, maxsize: int = 256) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[RLMDetail] = asyncio.Queue(maxsize=maxsize)
        self.overflowed = False

    def publish(self, detail: RLMDetail) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
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


class HostEventSource(Protocol):
    """Internal seam: host tool ledgers that emit safe public event dicts."""

    def drain_public_events(self) -> list[dict[str, Any]]: ...


def _map_host_ledger_item(
    item: dict[str, Any],
) -> tuple[RuntimeEventKind, dict[str, Any]] | None:
    """Map one host ledger dict to a public RuntimeEvent kind + payload."""
    kind = item.get("event_kind")
    if kind == "attachment.read":
        return (
            RuntimeEventKind.ATTACHMENT_READ,
            {
                "attachment_id": str(item.get("attachment_id", "")),
                "filename": str(item.get("filename", "")),
                "byte_size": int(item.get("byte_size") or 0),
            },
        )
    if kind in (None, "skill.loaded") and item.get("skill_id"):
        return (
            RuntimeEventKind.SKILL_LOADED,
            {
                "skill_id": str(item.get("skill_id", "")),
                "name": str(item.get("name", "")),
                "version": str(item.get("version", "")),
                "trust": str(item.get("trust", "")),
            },
        )
    return None


def _iter_host_event_sources(context: RLMTurnContext) -> list[Any]:
    sources: list[Any] = []
    for attr in ("skill_tool_host", "file_tool_host"):
        host = getattr(context, attr, None)
        if host is not None and callable(getattr(host, "drain_public_events", None)):
            sources.append(host)
    return sources


def _drain_host_public_events(
    context: RLMTurnContext,
) -> list[tuple[RuntimeEventKind, dict[str, Any]]]:
    """Collect safe host-tool events for SSE (never bodies/paths)."""
    out: list[tuple[RuntimeEventKind, dict[str, Any]]] = []
    for host in _iter_host_event_sources(context):
        try:
            for item in host.drain_public_events() or []:
                if not isinstance(item, dict):
                    continue
                mapped = _map_host_ledger_item(item)
                if mapped is not None:
                    out.append(mapped)
        except Exception:  # noqa: BLE001 - host ledger must not break the public stream
            continue
    return out


async def _raise_if_cancelled(run_id: Any, *, cancel_probe: Any = None) -> None:
    registry = get_run_cancel_registry()
    if registry.is_cancelled(run_id):
        raise TurnCancelled()
    if cancel_probe is not None:
        requested = await cancel_probe(run_id)
        if requested:
            registry.request_cancel(run_id)
            raise TurnCancelled()


def _terminal_status_for(exc: BaseException) -> TerminalStatus:
    if isinstance(exc, TurnTerminalError):
        status = exc.status
        mapping: dict[str, TerminalStatus] = {
            "completed": "completed",
            "cancelled": "cancelled",
            "timeout": "timeout",
            "budget_exhausted": "budget_exhausted",
            "failed": "failed",
        }
        if status in mapping:
            return mapping[status]
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, RLMBudgetError):
        return "budget_exhausted"
    name = type(exc).__name__.lower()
    if "budget" in name or "max_llm" in str(exc).lower() or "max_iter" in str(exc).lower():
        return "budget_exhausted"
    if "cancel" in name:
        return "cancelled"
    if "timeout" in name:
        return "timeout"
    return "failed"


class RLMRunner:
    """Deep module: one turn in, non-terminal RuntimeEvents + TurnExecutionOutcome."""

    def __init__(
        self,
        *,
        factory: RLMFactoryLike | None = None,
        turn_exporter: Any | None = None,
    ) -> None:
        self._factory: RLMFactoryLike = factory if factory is not None else RLMFactory()
        self._turn_exporter = turn_exporter

    def stream(self, context: RLMTurnContext) -> TurnEventStream:
        """Run one turn. Yields non-terminal events only; outcome on the stream handle."""
        outcome_holder: dict[str, TurnExecutionOutcome | None] = {"value": None}

        async def _agen() -> AsyncIterator[RuntimeEvent]:
            recorder = EventRecorder(run_id=context.run_id, session_id=context.session_id)
            started = time.perf_counter()
            registry = get_run_cancel_registry()
            registry.bind(
                context.run_id,
                user_id=context.user_id,
                workspace_id=context.workspace_id,
                session_id=context.session_id,
            )
            lease = context.lease
            trace = TurnTrace(
                run_id=context.run_id,
                session_id=context.session_id,
                user_id=context.user_id,
                workspace_id=context.workspace_id,
                sandbox_id=getattr(lease, "sandbox_id", None),
                volume_id=getattr(lease, "volume_id", None),
                mount_path=getattr(lease, "mount_path", None),
                model_profiles={
                    "root": _model_profile(context.models.root_lm),
                    "sub": _model_profile(context.models.sub_lm),
                },
                budget_limits={
                    "iterations": context.budget.max_iterations,
                    "sub_lm_calls": context.budget.max_llm_calls,
                    "sub_lm_concurrency": context.budget.max_sub_lm_concurrency,
                    "tool_calls": context.budget.max_tool_calls,
                    "wall_seconds": context.budget.max_wall_seconds,
                },
            )
            durable_details: list[dict[str, Any]] = []
            rlm: Any = None

            def emit(kind: RuntimeEventKind, payload: dict[str, Any] | None = None) -> RuntimeEvent:
                event = recorder.emit(kind, payload)
                if kind not in {RuntimeEventKind.RUN_STARTED, RuntimeEventKind.STATUS}:
                    durable_details.append({"kind": kind.value, "payload": dict(event.payload)})
                try:
                    apply_event_to_trace(trace, kind.value, dict(event.payload))
                except Exception:  # noqa: BLE001
                    pass
                return event

            try:
                yield emit(
                    RuntimeEventKind.RUN_STARTED,
                    {
                        "user_id": str(context.user_id),
                        "workspace_id": str(context.workspace_id),
                    },
                )
                yield emit(RuntimeEventKind.STATUS, {"phase": "selection", "status": "running"})

                await _raise_if_cancelled(context.run_id, cancel_probe=context.cancel_probe)

                resolver = getattr(context, "capability_resolver", None)
                if resolver is not None:
                    blueprint = await resolver.resolve(context)
                else:
                    blueprint = TurnCapabilityBlueprint(tools=tuple(context.tools or ()))
                yield emit(
                    RuntimeEventKind.STATUS,
                    {
                        "phase": "execution",
                        "status": "running",
                        "selectedSkillIds": [str(card.id) for card in blueprint.activated_skills],
                        "primarySchemaId": (
                            blueprint.task_contract.id if blueprint.task_contract is not None else None
                        ),
                    },
                )
                for card in blueprint.activated_skills:
                    yield emit(
                        RuntimeEventKind.SKILL_ACTIVATED,
                        {
                            "skill_id": str(card.id),
                            "name": card.name,
                            "version": card.version,
                            "trust": card.trust,
                            "affordances": list(card.affordances),
                        },
                    )

                relay = _DetailRelay()

                rlm = self._factory.create(
                    models=context.models,
                    budget=context.budget,
                    interpreter=context.lease.interpreter,
                    tools=list(blueprint.tools) if blueprint.tools else None,
                    signature=blueprint.signature,
                    observer=relay.publish,
                )

                wall = max(1, int(getattr(context.budget, "max_wall_seconds", 300) or 300))
                execution: asyncio.Task[Any] | None = None
                pending_get: asyncio.Task[RLMDetail] | None = None
                try:
                    execute = self._execute_rlm
                    if "blueprint" in inspect.signature(execute).parameters:
                        execution = asyncio.create_task(execute(rlm, context, blueprint=blueprint))
                    else:  # Compatibility for narrow test/custom runner subclasses.
                        execution = asyncio.create_task(execute(rlm, context))
                    deadline = asyncio.get_running_loop().time() + wall
                    while not execution.done():
                        if bool(getattr(rlm, "tool_budget_exhausted", False)):
                            raise TurnBudgetExhausted()
                        await _raise_if_cancelled(
                            context.run_id,
                            cancel_probe=context.cancel_probe,
                        )
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            execution.cancel()
                            raise TurnTimeout()
                        pending_get = asyncio.create_task(relay.get())
                        done, _ = await asyncio.wait(
                            {execution, pending_get},
                            timeout=min(remaining, 0.25),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if pending_get in done:
                            detail = pending_get.result()
                            pending_get = None
                            yield emit(_DETAIL_KIND_MAP[detail.kind], detail.payload)
                        elif pending_get is not None:
                            pending_get.cancel()
                            await asyncio.gather(pending_get, return_exceptions=True)
                            pending_get = None
                    prediction = await execution
                    await asyncio.sleep(0)
                    for detail in relay.drain():
                        yield emit(_DETAIL_KIND_MAP[detail.kind], detail.payload)
                    if relay.overflowed:
                        yield emit(RuntimeEventKind.WARNING, {"message": "some detailed execution events were omitted"})
                    if bool(getattr(rlm, "tool_budget_exhausted", False)):
                        raise TurnBudgetExhausted()
                except TimeoutError as exc:
                    raise TurnTimeout() from exc
                finally:
                    cleanup_tasks: list[asyncio.Task[Any]] = []
                    if pending_get is not None:
                        pending_get.cancel()
                        cleanup_tasks.append(pending_get)
                    if execution is not None and not execution.done():
                        execution.cancel()
                        cleanup_tasks.append(execution)
                    if cleanup_tasks:
                        await asyncio.gather(*cleanup_tasks, return_exceptions=True)

                await _raise_if_cancelled(context.run_id, cancel_probe=context.cancel_probe)
                text = sanitize_public_text(
                    _prediction_text(prediction),
                    max_len=context.budget.max_output_chars,
                )

                for kind, payload in _drain_host_public_events(context):
                    yield emit(kind, payload)

                usage = _runtime_usage(
                    _usage_payload(prediction),
                    rlm=rlm,
                    context=context,
                    details=durable_details,
                )

                structured_output: dict[str, Any] | None = None
                schema_id: str | None = None
                schema_version: str | None = None
                if blueprint.task_contract is not None:
                    serialized = blueprint.task_contract.serialize(prediction)
                    structured_output = dict(sanitize_public_value(serialized, max_len=context.budget.max_output_chars))
                    for validator in blueprint.validators:
                        validator(structured_output)
                    schema_id = blueprint.task_contract.id
                    schema_version = blueprint.task_contract.schema_version

                artifact_candidates = ()
                file_host = getattr(context, "file_tool_host", None)
                drain_candidates = getattr(file_host, "drain_artifact_candidates", None)
                if callable(drain_candidates):
                    artifact_candidates = tuple(drain_candidates())

                duration_ms = int((time.perf_counter() - started) * 1000)
                outcome_holder["value"] = TurnExecutionOutcome(
                    terminal_status="completed",
                    assistant_text=text,
                    usage=usage,
                    artifact_candidates=artifact_candidates,
                    detail_parts=tuple(durable_details),
                    structured_output=structured_output,
                    result_schema_id=schema_id,
                    result_schema_version=schema_version,
                    duration_ms=duration_ms,
                )
            except (GeneratorExit, asyncio.CancelledError):
                duration_ms = int((time.perf_counter() - started) * 1000)
                outcome_holder["value"] = TurnExecutionOutcome(
                    terminal_status="cancelled",
                    usage=_runtime_usage({}, rlm=rlm, context=context, details=durable_details),
                    public_error_message="Turn cancelled",
                    duration_ms=duration_ms,
                )
                raise
            except Exception as exc:  # noqa: BLE001 - map to outcome; never emit public terminal
                for kind, payload in _drain_host_public_events(context):
                    yield emit(kind, payload)
                duration_ms = int((time.perf_counter() - started) * 1000)
                outcome_holder["value"] = TurnExecutionOutcome(
                    terminal_status=_terminal_status_for(exc),
                    usage=_runtime_usage({}, rlm=rlm, context=context, details=durable_details),
                    public_error_message=sanitize_public_error(exc),
                    duration_ms=duration_ms,
                )
            finally:
                registry.mark_terminal(context.run_id)
                if outcome_holder["value"] is None:
                    outcome_holder["value"] = TurnExecutionOutcome(
                        terminal_status="failed",
                        public_error_message="Turn ended without outcome",
                    )
                outcome = outcome_holder["value"]
                trace.terminal_status = outcome.terminal_status
                if outcome.usage:
                    trace.usage = dict(outcome.usage)
                if outcome.duration_ms is not None:
                    trace.duration_ms = outcome.duration_ms
                if outcome.public_error_message:
                    trace.error_message = outcome.public_error_message
                trace.finished_at = datetime.now(UTC)
                safe_export(self._turn_exporter, trace)

        return TurnEventStream(
            _agen(),
            outcome_factory=lambda: (
                outcome_holder["value"]
                or TurnExecutionOutcome(
                    terminal_status="failed",
                    public_error_message="Turn ended without outcome",
                )
            ),
        )

    async def _execute_rlm(
        self,
        rlm: Any,
        context: RLMTurnContext,
        *,
        blueprint: TurnCapabilityBlueprint | None = None,
    ) -> Any:
        """Apply root LM via scoped DSPy context; run off the event loop when sync."""
        root_lm = context.models.root_lm
        if blueprint is not None and blueprint.task_contract is not None:
            call_kwargs = dict(blueprint.input_values)
            signature_fields = getattr(blueprint.signature, "fields", {})
            if blueprint.knowledge and "capability_knowledge" in signature_fields:
                call_kwargs["capability_knowledge"] = list(blueprint.knowledge)
        else:
            visible_cards = (
                blueprint.activated_skills
                if blueprint is not None and getattr(context, "capability_resolver", None) is not None
                else context.skill_cards
            )
            call_kwargs = build_rlm_input_kwargs(
                request=context.request,
                history=context.history,
                session_summary=context.session_summary,
                skill_cards=visible_cards,
                attachments=context.attachments,
            )
            if blueprint is not None and blueprint.knowledge:
                call_kwargs["capability_knowledge"] = list(blueprint.knowledge)
            if blueprint is not None:
                call_kwargs.update(blueprint.input_values)
        await _raise_if_cancelled(context.run_id, cancel_probe=context.cancel_probe)

        aforward = getattr(rlm, "aforward", None)
        if callable(aforward):

            async def _async_call() -> Any:
                with dspy.settings.context(lm=root_lm):
                    return await aforward(**call_kwargs)

            return await _async_call()

        def _sync_call() -> Any:
            with dspy.settings.context(lm=root_lm):
                if callable(rlm):
                    return rlm(**call_kwargs)
                forward = getattr(rlm, "forward", None)
                if callable(forward):
                    return forward(**call_kwargs)
                msg = "RLM module is not callable"
                raise TypeError(msg)

        return await asyncio.to_thread(_sync_call)
