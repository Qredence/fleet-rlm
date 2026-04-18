"""Sync/async streaming iterators for the RLM ReAct chat agent.

Yields :class:`~fleet_rlm.models.StreamEvent` objects through DSPy's
:func:`dspy.streamify` pipeline.

Event construction, status parsing, citation handling, and payload building
live in :mod:`fleet_rlm.runtime.execution.streaming_events`.

The :class:`StreamingContext` dataclass lives in
:mod:`fleet_rlm.runtime.execution.streaming_context`.

Both are re-exported here for backwards compatibility.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, cast
import uuid

import dspy
from dspy.streaming.messages import StatusMessage, StreamResponse
from dspy.streaming.streaming_listener import StreamListener

from fleet_rlm.runtime.config import build_dspy_context
from fleet_rlm.runtime.execution.streaming_context import (
    StreamingContext as StreamingContext,
)
from fleet_rlm.runtime.execution.streaming_events import (
    ReActStatusProvider as ReActStatusProvider,
    _build_final_payload,
    _normalize_trajectory as _normalize_trajectory,
    classify_tool_event_kind as classify_tool_event_kind,
    parse_tool_call_payload as parse_tool_call_payload,
    parse_tool_call_status as parse_tool_call_status,
    parse_tool_result_payload as parse_tool_result_payload,
    parse_tool_result_status as parse_tool_result_status,
    try_parse_hitl_request as try_parse_hitl_request,
)
from fleet_rlm.runtime.models.streaming import StreamEvent

if TYPE_CHECKING:
    from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent


# ═══════════════════════════════════════════════════════════════════════
# Streaming orchestration
# ═══════════════════════════════════════════════════════════════════════

logger = logging.getLogger(__name__)
TERMINAL_STREAM_EVENT_KINDS: frozenset[str] = frozenset({"final", "cancelled", "error"})


def _persist_streaming_turn_best_effort(
    *,
    db_session_id: Any | None,
    user_message: str,
    assistant_message: str,
    error_log_message: str,
    error_log_args: tuple[Any, ...],
    agent: RLMReActChatAgent | None = None,
) -> None:
    """Persist a streaming turn without assuming sync or async execution."""
    if db_session_id is None:
        return

    def _session_uuid(value: object) -> uuid.UUID | None:
        if isinstance(value, uuid.UUID):
            return value
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return uuid.UUID(candidate)
        except ValueError:
            return None

    if agent is not None:
        repository = getattr(agent, "_repository", None)
        identity_rows = getattr(agent, "_identity_rows", None)
        tenant_id = getattr(identity_rows, "tenant_id", None)
        user_id = getattr(identity_rows, "user_id", None)
        workspace_id = getattr(identity_rows, "workspace_id", None)
        session_uuid = _session_uuid(db_session_id)
        if (
            repository is not None
            and tenant_id is not None
            and session_uuid is not None
        ):
            from fleet_rlm.integrations.database.types import ChatTurnCreateRequest

            async def _write_turn_repo_async() -> None:
                try:
                    resolved_workspace_id = (
                        workspace_id
                        if isinstance(workspace_id, uuid.UUID)
                        else await repository.resolve_workspace_id(
                            tenant_id=tenant_id,
                            user_id=user_id,
                        )
                    )
                    await repository.append_chat_turn(
                        ChatTurnCreateRequest(
                            tenant_id=tenant_id,
                            workspace_id=resolved_workspace_id,
                            session_id=session_uuid,
                            user_message=user_message,
                            assistant_message=assistant_message,
                            user_id=user_id,
                        )
                    )
                except Exception:
                    logger.exception(error_log_message, *error_log_args)

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                try:
                    asyncio.run(_write_turn_repo_async())
                except Exception:
                    logger.exception(error_log_message, *error_log_args)
            else:
                asyncio.create_task(_write_turn_repo_async())
            return

    from fleet_rlm.integrations.local_store import add_turn

    def _write_turn() -> None:
        add_turn(db_session_id, 0, user_message, assistant_message)

    async def _write_turn_async() -> None:
        try:
            await asyncio.to_thread(_write_turn)
        except Exception:
            logger.exception(error_log_message, *error_log_args)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            _write_turn()
        except Exception:
            logger.exception(error_log_message, *error_log_args)
        return

    asyncio.create_task(_write_turn_async())


@dataclass(slots=True)
class PreparedStreamingTurn:
    """Shared per-turn streaming metadata used by sync and async flows."""

    effective_max_iters: int
    ctx: StreamingContext
    stream_listeners: list[StreamListener]


@dataclass(slots=True)
class _ActiveStreamingTurn:
    """Mutable per-stream state shared by sync and async iterators."""

    assistant_chunks: list[str] = field(default_factory=list)
    final_prediction: dspy.Prediction | None = None
    last_tool_name_ref: list[str | None] = field(default_factory=lambda: [None])
    pending_live_events: list[StreamEvent] = field(default_factory=list)
    previous_live_callback: Any = None


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
    _db_sid = getattr(agent, "_db_session_id", None)
    _persist_streaming_turn_best_effort(
        db_session_id=_db_sid,
        user_message=message,
        assistant_message=marked_partial,
        error_log_message=(
            "Failed to persist cancelled streaming turn to local_store "
            "(session_id=%r, marked_partial=%r)"
        ),
        error_log_args=(_db_sid, marked_partial),
        agent=agent,
    )
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
    _db_sid = getattr(agent, "_db_session_id", None)
    _persist_streaming_turn_best_effort(
        db_session_id=_db_sid,
        user_message=message,
        assistant_message=assistant_response,
        error_log_message=(
            "Failed to persist final streaming turn to local_store (session_id=%r)"
        ),
        error_log_args=(_db_sid,),
        agent=agent,
    )
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


def _build_stream_program(
    agent: RLMReActChatAgent,
    *,
    trace: bool,
    is_async_program: bool,
    async_streaming: bool,
    stream_listeners: list[StreamListener],
) -> Any:
    return cast(
        Any,
        dspy.streamify(
            agent.react,
            status_message_provider=ReActStatusProvider(),
            stream_listeners=stream_listeners,
            include_final_prediction_in_output_stream=True,
            is_async_program=is_async_program,
            async_streaming=async_streaming,
        ),
    )


def _activate_live_event_queue(agent: RLMReActChatAgent) -> _ActiveStreamingTurn:
    state = _ActiveStreamingTurn()
    state.previous_live_callback = getattr(agent, "_live_event_callback", None)

    def _queue_live_event(event: StreamEvent) -> None:
        if not isinstance(event, StreamEvent):
            return
        if event.kind in {"final", "cancelled", "error"}:
            return
        state.pending_live_events.append(event)

    agent._live_event_callback = _queue_live_event
    return state


def _restore_live_event_queue(
    agent: RLMReActChatAgent,
    state: _ActiveStreamingTurn,
) -> None:
    agent._live_event_callback = state.previous_live_callback


def _handle_stream_value(
    *,
    value: Any,
    trace: bool,
    state: _ActiveStreamingTurn,
    ctx: StreamingContext,
) -> Iterable[StreamEvent]:
    if isinstance(value, dspy.Prediction):
        state.final_prediction = value
        yield from _emit_prediction_trajectory_events(value, ctx)
        return

    yield from _process_stream_value(
        value=value,
        trace=trace,
        assistant_chunks=state.assistant_chunks,
        last_tool_name_ref=state.last_tool_name_ref,
        ctx=ctx,
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
        stream_program = _build_stream_program(
            agent,
            trace=trace,
            is_async_program=False,
            async_streaming=False,
            stream_listeners=stream_listeners,
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

    state = _activate_live_event_queue(agent)

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
                        assistant_chunks=state.assistant_chunks,
                        ctx=ctx,
                    )
                    return

                yield from _drain_live_events(state.pending_live_events)
                yield from _handle_stream_value(
                    value=value,
                    trace=trace,
                    state=state,
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
        _restore_live_event_queue(agent, state)

    yield from _drain_live_events(state.pending_live_events)
    yield build_final_stream_event(
        agent=agent,
        message=message,
        final_prediction=state.final_prediction,
        assistant_chunks=state.assistant_chunks,
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
        stream_program = _build_stream_program(
            agent,
            trace=trace,
            is_async_program=True,
            async_streaming=True,
            stream_listeners=stream_listeners,
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

    state = _activate_live_event_queue(agent)

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
                    assistant_chunks=state.assistant_chunks,
                    ctx=ctx,
                )
                return

            for event in _drain_live_events(state.pending_live_events):
                yield event

            for event in _handle_stream_value(
                value=value,
                trace=trace,
                state=state,
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
        _restore_live_event_queue(agent, state)

    for event in _drain_live_events(state.pending_live_events):
        yield event
    yield build_final_stream_event(
        agent=agent,
        message=message,
        final_prediction=state.final_prediction,
        assistant_chunks=state.assistant_chunks,
        ctx=ctx,
    )
