"""Rebuild dspy.History from completed turn records."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import dspy

from fleet_rlm.sessions.models import TurnRecord


def turns_to_history(turns: Sequence[TurnRecord]) -> dspy.History:
    """Map completed turns (stable sequence order) into dspy.History messages.

    Only completed turns should be passed (repository.load already filters).
    Roles other than user/assistant are skipped for the conversational History.
    """
    messages: list[dict[str, Any]] = []
    for turn in turns:
        if turn.status != "completed":
            continue
        role = turn.role.strip().lower()
        if role not in {"user", "assistant"}:
            continue
        messages.append({"role": role, "content": turn.content})
    return dspy.History(messages=messages)


def history_message_count(history: dspy.History | None) -> int:
    if history is None:
        return 0
    messages = getattr(history, "messages", None) or []
    return len(messages)
