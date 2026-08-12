"""Additional MLflow GenAI scorers for Fleet evaluation.

Deterministic custom scorers complement the LLM judges in ``judges.py`` and
can be combined with MLflow built-in scorers. Custom scorers run in-process
with no extra provider calls; built-in LLM scorers require a configured judge
model URI. Scorers accept the standard subset of ``inputs`` / ``outputs`` /
``expectations`` / ``trace`` documented by ``mlflow.genai.scorers``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_GUIDELINES = "The response must be concise, stay within the requested scope, and avoid unsupported claims."

CUSTOM_SCORER_NAMES: tuple[str, ...] = ("response_present", "tool_evidence_used")
BUILTIN_SCORER_NAMES: tuple[str, ...] = ("guidelines", "retrieval_groundedness")
SCORER_NAMES: tuple[str, ...] = CUSTOM_SCORER_NAMES + BUILTIN_SCORER_NAMES

RESPONSE_PRESENT_DESCRIPTION = "Whether the response is a non-empty answer."
TOOL_EVIDENCE_DESCRIPTION = (
    "Whether the trace performed tool calls whose output text covers every required_evidence item."
)


def response_present_impl(*, outputs: Any = None) -> bool:
    """Return whether the response is a non-empty answer string."""
    return bool(str(outputs or "").strip())


def _span_text(span: Any) -> str:
    """Collect bounded text from a span's outputs and attributes for matching."""
    parts: list[str] = []
    for attribute in ("outputs", "attributes"):
        value = getattr(span, attribute, None)
        if isinstance(value, Mapping) or value is not None:
            parts.append(str(value))
    return " ".join(parts)


def tool_evidence_used_impl(*, trace: Any = None, expectations: Any = None) -> bool:
    """
    Return whether the trace's tool spans covered the required evidence.

    Parameters:
        trace (Any): MLflow Trace for the evaluated row, or ``None`` when the
            evaluation dataset carries no trace column.
        expectations (Any): Expectations mapping; ``required_evidence`` must be
            a list of evidence identifiers when present.

    Returns:
        bool: `True` only when tool spans exist and every required evidence
            identifier appears in their output text. `False` when the trace is
            absent or evidence cannot be confirmed.
    """
    expectations = expectations or {}
    required = expectations.get("required_evidence")
    if not isinstance(required, list) or not required:
        return False
    data = getattr(trace, "data", None)
    spans = list(getattr(data, "spans", None) or []) if data is not None else []
    tool_text = " ".join(
        _span_text(span) for span in spans if str(getattr(span, "span_type", "") or "").upper() == "TOOL"
    ).lower()
    if not tool_text:
        return False
    return all(str(item).strip().lower() in tool_text for item in required if str(item).strip())


def build_scorer(name: str, *, judge_model: str | None = None, guidelines: str | None = None) -> Any:
    """
    Build one Fleet or MLflow built-in scorer by name.

    Parameters:
        name (str): Scorer name, one of ``SCORER_NAMES``.
        judge_model (str | None): Judge model URI required by built-in LLM scorers.
        guidelines (str | None): Guideline text for the ``guidelines`` scorer.

    Returns:
        Any: A callable scorer usable in ``mlflow.genai.evaluate``.

    Raises:
        ValueError: If ``name`` is unknown or a built-in scorer lacks ``judge_model``.
    """
    if name == "response_present":
        from mlflow.genai.scorers import scorer

        return scorer(name="response_present", description=RESPONSE_PRESENT_DESCRIPTION)(response_present_impl)
    if name == "tool_evidence_used":
        from mlflow.genai.scorers import scorer

        return scorer(name="tool_evidence_used", description=TOOL_EVIDENCE_DESCRIPTION)(tool_evidence_used_impl)
    if name == "guidelines":
        if not judge_model:
            raise ValueError("the guidelines scorer requires a --judge-model URI")
        from mlflow.genai.scorers import Guidelines

        return Guidelines(name="guidelines", guidelines=guidelines or DEFAULT_GUIDELINES, model=judge_model)
    if name == "retrieval_groundedness":
        if not judge_model:
            raise ValueError("the retrieval_groundedness scorer requires a --judge-model URI")
        from mlflow.genai.scorers import RetrievalGroundedness

        return RetrievalGroundedness(name="retrieval_groundedness", model=judge_model)
    raise ValueError(f"unknown Fleet scorer: {name!r}")


def build_scorers(
    names: list[str],
    *,
    judge_model: str | None = None,
    guidelines: str | None = None,
) -> list[Any]:
    """
    Build multiple scorers by name, preserving order.

    Parameters:
        names (list[str]): Scorer names from ``SCORER_NAMES``.
        judge_model (str | None): Judge model URI required by built-in LLM scorers.
        guidelines (str | None): Guideline text for the ``guidelines`` scorer.

    Returns:
        list[Any]: One callable scorer per requested name.
    """
    return [build_scorer(name, judge_model=judge_model, guidelines=guidelines) for name in names]


__all__ = [
    "BUILTIN_SCORER_NAMES",
    "CUSTOM_SCORER_NAMES",
    "DEFAULT_GUIDELINES",
    "RESPONSE_PRESENT_DESCRIPTION",
    "SCORER_NAMES",
    "TOOL_EVIDENCE_DESCRIPTION",
    "build_scorer",
    "build_scorers",
    "response_present_impl",
    "tool_evidence_used_impl",
]
