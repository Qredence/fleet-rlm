"""Forced-RLM helpers shared by sync and async chat entrypoints."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import dspy

from fleet_rlm.core.execution.streaming_context import StreamingContext
from fleet_rlm.core.models.streaming import StreamEvent

from .chat_session_state import append_history, forced_delegate_context, history_turns
from .chat_turns import (
    finalize_turn,
    prediction_guardrail_warnings,
    prediction_response_and_trajectory,
    snapshot_turn_metrics,
)
from .rlm_agent import spawn_delegate_sub_agent_async

if TYPE_CHECKING:
    from .chat_agent import RLMReActChatAgent


@dataclass(slots=True)
class ForcedFinalPayloadInput:
    """Inputs required to build the terminal forced-RLM stream payload."""

    trajectory: dict[str, Any]
    guardrail_warnings: list[str]
    final_reasoning: str


def prediction_from_forced_rlm_result(
    agent: "RLMReActChatAgent", result: dict[str, Any]
) -> dspy.Prediction:
    """Convert a forced child-RLM result into a DSPy prediction."""
    trajectory = result.get("trajectory", {})
    if not isinstance(trajectory, dict):
        trajectory = {}

    assistant_response = str(
        result.get("assistant_response") or result.get("answer") or ""
    ).strip()
    finalize_turn(agent, trajectory)
    assistant_response, warnings = agent._validate_assistant_response(
        assistant_response=assistant_response,
        trajectory=trajectory,
    )

    prediction = dspy.Prediction(
        assistant_response=assistant_response,
        trajectory=trajectory,
    )
    final_reasoning = str(result.get("final_reasoning") or "").strip()
    if final_reasoning:
        setattr(prediction, "final_reasoning", final_reasoning)
    if warnings:
        setattr(prediction, "guardrail_warnings", warnings)

    for key, value in snapshot_turn_metrics(agent).as_payload().items():
        setattr(prediction, key, value)
    return prediction


def forced_stream_final_payload(
    agent: "RLMReActChatAgent",
    *,
    payload_input: ForcedFinalPayloadInput,
    ctx: StreamingContext,
) -> dict[str, Any]:
    """Build the canonical terminal payload for forced-RLM streaming."""
    return ctx.enrich(
        {
            "trajectory": payload_input.trajectory,
            "history_turns": history_turns(agent),
            "guardrail_warnings": payload_input.guardrail_warnings,
            "final_reasoning": payload_input.final_reasoning,
            **snapshot_turn_metrics(agent).as_payload(),
        }
    )


async def aiter_forced_rlm_turn_stream(
    agent: "RLMReActChatAgent",
    *,
    message: str,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Yield forced-RLM streaming events while preserving existing semantics."""
    if not message or not message.strip():
        raise ValueError("message cannot be empty")

    await agent.astart()
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

    pending_events: asyncio.Queue[StreamEvent] = asyncio.Queue()

    async def _queue_event(event: Any) -> None:
        if isinstance(event, StreamEvent):
            await pending_events.put(event)

    task = asyncio.create_task(
        spawn_delegate_sub_agent_async(
            agent,
            prompt=message,
            context=forced_delegate_context(agent),
            stream_event_callback=_queue_event,
        )
    )

    try:
        while True:
            if cancel_check is not None and cancel_check():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    _ = await task
                cancelled_text = "[cancelled]"
                append_history(agent, message, cancelled_text)
                yield StreamEvent(
                    kind="cancelled",
                    text=cancelled_text,
                    payload={
                        "history_turns": history_turns(agent),
                        **snapshot_turn_metrics(agent).as_payload(),
                    },
                )
                return

            try:
                event = await asyncio.wait_for(pending_events.get(), timeout=0.05)
            except asyncio.TimeoutError:
                if task.done():
                    break
                continue

            yield event

        forced_result = await task
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                _ = await task

    while not pending_events.empty():
        yield pending_events.get_nowait()

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
