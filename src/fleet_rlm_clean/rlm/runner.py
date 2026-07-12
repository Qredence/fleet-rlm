"""Execute one recursive DSPy turn and stream public RuntimeEvents."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any, Protocol

import dspy

from fleet_rlm_clean.rlm.context import RLMTurnContext
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


def _drain_skill_loaded_events(context: RLMTurnContext) -> list[dict[str, Any]]:
    host = getattr(context, "skill_tool_host", None)
    if host is None:
        return []
    drain = getattr(host, "drain_public_events", None)
    if not callable(drain):
        return []
    try:
        events = drain()
    except Exception:  # noqa: BLE001 - event drain must not break the turn
        return []
    if not events:
        return []
    safe: list[dict[str, Any]] = []
    for item in events:
        if not isinstance(item, dict):
            continue
        # Strip any accidental body fields
        safe.append(
            {
                "skill_id": str(item.get("skill_id", "")),
                "name": str(item.get("name", "")),
                "version": str(item.get("version", "")),
                "trust": str(item.get("trust", "")),
            }
        )
    return safe


class RLMRunner:
    """Deep module: one turn in, ordered RuntimeEvents out, lease always released."""

    def __init__(self, *, factory: RLMFactoryLike | None = None) -> None:
        self._factory: RLMFactoryLike = factory if factory is not None else RLMFactory()

    async def stream(self, context: RLMTurnContext) -> AsyncIterator[RuntimeEvent]:
        """Run one turn. Always emits exactly one terminal event and releases the lease."""
        recorder = EventRecorder(run_id=context.run_id, session_id=context.session_id)
        started = time.perf_counter()
        terminal_emitted = False

        def emit(kind: RuntimeEventKind, payload: dict[str, Any] | None = None) -> RuntimeEvent:
            return recorder.emit(kind, payload)

        try:
            yield emit(
                RuntimeEventKind.RUN_STARTED,
                {
                    "user_id": str(context.user_id),
                    "workspace_id": str(context.workspace_id),
                },
            )
            yield emit(RuntimeEventKind.STATUS, {"message": "running"})

            rlm = self._factory.create(
                models=context.models,
                budget=context.budget,
                interpreter=context.lease.interpreter,
                tools=list(context.tools) if context.tools else None,
            )
            prediction = await self._execute_rlm(rlm, context)
            text = _prediction_text(prediction)

            if text:
                yield emit(RuntimeEventKind.TEXT_DELTA, {"text": text})
                yield emit(RuntimeEventKind.TEXT_COMPLETED, {"text": text})

            # Safe skill.loaded events (no instruction bodies) from host-mediated tools
            for payload in _drain_skill_loaded_events(context):
                yield emit(RuntimeEventKind.SKILL_LOADED, payload)

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
        except Exception as exc:  # noqa: BLE001 - public stream must never raise raw failures
            # Still surface skill.loaded that completed before the failure
            if not terminal_emitted:
                for payload in _drain_skill_loaded_events(context):
                    yield emit(RuntimeEventKind.SKILL_LOADED, payload)
                duration_ms = int((time.perf_counter() - started) * 1000)
                yield emit(
                    RuntimeEventKind.ERROR,
                    {
                        "status": "failed",
                        "duration_ms": duration_ms,
                        "message": sanitize_public_error(exc),
                    },
                )
                terminal_emitted = True
        finally:
            try:
                context.lease.release()
            except Exception:  # noqa: BLE001 - lease release must not replace terminal event
                if not terminal_emitted:
                    yield emit(
                        RuntimeEventKind.ERROR,
                        {
                            "status": "failed",
                            "message": "Turn failed during cleanup",
                        },
                    )

    async def _execute_rlm(self, rlm: Any, context: RLMTurnContext) -> Any:
        """Apply root LM via scoped DSPy context; run off the event loop when sync."""
        root_lm = context.models.root_lm
        request = context.request

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
