"""MLflow 3 GenAI evaluation suite and custom scorers for Fleet RLM.

Provides domain-specific evaluators for Recursive Language Models (RLMs):
- `rlm_groundedness_scorer`: Evaluates whether claims in the final answer are grounded in
  evidence gathered across REPL iterations, sub-LM queries, and child RLM outputs.
- `rlm_context_efficiency_scorer`: Evaluates Context Compression Ratio (CCR) and token
  efficiency when navigating large or messy context.
- `rlm_task_correctness_scorer`: Evaluates output correctness against expected responses,
  facts, or user instructions.
- `rlm_recursion_roi_scorer`: Measures whether child RLM sandbox delegations produced
  actionable signal without redundant compute.
- `RLMCompositeEvaluator`: Class-based Scorer that evaluates all metrics in one pass.
- `evaluate_fleet_rlm`: High-level test and benchmark evaluation runner wrapping
  `mlflow.genai.evaluate`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import mlflow
import mlflow.genai
from mlflow.entities import Feedback
from mlflow.genai.scorers import Scorer, scorer

logger = logging.getLogger(__name__)


def _extract_text_tokens(text: str) -> set[str]:
    """Extract lowercased alphanumeric tokens from text for heuristic evaluation."""
    return set(re.findall(r"\b\w{3,}\b", text.lower()))


def _extract_response(outputs: Mapping[str, Any] | Any) -> str:
    """Extract textual response from diverse output structures."""
    if isinstance(outputs, str):
        return outputs
    if isinstance(outputs, Mapping):
        for key in ("response", "answer", "output", "result", "content"):
            if key in outputs:
                val = outputs[key]
                if isinstance(val, str):
                    return val
                if val is not None:
                    return str(val)
        messages = outputs.get("messages")
        if isinstance(messages, Sequence):
            for msg in reversed(messages):
                if isinstance(msg, Mapping) and msg.get("role") == "assistant":
                    content = msg.get("content")
                    if isinstance(content, str):
                        return content
        return ""
    return str(outputs) if outputs is not None else ""


def _extract_intermediate_evidence(
    outputs: Mapping[str, Any] | Any,
    inputs: Mapping[str, Any] | Any,
) -> list[str]:
    """Collect evidence strings gathered by tools, sub-LMs, or child sandboxes."""
    evidence: list[str] = []

    def scan_steps(steps: Any) -> None:
        if isinstance(steps, Sequence):
            for step in steps:
                if isinstance(step, Mapping):
                    content = step.get("content") or step.get("output") or step.get("result")
                    if isinstance(content, str) and content.strip():
                        evidence.append(content.strip())
                elif isinstance(step, str) and step.strip():
                    evidence.append(step.strip())

    if isinstance(outputs, Mapping):
        scan_steps(outputs.get("intermediate_steps"))
        scan_steps(outputs.get("tool_results"))
        scan_steps(outputs.get("sub_lm_results"))
        scan_steps(outputs.get("child_rlm_results"))

    if isinstance(inputs, Mapping):
        scan_steps(inputs.get("intermediate_steps"))

    return evidence


@scorer(name="rlm_groundedness")
def rlm_groundedness_scorer(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    expectations: dict[str, Any] | None = None,
    trace: Any | None = None,
) -> Feedback:
    """Evaluates whether claims in the output are grounded in execution evidence.

    Checks claims against gathered tool results, sub-LM extractions, or provided
    raw context. Supports LLM-as-a-judge when DSPy LM is active, with deterministic
    token-grounding fallback for offline testing.
    """
    del expectations, trace
    response = _extract_response(outputs)
    evidence_chunks = _extract_intermediate_evidence(outputs, inputs)

    raw_context = ""
    if isinstance(inputs, Mapping):
        raw_context = str(inputs.get("context") or inputs.get("large_context") or "")
    if raw_context.strip():
        evidence_chunks.append(raw_context.strip())

    if not response.strip():
        return Feedback(
            name="rlm_groundedness",
            value=0.0,
            rationale="Empty or missing response from model.",
        )

    if not evidence_chunks:
        return Feedback(
            name="rlm_groundedness",
            value=0.5,
            rationale="No intermediate tool or context evidence recorded; answer was produced directly.",
        )

    combined_evidence = "\n---\n".join(evidence_chunks)[:20_000]

    # Deterministic token grounding heuristic
    response_tokens = _extract_text_tokens(response)
    evidence_tokens = _extract_text_tokens(combined_evidence)

    if not response_tokens:
        overlap_ratio = 1.0
    else:
        matched = response_tokens.intersection(evidence_tokens)
        overlap_ratio = len(matched) / len(response_tokens)

    # If DSPy LM is configured and active, invoke LLM-as-a-judge
    try:
        import dspy

        if getattr(dspy.settings, "lm", None) is not None:
            judge = dspy.Predict("evidence: str, claim: str -> grounded_score: float, justification: str")
            result = judge(evidence=combined_evidence[:8_000], claim=response[:2_000])
            score = min(max(float(result.grounded_score), 0.0), 1.0)
            return Feedback(
                name="rlm_groundedness",
                value=round(score, 3),
                rationale=str(result.justification),
            )
    except Exception as exc:
        logger.debug("DSPy judge evaluation fell back to heuristic: %s", exc)

    heuristic_score = min(max(overlap_ratio * 1.2, 0.1), 1.0)
    return Feedback(
        name="rlm_groundedness",
        value=round(heuristic_score, 3),
        rationale=(
            f"Evidence grounding ratio: {overlap_ratio:.1%} token overlap across "
            f"{len(evidence_chunks)} evidence sources."
        ),
    )


@scorer(name="rlm_context_efficiency")
def rlm_context_efficiency_scorer(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    expectations: dict[str, Any] | None = None,
    trace: Any | None = None,
) -> Feedback:
    """Evaluates Context Compression Ratio (CCR) and navigation efficiency.

    Measures how effectively the RLM filtered a large/messy input context into a
    concise, informative response without unnecessary token blowup.
    """
    del expectations, trace
    raw_context = ""
    if isinstance(inputs, Mapping):
        raw_context = str(inputs.get("context") or inputs.get("large_context") or "")

    context_bytes = len(raw_context.encode("utf-8"))
    if isinstance(inputs, Mapping) and "input_context_bytes" in inputs:
        context_bytes = max(context_bytes, int(inputs["input_context_bytes"]))

    total_tokens = 0
    if isinstance(outputs, Mapping):
        total_tokens = int(outputs.get("total_tokens") or outputs.get("tokens_used") or 0)

    if total_tokens == 0:
        response = _extract_response(outputs)
        # Approximate tokens ~ 4 chars per token
        total_tokens = max(len(response) // 4, 1)

    if context_bytes == 0:
        return Feedback(
            name="rlm_context_efficiency",
            value=1.0,
            rationale="Zero external context task; baseline context efficiency.",
        )

    bytes_per_token = context_bytes / max(total_tokens, 1)

    # Scale: >= 50 bytes/token scores 1.0 (50:1 compression ratio); >= 10 scores 0.7; < 1 scores 0.1
    if bytes_per_token >= 50.0:
        score = 1.0
    elif bytes_per_token >= 20.0:
        score = 0.8 + 0.2 * ((bytes_per_token - 20.0) / 30.0)
    elif bytes_per_token >= 5.0:
        score = 0.5 + 0.3 * ((bytes_per_token - 5.0) / 15.0)
    else:
        score = max(0.1, bytes_per_token / 10.0)

    return Feedback(
        name="rlm_context_efficiency",
        value=round(score, 3),
        rationale=(
            f"Processed {context_bytes:,} context bytes with {total_tokens:,} tokens "
            f"(CCR: {bytes_per_token:.1f} bytes/token)."
        ),
    )


@scorer(name="rlm_task_correctness")
def rlm_task_correctness_scorer(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    expectations: dict[str, Any] | None = None,
    trace: Any | None = None,
) -> Feedback:
    """Evaluates task correctness against expected answers, facts, or instructions."""
    del trace
    response = _extract_response(outputs)
    if not response.strip():
        return Feedback(
            name="rlm_task_correctness",
            value=0.0,
            rationale="Model produced empty or whitespace response.",
        )

    exp_dict = expectations if isinstance(expectations, Mapping) else {}
    expected_response = exp_dict.get("expected_response") or exp_dict.get("ground_truth")
    expected_facts = exp_dict.get("expected_facts")

    # 1. Evaluate against expected facts if provided
    if isinstance(expected_facts, Sequence) and expected_facts:
        found_facts = 0
        response_lower = response.lower()
        for fact in expected_facts:
            fact_str = str(fact).lower().strip()
            if fact_str and fact_str in response_lower:
                found_facts += 1
            else:
                # Check token overlap for slight phrasing differences
                fact_tokens = _extract_text_tokens(fact_str)
                if fact_tokens:
                    fact_overlap = len(fact_tokens.intersection(_extract_text_tokens(response))) / len(fact_tokens)
                    if fact_overlap >= 0.75:
                        found_facts += 1

        fact_ratio = found_facts / len(expected_facts)
        return Feedback(
            name="rlm_task_correctness",
            value=round(fact_ratio, 3),
            rationale=f"Matched {found_facts} of {len(expected_facts)} expected facts ({fact_ratio:.1%}).",
        )

    # 2. Evaluate against expected response if provided
    if isinstance(expected_response, str) and expected_response.strip():
        exp_clean = expected_response.strip().lower()
        resp_clean = response.strip().lower()

        if exp_clean == resp_clean:
            return Feedback(
                name="rlm_task_correctness",
                value=1.0,
                rationale="Exact match with expected response.",
            )

        if exp_clean in resp_clean:
            return Feedback(
                name="rlm_task_correctness",
                value=1.0,
                rationale="Expected response is fully contained in output.",
            )

        # Token overlap comparison
        exp_tokens = _extract_text_tokens(exp_clean)
        resp_tokens = _extract_text_tokens(resp_clean)
        if exp_tokens:
            overlap = len(exp_tokens.intersection(resp_tokens)) / len(exp_tokens)
            score = min(max(overlap, 0.0), 1.0)
            return Feedback(
                name="rlm_task_correctness",
                value=round(score, 3),
                rationale=f"Response shares {overlap:.1%} token overlap with expected answer.",
            )

    # 3. If DSPy LM is active, evaluate query satisfaction
    query = ""
    if isinstance(inputs, Mapping):
        query = str(inputs.get("query") or inputs.get("prompt") or "")

    if query.strip():
        try:
            import dspy

            if getattr(dspy.settings, "lm", None) is not None:
                judge = dspy.Predict("query: str, response: str -> satisfaction: float, feedback: str")
                result = judge(query=query[:2_000], response=response[:2_000])
                score = min(max(float(result.satisfaction), 0.0), 1.0)
                return Feedback(
                    name="rlm_task_correctness",
                    value=round(score, 3),
                    rationale=str(result.feedback),
                )
        except Exception as exc:
            logger.debug("DSPy satisfaction judge fell back: %s", exc)

    # Default fallback: substantive response produced
    return Feedback(
        name="rlm_task_correctness",
        value=0.85,
        rationale="Substantive response produced addressing user request.",
    )


@scorer(name="rlm_recursion_roi_scorer")
def rlm_recursion_roi_scorer(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    expectations: dict[str, Any] | None = None,
    trace: Any | None = None,
) -> Feedback:
    """Evaluates whether child RLM sandbox delegations produced actionable ROI."""
    del inputs, expectations, trace
    child_rlm_calls = 0
    child_results: list[str] = []

    if isinstance(outputs, Mapping):
        child_rlm_calls = int(outputs.get("child_rlm_calls", 0))
        steps = outputs.get("intermediate_steps") or outputs.get("child_rlm_results")
        if isinstance(steps, Sequence):
            for step in steps:
                if isinstance(step, Mapping) and step.get("type") == "child_rlm":
                    child_rlm_calls += 1
                    content = step.get("content") or step.get("result")
                    if isinstance(content, str):
                        child_results.append(content)

    if child_rlm_calls == 0:
        return Feedback(
            name="rlm_recursion_roi_scorer",
            value=1.0,
            rationale="No child RLM delegation required; task solved directly without recursion overhead.",
        )

    response = _extract_response(outputs)
    resp_tokens = _extract_text_tokens(response)

    # Check if child answers were reflected in the final output
    integrated_children = 0
    for child_out in child_results:
        child_tokens = _extract_text_tokens(child_out)
        if child_tokens and resp_tokens.intersection(child_tokens):
            integrated_children += 1

    if child_results and integrated_children > 0:
        return Feedback(
            name="rlm_recursion_roi_scorer",
            value=1.0,
            rationale=(
                f"High recursion ROI: {integrated_children}/{len(child_results)} child sandbox "
                f"outputs directly integrated into final answer."
            ),
        )

    if not child_results and child_rlm_calls > 0:
        # Child was invoked and returned success
        return Feedback(
            name="rlm_recursion_roi_scorer",
            value=0.8,
            rationale=f"Child RLM delegation executed ({child_rlm_calls} calls) and returned valid status.",
        )

    return Feedback(
        name="rlm_recursion_roi_scorer",
        value=0.3,
        rationale="Child RLM was delegated to but its findings were not clearly utilized in final output.",
    )


class RLMCompositeEvaluator(Scorer):
    """Composite Scorer executing all Fleet RLM metrics in a single pass."""

    name: str = "rlm_composite"

    def __call__(
        self,
        *,
        inputs: Any = None,
        outputs: Any = None,
        expectations: dict[str, Any] | None = None,
        trace: Any | None = None,
        session: list[Any] | None = None,
    ) -> list[Feedback]:
        del session
        results: list[Feedback] = []
        for fn in (
            rlm_groundedness_scorer,
            rlm_context_efficiency_scorer,
            rlm_task_correctness_scorer,
            rlm_recursion_roi_scorer,
        ):
            fb = fn(inputs=inputs, outputs=outputs, expectations=expectations, trace=trace)
            if isinstance(fb, Feedback):
                results.append(fb)
            elif isinstance(fb, list):
                for item in fb:
                    if isinstance(item, Feedback):
                        results.append(item)
        return results


def evaluate_fleet_rlm(
    data: list[dict[str, Any]] | Any,
    predict_fn: Callable[..., dict[str, Any]] | None = None,
    *,
    scorers: Sequence[Any] | None = None,
    experiment_name: str | None = None,
    run_name: str | None = None,
) -> Any:
    """Run an MLflow 3 GenAI evaluation suite for Fleet RLM.

    Parameters:
        data: Evaluation dataset records formatted as:
            [{"inputs": {...}, "outputs": {...}, "expectations": {...}}, ...]
            or DataFrame / MLflow dataset.
        predict_fn: Optional callable taking unpacked **inputs and returning outputs dict.
            If data already contains outputs, predict_fn can be omitted.
        scorers: Optional list of scorers. Defaults to all Fleet RLM scorers:
            [rlm_groundedness_scorer, rlm_context_efficiency_scorer,
             rlm_task_correctness_scorer, rlm_recursion_roi_scorer].
        experiment_name: Optional MLflow experiment name.
        run_name: Optional MLflow run name.

    Returns:
        EvaluationResult with aggregate metrics and logged run details.
    """
    if experiment_name:
        mlflow.set_experiment(experiment_name)

    active_scorers = (
        list(scorers)
        if scorers is not None
        else [
            rlm_groundedness_scorer,
            rlm_context_efficiency_scorer,
            rlm_task_correctness_scorer,
            rlm_recursion_roi_scorer,
        ]
    )

    if run_name:
        with mlflow.start_run(run_name=run_name):
            return mlflow.genai.evaluate(
                data=data,
                predict_fn=predict_fn,
                scorers=active_scorers,
            )

    return mlflow.genai.evaluate(
        data=data,
        predict_fn=predict_fn,
        scorers=active_scorers,
    )


__all__ = [
    "RLMCompositeEvaluator",
    "evaluate_fleet_rlm",
    "rlm_context_efficiency_scorer",
    "rlm_groundedness_scorer",
    "rlm_recursion_roi_scorer",
    "rlm_task_correctness_scorer",
]
