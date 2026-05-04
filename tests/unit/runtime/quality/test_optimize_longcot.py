"""Tests for the LongCoT QA reasoner optimization module."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from fleet_rlm.runtime.agent.signatures import LongCoTQASignature
from fleet_rlm.runtime.quality.module_registry import (
    _reset_registry,
    get_module_spec,
    list_module_slugs,
)

# ── Signature tests ──────────────────────────────────────────────────


def test_longcot_signature_is_dspy_signature() -> None:
    import dspy

    assert issubclass(LongCoTQASignature, dspy.Signature)


def test_longcot_signature_has_question_input() -> None:
    fields = LongCoTQASignature.fields
    assert "question" in fields
    assert fields["question"].json_schema_extra.get("__dspy_field_type") == "input"


def test_longcot_signature_has_reasoning_output() -> None:
    fields = LongCoTQASignature.fields
    assert "reasoning" in fields
    assert fields["reasoning"].json_schema_extra.get("__dspy_field_type") == "output"


def test_longcot_signature_has_answer_output() -> None:
    fields = LongCoTQASignature.fields
    assert "answer" in fields
    assert fields["answer"].json_schema_extra.get("__dspy_field_type") == "output"


# ── Registry / module spec tests ─────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """Reset the registry before each test."""
    _reset_registry()


def test_longcot_reasoner_registered() -> None:
    slugs = list_module_slugs()
    assert "longcot-reasoner" in slugs


def test_longcot_spec_structure() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    assert spec.module_slug == "longcot-reasoner"
    assert spec.label == "LongCoT QA Reasoner"
    assert spec.artifact_filename == "longcot_reasoner.json"
    assert spec.input_keys == ["question"]
    assert spec.required_dataset_keys == ["question", "answer"]
    assert callable(spec.module_factory)
    assert callable(spec.row_converter)
    assert callable(spec.metric_builder)


# ── Row converter tests ──────────────────────────────────────────────


def test_row_converter_basic() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    rows = [
        {"question": "What is 2+2?", "answer": "4"},
        {
            "question": "Capital of France?",
            "answer": "Paris",
            "reasoning": "France is in Europe.",
        },
    ]
    examples = spec.row_converter(rows)
    assert len(examples) == 2
    assert examples[0].question == "What is 2+2?"
    assert examples[0].answer == "4"
    assert examples[1].reasoning == "France is in Europe."


def test_row_converter_with_inputs() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    rows = [{"question": "Q1", "answer": "A1"}]
    examples = spec.row_converter(rows)
    assert len(examples) == 1
    # with_inputs("question") means question should be in the inputs set
    assert "question" in examples[0].inputs()


# ── Metric tests ─────────────────────────────────────────────────────


def _make_gold_pred(
    *,
    gold_answer: str = "",
    gold_reasoning: str = "",
    pred_answer: str = "",
    pred_reasoning: str = "",
) -> tuple[Any, Any]:
    gold = MagicMock()
    gold.answer = gold_answer
    gold.reasoning = gold_reasoning
    pred = MagicMock()
    pred.answer = pred_answer
    pred.reasoning = pred_reasoning
    return gold, pred


STRONG_REASONING = (
    "Step 1: identify the relevant fact. Step 2: because Paris is the capital "
    "of France, the final answer should be Paris. Step 3: verify the "
    "conclusion matches the question."
)
SHALLOW_REASONING = "Because Paris is France's capital."
FILLER_REASONING = (
    "Paris answer Paris answer Paris answer Paris answer Paris answer Paris answer."
)


def _score_prediction(
    metric: Any,
    *,
    gold_answer: str = "Paris",
    pred_answer: str = "Paris",
    pred_reasoning: str = STRONG_REASONING,
) -> Any:
    gold, pred = _make_gold_pred(
        gold_answer=gold_answer,
        pred_answer=pred_answer,
        pred_reasoning=pred_reasoning,
    )
    return metric(gold, pred)


def test_metric_returns_bounded_scores_with_feedback_for_representative_cases() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()

    representative_cases = [
        ("Strong", "Paris", STRONG_REASONING, "Paris"),
        ("Partial", "The final answer is Paris.", STRONG_REASONING, "Paris"),
        ("Weak", "Lyon France", STRONG_REASONING, "Paris France"),
        ("Failed", "", "", "Paris"),
    ]

    for expected_tier, pred_answer, pred_reasoning, gold_answer in representative_cases:
        result = _score_prediction(
            metric,
            gold_answer=gold_answer,
            pred_answer=pred_answer,
            pred_reasoning=pred_reasoning,
        )
        assert 0.0 <= result.score <= 1.0
        assert isinstance(result.feedback, str)
        assert result.feedback
        assert f"Tier: {expected_tier}" in result.feedback


def test_metric_continuous_answer_tiers_with_substantive_reasoning() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()

    exact = _score_prediction(
        metric, pred_answer="Paris", pred_reasoning=STRONG_REASONING
    )
    partial = _score_prediction(
        metric,
        pred_answer="The final answer is Paris.",
        pred_reasoning=STRONG_REASONING,
    )
    related_wrong = _score_prediction(
        metric,
        gold_answer="Paris France",
        pred_answer="Lyon France",
        pred_reasoning=STRONG_REASONING,
    )
    missing = _score_prediction(metric, pred_answer="", pred_reasoning=STRONG_REASONING)

    assert exact.score > partial.score > related_wrong.score > missing.score
    assert 0.0 < partial.score < 1.0
    assert 0.0 < related_wrong.score < 1.0


def test_metric_reasoning_changes_score_when_answer_is_fixed() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()

    strong = _score_prediction(
        metric, pred_answer="Paris", pred_reasoning=STRONG_REASONING
    )
    shallow = _score_prediction(
        metric, pred_answer="Paris", pred_reasoning=SHALLOW_REASONING
    )
    none = _score_prediction(metric, pred_answer="Paris", pred_reasoning="")

    assert strong.score > shallow.score > none.score
    assert "Reasoning:" in strong.feedback
    assert "Reasoning:" in shallow.feedback
    assert "Reasoning:" in none.feedback


def test_metric_answer_correctness_dominates_but_reasoning_still_helps() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()

    correct_no_reasoning = _score_prediction(
        metric,
        pred_answer="Paris",
        pred_reasoning="",
    )
    wrong_strong_reasoning = _score_prediction(
        metric,
        pred_answer="London",
        pred_reasoning=STRONG_REASONING,
    )
    wrong_no_reasoning = _score_prediction(
        metric,
        pred_answer="London",
        pred_reasoning="",
    )

    assert correct_no_reasoning.score > wrong_strong_reasoning.score
    assert wrong_strong_reasoning.score > wrong_no_reasoning.score


def test_metric_structured_reasoning_beats_matched_length_filler() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()

    structured = _score_prediction(
        metric,
        pred_answer="Paris",
        pred_reasoning=STRONG_REASONING,
    )
    filler = _score_prediction(
        metric,
        pred_answer="Paris",
        pred_reasoning=FILLER_REASONING,
    )

    assert structured.score > filler.score
    assert (
        "step" in structured.feedback.lower()
        or "structured" in structured.feedback.lower()
    )
    assert (
        "filler" in filler.feedback.lower() or "repetitive" in filler.feedback.lower()
    )


def test_metric_formatting_only_answer_variants_beat_materially_wrong_answers() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()

    normalized = _score_prediction(
        metric,
        pred_answer="  paris  ",
        pred_reasoning=STRONG_REASONING,
    )
    wrapped = _score_prediction(
        metric,
        pred_answer="The final answer is Paris.",
        pred_reasoning=STRONG_REASONING,
    )
    wrong = _score_prediction(
        metric,
        pred_answer="London",
        pred_reasoning=STRONG_REASONING,
    )

    assert normalized.score == pytest.approx(1.0)
    assert wrapped.score > wrong.score
    assert normalized.score > wrong.score


def test_metric_sparse_predictions_are_safe_and_deterministic() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()

    sparse_cases = [
        {"pred_answer": "", "pred_reasoning": STRONG_REASONING},
        {"pred_answer": "Paris", "pred_reasoning": ""},
        {"pred_answer": "", "pred_reasoning": ""},
    ]

    for sparse_case in sparse_cases:
        first = _score_prediction(metric, **sparse_case)
        second = _score_prediction(metric, **sparse_case)
        assert 0.0 <= first.score <= 1.0
        assert isinstance(first.feedback, str)
        assert first.feedback
        assert first.score == second.score
        assert first.feedback == second.feedback


def test_metric_canonical_weighting_fixtures() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()

    exact_and_strong = _score_prediction(
        metric,
        pred_answer="Paris",
        pred_reasoning=STRONG_REASONING,
    )
    exact_and_none = _score_prediction(
        metric,
        pred_answer="Paris",
        pred_reasoning="",
    )
    wrong_and_strong = _score_prediction(
        metric,
        pred_answer="London",
        pred_reasoning=STRONG_REASONING,
    )
    missing_and_none = _score_prediction(
        metric,
        pred_answer="",
        pred_reasoning="",
    )

    assert exact_and_strong.score == pytest.approx(1.0)
    assert exact_and_none.score == pytest.approx(0.6)
    assert wrong_and_strong.score == pytest.approx(0.4)
    assert missing_and_none.score == pytest.approx(0.0)


def test_metric_partial_answer_bands() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()

    wrapper_overlap = _score_prediction(
        metric,
        pred_answer="The final answer is Paris.",
        pred_reasoning=STRONG_REASONING,
    )
    related_wrong = _score_prediction(
        metric,
        gold_answer="Paris France",
        pred_answer="Lyon France",
        pred_reasoning=STRONG_REASONING,
    )

    assert 0.75 <= wrapper_overlap.score <= 0.85
    assert 0.50 <= related_wrong.score <= 0.60
    assert wrapper_overlap.score > related_wrong.score


def test_metric_feedback_tiers_are_actionable_and_separate_answer_and_reasoning() -> (
    None
):
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()

    strong = _score_prediction(
        metric, pred_answer="Paris", pred_reasoning=STRONG_REASONING
    )
    partial = _score_prediction(
        metric,
        pred_answer="The final answer is Paris.",
        pred_reasoning=STRONG_REASONING,
    )
    weak = _score_prediction(
        metric,
        gold_answer="Paris France",
        pred_answer="Lyon France",
        pred_reasoning=STRONG_REASONING,
    )
    failed = _score_prediction(
        metric,
        gold_answer="Paris France",
        pred_answer="Lyon France",
        pred_reasoning=FILLER_REASONING,
    )

    tier_headers = {
        strong.feedback.split(".")[0],
        partial.feedback.split(".")[0],
        weak.feedback.split(".")[0],
        failed.feedback.split(".")[0],
    }
    assert tier_headers == {
        "Tier: Strong",
        "Tier: Partial",
        "Tier: Weak",
        "Tier: Failed",
    }

    assert "Answer:" in failed.feedback
    assert "Reasoning:" in failed.feedback
    assert "replace" in failed.feedback.lower() or "state" in failed.feedback.lower()
    assert "step" in failed.feedback.lower() or "reasoning" in failed.feedback.lower()


# ── Module factory tests ─────────────────────────────────────────────


def test_module_factory_returns_predict() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    module = spec.module_factory()
    import dspy

    assert isinstance(module, dspy.Module)
