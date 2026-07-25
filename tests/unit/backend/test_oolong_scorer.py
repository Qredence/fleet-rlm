"""Unit tests for the official OOLONG-synth scoring rubric.

These tests verify the scorer's behavior against the deterministic rules
from the Oolong paper (arXiv 2511.02817) as implemented in
``primeintellect/verifiers`` oolong-synth-v1.
"""

from __future__ import annotations

import pytest

from scripts.benchmarks.oolong_scorer import (
    aggregate_scores,
    attempt_answer_parse,
    normalize_answer_type,
    parse_gold,
    score,
)


class TestAttemptAnswerParse:
    def test_plain_string_returned_as_is(self) -> None:
        assert attempt_answer_parse("42") == "42"

    def test_colon_prefixed_extracts_value(self) -> None:
        assert attempt_answer_parse("Answer: 42") == "42"

    def test_strips_markdown_brackets(self) -> None:
        assert attempt_answer_parse("Result: [**42**]") == "42"

    def test_preserves_frequency_phrases(self) -> None:
        assert attempt_answer_parse("more common") == "more common"
        assert attempt_answer_parse("Answer: less common") == "less common"
        assert attempt_answer_parse("same frequency") == "same frequency"


class TestParseGold:
    def test_numeric_list_literal(self) -> None:
        assert parse_gold("[42]") == 42

    def test_string_list_literal(self) -> None:
        assert parse_gold("['hello']") == "hello"

    def test_date_wrapped(self) -> None:
        from datetime import datetime

        result = parse_gold("[datetime.date(2025, 3, 15)]")
        assert result == datetime(2025, 3, 15)

    def test_negative_number(self) -> None:
        assert parse_gold("[-7]") == -7


class TestScore:
    def test_exact_numeric_match(self) -> None:
        assert score("[42]", "", "42") == 1.0

    def test_exact_string_match(self) -> None:
        assert score("['cat']", "", "cat") == 1.0

    def test_exact_date_match(self) -> None:
        assert score("[datetime.date(2025, 3, 15)]", "ANSWER_TYPE.DATE", "2025-03-15") == 1.0

    def test_frequency_phrase_match(self) -> None:
        assert score("['more common']", "", "more common") == 1.0

    def test_numeric_partial_credit_close(self) -> None:
        result = score("[100]", "ANSWER_TYPE.NUMERIC", "99")
        assert result == pytest.approx(0.75, abs=1e-6)

    def test_numeric_partial_credit_far(self) -> None:
        result = score("[100]", "ANSWER_TYPE.NUMERIC", "90")
        assert result == pytest.approx(0.75**10, abs=1e-6)

    def test_numeric_no_partial_credit_when_type_empty(self) -> None:
        assert score("[100]", "", "99") == 0.0

    def test_date_wrong_value(self) -> None:
        assert score("[datetime.date(2025, 3, 15)]", "ANSWER_TYPE.DATE", "2025-03-16") == 0.0

    def test_date_unparseable(self) -> None:
        assert score("[datetime.date(2025, 3, 15)]", "ANSWER_TYPE.DATE", "not-a-date") == 0.0

    def test_empty_output(self) -> None:
        assert score("[42]", "ANSWER_TYPE.NUMERIC", "") == 0.0

    def test_wrong_answer(self) -> None:
        assert score("[42]", "", "wrong") == 0.0

    def test_numeric_parse_failure(self) -> None:
        assert score("[42]", "ANSWER_TYPE.NUMERIC", "abc") == 0.0

    def test_numeric_with_answer_prefix(self) -> None:
        assert score("[42]", "ANSWER_TYPE.NUMERIC", "Answer: 42") == 1.0

    def test_numeric_partial_credit_with_prefix(self) -> None:
        result = score("[42]", "ANSWER_TYPE.NUMERIC", "Answer: 41")
        assert result == pytest.approx(0.75, abs=1e-6)


class TestNormalizeAnswerType:
    def test_numeric_preserved(self) -> None:
        assert normalize_answer_type("ANSWER_TYPE.NUMERIC") == "ANSWER_TYPE.NUMERIC"

    def test_date_preserved(self) -> None:
        assert normalize_answer_type("ANSWER_TYPE.DATE") == "ANSWER_TYPE.DATE"

    def test_other_normalized_to_empty(self) -> None:
        assert normalize_answer_type("ANSWER_TYPE.STRING") == ""
        assert normalize_answer_type("") == ""
        assert normalize_answer_type(None) == ""


class TestAggregateScores:
    def test_empty_results(self) -> None:
        result = aggregate_scores([])
        assert result["tasks_total"] == 0
        assert result["avg_score"] == 0.0

    def test_single_perfect_score(self) -> None:
        results = [{"score": 1.0, "answer_type": "", "context_len": 1024, "question_type": "counting"}]
        result = aggregate_scores(results)
        assert result["tasks_total"] == 1
        assert result["avg_score"] == 1.0
        assert result["perfect_scores"] == 1
        assert result["zero_scores"] == 0

    def test_mixed_scores(self) -> None:
        results = [
            {"score": 1.0, "answer_type": "", "context_len": 1024, "question_type": "counting"},
            {"score": 0.75, "answer_type": "ANSWER_TYPE.NUMERIC", "context_len": 1024, "question_type": "counting"},
            {"score": 0.0, "answer_type": "", "context_len": 2048, "question_type": "temporal"},
        ]
        result = aggregate_scores(results)
        assert result["tasks_total"] == 3
        assert result["avg_score"] == pytest.approx((1.0 + 0.75 + 0.0) / 3, abs=1e-6)
        assert result["perfect_scores"] == 1
        assert result["partial_scores"] == 1
        assert result["zero_scores"] == 1

    def test_bucketing_by_answer_type(self) -> None:
        results = [
            {"score": 1.0, "answer_type": "NUMERIC", "context_len": 1024, "question_type": "q"},
            {"score": 0.5, "answer_type": "NUMERIC", "context_len": 1024, "question_type": "q"},
            {"score": 0.0, "answer_type": "DATE", "context_len": 2048, "question_type": "q"},
        ]
        result = aggregate_scores(results)
        assert result["by_answer_type"]["NUMERIC"] == pytest.approx(0.75, abs=1e-6)
        assert result["by_answer_type"]["DATE"] == 0.0

    def test_bucketing_by_context_len(self) -> None:
        results = [
            {"score": 1.0, "answer_type": "", "context_len": 1024, "question_type": "q"},
            {"score": 0.5, "answer_type": "", "context_len": 1024, "question_type": "q"},
            {"score": 0.0, "answer_type": "", "context_len": 8192, "question_type": "q"},
        ]
        result = aggregate_scores(results)
        assert "1024" in result["by_context_len"]
        assert "8192" in result["by_context_len"]
