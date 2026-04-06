"""Tests for ``fleet_rlm.integrations.observability.dspy_evaluation``."""

from __future__ import annotations

import json

import dspy

from fleet_rlm.integrations.observability.dspy_evaluation import (
    evaluate_program,
    evaluate_program_from_dataset,
)
from fleet_rlm.integrations.observability.workspace_metrics import (
    workspace_feedback_metric,
)


class _EchoProgram(dspy.Module):
    def forward(self, question: str):
        return dspy.Prediction(assistant_response=question)


def _example(question: str, expected: str) -> dspy.Example:
    return dspy.Example(
        question=question,
        assistant_response=expected,
    ).with_inputs("question")


def test_evaluate_program_default_metric_returns_numeric_score() -> None:
    result = evaluate_program(
        _EchoProgram(),
        [_example("hello", "hello")],
        display_progress=False,
    )

    assert result == {
        "score": 100.0,
        "num_examples": 1,
        "metric": "workspace_score_metric",
    }


def test_evaluate_program_coerces_feedback_metric_to_numeric_score() -> None:
    result = evaluate_program(
        _EchoProgram(),
        [_example("hello", "hello")],
        metric=workspace_feedback_metric,
        display_progress=False,
    )

    assert result["score"] == 100.0
    assert result["metric"] == "workspace_feedback_metric"


def test_evaluate_program_supports_return_all_scores_and_outputs() -> None:
    result = evaluate_program(
        _EchoProgram(),
        [_example("hello", "hello"), _example("goodbye", "hello")],
        display_progress=False,
        return_all_scores=True,
        return_outputs=True,
    )

    assert result["score"] == 50.0
    assert result["all_scores"] == [1.0, 0.0]
    outputs = result["outputs"]
    assert len(outputs) == 2
    assert [prediction.assistant_response for prediction in outputs] == [
        "hello",
        "goodbye",
    ]


def test_evaluate_program_from_dataset_uses_exact_match_default(
    tmp_path,
) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "inputs": {"question": "hello"},
                    "expectations": {"expected_response": "hello"},
                }
            ]
        ),
        encoding="utf-8",
    )

    result = evaluate_program_from_dataset(
        program=_EchoProgram(),
        dataset_path=dataset_path,
    )

    assert result["score"] == 100.0
    assert result["metric"] == "metric"
