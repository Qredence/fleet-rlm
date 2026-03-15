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

from mlflow.entities import AssessmentSource, Feedback
from mlflow.genai.scorers import (
    RelevanceToQuery,
    RetrievalGroundedness,
    ToolCallCorrectness,
    ToolCallEfficiency,
    scorer,
)


def get_default_judge_model() -> str:
    """Get the model ID configured for the LLM judge.
    Returns the DSPY_LM_MODEL or a default appropriate for litellm."""
    return os.environ.get("DSPY_LM_MODEL", "openai:/gemini/gemini-3.1-pro-preview")


def build_rlm_scorers(model: str | None = None) -> list[Any]:
    """
    Build the recommended MLflow GenAI scorers for evaluating the RLM agent.

    Args:
        model: Optional LLM backend to use for judging (e.g., 'openai:/gemini/gemini-3.1-pro-preview').
               If not provided, uses get_default_judge_model().

    Returns:
        List of MLflow GenAI scorers.
    """
    judge_model = model or get_default_judge_model()

    scorers = [
        # Evaluates if the agent's final answer solves the user's initial query
        RelevanceToQuery(model=judge_model),
        # Evaluates if the agent called tools correctly given their schemas
        ToolCallCorrectness(model=judge_model),
        # Evaluates if the agent was efficient (no redundant/repeated tool calls)
        ToolCallEfficiency(model=judge_model),
        # Evaluates if the agent's answer was grounded in the tool output (prevents hallucination)
        RetrievalGroundedness(model=judge_model),
    ]

    # The reasoning-quality judge may expose trace inputs (including tool I/O, URLs,
    # or user data) to an external LLM. Require explicit opt-in to enable it.
    enable_reasoning_judge = os.environ.get(
        "FLEET_RLM_ENABLE_REASONING_JUDGE", ""
    ).lower() in {"1", "true", "yes", "on"}
    if enable_reasoning_judge:
        # Custom reasoning judge
        scorers.append(reasoning_quality_scorer(judge_model))

    return scorers


def reasoning_quality_scorer(model: str) -> Any:
    """
    A custom MLflow GenAI scorer using the @scorer decorator to evaluate
    the internal Chain of Thought (Thoughts/Actions).
    """

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
        import litellm

        # Extract the thoughts/events from the trace
        # We look for spans that contain reasoning or tool calls
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

        # Cap the total reasoning text length to avoid sending excessively large payloads.
        max_reasoning_len = 4000
        if len(reasoning_text) > max_reasoning_len:
            reasoning_text = (
                reasoning_text[: max_reasoning_len - 28] + "\n[TRACE TRUNCATED]"
            )

        prompt = f"""
            Evaluate the reasoning quality of an AI agent based on its execution trace.

            Trace Reasoning Steps:
            {reasoning_text}

            Score the reasoning from 1 to 5:
            5: Perfectly logical, efficient, and clear reasoning leading to the goal.
            3: Somewhat convoluted or circular, but eventually makes sense.
            1: Illogical, hallucinations, or failure to reason about the tool outputs.

            Return ONLY a JSON object with this format:
            {{
                "score": 5,
                "reason": "Clear step-by-step logic."
            }}
        """

        # Resolve credentials from the project's standard env vars so this scorer
        # works with any LiteLLM-routed provider (not just OPENAI_API_KEY).
        api_key = os.environ.get("DSPY_LLM_API_KEY") or os.environ.get(
            "DSPY_LM_API_KEY"
        )
        api_base = os.environ.get("DSPY_LM_API_BASE") or None

        try:
            # Strip the DSPy-style "provider:/" prefix (e.g. "openai:/") so the
            # remaining string is a plain LiteLLM model identifier like
            # "gemini/gemini-3.1-pro-preview" or "gpt-4o".
            lm_model = model.split(":/")[-1] if ":/" in model else model

            response = litellm.completion(
                model=lm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                api_key=api_key,
                api_base=api_base,
            )

            # Use raw access instead of message.content to handle potential errors
            content = response.choices[0].message.content

            # Basic JSON extraction in case there's markdown formatting
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]

            payload = json.loads(content.strip())

            return Feedback(
                value=payload.get("score", 1),
                rationale=payload.get("reason", "Failed to parse reasoning"),
                source=AssessmentSource(
                    source_type="LLM_JUDGE",
                    source_id=model,
                ),
            )
        except Exception as e:
            return Feedback(
                value=1,
                rationale=f"Error evaluating reasoning: {str(e)}",
                source=AssessmentSource(source_type="ERROR", source_id="script"),
            )

    return judge
