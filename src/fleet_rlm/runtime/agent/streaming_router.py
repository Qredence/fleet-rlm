"""Routing helpers for sync and async chat streaming entrypoints."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from typing import TYPE_CHECKING

from fleet_rlm.runtime.execution.streaming import (
    aiter_chat_turn_stream as _aiter_stream,
)
from fleet_rlm.runtime.execution.streaming import iter_chat_turn_stream as _iter_stream
from fleet_rlm.runtime.execution.streaming_context import StreamingContext
from fleet_rlm.runtime.models.streaming import StreamEvent

from .chat_session_state import append_history, forced_delegate_context
from .chat_turns import (
    prediction_guardrail_warnings,
    prediction_response_and_trajectory,
)
from .forced_routing import (
    ForcedFinalPayloadInput,
    aiter_forced_rlm_turn_stream,
    forced_stream_final_payload,
    prediction_from_forced_rlm_result,
)
from .tool_delegation import get_tool_by_name

if TYPE_CHECKING:
    from .chat_agent import RLMReActChatAgent


def iter_routed_chat_turn_stream(
    agent: RLMReActChatAgent,
    *,
    message: str,
    trace: bool,
    cancel_check: Callable[[], bool] | None = None,
) -> Iterable[StreamEvent]:
    """Route sync chat streaming through ReAct or forced RLM execution."""
    if agent.execution_mode == "rlm_only":
        yield from _iter_forced_rlm_stream(
            agent,
            message=message,
            cancel_check=cancel_check,
        )
        return

    yield from _iter_stream(agent, message, trace, cancel_check)


async def aiter_routed_chat_turn_stream(
    agent: RLMReActChatAgent,
    *,
    message: str,
    trace: bool,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Route async chat streaming through ReAct or forced RLM execution."""
    if agent.execution_mode == "rlm_only":
        async for event in aiter_forced_rlm_turn_stream(
            agent,
            message=message,
            cancel_check=cancel_check,
        ):
            yield event
        return

    async for event in _aiter_stream(agent, message, trace, cancel_check):
        yield event


def _iter_forced_rlm_stream(
    agent: RLMReActChatAgent,
    *,
    message: str,
    cancel_check: Callable[[], bool] | None = None,
) -> Iterable[StreamEvent]:
    """Yield the explicit sync ReAct→RLM streaming contract."""
    _ = cancel_check
    if not message or not message.strip():
        raise ValueError("message cannot be empty")

    agent.start()
    effective_max_iters = agent.prepare_routed_turn()
    ctx = StreamingContext.from_agent(agent, effective_max_iters=effective_max_iters)
    yield StreamEvent(
        kind="status",
        text="Execution mode: RLM only",
        payload=ctx.enrich({"forced": True}),
    )
    yield StreamEvent(
        kind="rlm_executing",
        text="tool call: rlm_query",
        payload=ctx.enrich({"tool_name": "rlm_query", "forced": True}),
    )

    forced_result = get_tool_by_name(agent, "rlm_query")(
        query=message,
        context=forced_delegate_context(agent),
    )
    prediction = prediction_from_forced_rlm_result(agent, forced_result)
    assistant_response, trajectory = prediction_response_and_trajectory(prediction)
    guardrail_warnings = prediction_guardrail_warnings(prediction)
    append_history(agent, message, assistant_response)

    yield StreamEvent(
        kind="tool_result",
        text="tool result: rlm_query completed",
        payload=ctx.enrich({"tool_name": "rlm_query", "forced": True}),
    )
    yield StreamEvent(
        kind="final",
        flush_tokens=True,
        text=assistant_response,
        payload=forced_stream_final_payload(
            agent,
            payload_input=ForcedFinalPayloadInput(
                trajectory=trajectory,
                guardrail_warnings=guardrail_warnings,
                final_reasoning=str(getattr(prediction, "final_reasoning", "") or ""),
            ),
            ctx=ctx,
        ),
    )
