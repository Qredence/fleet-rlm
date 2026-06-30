"""LLM-as-judge scorers for evaluating agent traces.

This module provides 4 judges that use LLMs to score different aspects of
agent behavior. Each judge loads its prompt from disk and returns a float
in [0.0, 1.0].

BYOK (Bring-Your-Own-Key) compliance (VAL-C-025):
    Judges use the configured chat LM from the BYOK environment, not hardcoded
    API keys. The LM is resolved via get_delegate_lm_from_env or
    build_bounded_lm from runtime.config, which respect the per-user BYOK
    credentials stored in llm_provider_profiles with RLS.

Score clamping (VAL-C-026):
    Each judge returns a single float clamped to [0.0, 1.0], even when the
    underlying LM emits extraneous prose or out-of-range numerics. The
    _extract_score helper handles various response formats and clamps.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .trace_record import TraceRecord

logger = logging.getLogger(__name__)

# Path to prompts directory (anchored to this package)
_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Judge names in canonical order
JUDGE_NAMES = [
    "answer_relevance",
    "faithfulness_to_context",
    "trajectory_coherence",
    "tool_selection_quality",
]


def _load_prompt(judge_name: str) -> str:
    """Load a judge prompt from disk.

    Args:
        judge_name: Name of the judge (matches prompt filename without .txt)

    Returns:
        The prompt text.

    Raises:
        FileNotFoundError: If the prompt file doesn't exist.
    """
    prompt_path = _PROMPTS_DIR / f"{judge_name}.txt"
    return prompt_path.read_text(encoding="utf-8")


def _extract_score(response: str) -> float:
    """Extract a float score from an LLM response.

    Handles various formats:
    - Pure numeric: "0.85"
    - JSON: {"score": 0.85}
    - With explanation: "Score: 0.85" or "0.85 - good answer"
    - Out-of-range values are clamped to [0.0, 1.0] (VAL-C-026)

    VAL-CORR-001: When multiple numbers are present, the LAST numeric match is
    returned (not the first). Numbers already in the [0.0, 1.0] range are
    preferred over out-of-range numbers, so a response like
    ``"On a scale of 100, I rate this 0.85"`` extracts ``0.85`` rather than the
    clamped ``100``. The final value is always clamped to ``[0.0, 1.0]``.

    Args:
        response: Raw LLM response text.

    Returns:
        Extracted score clamped to [0.0, 1.0], or 0.0 if extraction fails.
    """
    import re

    if not response or not isinstance(response, str):
        return 0.0

    response = response.strip()

    # Try to extract from JSON
    if "{" in response and "}" in response:
        try:
            import json

            data = json.loads(response)
            if isinstance(data, dict) and "score" in data:
                score = float(data["score"])
                # Clamp to [0.0, 1.0] (VAL-C-026)
                return max(0.0, min(1.0, score))
        except (json.JSONDecodeError, ValueError, TypeError):
            # JSON parsing is optional; fall through to other score extraction
            # strategies below (pure float parsing, then regex extraction).
            logger.debug("Failed to parse JSON score from judge response; falling back.")

    # Try to parse as a pure float first
    try:
        score = float(response)
        # Clamp to [0.0, 1.0] (VAL-C-026)
        return max(0.0, min(1.0, score))
    except ValueError:
        pass

    # Find ALL numbers in the response. Prefer the LAST number that is already
    # in the [0.0, 1.0] range; if none qualify, fall back to the LAST number
    # (clamped). This avoids grabbing the first number (e.g. "100" in "On a
    # scale of 100, I rate this 0.85") instead of the intended score.
    number_pattern = r"[-+]?\d*\.?\d+"
    matches = re.findall(number_pattern, response)
    floats: list[float] = []
    for match in matches:
        try:
            floats.append(float(match))
        except ValueError:
            continue
    if not floats:
        return 0.0

    in_range = [f for f in floats if 0.0 <= f <= 1.0]
    if in_range:
        # Last in-range number; clamp defensively in case of float artifacts.
        return max(0.0, min(1.0, in_range[-1]))
    # No in-range number: take the last number found and clamp it.
    return max(0.0, min(1.0, floats[-1]))


def _call_judge_lm(
    judge_name: str,
    prompt: str,
    trace_record: TraceRecord,
    lm: Any,
) -> float | None:
    """Call an LLM to judge a trace.

    Args:
        judge_name: Name of the judge.
        prompt: The judge prompt.
        trace_record: The trace to evaluate.
        lm: Language model to use for judging.

    Returns:
        Score in [0.0, 1.0], ``None`` when no LM is configured (so callers
        record a null score), or ``0.0`` on error.
    """
    if lm is None:
        logger.info(
            "Skipping LLM judge '%s': no judge model is configured. "
            "Set DSPY_LM_MODEL or configure a BYOK LLM profile to enable LLM-judge scoring.",
            judge_name,
        )
        return None
    try:
        # Build evaluation context
        context_parts = [
            f"User Request: {trace_record.user_request}",
            f"Final Answer: {trace_record.final_answer}",
            f"Route: {trace_record.route}",
        ]

        if trace_record.core_memory:
            context_parts.append(f"Core Memory: {trace_record.core_memory}")

        if trace_record.active_skills:
            context_parts.append(f"Active Skills: {', '.join(trace_record.active_skills)}")

        if trace_record.trajectory_spans:
            trajectory_summary = []
            for i, span in enumerate(trace_record.trajectory_spans[:10], 1):  # Limit to first 10 spans
                tool_info = f" (tool: {span.tool_name})" if span.tool_name else ""
                trajectory_summary.append(f"{i}. {span.name}{tool_info}")
            context_parts.append("Trajectory:\n" + "\n".join(trajectory_summary))

        evaluation_context = "\n\n".join(context_parts)

        # Combine prompt and context
        full_prompt = f"{prompt}\n\nEvaluation Context:\n{evaluation_context}"

        # Stock dspy.LM uses the legacy forward contract: invoke as lm(messages=...)
        # and read the OpenAI-like chat completion response.
        messages: list[dict[str, str]] = [{"role": "user", "content": full_prompt}]
        text = ""
        if callable(lm):
            try:
                response = lm(messages=messages)
                if isinstance(response, list):
                    text = response[0] if response else ""
                elif hasattr(response, "choices") and response.choices:
                    text = response.choices[0].message.content
                elif hasattr(response, "content"):
                    text = response.content
                elif hasattr(response, "outputs") and response.outputs:
                    # LMResponse.outputs is a list of LMOutput objects
                    text = (
                        response.outputs[0].text if hasattr(response.outputs[0], "text") else str(response.outputs[0])
                    )
                else:
                    text = str(response)
            except Exception as e:
                logger.warning("LM call failed for judge %s: %s", judge_name, e)
                return 0.0
        else:
            logger.warning("Non-callable LM for judge %s", judge_name)
            return 0.0

        # Extract score from response
        return _extract_score(str(text))

    except Exception as e:
        logger.warning("Judge %s failed: %s", judge_name, e)
        return 0.0


def answer_relevance(trace_record: TraceRecord, lm: Any) -> float | None:
    """Score how relevant the final answer is to the user's request.

    Args:
        trace_record: The trace to evaluate.
        lm: Language model to use for judging.

    Returns:
        Score in [0.0, 1.0], or ``None`` when no LM is configured.
    """
    try:
        prompt = _load_prompt("answer_relevance")
    except FileNotFoundError:
        logger.error("Prompt file not found: answer_relevance.txt")
        return 0.0

    return _call_judge_lm("answer_relevance", prompt, trace_record, lm)


def faithfulness_to_context(trace_record: TraceRecord, lm: Any) -> float | None:
    """Score how faithful the answer is to the provided context.

    Args:
        trace_record: The trace to evaluate.
        lm: Language model to use for judging.

    Returns:
        Score in [0.0, 1.0], or ``None`` when no LM is configured.
    """
    try:
        prompt = _load_prompt("faithfulness_to_context")
    except FileNotFoundError:
        logger.error("Prompt file not found: faithfulness_to_context.txt")
        return 0.0

    return _call_judge_lm("faithfulness_to_context", prompt, trace_record, lm)


def trajectory_coherence(trace_record: TraceRecord, lm: Any) -> float | None:
    """Score how coherent the execution trajectory is.

    Args:
        trace_record: The trace to evaluate.
        lm: Language model to use for judging.

    Returns:
        Score in [0.0, 1.0], or ``None`` when no LM is configured.
    """
    try:
        prompt = _load_prompt("trajectory_coherence")
    except FileNotFoundError:
        logger.error("Prompt file not found: trajectory_coherence.txt")
        return 0.0

    return _call_judge_lm("trajectory_coherence", prompt, trace_record, lm)


def tool_selection_quality(trace_record: TraceRecord, lm: Any) -> float | None:
    """Score how appropriate the tool selection was.

    Args:
        trace_record: The trace to evaluate.
        lm: Language model to use for judging.

    Returns:
        Score in [0.0, 1.0], or ``None`` when no LM is configured.
    """
    try:
        prompt = _load_prompt("tool_selection_quality")
    except FileNotFoundError:
        logger.error("Prompt file not found: tool_selection_quality.txt")
        return 0.0

    return _call_judge_lm("tool_selection_quality", prompt, trace_record, lm)


# Callable registry for all judges
JUDGE_CALLABLES = {
    "answer_relevance": answer_relevance,
    "faithfulness_to_context": faithfulness_to_context,
    "trajectory_coherence": trajectory_coherence,
    "tool_selection_quality": tool_selection_quality,
}
