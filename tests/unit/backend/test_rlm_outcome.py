"""Runner outcome domain contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest


def test_rlm_outcome_is_internal_immutable_and_terminally_typed() -> None:
    from fleet_rlm.rlm.events import RLMReasoning
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome

    outcome = RLMOutcome(
        terminal_status="completed",
        prediction=PredictionResult("answer", {"answer": "answer"}, "default", "1"),
        usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2},
        execution_details=(RLMReasoning(text="bounded", step=1),),
    )

    assert outcome.succeeded is True
    assert outcome.prediction.display_text == "answer"
    assert outcome.execution_details == (RLMReasoning(text="bounded", step=1),)
    with pytest.raises(FrozenInstanceError):
        outcome.prediction = None  # type: ignore[misc]


def test_success_requires_prediction_and_failure_forbids_it() -> None:
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome

    with pytest.raises(ValueError, match="prediction"):
        RLMOutcome(terminal_status="completed")
    with pytest.raises(ValueError, match="prediction"):
        RLMOutcome(
            terminal_status="failed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
        )
