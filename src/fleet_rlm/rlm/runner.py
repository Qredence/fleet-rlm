"""Direct RLM runner behind the ExecutionBackend seam."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext
from fleet_rlm.rlm.errors import (
    MISSING_INTERPRETER,
    MISSING_PLANNER_LM,
    RLM_EXECUTION_FAILED,
    TURN_CANCELLED,
    DirectRLMErrorDetail,
    direct_rlm_error_event,
    direct_rlm_status_event,
)
from fleet_rlm.rlm.execution import DirectRLMTurnExecutor, extract_direct_rlm_response, run_direct_rlm_turn
from fleet_rlm.rlm.inputs import build_direct_rlm_turn_inputs, history_turn_count
from fleet_rlm.rlm.trajectory import build_direct_rlm_done_event, iter_trajectory_runtime_events
from fleet_rlm.runtime.agent.runtime_helpers import append_turn_to_history
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind

logger = logging.getLogger(__name__)

CancelCheck = Callable[[], bool | Awaitable[bool]]


class DirectRLMRunner:
    """Direct-RLM backend used by ``stream_turn()`` when opted in.

    Phase 2C runs one real ``RLMTurnSignature`` turn through the acquired
    Daytona interpreter when available. Unit tests can inject ``stream_override``
    or ``turn_executor`` without live credentials.
    """

    def __init__(
        self,
        *,
        stream_override: Callable[..., AsyncIterator[RuntimeEvent]] | None = None,
        turn_executor: DirectRLMTurnExecutor | None = None,
    ) -> None:
        self._stream_override = stream_override
        self._turn_executor = turn_executor

    async def stream(
        self,
        *,
        ctx: ChatExecutionContext,
        message: str,
        agent_runtime: Any | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        """Stream one direct-RLM turn as canonical RuntimeEvent objects."""
        if cancel_check is None:

            def _default_cancel_check() -> bool:
                return bool(ctx.cancel_flag.get("cancelled", False))

            resolved_cancel_check = _default_cancel_check
        else:
            resolved_cancel_check = cancel_check

        if self._stream_override is not None:
            async for event in self._stream_override(
                ctx=ctx,
                message=message,
                agent_runtime=agent_runtime,
                cancel_check=resolved_cancel_check,
            ):
                yield event
            return

        async for event in self._stream_turn_events(
            ctx=ctx,
            message=message,
            agent_runtime=agent_runtime,
            cancel_check=resolved_cancel_check,
        ):
            yield event

    async def _stream_turn_events(
        self,
        *,
        ctx: ChatExecutionContext,
        message: str,
        agent_runtime: Any | None,
        cancel_check: CancelCheck | None,
    ) -> AsyncIterator[RuntimeEvent]:
        yield direct_rlm_status_event("Starting direct RLM turn")

        if await _is_cancelled(cancel_check):
            yield direct_rlm_error_event(TURN_CANCELLED)
            return

        missing = _missing_dependencies(ctx)
        if missing is not None:
            yield direct_rlm_error_event(missing)
            return

        interpreter = getattr(agent_runtime, "interpreter", None) if agent_runtime is not None else None
        if interpreter is None:
            yield direct_rlm_error_event(MISSING_INTERPRETER)
            return

        yield RuntimeEvent.turn_inputs(build_direct_rlm_turn_inputs(ctx, message, agent_runtime))

        yield direct_rlm_status_event("Running direct RLM analysis", phase="direct_rlm_execute")

        try:
            prediction = await asyncio.to_thread(
                self._turn_executor or run_direct_rlm_turn,
                ctx=ctx,
                message=message,
                interpreter=interpreter,
                agent_runtime=agent_runtime,
            )
        except Exception as exc:
            logger.exception("direct_rlm turn failed")
            yield direct_rlm_error_event(RLM_EXECUTION_FAILED, error=str(exc))
            return

        if await _is_cancelled(cancel_check):
            yield direct_rlm_error_event(TURN_CANCELLED)
            return

        trajectory_raw = getattr(prediction, "trajectory", None)
        for event in iter_trajectory_runtime_events(trajectory_raw):
            yield event

        response = extract_direct_rlm_response(prediction)
        if response:
            yield RuntimeEvent(kind=RuntimeEventKind.TEXT, text=response)

        history = getattr(agent_runtime, "history", None)
        if agent_runtime is not None and history is not None:
            agent_runtime.history = append_turn_to_history(
                history,
                user_message=message,
                response=response,
                history_max_turns=getattr(agent_runtime, "history_max_turns", None),
            )

        yield build_direct_rlm_done_event(
            response=response,
            trajectory_raw=trajectory_raw,
            history_turns=history_turn_count(agent_runtime),
        )


def _missing_dependencies(ctx: ChatExecutionContext) -> DirectRLMErrorDetail | None:
    if ctx.prepared.planner_lm is None:
        return MISSING_PLANNER_LM
    return None


async def _is_cancelled(cancel_check: CancelCheck | None) -> bool:
    if cancel_check is None:
        return False
    result = cancel_check()
    if inspect.isawaitable(result):
        return bool(await result)
    return bool(result)


__all__ = ["CancelCheck", "DirectRLMRunner"]
