"""Streaming orchestration for the RLM ReAct chat agent.

Provides synchronous and asynchronous streaming iterators that yield
:class:`~fleet_rlm.interactive.models.StreamEvent` objects, plus a DSPy
:class:`StatusMessageProvider` for concise ReAct status messages.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Callable, Iterable, cast

import dspy
from dspy.streaming.messages import StatusMessage, StatusMessageProvider, StreamResponse
from dspy.streaming.streaming_listener import StreamListener

from ..interactive.models import StreamEvent

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status-message parsing helpers
# ---------------------------------------------------------------------------


def parse_tool_call_status(message: str) -> str | None:
    """Extract a tool-call description from a DSPy status message."""
    match = re.match(r"^Calling tool:\s*(.+)$", message.strip())
    if not match:
        return None
    return f"tool call: {match.group(1).strip()}"


def parse_tool_result_status(message: str) -> str | None:
    """Detect a tool-finished status message."""
    if message.strip() == "Tool finished.":
        return "tool result: finished"
    return None


# ---------------------------------------------------------------------------
# Status message provider
# ---------------------------------------------------------------------------


class ReActStatusProvider(StatusMessageProvider):
    """Concise status messaging for streamed ReAct sessions."""

    def tool_start_status_message(self, instance: Any, inputs: dict[str, Any]):
        return f"Calling tool: {instance.name}"

    def tool_end_status_message(self, outputs: Any):
        return "Tool finished."

    def module_start_status_message(self, instance: Any, inputs: dict[str, Any]):
        return f"Running module: {instance.__class__.__name__}"

    def module_end_status_message(self, outputs: Any):
        return None


# ---------------------------------------------------------------------------
# Synchronous streaming
# ---------------------------------------------------------------------------


def iter_chat_turn_stream(
    agent: RLMReActChatAgent,
    message: str,
    trace: bool,
    cancel_check: Callable[[], bool] | None = None,
) -> Iterable[StreamEvent]:
    """Yield typed streaming events for one chat turn (synchronous).

    This is the canonical streaming surface used by interactive runtimes.
    """
    if not message or not message.strip():
        raise ValueError("message cannot be empty")

    agent.start()

    stream_listeners = [StreamListener(signature_field_name="assistant_response")]
    if trace:
        stream_listeners.append(
            StreamListener(signature_field_name="next_thought", allow_reuse=True)
        )

    try:
        stream_program = cast(
            Any,
            dspy.streamify(
                agent.agent,
                status_message_provider=ReActStatusProvider(),
                stream_listeners=stream_listeners,
                include_final_prediction_in_output_stream=True,
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
        yield StreamEvent(
            kind="status",
            text=f"streaming unavailable; fell back to non-streaming ({exc})",
            payload={"fallback": True, "error_type": type(exc).__name__},
        )
        yield StreamEvent(
            kind="final",
            text=str(fallback.get("assistant_response", "")),
            payload={
                "trajectory": fallback.get("trajectory", {}),
                "history_turns": fallback.get(
                    "history_turns", len(agent.history.messages)
                ),
                "fallback": True,
            },
        )
        return

    assistant_chunks: list[str] = []
    final_prediction: dspy.Prediction | None = None

    try:
        stream = stream_program(user_request=message, history=agent.history)
        for value in stream:
            if cancel_check is not None and cancel_check():
                partial = "".join(assistant_chunks).strip()
                marked_partial = (
                    f"{partial}\n\n[cancelled]" if partial else "[cancelled]"
                )
                agent._append_history(message, marked_partial)
                yield StreamEvent(
                    kind="cancelled",
                    text=marked_partial,
                    payload={"history_turns": len(agent.history.messages)},
                )
                return

            if isinstance(value, StreamResponse):
                if value.signature_field_name == "assistant_response":
                    assistant_chunks.append(value.chunk)
                    yield StreamEvent(kind="assistant_token", text=value.chunk)
                elif value.signature_field_name == "next_thought" and trace:
                    yield StreamEvent(
                        kind="reasoning_step",
                        text=value.chunk,
                        payload={"source": "next_thought"},
                    )
            elif isinstance(value, StatusMessage):
                text = value.message
                yield StreamEvent(kind="status", text=text)
                tool_call = parse_tool_call_status(text)
                if tool_call:
                    yield StreamEvent(kind="tool_call", text=tool_call)
                tool_result = parse_tool_result_status(text)
                if tool_result:
                    yield StreamEvent(kind="tool_result", text=tool_result)
            elif isinstance(value, dspy.Prediction):
                final_prediction = value
    except Exception as exc:
        logger.error(
            "Streaming error, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = agent.chat_turn(message)
        yield StreamEvent(
            kind="status",
            text=f"stream error; fell back to non-streaming ({exc})",
            payload={"fallback": True, "error_type": type(exc).__name__},
        )
        yield StreamEvent(
            kind="final",
            text=str(fallback.get("assistant_response", "")),
            payload={
                "trajectory": fallback.get("trajectory", {}),
                "history_turns": fallback.get(
                    "history_turns", len(agent.history.messages)
                ),
                "fallback": True,
            },
        )
        return

    if final_prediction is not None:
        assistant_response = str(
            getattr(final_prediction, "assistant_response", "")
        ).strip()
        trajectory = getattr(final_prediction, "trajectory", {})
    else:
        assistant_response = "".join(assistant_chunks).strip()
        trajectory = {}

    agent._append_history(message, assistant_response)
    yield StreamEvent(
        kind="final",
        text=assistant_response,
        payload={
            "trajectory": trajectory,
            "history_turns": len(agent.history.messages),
        },
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
    if not message or not message.strip():
        raise ValueError("message cannot be empty")

    agent.start()

    stream_listeners = [StreamListener(signature_field_name="assistant_response")]
    if trace:
        stream_listeners.append(
            StreamListener(signature_field_name="next_thought", allow_reuse=True)
        )

    try:
        stream_program = cast(
            Any,
            dspy.streamify(
                agent.agent,
                status_message_provider=ReActStatusProvider(),
                stream_listeners=stream_listeners,
                include_final_prediction_in_output_stream=True,
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
        yield StreamEvent(
            kind="status",
            text=f"streaming unavailable; fell back to non-streaming ({exc})",
            payload={"fallback": True, "error_type": type(exc).__name__},
        )
        yield StreamEvent(
            kind="final",
            text=str(fallback.get("assistant_response", "")),
            payload={
                "trajectory": fallback.get("trajectory", {}),
                "history_turns": fallback.get(
                    "history_turns", len(agent.history.messages)
                ),
                "fallback": True,
            },
        )
        return

    assistant_chunks: list[str] = []
    final_prediction: dspy.Prediction | None = None

    try:
        output_stream = stream_program(user_request=message, history=agent.history)
        async for value in output_stream:
            if cancel_check is not None and cancel_check():
                partial = "".join(assistant_chunks).strip()
                marked_partial = (
                    f"{partial}\n\n[cancelled]" if partial else "[cancelled]"
                )
                agent._append_history(message, marked_partial)
                yield StreamEvent(
                    kind="cancelled",
                    text=marked_partial,
                    payload={"history_turns": len(agent.history.messages)},
                )
                return

            if isinstance(value, StreamResponse):
                if value.signature_field_name == "assistant_response":
                    assistant_chunks.append(value.chunk)
                    yield StreamEvent(kind="assistant_token", text=value.chunk)
                elif value.signature_field_name == "next_thought" and trace:
                    yield StreamEvent(
                        kind="reasoning_step",
                        text=value.chunk,
                        payload={"source": "next_thought"},
                    )
            elif isinstance(value, StatusMessage):
                text = value.message
                yield StreamEvent(kind="status", text=text)
                tool_call = parse_tool_call_status(text)
                if tool_call:
                    yield StreamEvent(kind="tool_call", text=tool_call)
                tool_result = parse_tool_result_status(text)
                if tool_result:
                    yield StreamEvent(kind="tool_result", text=tool_result)
            elif isinstance(value, dspy.Prediction):
                final_prediction = value
    except Exception as exc:
        logger.error(
            "Async streaming error, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = await agent.achat_turn(message)
        yield StreamEvent(
            kind="status",
            text=f"stream error; fell back to non-streaming ({exc})",
            payload={"fallback": True, "error_type": type(exc).__name__},
        )
        yield StreamEvent(
            kind="final",
            text=str(fallback.get("assistant_response", "")),
            payload={
                "trajectory": fallback.get("trajectory", {}),
                "history_turns": fallback.get(
                    "history_turns", len(agent.history.messages)
                ),
                "fallback": True,
            },
        )
        return

    if final_prediction is not None:
        assistant_response = str(
            getattr(final_prediction, "assistant_response", "")
        ).strip()
        trajectory = getattr(final_prediction, "trajectory", {})
    else:
        assistant_response = "".join(assistant_chunks).strip()
        trajectory = {}

    agent._append_history(message, assistant_response)
    yield StreamEvent(
        kind="final",
        text=assistant_response,
        payload={
            "trajectory": trajectory,
            "history_turns": len(agent.history.messages),
        },
    )
