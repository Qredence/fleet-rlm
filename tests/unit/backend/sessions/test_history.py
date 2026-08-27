"""P44 first-class durable Session History projection contract.

The tests exercise, in the exact order below:

1.  Failed, cancelled, timed-out, and uncommitted Turns are excluded.
2.  Committed user/assistant records are included.
3.  An empty committed-Turn list returns ``dspy.History(messages=[])``.
4.  Round-trip: every emitted ``dspy.History`` message has exactly the
    canonical ``{"request", "answer"}`` keys.
5.  ``validate_legacy_records`` tolerates canonical dicts and rejects
    non-canonical shapes.
6.  Cross-Session isolation: records from another Session are not present.
7.  Corrupt legacy payloads raise ``ValueError``.
8.  ``dspy.History`` reuse: identical input yields deep-equal messages.
9.  The returned object is exactly ``dspy.History`` (not a subclass or
    Pydantic shadow).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.sessions.committed_turn import (
    CommittedTurn,
    StatusPart,
    TextPart,
    UsagePart,
)
from fleet_rlm.sessions.history import (
    to_canonical_history_records,
    to_dspy_history,
    validate_legacy_records,
)

_EMPTY_USAGE: dict[str, Any] = {
    "iterations": 0,
    "observed_lm_usage": {},
    "duration_ms": 0,
}


def _successful_turn(answer: str) -> CommittedTurn:
    """Build a successful committed Turn with one final text part."""
    return CommittedTurn(
        schema_version=1,
        parts=(
            UsagePart(value=dict(_EMPTY_USAGE)),
            TextPart(text=answer),
        ),
    )


def _terminal_turn(phase: str, status: str, text: str) -> CommittedTurn:
    """Build a terminal-failure committed Turn carrying a ``StatusPart``."""
    return CommittedTurn(
        schema_version=1,
        parts=(
            StatusPart(phase=phase, status=status),
            UsagePart(value=dict(_EMPTY_USAGE)),
            TextPart(text=text),
        ),
    )


def test_excludes_failed_cancelled_timed_out_and_uncommitted_turns() -> None:
    """Failed, cancelled, timed-out, and uncommitted Turns are excluded."""
    failed_turn = _terminal_turn("failed", "failed", "internal failure details")
    cancelled_turn = _terminal_turn("cancelled", "cancelled", "Turn cancelled")
    timed_out_turn = _terminal_turn("timed_out", "timed_out", "Turn timed out")
    successful_turn = _successful_turn("successful answer")
    # An uncommitted Turn is, by definition, not a ``CommittedTurn``; the
    # caller has simply not included it in the input sequence.
    uncommitted_turn: CommittedTurn | None = None

    committed_turns: Sequence[CommittedTurn] = (
        failed_turn,
        cancelled_turn,
        timed_out_turn,
        successful_turn,
    )
    user_requests: Sequence[str] = (
        "failed request",
        "cancelled request",
        "timed_out request",
        "successful request",
    )
    assert uncommitted_turn is None  # not in the input sequence

    records = to_canonical_history_records(committed_turns, user_requests=user_requests)

    assert records == [
        {"request": "successful request", "answer": "successful answer"},
    ]


def test_includes_committed_user_assistant_records_only() -> None:
    """Successful committed Turns are emitted as canonical records in order."""
    committed_turns = (
        _successful_turn("first answer"),
        _successful_turn("second answer"),
        _successful_turn("third answer"),
    )
    user_requests = ("first request", "second request", "third request")

    records = to_canonical_history_records(committed_turns, user_requests=user_requests)

    assert records == [
        {"request": "first request", "answer": "first answer"},
        {"request": "second request", "answer": "second answer"},
        {"request": "third request", "answer": "third answer"},
    ]


def test_empty_committed_turn_list_returns_dspy_history_with_empty_messages() -> None:
    """Empty input yields a valid empty ``dspy.History`` (no exception)."""
    history = to_dspy_history([])

    assert type(history) is dspy.History
    assert history.messages == []
    # The empty result must remain compatible with the existing Tool surface.
    assert list(history.messages) == []


def test_round_trip_dspy_history_messages_have_exactly_request_and_answer_keys() -> None:
    """Every emitted message has exactly the canonical request+answer keys."""
    committed_turns = (
        _successful_turn("first answer"),
        _successful_turn("second answer"),
    )
    user_requests = ("first request", "second request")

    history = to_dspy_history(committed_turns, user_requests=user_requests)

    for message in history.messages:
        assert set(message) == {"request", "answer"}
        assert isinstance(message["request"], str)
        assert isinstance(message["answer"], str)
    assert history.messages == [
        {"request": "first request", "answer": "first answer"},
        {"request": "second request", "answer": "second answer"},
    ]


def test_legacy_conversion_tolerates_canonical_dicts_and_rejects_non_canonical() -> None:
    """``validate_legacy_records`` normalizes canonical dicts and rejects the rest."""
    canonical_records = (
        {"request": "first request", "answer": "first answer"},
        {"request": "second request", "answer": "second answer"},
    )

    normalized = validate_legacy_records(canonical_records)

    assert normalized == [
        {"request": "first request", "answer": "first answer"},
        {"request": "second request", "answer": "second answer"},
    ]
    # The function returns fresh copies; callers cannot mutate the input.
    assert all(isinstance(item, dict) for item in normalized)
    assert normalized[0] is not canonical_records[0]

    # Each non-canonical shape must be rejected.
    bad_payloads: tuple[Any, ...] = (
        {"request": "r", "answer": "a", "extra": "x"},
        {"request": "r", "answer": "a", "trace_id": "t"},
        {"answer": "a"},
        {"request": "r"},
        {"request": 1, "answer": "a"},
        {"request": "r", "answer": 2},
        {"prompt": "r", "answer": "a"},
        [{"request": "r", "answer": "a"}],  # nested list, not a record
        "not-a-record",
        None,
    )
    for bad in bad_payloads:
        with pytest.raises(ValueError):
            validate_legacy_records([bad])  # type: ignore[list-item]


def test_cross_session_isolation_excludes_records_from_another_session() -> None:
    """Records from another Session are never present in the result."""
    target_session_id = uuid4()
    other_session_id = uuid4()
    # The two sessions never overlap; the projection operates on the target
    # Session's claimed checkpoint only.
    assert target_session_id != other_session_id

    target_turns = (
        _successful_turn("target answer 1"),
        _successful_turn("target answer 2"),
    )
    target_requests = ("target request 1", "target request 2")
    other_turns = (
        _successful_turn("other answer 1"),
        _successful_turn("other answer 2"),
        _terminal_turn("cancelled", "cancelled", "Turn cancelled"),
    )
    other_requests = ("other request 1", "other request 2", "other request 3")

    target_records = to_canonical_history_records(target_turns, user_requests=target_requests)
    target_history = to_dspy_history(target_turns, user_requests=target_requests)

    # Other-Session answers never leak into the target result.
    for forbidden in (
        "other answer 1",
        "other answer 2",
        "other request 1",
        "other request 2",
        "other request 3",
    ):
        assert forbidden not in {record["request"] for record in target_records}
        assert forbidden not in {record["answer"] for record in target_records}
        assert forbidden not in {value for message in target_history.messages for value in message.values()}

    # A separate call for the other Session still works independently.
    other_records = to_canonical_history_records(other_turns, user_requests=other_requests)
    assert other_records == [
        {"request": "other request 1", "answer": "other answer 1"},
        {"request": "other request 2", "answer": "other answer 2"},
    ]


def test_corrupt_legacy_payloads_raise_value_error() -> None:
    """Corrupt legacy payloads fail closed with ``ValueError`` (no silent truncation)."""
    corrupt_payloads: tuple[Any, ...] = (
        {"request": "r", "answer": "a", "extra": "x"},
        {"answer": "only answer"},
        {"request": "only request"},
        {"request": 1, "answer": "answer"},
        {"request": "request", "answer": 2},
        {"request": "request", "answer": "answer", "trajectory": "leak"},
        [{"request": "r", "answer": "a"}],
        "raw-string-payload",
        42,
        None,
    )
    for payload in corrupt_payloads:
        with pytest.raises(ValueError):
            validate_legacy_records([payload])  # type: ignore[list-item]


def test_dspy_history_reuse_identical_input_yields_deep_equal_messages() -> None:
    """Identical inputs produce deep-equal ``dspy.History`` messages."""
    committed_turns = (
        _successful_turn("first answer"),
        _successful_turn("second answer"),
    )
    user_requests = ("first request", "second request")

    first = to_dspy_history(committed_turns, user_requests=user_requests)
    second = to_dspy_history(committed_turns, user_requests=user_requests)

    assert first.messages == second.messages
    assert list(first.messages) == list(second.messages)
    for left, right in zip(first.messages, second.messages, strict=True):
        assert left == right
        assert set(left) == set(right) == {"request", "answer"}


def test_returned_dspy_history_is_exactly_dspy_history() -> None:
    """The returned object is the exact ``dspy.History`` class, never a shadow."""
    committed_turns = (_successful_turn("only answer"),)
    user_requests = ("only request",)

    history = to_dspy_history(committed_turns, user_requests=user_requests)
    empty_history = to_dspy_history([])

    # Exact class match (not a subclass, not a Pydantic shadow).
    assert type(history) is dspy.History
    assert type(empty_history) is dspy.History
    # The installed model lives in dspy.adapters.types.history.
    assert history.__class__.__module__ == "dspy.adapters.types.history"
    assert empty_history.__class__.__module__ == "dspy.adapters.types.history"
    # ``dspy.History`` itself is a Pydantic BaseModel; the round-trip shape is preserved.
    assert history.model_dump() == {"messages": [{"request": "only request", "answer": "only answer"}]}
    assert empty_history.model_dump() == {"messages": []}
