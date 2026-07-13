"""Runner outcome domain contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest


def test_rlm_outcome_is_internal_immutable_and_terminally_typed() -> None:
    from fleet_rlm.rlm.events import RLMReasoning
    from fleet_rlm.rlm.outcome import RLMOutcome

    outcome = RLMOutcome(
        terminal_status="completed",
        text="answer",
        usage={"iterations": 1},
        execution_details=(RLMReasoning(text="bounded", step=1),),
    )

    assert outcome.succeeded is True
    assert outcome.text == "answer"
    assert outcome.execution_details == (RLMReasoning(text="bounded", step=1),)
    with pytest.raises(FrozenInstanceError):
        outcome.text = "changed"  # type: ignore[misc]
