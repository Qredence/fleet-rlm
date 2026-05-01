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


def test_metric_exact_match() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()
    gold, pred = _make_gold_pred(
        gold_answer="Paris",
        pred_answer="Paris",
        pred_reasoning="France is a country in Europe and its capital is Paris.",
    )
    result = metric(gold, pred)
    assert result.score == 1.0


def test_metric_case_insensitive_match() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()
    gold, pred = _make_gold_pred(
        gold_answer="Paris",
        pred_answer="paris",
        pred_reasoning="France is a country in Europe and its capital is Paris.",
    )
    result = metric(gold, pred)
    assert result.score == 1.0


def test_metric_substring_answer() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()
    gold, pred = _make_gold_pred(
        gold_answer="Paris",
        pred_answer="The answer is Paris.",
        pred_reasoning="Some reasoning here that is long enough.",
    )
    result = metric(gold, pred)
    # reasoning has 7 words -> score 0.5; answer substring -> score 0.7
    # 0.7 * 0.7 + 0.3 * 0.5 = 0.49 + 0.15 = 0.64
    assert result.score == pytest.approx(0.64, abs=0.01)


def test_metric_missing_answer() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()
    gold, pred = _make_gold_pred(
        gold_answer="Paris",
        pred_answer="",
        pred_reasoning="Some reasoning here that is long enough.",
    )
    result = metric(gold, pred)
    # reasoning has 7 words -> score 0.5; missing answer -> score 0.0
    # 0.7 * 0.0 + 0.3 * 0.5 = 0.15
    assert result.score == pytest.approx(0.15, abs=0.01)


def test_metric_missing_reasoning() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()
    gold, pred = _make_gold_pred(
        gold_answer="Paris",
        pred_answer="Paris",
        pred_reasoning="",
    )
    result = metric(gold, pred)
    # 0.7 * 1.0 + 0.3 * 0.0 = 0.7
    assert result.score == pytest.approx(0.7, abs=0.01)


def test_metric_short_reasoning() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()
    gold, pred = _make_gold_pred(
        gold_answer="Paris",
        pred_answer="Paris",
        pred_reasoning="Short.",
    )
    result = metric(gold, pred)
    # 0.7 * 1.0 + 0.3 * 0.5 = 0.85
    assert result.score == pytest.approx(0.85, abs=0.01)


def test_metric_returns_feedback() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    metric = spec.metric_builder()
    gold, pred = _make_gold_pred(
        gold_answer="Paris",
        pred_answer="London",
        pred_reasoning="",
    )
    result = metric(gold, pred)
    assert hasattr(result, "feedback")
    assert isinstance(result.feedback, str)
    assert (
        "mismatch" in result.feedback.lower()
        or "no reasoning" in result.feedback.lower()
    )


# ── Module factory tests ─────────────────────────────────────────────


def test_module_factory_returns_predict() -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None
    module = spec.module_factory()
    import dspy

    assert isinstance(module, dspy.Module)
