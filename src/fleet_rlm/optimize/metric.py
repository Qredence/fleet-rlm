"""The GEPA metric: judged correctness plus reflective feedback."""

from __future__ import annotations

from typing import Any

import dspy

from fleet_rlm.engine.signatures import AnswerJudge


def gepa_mlflow_metric(
    gold: dspy.Example,
    pred: dspy.Prediction,
    trace: Any = None,  # noqa: ARG001
    pred_name: str | None = None,  # noqa: ARG001
    pred_trace: Any = None,  # noqa: ARG001
    program_trace: Any = None,
    llm_call_penalty: float = 0.02,
    *,
    judge_lm: dspy.LM,
) -> dspy.Prediction:
    """Return correctness plus concise reflective guidance for GEPA."""
    expected = str(getattr(gold, "answer", ""))
    actual = str(getattr(pred, "answer", ""))
    call_count = len(program_trace) if isinstance(program_trace, (list, tuple)) else 0
    judge = dspy.ChainOfThought(AnswerJudge)
    judge.set_lm(judge_lm)
    assessment = judge(
        task=str(getattr(gold, "task", "")),
        expected_answer=expected,
        candidate_answer=actual,
    )
    correct = bool(getattr(assessment, "is_correct", False))
    score = max(0.0, (1.0 if correct else 0.0) - llm_call_penalty * max(0, call_count - 1))
    critique = str(getattr(assessment, "critique", ""))[:1000]
    feedback = (
        f"Semantic correctness={correct}: {critique}. Execution used {call_count} predictor calls. "
        "Prefer deterministic Python and existing Skill scripts for arithmetic and parsing."
    )
    return dspy.Prediction(score=score, feedback=feedback)
