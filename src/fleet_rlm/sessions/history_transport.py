"""Narrow committed-Session-History transport for native ``dspy.RLM``.

This is the single permitted P43.7 fallback for the fact that DSPy 3.3.1's
Daytona interpreter bridge cannot inject a raw ``dspy.History`` Pydantic value
into the Sandbox. The wrapper carries exactly the same canonical
``{"request": ..., "answer": ...}`` records that ``dspy.History`` would carry;
it never truncates them, never replaces them with previews, and never injects
the transcript into an LM prompt. Inside the Sandbox the reconstructed value
exposes ``history.messages`` so model-authored Python inspects complete
committed conversation exactly as the preferred native shape allows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import dspy

_PREVIEW_BUDGET_CHARS = 500


def _validate_messages(messages: tuple[dict[str, str], ...]) -> None:
    """Enforce the canonical conversation record contract without truncation."""
    for record in messages:
        if not isinstance(record, dict) or set(record) != {"request", "answer"}:
            raise ValueError("committed Session History records must have exactly 'request' and 'answer' keys")
        if not isinstance(record["request"], str) or not isinstance(record["answer"], str):
            raise ValueError("committed Session History record fields must be strings")


@dataclass(frozen=True, slots=True)
class CommittedSessionHistory(dspy.SandboxSerializable):
    """Complete canonical Session conversation materialized inside a Sandbox."""

    messages: tuple[dict[str, str], ...]

    def __init__(self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]) -> None:
        if not all(isinstance(record, dict) for record in messages):
            raise ValueError("committed Session History records must be dictionaries")
        validated = tuple(dict(record) for record in messages)
        _validate_messages(validated)
        object.__setattr__(self, "messages", validated)

    def __repr__(self) -> str:
        """Describe the transport without copying conversation bodies into logs."""
        return f"{type(self).__name__}(messages={len(self.messages)})"

    def __str__(self) -> str:
        """Keep explicit string formatting subject to the same redaction rule."""
        return repr(self)

    def sandbox_setup(self) -> str:
        return (
            "import json as _fleet_history_json\n"
            "\n"
            "class _FleetCommittedHistory:\n"
            '    """Host-materialized committed Session conversation."""\n'
            "\n"
            '    __slots__ = ("messages",)\n'
            "\n"
            "    def __init__(self, messages):\n"
            '        object.__setattr__(self, "messages", list(messages))\n'
            "\n"
            "    def __repr__(self):\n"
            '        return f"_FleetCommittedHistory(messages={len(self.messages)})"\n'
            "\n"
            "def _fleet_load_committed_history(raw):\n"
            "    return _FleetCommittedHistory(_fleet_history_json.loads(raw))\n"
        )

    def to_sandbox(self) -> bytes:
        return json.dumps(
            list(self.messages),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        return (
            "try:\n"
            f"    {var_name} = _fleet_load_committed_history({data_expr})\n"
            "finally:\n"
            "    del _fleet_load_committed_history\n"
        )

    def rlm_preview(self, max_chars: int = _PREVIEW_BUDGET_CHARS) -> str:
        preview = (
            f"committed session conversation: {len(self.messages)} request/answer records "
            "(inspect `history.messages` with Python when earlier turns matter)"
        )
        return preview[: max(1, min(max_chars, _PREVIEW_BUDGET_CHARS))]


def committed_session_history_payload(value: Any) -> Any:
    """Return the JSON-safe payload used for host-side diagnostics and tests."""
    if isinstance(value, CommittedSessionHistory):
        return [dict(record) for record in value.messages]
    raise TypeError(f"expected CommittedSessionHistory, got {type(value).__name__}")


__all__ = ["CommittedSessionHistory", "committed_session_history_payload"]
