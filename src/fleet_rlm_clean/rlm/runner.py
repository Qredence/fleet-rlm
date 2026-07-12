"""Execute one recursive DSPy turn and stream non-terminal RuntimeEvents."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any, Protocol, Self

import dspy

from fleet_rlm_clean.observability.exporters import safe_export
from fleet_rlm_clean.observability.record import TurnTrace, apply_event_to_trace
from fleet_rlm_clean.rlm.cancel import get_run_cancel_registry
from fleet_rlm_clean.rlm.context import RLMTurnContext
from fleet_rlm_clean.rlm.errors import (
    RLMBudgetError,
    TurnCancelled,
    TurnTerminalError,
    TurnTimeout,
)
from fleet_rlm_clean.rlm.events import EventRecorder, RuntimeEvent, RuntimeEventKind
from fleet_rlm_clean.rlm.factory import RLMFactory
from fleet_rlm_clean.rlm.inputs import build_rlm_input_kwargs
from fleet_rlm_clean.rlm.outcome import TerminalStatus, TurnExecutionOutcome
from fleet_rlm_clean.rlm.sanitize import sanitize_public_error


class RLMFactoryLike(Protocol):
    def create(
        self,
        *,
        models: Any,
        budget: Any,
        interpreter: Any,
        tools: Sequence[Callable[..., Any]] | None = None,
        signature: Any = None,
        verbose: bool = False,
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
            )

            def emit(kind: RuntimeEventKind, payload: dict[str, Any] | None = None) -> RuntimeEvent:
                event = recorder.emit(kind, payload)
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
                yield emit(RuntimeEventKind.STATUS, {"message": "running"})

                await _raise_if_cancelled(context.run_id, cancel_probe=context.cancel_probe)

                rlm = self._factory.create(
                    models=context.models,
                    budget=context.budget,
                    interpreter=context.lease.interpreter,
                    tools=list(context.tools) if context.tools else None,
                )

                wall = max(1, int(getattr(context.budget, "max_wall_seconds", 300) or 300))
                try:
                    prediction = await asyncio.wait_for(
                        self._execute_rlm(rlm, context),
                        timeout=wall,
                    )
                except TimeoutError as exc:
                    raise TurnTimeout() from exc

                await _raise_if_cancelled(context.run_id, cancel_probe=context.cancel_probe)
                text = _prediction_text(prediction)

                if text:
                    yield emit(RuntimeEventKind.TEXT_DELTA, {"text": text})
                    yield emit(RuntimeEventKind.TEXT_COMPLETED, {"text": text})

                for kind, payload in _drain_host_public_events(context):
                    yield emit(kind, payload)

                usage = _usage_payload(prediction)
                yield emit(RuntimeEventKind.USAGE, {"usage": usage})

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
                    duration_ms=duration_ms,
                )
            except asyncio.CancelledError:
                duration_ms = int((time.perf_counter() - started) * 1000)
                outcome_holder["value"] = TurnExecutionOutcome(
                    terminal_status="cancelled",
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

    async def _execute_rlm(self, rlm: Any, context: RLMTurnContext) -> Any:
        """Apply root LM via scoped DSPy context; run off the event loop when sync."""
        root_lm = context.models.root_lm
        call_kwargs = build_rlm_input_kwargs(
            request=context.request,
            history=context.history,
            session_summary=context.session_summary,
            skill_cards=context.skill_cards,
            attachments=context.attachments,
        )
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
