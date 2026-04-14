"""Reusable scoring primitives for offline GEPA optimization metrics.

Each per-module metric function may optionally compose these primitives for
additive sub-scores.  Module metrics remain handwritten functions — the helpers
reduce duplication without forcing a one-size-fits-all abstraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def set_overlap_score(expected: set[Any], actual: set[Any]) -> float:
    """Jaccard-style overlap: ``|intersection| / |expected|``.

    Returns 1.0 when *expected* is empty (vacuously correct).
    """
    if not expected:
        return 1.0
    return len(expected & actual) / len(expected)


def text_presence_score(text: str | None) -> float:
    """Return 1.0 if *text* is a non-empty string, else 0.0."""
    return 1.0 if text and str(text).strip() else 0.0


def boundedness_score(actual_count: int, budget: int) -> float:
    """Return 1.0 if *actual_count* ≤ *budget*, else 0.0.

    When *budget* ≤ 0 the check is skipped (returns 1.0).
    """
    if budget <= 0:
        return 1.0
    return 1.0 if actual_count <= budget else 0.0


def action_match_score(expected: str, actual: str) -> float:
    """Return 1.0 if *expected* and *actual* match (case-insensitive), else 0.0."""
    return 1.0 if str(expected).strip().lower() == str(actual).strip().lower() else 0.0


@dataclass
class ScoreFeedbackBuilder:
    """Optional accumulator for weighted (score, feedback) pairs.

    Usage::

        builder = ScoreFeedbackBuilder()
        builder.add(0.7, action_match_score(expected, actual), "Action matches." if ... else "Mismatch.")
        builder.add(0.2, set_overlap_score(exp, act), "Overlap good." if ... else "Overlap low.")
        result = builder.build()  # ScoreWithFeedback(score=..., feedback="...")
    """

    _entries: list[tuple[float, float, str]] = field(default_factory=list)

    def add(self, weight: float, score: float, feedback: str) -> "ScoreFeedbackBuilder":
        """Add a weighted score component with feedback text."""
        self._entries.append((weight, score, feedback))
        return self

    def build(self) -> Any:
        """Produce a ``ScoreWithFeedback`` from accumulated entries.

        Lazily imports ``ScoreWithFeedback`` so the module is usable
        without a full DSPy installation at import time.
        """
        from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

        total = sum(w * s for w, s, _ in self._entries)
        feedback = " ".join(fb for _, _, fb in self._entries)
        return ScoreWithFeedback(
            score=max(0.0, min(1.0, total)),
            feedback=feedback,
        )

    @property
    def raw_score(self) -> float:
        """Compute the weighted score without constructing ``ScoreWithFeedback``."""
        return max(0.0, min(1.0, sum(w * s for w, s, _ in self._entries)))
