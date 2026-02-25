"""Sub-agent spawning helper for RLM delegate tools.

Provides a unified mechanism for delegate tools to spawn full recursive
sub-agents (``RLMReActChatAgent`` instances) instead of invoking single-shot
``dspy.RLM`` modules directly.  This aligns delegate tools with the true
recursion pattern used by ``rlm_query``.
"""

from __future__ import annotations

import json
import logging
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

import dspy

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

    claim_slot = getattr(agent, "_claim_delegate_slot", None)
    if callable(claim_slot):
        claim_result = claim_slot()
        if (
            isinstance(claim_result, tuple)
            and len(claim_result) == 2
            and isinstance(claim_result[0], bool)
        ):
            allowed = bool(claim_result[0])
            limit = int(claim_result[1])
            if not allowed:
                return {
                    "status": "error",
                    "error": (
                        "Delegate call budget reached for this turn. "
                        f"Maximum delegate calls per turn is {limit}."
                    ),
                    "delegate_max_calls_per_turn": limit,
                }

    SubAgentClass = agent.__class__

    delegate_lm = getattr(agent, "delegate_lm", None)
    parent_lm = getattr(dspy.settings, "lm", None)
    fallback_used = delegate_lm is None
    if fallback_used and callable(getattr(agent, "_record_delegate_fallback", None)):
        agent._record_delegate_fallback()

    sub_agent = SubAgentClass(
        react_max_iters=getattr(agent, "react_max_iters", 10),
        deep_react_max_iters=getattr(agent, "deep_react_max_iters", 35),
        enable_adaptive_iters=getattr(agent, "enable_adaptive_iters", True),
        rlm_max_iterations=getattr(agent, "rlm_max_iterations", 30),
        rlm_max_llm_calls=getattr(agent, "rlm_max_llm_calls", 50),
        verbose=getattr(agent, "verbose", False),
        history_max_turns=getattr(agent, "history_max_turns", None),
        interpreter=agent.interpreter,
        max_depth=agent._max_depth,
        current_depth=agent._current_depth + 1,
        guardrail_mode=getattr(agent, "guardrail_mode", "off"),
        max_output_chars=getattr(agent, "max_output_chars", 10000),
        min_substantive_chars=getattr(agent, "min_substantive_chars", 20),
        delegate_lm=delegate_lm,
        delegate_max_calls_per_turn=getattr(agent, "delegate_max_calls_per_turn", 8),
        delegate_result_truncation_chars=getattr(
            agent, "delegate_result_truncation_chars", 8000
        ),
    )

    if document is not None:
        sub_agent._set_document(document_alias, document)

    lm_context = (
        dspy.context(lm=delegate_lm)
        if delegate_lm is not None
        else (dspy.context(lm=parent_lm) if parent_lm is not None else nullcontext())
    )
    try:
        with lm_context:
            result = sub_agent.chat_turn(prompt)
    except Exception:
        if delegate_lm is not None and parent_lm is not None:
            if callable(getattr(agent, "_record_delegate_fallback", None)):
                agent._record_delegate_fallback()
            fallback_used = True
            with dspy.context(lm=parent_lm):
                result = sub_agent.chat_turn(prompt)
        else:
            raise

    result_copy = dict(result)
    result_copy.setdefault("status", "ok")
    response_text = str(result_copy.get("assistant_response", ""))
    truncation_limit = int(getattr(agent, "delegate_result_truncation_chars", 8000))
    if truncation_limit > 0 and len(response_text) > truncation_limit:
        truncated = response_text[:truncation_limit].rstrip()
        result_copy["assistant_response"] = (
            f"{truncated}\n\n[truncated delegate output]"
        )
        result_copy["delegate_output_truncated"] = True
        if callable(getattr(agent, "_record_delegate_truncation", None)):
            agent._record_delegate_truncation()
    else:
        result_copy["delegate_output_truncated"] = False

    return {
        **result_copy,
        "depth": agent._current_depth + 1,
        "sub_agent_history": sub_agent.history_turns(),
        "delegate_lm_fallback": fallback_used,
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
