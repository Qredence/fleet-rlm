"""P44 first-class durable Session History projection.

This module is the P44.1 Session-History entry point. It owns the projection
from durable committed Turns to the canonical ``{"request": str, "answer": str}``
records consumed by ``dspy.History`` and the existing
``read_session_history`` Tool.

Only the closed user-facing conversation ever enters the history:

* no hidden reasoning, generated code, or raw Tool output;
* no uncommitted Artifact Candidates;
* no internal errors, provider messages, or live trajectory;
* no failed, cancelled, timed-out, or otherwise uncommitted Turns.

Exclusion is enforced through the existing
:class:`CommittedTurn` terminal status: a CommittedTurn with a
:class:`StatusPart` whose ``phase``/``status`` marks a terminal failure
(cancelled, failed, timed-out, or any non-``execution`` phase) is filtered
out before the canonical record is materialized.

The ``dspy.History`` instance is built directly from the installed
``dspy.History`` Pydantic model (DSPy 3.4.0). Fleet never re-implements the
History container; the function only ever returns the exact installed class.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, NoReturn, Self, cast

import dspy

from fleet_rlm.rlm.events import JsonValue, ToolEventView
from fleet_rlm.rlm.result import empty_rlm_usage
from fleet_rlm.sessions.committed_turn import CommittedTurn, StatusPart, TextPart, UsagePart
from fleet_rlm.sessions.models import HistoryMessage, SessionHistory
from fleet_rlm.sessions.run_state import ClaimedRun

__all__ = [
    "SESSION_HISTORY_RESULT_BYTE_BUDGET",
    "CommittedSessionHistory",
    "SessionHistoryToolHost",
    "claimed_history_records",
    "committed_history_for_claim",
    "committed_session_history_payload",
    "dspy_history_for_claim",
    "is_committed_conversation_turn",
    "to_canonical_history_records",
    "to_dspy_history",
    "validate_legacy_records",
]


_CANONICAL_RECORD_KEYS: Final[frozenset[str]] = frozenset({"request", "answer"})

# Terminal ``StatusPart`` phases that mark a Turn as failed/cancelled/timed-out
# and therefore excluded from the canonical Session conversation. A phase of
# ``"execution"`` (used for degraded execution notices) is *not* terminal and
# does not exclude the Turn.
_TERMINAL_FAILURE_PHASES: Final[frozenset[str]] = frozenset({"cancelled", "failed", "timed_out", "timeout"})
_TERMINAL_FAILURE_STATUSES: Final[frozenset[str]] = frozenset({"cancelled", "failed", "timed_out", "timeout"})


def _has_terminal_failure_status(committed_turn: CommittedTurn) -> bool:
    """Return ``True`` when ``committed_turn`` carries a terminal-failure ``StatusPart``.

    A successful committed Turn never carries a :class:`StatusPart` whose
    ``phase``/``status`` marks a terminal failure. Cancellation tombstones
    already use ``phase="cancelled"`` / ``status="cancelled"``; failed and
    timed-out Turns follow the same convention. The check is intentionally
    conservative: any non-``execution`` phase is treated as terminal.
    """
    for part in committed_turn.parts:
        if not isinstance(part, StatusPart):
            continue
        if part.phase in _TERMINAL_FAILURE_PHASES or part.status in _TERMINAL_FAILURE_STATUSES:
            return True
        if part.phase != "execution":
            return True
    return False


def is_committed_conversation_turn(committed_turn: CommittedTurn) -> bool:
    """Return ``True`` when ``committed_turn`` is a successful user-facing conversation Turn."""
    return not _has_terminal_failure_status(committed_turn)


def _validate_user_requests(
    committed_turns: Sequence[CommittedTurn],
    user_requests: Sequence[str],
) -> None:
    if len(user_requests) != len(committed_turns):
        raise ValueError(
            "user_requests must align with committed_turns ("
            f"got {len(user_requests)} requests for {len(committed_turns)} turns)"
        )
    for index, request in enumerate(user_requests):
        if not isinstance(request, str):
            raise ValueError(f"user_requests[{index}] must be a string, got {type(request).__name__}")


def to_canonical_history_records(
    committed_turns: Sequence[CommittedTurn],
    *,
    user_requests: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """Project a list of committed Turns to canonical ``{"request", "answer"}`` records.

    The returned list contains exactly the user-facing committed conversation
    for the supplied checkpoint: one record per successfully committed Turn
    whose ``request`` is the committed user-facing message and whose
    ``answer`` is the committed user-facing assistant answer text.

    Failed, cancelled, timed-out, and otherwise uncommitted Turns are excluded
    via the existing :class:`CommittedTurn` terminal-status contract.

    ``user_requests`` is a parallel sequence of committed user-facing messages
    in the same order as ``committed_turns``. It must align by length and
    must contain only ``str`` values. When ``user_requests`` is omitted the
    ``request`` field is an empty string, which keeps the function total but
    forces callers to provide the user-facing text when emitting the record
    into ``dspy.History`` (the existing ``read_session_history`` Tool keeps
    working because that surface is independent of the canonical record).
    """
    if user_requests is not None:
        _validate_user_requests(committed_turns, user_requests)

    records: list[dict[str, str]] = []
    request_index = 0
    for committed_turn in committed_turns:
        if _has_terminal_failure_status(committed_turn):
            request_index += 1
            continue
        request_text = user_requests[request_index] if user_requests is not None else ""
        records.append({"request": request_text, "answer": committed_turn.text})
        request_index += 1
    return records


def to_dspy_history(
    committed_turns: Sequence[CommittedTurn],
    *,
    user_requests: Sequence[str] | None = None,
) -> dspy.History:
    """Materialize the complete committed Session conversation as a ``dspy.History``.

    The returned object is the exact installed ``dspy.History`` Pydantic
    model (DSPy 3.4.0). It is never a subclass, replacement, or Pydantic
    shadow. An empty input sequence yields a valid ``dspy.History(messages=[])``
    that remains compatible with the existing ``read_session_history`` Tool
    and the canonical ``{request, answer}`` contract.

    See :func:`to_canonical_history_records` for the user-request pairing and
    exclusion rules.
    """
    records = to_canonical_history_records(committed_turns, user_requests=user_requests)
    return dspy.History(messages=records)


def claimed_history_records(claim: ClaimedRun) -> tuple[tuple[CommittedTurn, ...], tuple[str, ...]]:
    """Project the immutable claimed Session checkpoint to canonical history inputs.

    Failure tombstones may remain in the bounded Session audit history. Their
    ``CommittedTurn`` metadata excludes them from the model conversation,
    while preserving the user/assistant pairing for successful Turns.
    """
    committed_turns: list[CommittedTurn] = []
    user_requests: list[str] = []
    pending_user_text: str | None = None
    for message in claim.history.messages:
        if not isinstance(message, HistoryMessage):
            continue
        if message.role == "user":
            pending_user_text = message.content
            continue
        if message.role != "assistant":
            continue
        if message.committed_turn is not None and not is_committed_conversation_turn(message.committed_turn):
            pending_user_text = None
            continue
        if pending_user_text is None:
            continue
        committed_turns.append(
            CommittedTurn(
                schema_version=1,
                parts=(UsagePart(value=empty_rlm_usage()), TextPart(text=message.content)),
            )
        )
        user_requests.append(pending_user_text)
        pending_user_text = None
    return tuple(committed_turns), tuple(user_requests)


def dspy_history_for_claim(claim: ClaimedRun) -> dspy.History:
    """Materialize the exact DSPy History type from a lifecycle-issued claim."""
    committed_turns, user_requests = claimed_history_records(claim)
    return to_dspy_history(committed_turns, user_requests=user_requests)


def validate_legacy_records(
    records: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    """Normalize legacy Session-history payloads to canonical records.

    Accepts only the canonical ``{"request": str, "answer": str}`` shape: extra
    keys, missing keys, non-string fields, or non-mapping entries are all
    rejected with :class:`ValueError` (fail closed; no silent truncation).

    The returned list contains fresh ``dict`` copies, one per accepted input
    record, in the same order. Empty input is allowed and yields an empty list.
    """
    normalized: list[dict[str, str]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError(
                f"legacy Session-history record at index {index} must be a mapping, got {type(record).__name__}"
            )
        keys = set(record)
        if keys != _CANONICAL_RECORD_KEYS:
            raise ValueError(
                f"legacy Session-history record at index {index} must contain exactly "
                f"the canonical keys {sorted(_CANONICAL_RECORD_KEYS)}, got {sorted(keys)}"
            )
        request_value = record["request"]
        answer_value = record["answer"]
        if not isinstance(request_value, str):
            raise ValueError(
                f"legacy Session-history record at index {index} 'request' must be a string, "
                f"got {type(request_value).__name__}"
            )
        if not isinstance(answer_value, str):
            raise ValueError(
                f"legacy Session-history record at index {index} 'answer' must be a string, "
                f"got {type(answer_value).__name__}"
            )
        normalized.append({"request": request_value, "answer": answer_value})
    return normalized


SESSION_HISTORY_RESULT_BYTE_BUDGET = 262_144


@dataclass(frozen=True, slots=True)
class SessionHistoryToolHost:
    """Bind one immutable authorized Session History to generated code."""

    history: SessionHistory

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def read_session_history(offset: int, limit: int) -> dict[str, object]:
            """Read a bounded page of canonical committed Session messages."""
            if offset < 0 or limit < 1 or limit > 20:
                raise ValueError("Session history request is invalid")
            total = len(self.history.messages)
            selected: list[dict[str, object]] = []
            bytes_returned = 0
            truncated = False
            skipped_ordinal: int | None = None
            current_offset = offset
            while len(selected) < limit and current_offset < total:
                message = self.history.messages[current_offset]
                ordinal = current_offset + 1
                content_bytes = len(message.content.encode("utf-8"))
                if content_bytes > SESSION_HISTORY_RESULT_BYTE_BUDGET:
                    skipped_ordinal = ordinal
                    truncated = True
                    current_offset += 1
                    continue
                if bytes_returned + content_bytes > SESSION_HISTORY_RESULT_BYTE_BUDGET:
                    truncated = True
                    break
                selected.append(
                    {
                        "ordinal": ordinal,
                        "role": message.role,
                        "content": message.content,
                    }
                )
                bytes_returned += content_bytes
                current_offset += 1
            done = current_offset >= total
            result: dict[str, object] = {
                "offset": offset,
                "next_offset": None if done else current_offset,
                "total": total,
                "has_more": not done,
                "done": done,
                "messages": selected,
                "truncated": truncated,
                "bytes_returned": bytes_returned,
                "byte_budget": SESSION_HISTORY_RESULT_BYTE_BUDGET,
            }
            if skipped_ordinal is not None:
                result["skipped_ordinal"] = skipped_ordinal
            return result

        return (
            dspy.Tool(
                read_session_history,
                name="read_session_history",
                desc=(
                    "Read a bounded page dictionary of older committed messages only when the current request "
                    'requires prior-turn evidence. Iterate result["messages"]; each message contains role and '
                    "content; do not read history for self-contained requests."
                ),
                args={
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        def project_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {key: arguments[key] for key in ("offset", "limit") if key in arguments}

        def project_output(result: object) -> JsonValue:
            if not isinstance(result, Mapping):
                return {}
            messages = result.get("messages")
            values = cast(Mapping[str, JsonValue], result)
            projected: dict[str, JsonValue] = {
                key: values[key]
                for key in (
                    "offset",
                    "next_offset",
                    "total",
                    "has_more",
                    "done",
                    "truncated",
                    "bytes_returned",
                    "byte_budget",
                    "skipped_ordinal",
                )
                if key in values
            }
            projected["message_count"] = len(messages) if isinstance(messages, list) else 0
            return projected

        return MappingProxyType(
            {
                "read_session_history": ToolEventView(
                    input_projection=project_input,
                    output_projection=project_output,
                )
            }
        )


_PREVIEW_BUDGET_CHARS = 500


class _ImmutableHistoryRecord(dict[str, str]):
    """Dict-shaped canonical record that cannot be mutated after snapshotting."""

    def _immutable(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("committed Session History is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable  # type: ignore[assignment]


def _validate_messages(messages: tuple[dict[str, str], ...]) -> None:
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
        validated = tuple(_ImmutableHistoryRecord(record) for record in messages)
        _validate_messages(validated)
        object.__setattr__(self, "messages", validated)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(messages={len(self.messages)})"

    def __str__(self) -> str:
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
    if isinstance(value, CommittedSessionHistory):
        return [dict(record) for record in value.messages]
    raise TypeError(f"expected CommittedSessionHistory, got {type(value).__name__}")


def committed_history_for_claim(claim: ClaimedRun) -> CommittedSessionHistory:
    committed_turns, user_requests = claimed_history_records(claim)
    return CommittedSessionHistory(to_canonical_history_records(committed_turns, user_requests=user_requests))
