"""Internal lifecycle, streaming, and tool-access helpers for chat agents."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import dspy

from fleet_rlm.runtime.tools import build_tool_list

from .tool_delegation import TOOL_DELEGATE_NAMES, get_tool_by_name

if TYPE_CHECKING:
    from .chat_agent import RLMReActChatAgent


def start_agent_session(agent: RLMReActChatAgent) -> None:
    if agent._started:
        return
    agent.interpreter.start()
    agent._started = True


def shutdown_agent_session(agent: RLMReActChatAgent) -> None:
    agent.interpreter.shutdown()
    agent._started = False


async def astart_agent_session(agent: RLMReActChatAgent) -> None:
    if agent._started:
        return
    if getattr(agent.interpreter, "async_execute", False) and hasattr(
        agent.interpreter, "astart"
    ):
        await agent.interpreter.astart()
    else:
        agent.interpreter.start()
    agent._started = True


async def ashutdown_agent_session(agent: RLMReActChatAgent) -> None:
    if getattr(agent.interpreter, "async_execute", False) and hasattr(
        agent.interpreter, "ashutdown"
    ):
        await agent.interpreter.ashutdown()
    else:
        agent.interpreter.shutdown()
    agent._started = False


def reset_agent_history_and_cache(agent: RLMReActChatAgent) -> int:
    agent.history = dspy.History(messages=[])
    return agent.clear_document_cache()


def reset_agent_state(
    agent: RLMReActChatAgent, *, clear_sandbox_buffers: bool
) -> dict[str, Any]:
    docs_count = reset_agent_history_and_cache(agent)
    if clear_sandbox_buffers:
        result = get_tool_by_name(agent, "clear_buffer")()
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                raise RuntimeError(
                    "reset() cannot clear async sandbox buffers from a running "
                    "event loop. Use areset() instead."
                )
    return {
        "status": "ok",
        "history_turns": 0,
        "documents_cleared": docs_count,
        "buffers_cleared": clear_sandbox_buffers,
    }


async def areset_agent_state(
    agent: RLMReActChatAgent, *, clear_sandbox_buffers: bool
) -> dict[str, Any]:
    docs_count = reset_agent_history_and_cache(agent)
    if clear_sandbox_buffers:
        result = get_tool_by_name(agent, "clear_buffer")()
        if inspect.isawaitable(result):
            await result
    return {
        "status": "ok",
        "history_turns": 0,
        "documents_cleared": docs_count,
        "buffers_cleared": clear_sandbox_buffers,
    }


def build_react_module(
    agent: RLMReActChatAgent, *, signature: type[dspy.Signature]
) -> dspy.Module:
    agent.react_tools = build_tool_list(agent, agent._extra_tools)
    return dspy.ReAct(
        signature=signature,
        tools=list(agent.react_tools),
        max_iters=agent.react_max_iters,
    )


def collect_chat_turn_stream(
    agent: RLMReActChatAgent, *, message: str, trace: bool
) -> dict[str, Any]:
    assistant_chunks: list[str] = []
    thought_chunks: list[str] = []
    status_messages: list[str] = []
    trajectory: dict[str, Any] = {}
    assistant_response = ""
    cancelled = False
    guardrail_warnings: list[str] = []

    for event in agent.iter_chat_turn_stream(message=message, trace=trace):
        if event.kind == "assistant_token":
            assistant_chunks.append(event.text)
        elif event.kind == "reasoning_step":
            thought_chunks.append(event.text)
        elif event.kind == "status":
            status_messages.append(event.text)
        elif event.kind == "final":
            assistant_response = event.text
            trajectory = dict(event.payload.get("trajectory", {}) or {})
            guardrail_warnings = list(event.payload.get("guardrail_warnings", []) or [])
        elif event.kind == "cancelled":
            cancelled = True
            assistant_response = event.text

    if not assistant_response:
        assistant_response = "".join(assistant_chunks).strip()

    return {
        "assistant_response": assistant_response,
        "trajectory": trajectory,
        "history_turns": agent.history_turns(),
        "stream_chunks": assistant_chunks,
        "thought_chunks": thought_chunks if trace else [],
        "status_messages": status_messages,
        "cancelled": cancelled,
        "guardrail_warnings": guardrail_warnings,
    }


def resolve_tool(agent: RLMReActChatAgent, name: str) -> Callable[..., Any]:
    return get_tool_by_name(agent, name)


def resolve_tool_delegate(agent: RLMReActChatAgent, name: str) -> Callable[..., Any]:
    if name in TOOL_DELEGATE_NAMES:
        return get_tool_by_name(agent, name)
    raise AttributeError(f"{type(agent).__name__!r} object has no attribute {name!r}")
