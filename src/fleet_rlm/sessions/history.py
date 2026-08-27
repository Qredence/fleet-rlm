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
``dspy.History`` Pydantic model (DSPy 3.3.1). Fleet never re-implements the
History container; the function only ever returns the exact installed class.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

import dspy

from fleet_rlm.sessions.committed_turn import CommittedTurn, StatusPart

__all__ = [
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
    model (DSPy 3.3.1). It is never a subclass, replacement, or Pydantic
    shadow. An empty input sequence yields a valid ``dspy.History(messages=[])``
    that remains compatible with the existing ``read_session_history`` Tool
    and the canonical ``{request, answer}`` contract.

    See :func:`to_canonical_history_records` for the user-request pairing and
    exclusion rules.
    """
    records = to_canonical_history_records(committed_turns, user_requests=user_requests)
    return dspy.History(messages=records)


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
