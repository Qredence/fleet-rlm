"""Direct RLM runner skeleton behind the ExecutionBackend seam."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable

from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext
from fleet_rlm.rlm.errors import (
    DIRECT_RLM_NOT_IMPLEMENTED,
    MISSING_PLANNER_LM,
    TURN_CANCELLED,
    DirectRLMErrorDetail,
    direct_rlm_error_event,
    direct_rlm_status_event,
)
from fleet_rlm.runtime.events import RuntimeEvent

CancelCheck = Callable[[], bool | Awaitable[bool]]


class DirectRLMRunner:
    """Minimal direct-RLM backend used by ``stream_turn()`` when opted in.

    Phase 2B skeleton only: emits RuntimeEvent-compatible status/error events
    without calling ``dspy.RLM`` or requiring live Daytona/LLM credentials.
    """

    async def stream(
        self,
        *,
        ctx: ChatExecutionContext,
        message: str,
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
                cancel_check=resolved_cancel_check,
            ):
                yield event
            return

        async for event in self._skeleton_stream(
            ctx=ctx,
            message=message,
            cancel_check=resolved_cancel_check,
        ):
            yield event

    def __init__(
        self,
        *,
        stream_override: Callable[..., AsyncIterator[RuntimeEvent]] | None = None,
    ) -> None:
        self._stream_override = stream_override

    async def _skeleton_stream(
        self,
        *,
        ctx: ChatExecutionContext,
        message: str,
        cancel_check: CancelCheck | None,
    ) -> AsyncIterator[RuntimeEvent]:
        _ = message
        yield direct_rlm_status_event("Starting direct RLM turn")

        if await _is_cancelled(cancel_check):
            yield direct_rlm_error_event(TURN_CANCELLED)
            return

        missing = _missing_dependencies(ctx)
        if missing is not None:
            yield direct_rlm_error_event(missing)
            return

        yield direct_rlm_error_event(DIRECT_RLM_NOT_IMPLEMENTED)


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
