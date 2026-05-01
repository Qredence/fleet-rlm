"""GEPA optimization entrypoint for the LongCoT QA reasoner module.

Registers a :class:`~fleet_rlm.runtime.quality.module_registry.ModuleOptimizationSpec`
for ``longcot-reasoner`` so that the offline CLI and API can compile it with GEPA.

The module uses :class:`~fleet_rlm.runtime.agent.signatures.LongCoTQASignature`
(question → reasoning + answer) via ``dspy.Predict``.
"""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.quality.module_registry import (
    ModuleOptimizationSpec,
    register_module,
)


# ---------------------------------------------------------------------------
# Module factory
# ---------------------------------------------------------------------------


def _module_factory() -> Any:
    """Build the LongCoT QA DSPy module."""
    import dspy

    from fleet_rlm.runtime.agent.signatures import LongCoTQASignature

    return dspy.Predict(LongCoTQASignature)


# ---------------------------------------------------------------------------
# Row converter
# ---------------------------------------------------------------------------


def _row_converter(rows: list[dict[str, Any]]) -> list[Any]:
    """Convert dataset rows into DSPy examples.

    Each row is expected to contain at least ``question`` and ``answer``.
    An optional ``reasoning`` field is used when present.
    """
    import dspy

    examples: list[dspy.Example] = []
    for row in rows:
        ex = dspy.Example(
            question=str(row.get("question", "")),
            answer=str(row.get("answer", "")),
            reasoning=str(row.get("reasoning", "")),
        ).with_inputs("question")
        examples.append(ex)
    return examples


# ---------------------------------------------------------------------------
# Metric builder
# ---------------------------------------------------------------------------


def _metric_builder() -> Any:
    """Build a GEPA-compatible feedback metric for LongCoT QA.

    Weights:
        - 70 % answer correctness (exact / substring / missing)
        - 30 % reasoning presence and length
    """
    from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
    ) -> float | ScoreWithFeedback:
        expected_answer = str(getattr(gold, "answer", "") or "").strip()
        actual_answer = str(getattr(pred, "answer", "") or "").strip()
        actual_reasoning = str(getattr(pred, "reasoning", "") or "").strip()

        # Answer correctness
        if expected_answer and actual_answer:
            if expected_answer.lower() == actual_answer.lower():
                answer_score = 1.0
                answer_fb = "Answer matches exactly."
            elif expected_answer.lower() in actual_answer.lower():
                answer_score = 0.7
                answer_fb = "Answer is a substring of the prediction."
            else:
                answer_score = 0.0
                answer_fb = (
                    f"Answer mismatch. Expected: '{expected_answer[:120]}'. "
                    f"Got: '{actual_answer[:120]}'."
                )
        elif not actual_answer:
            answer_score = 0.0
            answer_fb = "No answer produced."
        else:
            answer_score = 0.0
            answer_fb = "Unexpected answer format."

        # Reasoning presence
        reasoning_words = len(actual_reasoning.split()) if actual_reasoning else 0
        if reasoning_words >= 10:
            reasoning_score = 1.0
            reasoning_fb = "Reasoning is present and substantive."
        elif reasoning_words > 0:
            reasoning_score = 0.5
            reasoning_fb = "Reasoning is present but very short."
        else:
            reasoning_score = 0.0
            reasoning_fb = "No reasoning produced."

        score = 0.7 * answer_score + 0.3 * reasoning_score
        feedback = (
            f"[answer={answer_score:.1f}] {answer_fb} "
            f"[reasoning={reasoning_score:.1f}] {reasoning_fb}"
        )
        return ScoreWithFeedback(score=score, feedback=feedback)

    return metric


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_LONGCOT_SPEC = ModuleOptimizationSpec(
    module_slug="longcot-reasoner",
    label="LongCoT QA Reasoner",
    program_spec="fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
    artifact_filename="longcot_reasoner.json",
    input_keys=["question"],
    required_dataset_keys=["question", "answer"],
    module_factory=_module_factory,
    row_converter=_row_converter,
    metric_builder=_metric_builder,
    metric_name="longcot_qa_metric",
    description=(
        "Long chain-of-thought question answering module with explicit "
        "reasoning and answer fields."
    ),
)

register_module(_LONGCOT_SPEC)
