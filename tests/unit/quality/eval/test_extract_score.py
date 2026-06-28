"""Unit tests for ``_extract_score`` last-number and [0,1]-preference behavior.

VAL-CORR-001: ``_extract_score`` must take the LAST numeric match in the LLM
response (not the first) and prefer numbers already in the [0.0, 1.0] range
when multiple matches exist. The result is always clamped to [0.0, 1.0].
"""

from __future__ import annotations

from fleet_rlm.quality.eval.judges import _extract_score


class TestExtractScoreLastNumber:
    """VAL-CORR-001: take the last number, preferring [0,1] range."""

    def test_prefers_in_range_number_over_earlier_out_of_range(self) -> None:
        """'On a scale of 100, I rate this 0.85' must extract 0.85, not 1.0."""
        assert _extract_score("On a scale of 100, I rate this 0.85") == 0.85

    def test_prefers_last_in_range_number(self) -> None:
        """'Score: 0.92. The maximum is 100.' must extract 0.92."""
        assert _extract_score("Score: 0.92. The maximum is 100.") == 0.92

    def test_single_in_range_number(self) -> None:
        """'I give it a 0.7' must extract 0.7."""
        assert _extract_score("I give it a 0.7") == 0.7

    def test_pure_float_in_range(self) -> None:
        """'0.85' (pure float) must extract 0.85."""
        assert _extract_score("0.85") == 0.85

    def test_json_score_in_range(self) -> None:
        """'{"score": 0.9}' (JSON) must extract 0.9."""
        assert _extract_score('{"score": 0.9}') == 0.9

    def test_no_numbers_returns_zero(self) -> None:
        """'no numbers here' must extract 0.0."""
        assert _extract_score("no numbers here") == 0.0

    def test_date_prefix_does_not_shadow_score(self) -> None:
        """'2024-01-15: score 0.7' must extract 0.7, not 2024 (clamped to 1.0)."""
        assert _extract_score("2024-01-15: score 0.7") == 0.7

    def test_last_in_range_number_when_multiple_in_range(self) -> None:
        """When multiple in-range numbers exist, the last one wins."""
        # 0.3 and 0.6 are both in range; expect 0.6 (the last in-range match).
        assert _extract_score("First draft 0.3, revised to 0.6") == 0.6

    def test_no_in_range_numbers_takes_last_and_clamps(self) -> None:
        """When no number is in [0,1], take the last number and clamp it."""
        # Only 50 and 100 appear; last is 100, clamped to 1.0.
        assert _extract_score("rated 50 out of 100") == 1.0

    def test_only_out_of_range_single_number_clamps(self) -> None:
        """A single out-of-range number is clamped."""
        assert _extract_score("100") == 1.0
        assert _extract_score("-5") == 0.0

    def test_empty_and_non_string_return_zero(self) -> None:
        """Empty / non-string inputs return 0.0."""
        assert _extract_score("") == 0.0
        assert _extract_score("   ") == 0.0
        assert _extract_score(None) == 0.0  # type: ignore[arg-type]
        assert _extract_score(123) == 0.0  # type: ignore[arg-type]

    def test_clamping_still_applies_to_in_range_fallback(self) -> None:
        """In-range numbers are returned clamped (no float drift above 1.0)."""
        # 0.999 is in range and returned as-is (clamped is a no-op).
        assert _extract_score("score 0.999") == 0.999
