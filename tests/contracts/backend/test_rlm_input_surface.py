"""Contract: FleetRLMSignature + History construct for B2 input surface."""

from __future__ import annotations

from uuid import uuid4

import dspy

from fleet_rlm.rlm.signature import FleetRLMSignature
from fleet_rlm.sessions.history import turns_to_history
from fleet_rlm.sessions.models import TurnRecord


def test_fleet_signature_constructs_with_history() -> None:
    history = dspy.History(
        messages=[
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
    )
    assert "history" in FleetRLMSignature.input_fields
    assert len(history.messages) == 2


def test_turns_to_history_supports_turn_two_nonempty() -> None:
    session_id = uuid4()
    turns = [
        TurnRecord(
            id=uuid4(),
            session_id=session_id,
            sequence=1,
            role="user",
            content="hello",
            status="completed",
            run_id=uuid4(),
        ),
        TurnRecord(
            id=uuid4(),
            session_id=session_id,
            sequence=2,
            role="assistant",
            content="world",
            status="completed",
            run_id=uuid4(),
        ),
    ]
    history = turns_to_history(turns)
    assert len(history.messages) >= 2
    assert history.messages[0]["role"] == "user"
    assert history.messages[1]["role"] == "assistant"
