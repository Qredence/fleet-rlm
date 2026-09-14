"""Trusted GEPA feedback metrics for host-owned quality campaigns.

The candidate runs in a Daytona sandbox, but scoring stays on the trusted host.
This module only adapts a reviewed scorer to DSPy's ``Prediction(score,
feedback)`` contract; it never executes candidate text or treats an observed
answer as an expectation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import dspy

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SENSITIVE_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "/home/",
    "/users/",
    ".fleet_rlm",
)
_MAX_FEEDBACK_CHARS = 2_000


class TrustedMetricError(ValueError):
    """A scorer result is not safe or complete enough for GEPA."""


@dataclass(frozen=True, slots=True)
class ScoreFeedback:
    """Bounded host-side score and reflection feedback."""

    score: float
    feedback: str

    def __post_init__(self) -> None:
        if not isinstance(self.score, (int, float)) or isinstance(self.score, bool):
            raise TrustedMetricError("metric score must be numeric")
        if not math.isfinite(float(self.score)) or not 0 <= float(self.score) <= 1:
            raise TrustedMetricError("metric score must be finite and between zero and one")
        if not isinstance(self.feedback, str) or not self.feedback.strip():
            raise TrustedMetricError("metric feedback must be non-empty text")
        if len(self.feedback) > _MAX_FEEDBACK_CHARS:
            raise TrustedMetricError("metric feedback exceeds the bounded maximum")
        lowered = self.feedback.lower()
        if any(marker in lowered for marker in _SENSITIVE_MARKERS):
            raise TrustedMetricError("metric feedback contains a forbidden sensitive marker")


TrustedScorer = Callable[..., ScoreFeedback | Mapping[str, Any] | dspy.Prediction]


@dataclass(frozen=True, slots=True)
class TrustedGEPAFeedbackMetric:
    """Adapt one reviewed host scorer to GEPA's feedback metric protocol."""

    scorer: TrustedScorer
    scorer_sha256: str
    failure_score: float = 0.0

    def __post_init__(self) -> None:
        if not callable(self.scorer):
            raise TrustedMetricError("trusted scorer must be callable")
        if not isinstance(self.scorer_sha256, str) or not _SHA256.fullmatch(self.scorer_sha256):
            raise TrustedMetricError("trusted scorer identity must be a SHA-256 digest")
        if not isinstance(self.failure_score, (int, float)) or isinstance(self.failure_score, bool):
            raise TrustedMetricError("failure score must be numeric")
        # A scorer exception is an infrastructure/quality failure, never a
        # valid model result.  Do not allow a caller to turn that failure into
        # a passing (or even nonzero) GEPA score.
        if self.failure_score != 0:
            raise TrustedMetricError("failure score must be exactly zero")

    def __call__(
        self,
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> dspy.Prediction:
        """Return bounded score plus feedback for aggregate or predictor scoring.

        Scorer failures become ``failure_score`` with feedback that exposes only
        the exception type.
        """
        try:
            result = self.scorer(
                gold,
                pred,
                trace=trace,
                pred_name=pred_name,
                pred_trace=pred_trace,
            )
            score_feedback = _coerce_score_feedback(result)
        except Exception as exc:
            score_feedback = ScoreFeedback(
                score=float(self.failure_score),
                feedback=f"trusted_scorer_failure: {type(exc).__name__}",
            )
        return dspy.Prediction(score=score_feedback.score, feedback=score_feedback.feedback)


def expectation_score(
    gold: Any,
    pred: Any,
    *,
    trace: Any = None,
    pred_name: str | None = None,
    pred_trace: Any = None,
    program_trace: Any = None,
) -> ScoreFeedback:
    """Score only explicit machine-checkable expectations from a reviewed record.

    Qualitative ``criteria`` alone are deliberately not treated as a pass.  A
    campaign must supply a separately reviewed judge for those cases instead of
    manufacturing a score from the model's observed answer.
    """
    del trace, pred_name, pred_trace, program_trace
    expectations = _mapping_value(gold, "expectations")
    answer = _prediction_text(pred)
    checks: list[tuple[str, bool]] = []
    expected_response = expectations.get("expected_response")
    if isinstance(expected_response, str) and expected_response.strip():
        checks.append(("expected_response", _normalize(answer) == _normalize(expected_response)))
    marker = expectations.get("marker")
    if isinstance(marker, str) and marker.strip():
        checks.append(("marker", marker in answer))
    for key, expected in expectations.items():
        if key in {"criteria", "expected_response", "marker"}:
            continue
        if isinstance(expected, (str, int, float, bool)):
            checks.append((key, _normalize(str(expected)) in _normalize(answer)))
        elif isinstance(expected, list) and expected and all(isinstance(item, (str, int, float)) for item in expected):
            checks.append((key, all(_normalize(str(item)) in _normalize(answer) for item in expected)))
    if not checks:
        return ScoreFeedback(0.0, "unscorable: reviewed criteria require a trusted judge")
    passed = sum(int(value) for _, value in checks)
    failed = [name for name, value in checks if not value]
    score = passed / len(checks)
    feedback = (
        "all machine-checkable expectations passed" if not failed else f"failed expectations: {', '.join(failed)}"
    )
    return ScoreFeedback(score, feedback)


def scorer_policy_sha256(policy: Mapping[str, Any]) -> str:
    """Hash a canonical non-secret scorer policy for promotion bundle identity."""
    encoded = json.dumps(dict(policy), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _coerce_score_feedback(value: Any) -> ScoreFeedback:
    if isinstance(value, ScoreFeedback):
        return value
    if isinstance(value, dspy.Prediction):
        return ScoreFeedback(score=value.score, feedback=value.feedback)
    if isinstance(value, Mapping) and set(value) == {"score", "feedback"}:
        return ScoreFeedback(score=value["score"], feedback=value["feedback"])
    raise TrustedMetricError("trusted scorer must return ScoreFeedback, Prediction, or score/feedback mapping")


def _mapping_value(value: Any, key: str) -> Mapping[str, Any]:
    nested = value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
    if not isinstance(nested, Mapping):
        raise TrustedMetricError(f"gold.{key} must be a mapping")
    return nested


def _prediction_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    for key in ("answer", "display_text", "output"):
        candidate = value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
        if isinstance(candidate, str):
            return candidate
    raise TrustedMetricError("prediction does not expose a text answer")


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


__all__ = [
    "ScoreFeedback",
    "TrustedGEPAFeedbackMetric",
    "TrustedMetricError",
    "expectation_score",
    "scorer_policy_sha256",
]
