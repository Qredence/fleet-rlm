"""Workspace-oriented metrics with text feedback for GEPA optimization.

These metrics return ``(score, feedback)`` tuples so that
:mod:`.gepa_optimization` can feed rich textual explanations back into
the GEPA reflective prompt evolution loop.
"""

from __future__ import annotations

from typing import Any


__all__ = [
    "workspace_feedback_metric",
    "workspace_score_metric",
    "exact_match_feedback_metric",
    "completeness_feedback_metric",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_field(obj: Any, key: str) -> str:
    """Extract a string field from a Prediction, Example, or dict."""
    if isinstance(obj, dict):
        return str(obj.get(key, "") or "").strip()
    return str(getattr(obj, key, "") or "").strip()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def exact_match_feedback_metric(
    gold: Any,
    pred: Any,
    *,
    output_key: str = "assistant_response",
    trace: Any = None,
) -> tuple[float, str]:
    """Exact-match metric that returns text feedback explaining the result.

    Returns ``(1.0, "...")`` on match, ``(0.0, "...")`` on mismatch.
    """
    _ = trace
    expected = _get_field(gold, output_key)
    actual = _get_field(pred, output_key)

    if not expected:
        return 0.0, "No expected response provided in the gold example."

    if expected == actual:
        return 1.0, "Exact match with expected response."

    # Provide actionable feedback for GEPA
    if actual and expected.lower() == actual.lower():
        return 0.8, (
            f"Case mismatch: expected '{expected[:120]}' but got '{actual[:120]}'. "
            "The content is correct but casing differs."
        )

    if actual and expected in actual:
        return 0.6, (
            f"Expected response is a substring of the prediction. "
            f"Expected '{expected[:80]}' but got a longer response ({len(actual)} chars). "
            "Try to be more concise."
        )

    if not actual:
        return 0.0, (
            f"Empty response. Expected: '{expected[:200]}'. "
            "The model produced no output for this task."
        )

    return 0.0, (
        f"Mismatch. Expected: '{expected[:120]}'. Got: '{actual[:120]}'. "
        "The response does not match the expected output."
    )


def completeness_feedback_metric(
    gold: Any,
    pred: Any,
    *,
    output_key: str = "assistant_response",
    trace: Any = None,
) -> tuple[float, str]:
    """Score based on whether the prediction addresses key terms from the gold.

    Useful when exact match is too strict but you want to reward coverage
    of important concepts from the expected response.
    """
    _ = trace
    expected = _get_field(gold, output_key)
    actual = _get_field(pred, output_key)

    if not expected:
        return 0.0, "No expected response provided."
    if not actual:
        return 0.0, f"Empty response. Expected coverage of: '{expected[:200]}'."

    expected_tokens = set(expected.lower().split())
    actual_tokens = set(actual.lower().split())

    # Remove very short tokens (articles, prepositions) for meaningful overlap
    meaningful_expected = {t for t in expected_tokens if len(t) > 3}
    if not meaningful_expected:
        meaningful_expected = expected_tokens

    if not meaningful_expected:
        return 0.5, "Expected response has no meaningful tokens to compare."

    overlap = meaningful_expected & actual_tokens
    coverage = len(overlap) / len(meaningful_expected)

    missing = meaningful_expected - actual_tokens
    missing_sample = sorted(missing)[:8]

    if coverage >= 0.9:
        return 1.0, f"Excellent coverage ({coverage:.0%}) of expected concepts."
    if coverage >= 0.6:
        return coverage, (
            f"Good coverage ({coverage:.0%}) but missing: {', '.join(missing_sample)}."
        )
    return coverage, (
        f"Low coverage ({coverage:.0%}). Missing key terms: {', '.join(missing_sample)}. "
        f"The response should address these concepts from the expected answer."
    )


def workspace_feedback_metric(
    gold: Any,
    pred: Any,
    *,
    trace: Any = None,
    output_key: str = "assistant_response",
) -> tuple[float, str]:
    """Combined workspace metric: weighted blend of exact match and completeness.

    This is the default metric used by :func:`.gepa_optimization.build_gepa_feedback_metric`.
    It returns ``(score, feedback)`` where feedback is a human-readable
    explanation suitable for GEPA's reflective prompt evolution.

    Weighting: 60% exact-match, 40% completeness coverage.
    """
    em_score, em_feedback = exact_match_feedback_metric(
        gold, pred, output_key=output_key, trace=trace
    )
    comp_score, comp_feedback = completeness_feedback_metric(
        gold, pred, output_key=output_key, trace=trace
    )

    score = 0.6 * em_score + 0.4 * comp_score
    feedback = f"[exact_match={em_score:.2f}] {em_feedback} [completeness={comp_score:.2f}] {comp_feedback}"
    return score, feedback


def workspace_score_metric(
    gold: Any,
    pred: Any,
    *,
    trace: Any = None,
    output_key: str = "assistant_response",
) -> float:
    """Numeric-only companion to :func:`workspace_feedback_metric`.

    This is the safe default for :class:`dspy.Evaluate`, which aggregates
    numeric scores but does not accept ``(score, feedback)`` tuples.
    """
    score, _feedback = workspace_feedback_metric(
        gold,
        pred,
        trace=trace,
        output_key=output_key,
    )
    return float(score)
