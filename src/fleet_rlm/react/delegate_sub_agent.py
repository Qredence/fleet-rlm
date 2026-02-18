"""Sub-agent spawning helper for RLM delegate tools.

Provides a unified mechanism for delegate tools to spawn full recursive
sub-agents (``RLMReActChatAgent`` instances) instead of invoking single-shot
``dspy.RLM`` modules directly.  This aligns delegate tools with the true
recursion pattern used by ``rlm_query``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


def spawn_delegate_sub_agent(
    agent: "RLMReActChatAgent",
    *,
    prompt: str,
    document: str | None = None,
    document_alias: str = "active",
) -> dict[str, Any]:
    """Spawn a recursive sub-agent and run a single chat turn.

    This mirrors the ``rlm_query`` pattern: a new ``RLMReActChatAgent`` is
    created at ``current_depth + 1``, sharing the parent's interpreter.
    Optionally pre-loads a document into the sub-agent's cache before
    executing the prompt.

    Args:
        agent: The parent agent requesting delegation.
        prompt: The task prompt for the sub-agent.
        document: Optional document text to pre-load.
        document_alias: Alias under which to store the document.

    Returns:
        The ``chat_turn`` result dict from the sub-agent, augmented with
        ``depth`` and ``sub_agent_history`` keys.
    """
    if agent._current_depth >= agent._max_depth:
        return {
            "status": "error",
            "error": (
                f"Max recursion depth ({agent._max_depth}) reached. "
                "Cannot spawn delegate sub-agent."
            ),
        }

    SubAgentClass = agent.__class__

    sub_agent = SubAgentClass(
        interpreter=agent.interpreter,
        max_depth=agent._max_depth,
        current_depth=agent._current_depth + 1,
    )

    if document is not None:
        sub_agent._set_document(document_alias, document)

    result = sub_agent.chat_turn(prompt)

    return {
        **result,
        "depth": agent._current_depth + 1,
        "sub_agent_history": sub_agent.history_turns(),
    }


def parse_json_from_response(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a JSON object from sub-agent prose.

    Tries the full text first, then looks for fenced code blocks.
    Returns ``None`` when no valid JSON object is found.
    """
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass  # not valid JSON; fall through to fenced-block / plain-text heuristics

    # Try fenced code block
    for marker in ("```json", "```"):
        start = text.find(marker)
        if start == -1:
            continue
        start += len(marker)
        end = text.find("```", start)
        if end == -1:
            continue
        candidate = text[start:end].strip()
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            continue

    return None
