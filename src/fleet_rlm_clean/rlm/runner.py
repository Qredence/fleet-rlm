"""Execute one recursive DSPy turn and stream public RuntimeEvents."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any, Protocol

import dspy

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
            return {"usage": usage}
    return {"usage": {}}


def _drain_host_public_events(
    context: RLMTurnContext,
) -> list[tuple[RuntimeEventKind, dict[str, Any]]]:
    """Collect safe skill/file host events for SSE (never bodies/paths)."""
    out: list[tuple[RuntimeEventKind, dict[str, Any]]] = []

    skill_host = getattr(context, "skill_tool_host", None)
    if skill_host is not None:
        drain = getattr(skill_host, "drain_public_events", None)
        if callable(drain):
            try:
                for item in drain() or []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("event_kind") in (None, "skill.loaded"):
                        out.append(
                            (
                                RuntimeEventKind.SKILL_LOADED,
                                {
                                    "skill_id": str(item.get("skill_id", "")),
                                    "name": str(item.get("name", "")),
                                    "version": str(item.get("version", "")),
                                    "trust": str(item.get("trust", "")),
                                },
                            )
                        )
            except Exception:  # noqa: BLE001
                pass

    file_host = getattr(context, "file_tool_host", None)
    if file_host is not None:
        drain = getattr(file_host, "drain_public_events", None)
        if callable(drain):
            try:
                for item in drain() or []:
                    if not isinstance(item, dict):
                        continue
                    kind = str(item.get("event_kind", ""))
                    if kind == "attachment.read":
                        out.append(
                            (
                                RuntimeEventKind.ATTACHMENT_READ,
                                {
                                    "attachment_id": str(item.get("attachment_id", "")),
                                    "filename": str(item.get("filename", "")),
                                    "byte_size": int(item.get("byte_size") or 0),
                                },
                            )
                        )
                    elif kind == "artifact.created":
                        out.append(
                            (
                                RuntimeEventKind.ARTIFACT_CREATED,
                                {
                                    "artifact_id": str(item.get("artifact_id", "")),
                                    "kind": str(item.get("kind", "")),
                                    "title": item.get("title"),
                                    "byte_size": int(item.get("byte_size") or 0),
                                    "checksum_sha256": str(item.get("checksum_sha256", "")),
                                },
                            )
                        )
            except Exception:  # noqa: BLE001
                pass

    return out


def _raise_if_cancelled(run_id: Any) -> None:
    registry = get_run_cancel_registry()
    if registry.is_cancelled(run_id):
        raise TurnCancelled()


def _terminal_status_for(exc: BaseException) -> str:
    if isinstance(exc, TurnTerminalError):
        return exc.status
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
    """Deep module: one turn in, ordered RuntimeEvents out, lease always released."""

    def __init__(
        self,
        *,
        factory: RLMFactoryLike | None = None,
        turn_exporter: Any | None = None,
    ) -> None:
        self._factory: RLMFactoryLike = factory if factory is not None else RLMFactory()
        self._turn_exporter = turn_exporter

    async def stream(self, context: RLMTurnContext) -> AsyncIterator[RuntimeEvent]:
        """Run one turn. Always emits exactly one terminal event and releases the lease."""
        from fleet_rlm_clean.observability.exporters import safe_export
        from fleet_rlm_clean.observability.record import TurnTrace, apply_event_to_trace

        recorder = EventRecorder(run_id=context.run_id, session_id=context.session_id)
        started = time.perf_counter()
        terminal_emitted = False
        registry = get_run_cancel_registry()
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

            _raise_if_cancelled(context.run_id)

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

            _raise_if_cancelled(context.run_id)
            text = _prediction_text(prediction)

            if text:
                yield emit(RuntimeEventKind.TEXT_DELTA, {"text": text})
                yield emit(RuntimeEventKind.TEXT_COMPLETED, {"text": text})

            for kind, payload in _drain_host_public_events(context):
                yield emit(kind, payload)

            yield emit(RuntimeEventKind.USAGE, _usage_payload(prediction))

            duration_ms = int((time.perf_counter() - started) * 1000)
            yield emit(
                RuntimeEventKind.RUN_COMPLETED,
                {
                    "status": "completed",
                    "duration_ms": duration_ms,
                    "assistant_text": text,
                },
            )
            terminal_emitted = True
        except asyncio.CancelledError:
            if not terminal_emitted:
                duration_ms = int((time.perf_counter() - started) * 1000)
                yield emit(
                    RuntimeEventKind.ERROR,
                    {
                        "status": "cancelled",
                        "duration_ms": duration_ms,
                        "message": "Turn cancelled",
                    },
                )
                terminal_emitted = True
            raise
        except Exception as exc:  # noqa: BLE001 - public stream must never raise raw failures
            if not terminal_emitted:
                for kind, payload in _drain_host_public_events(context):
                    yield emit(kind, payload)
                duration_ms = int((time.perf_counter() - started) * 1000)
                yield emit(
                    RuntimeEventKind.ERROR,
                    {
                        "status": _terminal_status_for(exc),
                        "duration_ms": duration_ms,
                        "message": sanitize_public_error(exc),
                    },
                )
                terminal_emitted = True
        finally:
            try:
                context.lease.release()
            except Exception:  # noqa: BLE001
                if not terminal_emitted:
                    yield emit(
                        RuntimeEventKind.ERROR,
                        {
                            "status": "failed",
                            "message": "Turn failed during cleanup",
                        },
                    )
            registry.clear(context.run_id)
            safe_export(self._turn_exporter, trace)

    async def _execute_rlm(self, rlm: Any, context: RLMTurnContext) -> Any:
        """Apply root LM via scoped DSPy context; run off the event loop when sync."""
        root_lm = context.models.root_lm
        request = context.request
        _raise_if_cancelled(context.run_id)

        aforward = getattr(rlm, "aforward", None)
        if callable(aforward):

            async def _async_call() -> Any:
                with dspy.settings.context(lm=root_lm):
                    return await aforward(request=request)

            return await _async_call()

        def _sync_call() -> Any:
            with dspy.settings.context(lm=root_lm):
                if callable(rlm):
                    return rlm(request=request)
                forward = getattr(rlm, "forward", None)
                if callable(forward):
                    return forward(request=request)
                msg = "RLM module is not callable"
                raise TypeError(msg)

        return await asyncio.to_thread(_sync_call)
