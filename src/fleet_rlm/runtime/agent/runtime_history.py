"""Conversation history compaction for AgentRuntime."""

from __future__ import annotations

import logging
from typing import Any

import dspy

logger = logging.getLogger(__name__)


def estimate_history_chars(history: dspy.History) -> int:
    """Estimate character count of history as a proxy for token usage."""
    messages = list(getattr(history, "messages", []) or [])
    return sum(len(str(msg.get("user_message", ""))) + len(str(msg.get("response", ""))) for msg in messages)


def maybe_refresh_summary(runtime: Any) -> None:
    """Regenerate conversation_summary when token budget or interval triggers compaction."""
    if not runtime._use_escalation:
        return

    runtime._turns_since_summary += 1

    history_chars = estimate_history_chars(runtime.history)
    max_context_chars = 64000 * 4
    threshold_chars = int(max_context_chars * runtime._compaction_threshold_pct)

    should_compact = history_chars > threshold_chars or runtime._turns_since_summary >= runtime._summary_interval

    if should_compact:
        escalating = runtime.agent
        if hasattr(escalating, "compress_history"):
            try:
                runtime.conversation_summary = escalating.compress_history(runtime.history)
                runtime._turns_since_summary = 0
                logger.debug(
                    "AgentRuntime: conversation summary refreshed (chars=%d, threshold=%d)",
                    history_chars,
                    threshold_chars,
                )
            except Exception as exc:
                logger.warning("AgentRuntime: summary refresh failed: %s", exc)
