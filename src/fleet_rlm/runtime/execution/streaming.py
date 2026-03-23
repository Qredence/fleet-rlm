"""Streaming orchestration for the RLM ReAct chat agent.

Provides synchronous and asynchronous streaming iterators that yield
:class:`~fleet_rlm.models.StreamEvent` objects, plus a DSPy
:class:`StatusMessageProvider` for concise ReAct status messages.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterable, cast

import dspy
from dspy.streaming.messages import StatusMessage, StreamResponse
from dspy.streaming.streaming_listener import StreamListener

from fleet_rlm.runtime.config import build_dspy_context
from fleet_rlm.runtime.models.streaming import StreamEvent

from .streaming_status import (
    ReActStatusProvider,
    classify_tool_event_kind,
    parse_tool_call_payload,
    parse_tool_call_status,
    parse_tool_result_payload,
    parse_tool_result_status,
    try_parse_hitl_request,
)
from .streaming_citations import _build_final_payload, _normalize_trajectory
from .streaming_context import StreamingContext

if TYPE_CHECKING:
    from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent

logger = logging.getLogger(__name__)
TERMINAL_STREAM_EVENT_KINDS: frozenset[str] = frozenset({"final", "cancelled", "error"})

# Backward-compatible alias for older tests/internal imports that still reach for
# the pre-refactor private helper from this module.
_try_parse_hitl_request = try_parse_hitl_request


@dataclass(slots=True)
class PreparedStreamingTurn:
    """Shared per-turn streaming metadata used by sync and async flows."""

    effective_max_iters: int
    ctx: StreamingContext
    stream_listeners: list[StreamListener]


def is_terminal_stream_event_kind(kind: str) -> bool:
    """Return whether *kind* is terminal for both runtime and websocket flows."""
    return kind in TERMINAL_STREAM_EVENT_KINDS


def prepare_streaming_turn(
    agent: RLMReActChatAgent, *, message: str, trace: bool
) -> PreparedStreamingTurn:
    """Validate a message and build the shared streaming turn context."""
    if not message or not message.strip():
        raise ValueError("message cannot be empty")

    agent.start()
    effective_max_iters = agent._prepare_turn(message)
    stream_listeners = [StreamListener(signature_field_name="assistant_response")]
    if trace:
        stream_listeners.append(
            StreamListener(signature_field_name="next_thought", allow_reuse=True)
        )
    return PreparedStreamingTurn(
        effective_max_iters=effective_max_iters,
        ctx=StreamingContext.from_agent(agent, effective_max_iters=effective_max_iters),
        stream_listeners=stream_listeners,
    )


def build_cancelled_stream_event(
    *,
    agent: RLMReActChatAgent,
    message: str,
    assistant_chunks: list[str],
    ctx: StreamingContext,
) -> StreamEvent:
    """Build the canonical cancellation event for a partially streamed turn."""
    partial = "".join(assistant_chunks).strip()
    marked_partial = f"{partial}\n\n[cancelled]" if partial else "[cancelled]"
    agent._append_history(message, marked_partial)
    return StreamEvent(
        kind="cancelled",
        text=marked_partial,
        payload=ctx.enrich(
            {
                "history_turns": agent.history_turns(),
                **agent._turn_metrics(),
            }
        ),
    )


def _extract_final_response(
    *,
    final_prediction: dspy.Prediction | None,
    assistant_chunks: list[str],
) -> tuple[str, dict[str, Any], str]:
    """Normalize the final assistant text, trajectory, and reasoning summary."""
    if final_prediction is not None:
        assistant_response = str(
            getattr(final_prediction, "assistant_response", "")
        ).strip()
        raw_trajectory = getattr(final_prediction, "trajectory", {})
        trajectory = raw_trajectory if isinstance(raw_trajectory, dict) else {}
        final_reasoning = ""
        if hasattr(final_prediction, "reasoning"):
            final_reasoning = str(final_prediction.reasoning)
        elif trajectory:
            steps = _normalize_trajectory(trajectory)
            reasoning_parts = []
            for step in steps:
                if isinstance(step, dict):
                    thought = step.get("thought", "")
                    if thought:
                        reasoning_parts.append(thought)
            final_reasoning = "\n".join(reasoning_parts)
        return assistant_response, trajectory, final_reasoning

    return "".join(assistant_chunks).strip(), {}, ""


def build_final_stream_event(
    *,
    agent: RLMReActChatAgent,
    message: str,
    final_prediction: dspy.Prediction | None,
    assistant_chunks: list[str],
    ctx: StreamingContext,
) -> StreamEvent:
    """Build the canonical terminal final event for a completed streamed turn."""
    assistant_response, trajectory, final_reasoning = _extract_final_response(
        final_prediction=final_prediction,
        assistant_chunks=assistant_chunks,
    )
    agent._finalize_turn(trajectory)
    assistant_response, guardrail_warnings = agent._validate_assistant_response(
        assistant_response=assistant_response,
        trajectory=trajectory,
    )
    agent._append_history(message, assistant_response)
    return StreamEvent(
        kind="final",
        flush_tokens=True,
        text=assistant_response,
        payload=ctx.enrich(
            {
                **_build_final_payload(
                    final_prediction=final_prediction,
                    trajectory=cast(dict[str, Any], trajectory or {}),
                    history_turns=agent.history_turns(),
                    guardrail_warnings=guardrail_warnings,
                    turn_metrics=agent._turn_metrics(),
                    fallback=False,
                ),
                "final_reasoning": final_reasoning,
            }
        ),
    )


# ---------------------------------------------------------------------------
# Synchronous streaming
# ---------------------------------------------------------------------------


def _build_fallback_events(
    *,
    fallback: dict[str, Any],
    exc: Exception,
    effective_max_iters: int,
    agent: RLMReActChatAgent,
    init_phase: bool,
    ctx: StreamingContext,
) -> Iterable[StreamEvent]:
    prefix = "streaming unavailable" if init_phase else "stream error"
    yield StreamEvent(
        kind="status",
        text=f"{prefix}; fell back to non-streaming ({exc})",
        payload=ctx.enrich({"fallback": True, "error_type": type(exc).__name__}),
    )
    yield StreamEvent(
        kind="final",
        flush_tokens=True,
        text=str(fallback.get("assistant_response", "")),
        payload=ctx.enrich(
            _build_final_payload(
                final_prediction=None,
                trajectory=cast(dict[str, Any], fallback.get("trajectory", {}) or {}),
                history_turns=int(fallback.get("history_turns", agent.history_turns())),
                guardrail_warnings=[],
                turn_metrics={
                    "delegate_calls_turn": fallback.get("delegate_calls_turn", 0),
                    "delegate_fallback_count_turn": fallback.get(
                        "delegate_fallback_count_turn", 0
                    ),
                    "delegate_result_truncated_count_turn": fallback.get(
                        "delegate_result_truncated_count_turn", 0
                    ),
                },
                fallback=True,
                fallback_error_type=type(exc).__name__,
                effective_max_iters=int(
                    fallback.get("effective_max_iters", effective_max_iters)
                ),
            )
        ),
    )


def _drain_live_events(
    pending_events: list[StreamEvent],
) -> Iterable[StreamEvent]:
    """Yield and clear queued nested events emitted by child runtimes."""
    # Snapshot current events, then clear the list so callers see it as drained.
    # This preserves the original event order while avoiding O(N) pop(0) calls.
    events = list(pending_events)
    pending_events.clear()
    yield from events


def _process_stream_value(
    *,
    value: Any,
    trace: bool,
    assistant_chunks: list[str],
    last_tool_name_ref: list[str | None],
    ctx: StreamingContext | None = None,
) -> Iterable[StreamEvent]:
    if isinstance(value, StreamResponse):
        if value.signature_field_name == "assistant_response":
            assistant_chunks.append(value.chunk)
            yield StreamEvent(
                kind="assistant_token",
                text=value.chunk,
                payload=ctx.enrich({}) if ctx else {},
            )
        elif value.signature_field_name == "next_thought" and trace:
            yield StreamEvent(
                kind="reasoning_step",
                text=value.chunk,
                payload=ctx.enrich({"source": "next_thought"})
                if ctx
                else {"source": "next_thought"},
            )
        return

    if isinstance(value, StatusMessage):
        text = value.message
        yield StreamEvent(
            kind="status",
            text=text,
            payload=ctx.enrich({"raw_status": text}) if ctx else {"raw_status": text},
        )

        tool_call = parse_tool_call_status(text)
        if tool_call:
            tool_payload = parse_tool_call_payload(text) or {}
            parsed_name = tool_payload.get("tool_name")
            if isinstance(parsed_name, str) and parsed_name:
                last_tool_name_ref[0] = parsed_name

            yield StreamEvent(
                kind=classify_tool_event_kind(
                    parsed_name if isinstance(parsed_name, str) else None
                ),
                text=tool_call,
                payload=ctx.enrich(tool_payload) if ctx else tool_payload,
            )

        tool_result = parse_tool_result_status(text)
        if tool_result:
            result_payload = (
                parse_tool_result_payload(text, tool_name=last_tool_name_ref[0]) or {}
            )
            yield StreamEvent(
                kind="tool_result",
                text=tool_result,
                payload=ctx.enrich(result_payload) if ctx else result_payload,
            )

            # Check for HITL triggers
            hitl_req = try_parse_hitl_request(
                tool_name=last_tool_name_ref[0],
                payload=result_payload,
            )
            if hitl_req:
                if ctx:
                    hitl_req.payload = ctx.enrich(hitl_req.payload)
                yield hitl_req


def _emit_prediction_trajectory_events(
    final_prediction: dspy.Prediction,
    ctx: StreamingContext | None = None,
) -> Iterable[StreamEvent]:
    trajectory = getattr(final_prediction, "trajectory", {})
    if not trajectory or not isinstance(trajectory, dict):
        return

    steps = _normalize_trajectory(trajectory)
    if not steps:
        return

    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        step_text = step.get("thought", step.get("action", str(step)))
        step_payload: dict[str, Any] = {
            "step_index": idx,
            "step_data": step,
            "total_steps": len(steps),
        }
        yield StreamEvent(
            kind="trajectory_step",
            flush_tokens=True,
            text=step_text,
            payload=ctx.enrich(step_payload) if ctx else step_payload,
        )


def iter_chat_turn_stream(
    agent: RLMReActChatAgent,
    message: str,
    trace: bool,
    cancel_check: Callable[[], bool] | None = None,
) -> Iterable[StreamEvent]:
    """Yield typed streaming events for one chat turn (synchronous).

    This is the canonical streaming surface used by interactive runtimes.
    """
    prepared_turn = prepare_streaming_turn(agent, message=message, trace=trace)
    effective_max_iters = prepared_turn.effective_max_iters
    ctx = prepared_turn.ctx
    stream_listeners = prepared_turn.stream_listeners

    try:
        stream_program = cast(
            Any,
            dspy.streamify(
                agent.react,
                status_message_provider=ReActStatusProvider(),
                stream_listeners=stream_listeners,
                include_final_prediction_in_output_stream=True,
                is_async_program=False,
                async_streaming=False,
            ),
        )
    except Exception as exc:
        logger.warning(
            "Streaming init failed, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = agent.chat_turn(message)
        yield from _build_fallback_events(
            fallback=fallback,
            exc=exc,
            effective_max_iters=effective_max_iters,
            agent=agent,
            init_phase=True,
            ctx=ctx,
        )
        return

    assistant_chunks: list[str] = []
    final_prediction: dspy.Prediction | None = None
    last_tool_name_ref: list[str | None] = [None]
    pending_live_events: list[StreamEvent] = []
    previous_live_callback = getattr(agent, "_live_event_callback", None)

    def _queue_live_event(event: StreamEvent) -> None:
        if not isinstance(event, StreamEvent):
            return
        if event.kind in {"final", "cancelled", "error"}:
            return
        pending_live_events.append(event)

    agent._live_event_callback = _queue_live_event

    try:
        with build_dspy_context(allow_tool_async_sync_conversion=True):
            stream = stream_program(
                user_request=message,
                history=agent.history,
                core_memory=agent.fmt_core_memory(),
                max_iters=effective_max_iters,
            )
            for value in stream:
                if cancel_check is not None and cancel_check():
                    yield build_cancelled_stream_event(
                        agent=agent,
                        message=message,
                        assistant_chunks=assistant_chunks,
                        ctx=ctx,
                    )
                    return

                yield from _drain_live_events(pending_live_events)

                if isinstance(value, dspy.Prediction):
                    final_prediction = value
                    yield from _emit_prediction_trajectory_events(final_prediction, ctx)
                else:
                    yield from _process_stream_value(
                        value=value,
                        trace=trace,
                        assistant_chunks=assistant_chunks,
                        last_tool_name_ref=last_tool_name_ref,
                        ctx=ctx,
                    )
    except Exception as exc:
        logger.error(
            "Streaming error, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = agent.chat_turn(message)
        yield from _build_fallback_events(
            fallback=fallback,
            exc=exc,
            effective_max_iters=effective_max_iters,
            agent=agent,
            init_phase=False,
            ctx=ctx,
        )
        return
    finally:
        agent._live_event_callback = previous_live_callback

    yield from _drain_live_events(pending_live_events)
    yield build_final_stream_event(
        agent=agent,
        message=message,
        final_prediction=final_prediction,
        assistant_chunks=assistant_chunks,
        ctx=ctx,
    )


# ---------------------------------------------------------------------------
# Asynchronous streaming
# ---------------------------------------------------------------------------


async def aiter_chat_turn_stream(
    agent: RLMReActChatAgent,
    message: str,
    trace: bool,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Yield typed streaming events for one chat turn (async).

    This is the canonical async streaming surface used by the FastAPI
    WebSocket endpoint.  Yields the same ``StreamEvent`` types as
    :func:`iter_chat_turn_stream`.
    """
    prepared_turn = prepare_streaming_turn(agent, message=message, trace=trace)
    effective_max_iters = prepared_turn.effective_max_iters
    ctx = prepared_turn.ctx
    stream_listeners = prepared_turn.stream_listeners

    try:
        stream_program = cast(
            Any,
            dspy.streamify(
                agent.react,
                status_message_provider=ReActStatusProvider(),
                stream_listeners=stream_listeners,
                include_final_prediction_in_output_stream=True,
                is_async_program=True,
                async_streaming=True,
            ),
        )
    except Exception as exc:
        logger.warning(
            "Async streaming init failed, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = await agent.achat_turn(message)
        for event in _build_fallback_events(
            fallback=fallback,
            exc=exc,
            effective_max_iters=effective_max_iters,
            agent=agent,
            init_phase=True,
            ctx=ctx,
        ):
            yield event
        return

    assistant_chunks: list[str] = []
    final_prediction: dspy.Prediction | None = None
    last_tool_name_ref: list[str | None] = [None]
    pending_live_events: list[StreamEvent] = []
    previous_live_callback = getattr(agent, "_live_event_callback", None)

    def _queue_live_event(event: StreamEvent) -> None:
        if not isinstance(event, StreamEvent):
            return
        if event.kind in {"final", "cancelled", "error"}:
            return
        pending_live_events.append(event)

    agent._live_event_callback = _queue_live_event

    try:
        output_stream = stream_program(
            user_request=message,
            history=agent.history,
            core_memory=agent.fmt_core_memory(),
            max_iters=effective_max_iters,
        )
        async for value in output_stream:
            if cancel_check is not None and cancel_check():
                yield build_cancelled_stream_event(
                    agent=agent,
                    message=message,
                    assistant_chunks=assistant_chunks,
                    ctx=ctx,
                )
                return

            for event in _drain_live_events(pending_live_events):
                yield event

            if isinstance(value, dspy.Prediction):
                final_prediction = value
                for event in _emit_prediction_trajectory_events(final_prediction, ctx):
                    yield event
            else:
                for event in _process_stream_value(
                    value=value,
                    trace=trace,
                    assistant_chunks=assistant_chunks,
                    last_tool_name_ref=last_tool_name_ref,
                    ctx=ctx,
                ):
                    yield event
    except Exception as exc:
        logger.error(
            "Async streaming error, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = await agent.achat_turn(message)
        for event in _build_fallback_events(
            fallback=fallback,
            exc=exc,
            effective_max_iters=effective_max_iters,
            agent=agent,
            init_phase=False,
            ctx=ctx,
        ):
            yield event
        return
    finally:
        agent._live_event_callback = previous_live_callback

    for event in _drain_live_events(pending_live_events):
        yield event
    yield build_final_stream_event(
        agent=agent,
        message=message,
        final_prediction=final_prediction,
        assistant_chunks=assistant_chunks,
        ctx=ctx,
    )
