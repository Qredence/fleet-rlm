"""GEPA optimization entrypoint for the LongCoT QA reasoner module.

Registers a :class:`~fleet_rlm.runtime.quality.module_registry.ModuleOptimizationSpec`
for ``longcot-reasoner`` so that the offline CLI and API can compile it with GEPA.

The module uses :class:`~fleet_rlm.runtime.agent.signatures.LongCoTQASignature`
(question → reasoning + answer) via ``dspy.Predict``.
"""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
import re
from typing import Any

from fleet_rlm.runtime.quality.module_registry import (
    ModuleOptimizationSpec,
    register_module,
)
from fleet_rlm.runtime.quality.scoring_helpers import set_overlap_score


_ANSWER_WEIGHT = 0.6
_REASONING_WEIGHT = 0.4
_PARTIAL_ANSWER_SCORE = 0.65
_RELATED_WRONG_ANSWER_SCORE = 0.25

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.IGNORECASE)
_STEP_MARKER_RE = re.compile(
    r"\b(step\s*\d+|first|second|third|next|then|finally)\b",
    re.IGNORECASE,
)
_CONNECTOR_RE = re.compile(
    r"\b(because|therefore|thus|hence|since|so)\b",
    re.IGNORECASE,
)
_VERIFY_RE = re.compile(
    r"\b(check|verify|verified|confirm|double-check|sanity check)\b",
    re.IGNORECASE,
)


def _normalize_text(value: Any) -> str:
    """Normalize sparse text inputs into a stripped single-line string."""
    return re.sub(r"\s+", " ", str(value or "").strip())


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase word-like units."""
    return _WORD_RE.findall(text.lower())


def _clip_text(text: str, limit: int = 80) -> str:
    """Return a clipped preview safe for inline feedback."""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _score_answer(expected: str, actual: str) -> tuple[float, str]:
    """Score final-answer quality with exact, partial, related, and failed tiers."""
    expected_normalized = _normalize_text(expected)
    actual_normalized = _normalize_text(actual)

    if not expected_normalized:
        return 0.0, "No reference answer was available for comparison."
    if not actual_normalized:
        return 0.0, "No final answer was produced. State the final answer explicitly."

    expected_lower = expected_normalized.lower()
    actual_lower = actual_normalized.lower()
    if expected_lower == actual_lower:
        return 1.0, "Answer exactly matches the reference."

    expected_tokens = set(_tokenize(expected_normalized))
    actual_tokens = set(_tokenize(actual_normalized))
    overlap = set_overlap_score(expected_tokens, actual_tokens)
    similarity = SequenceMatcher(None, expected_lower, actual_lower).ratio()
    contains_reference = (
        expected_lower in actual_lower or actual_lower in expected_lower
    )

    if contains_reference or overlap >= 0.75:
        return (
            _PARTIAL_ANSWER_SCORE,
            "Answer captures the reference but adds wrapper text or loses precision. "
            "Return the concise final answer directly.",
        )
    if overlap > 0.0 or similarity >= 0.45:
        return (
            _RELATED_WRONG_ANSWER_SCORE,
            "Answer is related but still wrong. Replace it with "
            f"'{_clip_text(expected_normalized)}'.",
        )
    return (
        0.0,
        "Answer does not match the reference. Replace it with "
        f"'{_clip_text(expected_normalized)}'.",
    )


def _looks_like_filler(tokens: list[str]) -> bool:
    """Heuristically flag repetitive long-form filler reasoning."""
    if len(tokens) < 6:
        return False
    counts = Counter(tokens)
    unique_ratio = len(counts) / len(tokens)
    dominant_ratio = max(counts.values()) / len(tokens)
    return unique_ratio < 0.55 or dominant_ratio > 0.25


def _score_reasoning(reasoning: str) -> tuple[float, str]:
    """Score reasoning quality with structure-versus-filler separation."""
    normalized = _normalize_text(reasoning)
    if not normalized:
        return (
            0.0,
            "No reasoning was provided. Add stepwise justification and a quick verification.",
        )

    tokens = _tokenize(normalized)
    token_count = len(tokens)
    filler_like = _looks_like_filler(tokens)
    has_step_markers = bool(_STEP_MARKER_RE.search(normalized))
    has_connectors = bool(_CONNECTOR_RE.search(normalized))
    has_verification = bool(_VERIFY_RE.search(normalized))

    score = 0.0
    if token_count >= 12:
        score += 0.2
    elif token_count >= 5:
        score += 0.1
    else:
        score += 0.05
    if has_step_markers:
        score += 0.2
    if has_connectors:
        score += 0.2
    if has_verification:
        score += 0.2
    if not filler_like:
        score += 0.2

    if score >= 0.85:
        feedback = "Reasoning is structured, stepwise, and checks the conclusion."
    elif filler_like:
        feedback = (
            "Reasoning is verbose but repetitive or filler-heavy. Replace filler "
            "with concise steps, a causal connector, and a verification check."
        )
    elif score >= 0.45:
        feedback = (
            "Reasoning has some support but is still shallow. Add explicit steps "
            "and a verification check."
        )
    else:
        feedback = (
            "Reasoning is too thin to justify the answer. Add stepwise support "
            "with a connector and a verification check."
        )

    return min(1.0, score), feedback


def _score_tier(score: float) -> tuple[str, str]:
    """Return a stable feedback tier label and next-step coaching text."""
    if score >= 0.9:
        return (
            "Strong",
            "Keep the concise answer and preserve the structured verification.",
        )
    if score >= 0.75:
        return (
            "Partial",
            "Tighten the answer wording and strengthen the weakest dimension noted below.",
        )
    if score >= 0.45:
        return (
            "Weak",
            "Fix the answer first, then make the reasoning more stepwise and specific.",
        )
    return (
        "Failed",
        "Provide the correct final answer and a structured justification before adding extra detail.",
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
            question_id=str(row.get("question_id", "")),
            domain=str(row.get("domain", "")),
            difficulty=str(row.get("difficulty", "")),
        ).with_inputs("question")
        examples.append(ex)
    return examples


# ---------------------------------------------------------------------------
# Metric builder
# ---------------------------------------------------------------------------


def _metric_builder() -> Any:
    """Build a GEPA-compatible feedback metric for LongCoT QA.

    Weights:
        - 60 % answer correctness
        - 40 % reasoning quality
    """
    from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> float | ScoreWithFeedback:
        expected_answer = _normalize_text(getattr(gold, "answer", ""))
        actual_answer = _normalize_text(getattr(pred, "answer", ""))
        actual_reasoning = _normalize_text(getattr(pred, "reasoning", ""))

        answer_score, answer_feedback = _score_answer(expected_answer, actual_answer)
        reasoning_score, reasoning_feedback = _score_reasoning(actual_reasoning)
        score = round(
            (_ANSWER_WEIGHT * answer_score) + (_REASONING_WEIGHT * reasoning_score),
            4,
        )
        tier, next_step = _score_tier(score)
        feedback = (
            f"Tier: {tier}. "
            f"Answer: {answer_feedback} "
            f"Reasoning: {reasoning_feedback} "
            f"Next: {next_step} "
            f"[answer={answer_score:.2f} reasoning={reasoning_score:.2f} total={score:.2f}]"
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
