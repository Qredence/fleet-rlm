"""
Custom and built-in MLflow judges for the fleet-rlm agent.
These scorers are designed to evaluate the RLM multi-turn behavior,
tool usage efficiency, correctness, and reasoning.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import dspy


class ReasoningQualityJudge(dspy.Signature):
    """Evaluate the reasoning quality of an AI agent based on its execution trace.

    Score the reasoning from 1 to 5:
    5: Perfectly logical, efficient, and clear reasoning leading to the goal.
    3: Somewhat convoluted or circular, but eventually makes sense.
    1: Illogical, hallucinations, or failure to reason about the tool outputs.
    """

    trace_reasoning: str = dspy.InputField(desc="Reasoning steps extracted from the agent's execution trace.")
    score: int = dspy.OutputField(desc="Reasoning quality score from 1 (worst) to 5 (best).")
    reason: str = dspy.OutputField(desc="Short justification for the score.")


def _judge_reasoning(lm: Any, reasoning_text: str) -> tuple[int, str]:
    """Score reasoning text with a typed Predict judge.

    DSPy's adapter handles output parsing, so no manual JSON extraction is
    needed.
    """
    with dspy.context(lm=lm):
        verdict = dspy.Predict(ReasoningQualityJudge)(trace_reasoning=reasoning_text)
    score = max(1, min(5, int(verdict.score)))
    return score, str(verdict.reason or "No justification provided")


def _load_mlflow_scorers() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    """Import MLflow scorer types lazily to avoid import-time side effects."""
    from mlflow.entities import AssessmentSource, Feedback

    RelevanceToQuery: Any
    RetrievalGroundedness: Any
    ToolCallCorrectness: Any
    ToolCallEfficiency: Any
    try:
        from mlflow.genai.scorers import (
            RelevanceToQuery as _RelevanceToQuery,
        )
        from mlflow.genai.scorers import (
            RetrievalGroundedness as _RetrievalGroundedness,
        )
        from mlflow.genai.scorers import (
            ToolCallCorrectness as _ToolCallCorrectness,
        )
        from mlflow.genai.scorers import (
            ToolCallEfficiency as _ToolCallEfficiency,
        )
        from mlflow.genai.scorers import (
            scorer,
        )

        RelevanceToQuery = _RelevanceToQuery
        RetrievalGroundedness = _RetrievalGroundedness
        ToolCallCorrectness = _ToolCallCorrectness
        ToolCallEfficiency = _ToolCallEfficiency
    except ImportError:
        from mlflow.genai.scorers import scorer

        class PlaceholderScorer:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __call__(self, *args: Any, **kwargs: Any) -> Any:
                return Feedback(value=1, rationale="Scorer not available in this environment")

        RelevanceToQuery = PlaceholderScorer
        RetrievalGroundedness = PlaceholderScorer
        ToolCallCorrectness = PlaceholderScorer
        ToolCallEfficiency = PlaceholderScorer

    return (
        AssessmentSource,
        Feedback,
        RelevanceToQuery,
        RetrievalGroundedness,
        ToolCallCorrectness,
        ToolCallEfficiency,
        scorer,
    )


def get_default_judge_model() -> str:
    """Get the model ID configured for the LLM judge.
    Returns the DSPY_LM_MODEL or a default appropriate for ``dspy.LM``."""
    return os.environ.get("DSPY_LM_MODEL", "openai/gemini-3-flash-preview")


def build_rlm_scorers(
    model: str | None = None,
    *,
    include_retrieval_groundedness: bool = True,
) -> list[Any]:
    """
    Build the recommended MLflow GenAI scorers for evaluating the RLM agent.

    Args:
        model: Optional LLM backend to use for judging (e.g., 'openai:/gemini/gemini-3.1-pro-preview').
               If not provided, uses get_default_judge_model().

    Returns:
        List of MLflow GenAI scorers.
    """
    (
        _AssessmentSource,
        _Feedback,
        RelevanceToQuery,
        RetrievalGroundedness,
        ToolCallCorrectness,
        ToolCallEfficiency,
        scorer,
    ) = _load_mlflow_scorers()
    _ = _AssessmentSource, _Feedback, scorer

    judge_model = model or get_default_judge_model()

    scorers = [
        # Evaluates if the agent's final answer solves the user's initial query
        RelevanceToQuery(model=judge_model),
        # Evaluates if the agent called tools correctly given their schemas
        ToolCallCorrectness(model=judge_model),
        # Evaluates if the agent was efficient (no redundant/repeated tool calls)
        ToolCallEfficiency(model=judge_model),
    ]
    if include_retrieval_groundedness:
        scorers.append(
            # Evaluates if the agent's answer was grounded in the tool output
            # (prevents hallucination when retrieval context exists).
            RetrievalGroundedness(model=judge_model)
        )

    # The reasoning-quality judge may expose trace inputs (including tool I/O, URLs,
    # or user data) to an external LLM. Require explicit opt-in to enable it.
    enable_reasoning_judge = os.environ.get("FLEET_RLM_ENABLE_REASONING_JUDGE", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if enable_reasoning_judge:
        # Custom reasoning judge
        scorers.append(reasoning_quality_scorer(judge_model))

    return scorers


def reasoning_quality_scorer(model: str) -> Any:
    """
    A custom MLflow GenAI scorer using the @scorer decorator to evaluate
    the internal Chain of Thought (Thoughts/Actions).
    """
    AssessmentSource, Feedback, _, _, _, _, scorer = _load_mlflow_scorers()

    # Simple redaction and truncation utilities to reduce the risk of leaking
    # secrets or large payloads from span.inputs into the judge prompt.
    def _redact_and_truncate_value(value: Any, max_len: int = 2000) -> str:
        # Convert to a reasonably stable string form, preferring JSON when possible.
        try:
            text = json.dumps(value, default=str, ensure_ascii=False)
        except TypeError:
            text = str(value)

        # Redact common secret-bearing fields in JSON-like content.
        # This is a best-effort heuristic and does not guarantee full removal,
        # but it significantly reduces obvious secret leakage.
        secret_keys = [
            "api_key",
            "apikey",
            "token",
            "access_token",
            "refresh_token",
            "authorization",
            "password",
            "secret",
        ]
        for key in secret_keys:
            # Match patterns like "key": "value" or 'key': 'value'
            pattern = rf'("{key}"\s*:\s*")[^"]*("|$)'
            text = re.sub(pattern, r"\1***\2", text, flags=re.IGNORECASE)
            pattern_single = rf"('{key}'\s*:\s*')[^']*('|$)"
            text = re.sub(pattern_single, r"\1***\2", text, flags=re.IGNORECASE)

        if len(text) > max_len:
            return text[: max_len - 12] + " [TRUNCATED]"
        return text

    def _safe_span_inputs(span: Any) -> str:
        # Guard against attributes not being present or being very large/complex.
        inputs = getattr(span, "inputs", "")
        return _redact_and_truncate_value(inputs)

    @scorer(name="reasoning_quality")
    def judge(trace: Any) -> Feedback:
        spans = trace.search_spans()

        reasoning_chunks: list[str] = []
        max_spans = 20
        for span in spans:
            if len(reasoning_chunks) >= max_spans:
                break
            name = str(getattr(span, "name", "")).lower()
            if name.startswith("thought") or name.startswith("llm"):
                safe_inputs = _safe_span_inputs(span)
                reasoning_chunks.append(f"Step {span.name}: {safe_inputs}")

        if not reasoning_chunks:
            reasoning_text = "No explicit reasoning steps found in trace."
        else:
            reasoning_text = "\n".join(reasoning_chunks)

        max_reasoning_len = 4000
        if len(reasoning_text) > max_reasoning_len:
            reasoning_text = reasoning_text[: max_reasoning_len - 28] + "\n[TRACE TRUNCATED]"

        try:
            from fleet_rlm.runtime.config import resolve_lm

            # Strip the DSPy-style "provider:/" prefix so the remaining string is
            # a plain LiteLLM model identifier (e.g. "gemini/gemini-3.1-pro-preview").
            lm_model = model.split(":/")[-1] if ":/" in model else model
            lm = resolve_lm("judge", model_name=lm_model)
            if lm is None:
                raise ValueError("No judge model configured")

            score, reason = _judge_reasoning(lm, reasoning_text)
            return Feedback(
                value=score,
                rationale=reason,
                source=AssessmentSource(
                    source_type="LLM_JUDGE",
                    source_id=lm_model,
                ),
            )
        except Exception as e:
            return Feedback(
                value=1,
                rationale=f"Error evaluating reasoning: {str(e)}",
                source=AssessmentSource(source_type="ERROR", source_id="script"),
            )

    return judge
