"""Session/history helpers for :mod:`fleet_rlm.runtime.agent.chat_agent`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import dspy
from fleet_rlm.integrations.daytona.types import normalize_history_turn
from fleet_rlm.utils.paths import dedupe_paths

if TYPE_CHECKING:
    from .chat_agent import RLMReActChatAgent


_HISTORY_SUMMARY_USER_REQUEST = "[summary of earlier conversation]"
_HISTORY_SNIPPET_LIMIT = 240


def _trim_history_text(value: Any, *, limit: int = _HISTORY_SNIPPET_LIMIT) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _summary_message(messages: list[Any]) -> dict[str, str] | None:
    lines: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        user_request = _trim_history_text(item.get("user_request"))
        assistant_response = _trim_history_text(item.get("assistant_response"))
        if user_request:
            lines.append(f"User: {user_request}")
        if assistant_response:
            lines.append(f"Assistant: {assistant_response}")
    if not lines:
        return None
    return {
        "user_request": _HISTORY_SUMMARY_USER_REQUEST,
        "assistant_response": "Earlier conversation summary:\n" + "\n".join(lines),
    }


def _enforce_history_cap(
    messages: list[Any], history_max_turns: int | None
) -> list[Any]:
    if history_max_turns is None or history_max_turns <= 0:
        return messages
    if len(messages) <= history_max_turns:
        return messages
    if history_max_turns == 1:
        return messages[-1:]

    preserved_tail = max(1, history_max_turns - 1)
    head = messages[:-preserved_tail]
    tail = messages[-preserved_tail:]
    summary = _summary_message(head)
    if summary is None:
        return tail[-history_max_turns:]
    return [summary, *tail][-history_max_turns:]


def history_messages(agent: RLMReActChatAgent) -> list[Any]:
    """Return chat history messages as a defensive list copy."""
    messages = getattr(agent.history, "messages", None)
    if messages is None:
        return []
    try:
        return list(messages)
    except TypeError:
        return []


def history_turns(agent: RLMReActChatAgent) -> int:
    """Return number of stored history turns safely."""
    return len(history_messages(agent))


def append_history(
    agent: RLMReActChatAgent, user_request: str, assistant_response: str
) -> None:
    """Append one chat turn and enforce the configured history cap."""
    messages = history_messages(agent)
    messages.append(
        {
            "user_request": user_request,
            "assistant_response": assistant_response,
        }
    )
    messages = _enforce_history_cap(messages, agent.history_max_turns)
    agent.history = dspy.History(messages=messages)


def export_session_state(agent: RLMReActChatAgent) -> dict[str, Any]:
    """Export serializable session state for persistence."""
    history: list[dict[str, str]] = []
    for item in history_messages(agent):
        if not isinstance(item, dict):
            continue
        turn = normalize_history_turn(item)
        if turn is not None:
            history.append(turn)
    payload = {
        "history": history,
        **agent.get_document_cache_state(),
        "core_memory": agent._core_memory,
    }
    interpreter = getattr(agent, "interpreter", None)
    export_state = getattr(interpreter, "export_session_state", None)
    if callable(export_state):
        extra = export_state()
        if isinstance(extra, dict):
            payload.update(extra)
    daytona_payload: dict[str, Any]
    existing_daytona_payload = payload.get("daytona")
    if isinstance(existing_daytona_payload, dict):
        daytona_payload = dict(existing_daytona_payload)
    else:
        daytona_payload = {}
    daytona_payload["loaded_document_paths"] = list(agent.loaded_document_paths)
    payload["daytona"] = daytona_payload
    return payload


def import_session_state(
    agent: RLMReActChatAgent, state: dict[str, Any]
) -> dict[str, Any]:
    """Restore session state from a previously exported payload."""
    history = _restore_agent_state(agent, state)

    interpreter = getattr(agent, "interpreter", None)
    import_state = getattr(interpreter, "import_session_state", None)
    if callable(import_state):
        import_state(state)

    return _import_result(agent, history)


async def aimport_session_state(
    agent: RLMReActChatAgent, state: dict[str, Any]
) -> dict[str, Any]:
    """Async restore variant for interpreters with async session state hooks."""
    history = _restore_agent_state(agent, state)

    interpreter = getattr(agent, "interpreter", None)
    async_import_state = getattr(interpreter, "aimport_session_state", None)
    if callable(async_import_state):
        await async_import_state(state)
    else:
        import_state = getattr(interpreter, "import_session_state", None)
        if callable(import_state):
            import_state(state)

    return _import_result(agent, history)


def _restore_agent_state(agent: RLMReActChatAgent, state: dict[str, Any]) -> list[Any]:
    """Restore shared chat/session state prior to interpreter-specific hooks."""
    history = state.get("history", [])
    if not isinstance(history, list):
        history = []
    normalized_history = []
    for item in history:
        if not isinstance(item, dict):
            continue
        turn = normalize_history_turn(item)
        if turn is not None:
            normalized_history.append(turn)
    agent.history = dspy.History(
        messages=_enforce_history_cap(normalized_history, agent.history_max_turns)
    )

    agent.restore_document_cache_state(state)
    daytona_state = state.get("daytona", {})
    if not isinstance(daytona_state, dict):
        daytona_state = {}
    agent.loaded_document_paths = dedupe_paths(
        [
            str(item)
            for item in daytona_state.get("loaded_document_paths", []) or []
            if str(item or "").strip()
        ]
    )

    core_memory = state.get("core_memory")
    agent.set_core_memory(core_memory)
    return normalized_history


def _import_result(agent: RLMReActChatAgent, history: list[Any]) -> dict[str, Any]:
    """Build the canonical import response payload."""
    return {
        "status": "ok",
        "history_turns": history_turns(agent),
        "documents": len(agent._document_cache),
        "active_alias": agent.active_alias,
        "core_memory_keys": agent.get_core_memory_keys(),
    }


def forced_delegate_context(agent: RLMReActChatAgent) -> str:
    """Build the compact forced-RLM context payload for recursive delegation."""
    parts: list[str] = []

    core_memory = str(agent.fmt_core_memory() or "").strip()
    if core_memory:
        parts.append(f"Core memory:\n{core_memory}")

    history_lines: list[str] = []
    for item in history_messages(agent)[-6:]:
        if not isinstance(item, dict):
            continue
        user_request = str(item.get("user_request", "") or "").strip()
        assistant_response = str(item.get("assistant_response", "") or "").strip()
        if user_request:
            history_lines.append(f"User: {user_request}")
        if assistant_response:
            history_lines.append(f"Assistant: {assistant_response}")

    if history_lines:
        parts.append("Recent conversation:\n" + "\n".join(history_lines))

    return "\n\n".join(parts)
