"""MLflow scorers for Fleet traces: deterministic trajectory and routing checks plus the judge suite."""

from __future__ import annotations

from typing import Any

from mlflow.entities import Feedback, SpanStatusCode, SpanType
from mlflow.genai.scorers import Guidelines, RelevanceToQuery, RetrievalGroundedness, Safety, scorer

from fleet_rlm.engine.routes import parse_route


def _lm_call_count(spans: list[Any]) -> int:
    """Count model calls by MLflow span type, not by span-name substrings."""
    return sum(1 for span in spans if getattr(span, "span_type", None) == SpanType.LLM)


def _used_rlm_or_flex(spans: list[Any]) -> bool:
    return any((span.name or "").startswith(("RLM", "Flex")) for span in spans)


@scorer
def rlm_trajectory_efficiency(trace: Any) -> Feedback:
    """Penalize unnecessary LM calls and visible sandbox exceptions."""
    if trace is None:
        return Feedback(value=None, rationale="No trace captured; trajectory evidence is unavailable.")
    spans = trace.search_spans()
    lm_calls = _lm_call_count(spans)
    error_spans = []
    for span in spans:
        outputs = getattr(span, "outputs", None)
        attributes = getattr(span, "attributes", {}) or {}
        remote_error = span.name == "sandbox.exec" and (
            attributes.get("outcome") == "remote_code_error"
            or getattr(getattr(span, "status", None), "status_code", None) == SpanStatusCode.ERROR
            or (isinstance(outputs, dict) and bool(outputs.get("error")))
        )
        if remote_error or "Traceback" in str(outputs):
            error_spans.append(span)
    score = max(0.0, 1.0 - 0.015 * max(0, lm_calls - 3) - 0.15 * len(error_spans))
    return Feedback(
        value=round(score, 3),
        rationale=f"Observed {lm_calls} LM calls and {len(error_spans)} sandbox exceptions.",
    )


@scorer
def jev_routing_efficiency(trace: Any) -> Feedback:
    """Score simple requests routed without starting a Flex/RLM execution."""
    if trace is None:
        return Feedback(value=None, rationale="No trace available; routing evidence is unavailable.")
    spans = trace.search_spans()
    used_rlm = _used_rlm_or_flex(spans)
    lm_calls = _lm_call_count(spans)
    if not used_rlm and lm_calls <= 2:
        return Feedback(value=1.0, rationale=f"Fast path completed in {lm_calls} LM calls.")
    if used_rlm and lm_calls <= 15:
        return Feedback(value=0.95, rationale=f"Bounded RLM/Flex execution used {lm_calls} LM calls.")
    score = max(0.2, 1.0 - 0.02 * max(0, lm_calls - 15))
    return Feedback(value=round(score, 3), rationale=f"Heavy route used {lm_calls} LM calls.")


@scorer
def route_correctness(outputs: Any, expectations: dict[str, Any]) -> Feedback:
    """Check that the turn took the labeled gold route (direct, flex, or rlm; legacy labels are read too)."""
    expected = parse_route((expectations or {}).get("route"))
    if expected is None:
        return Feedback(value=None, rationale="No gold route labeled for this case.")
    actual = parse_route(outputs.get("route")) if isinstance(outputs, dict) else None
    if actual is None:
        return Feedback(value=None, rationale="Turn output did not report a route.")
    correct = str(actual) == str(expected)
    return Feedback(value=correct, rationale=f"Expected route {expected!r}, observed {actual!r}.")


def build_mlflow_scorer_suite(judge_model: str = "openai:/system.ai.deepseek-v4-1-flash") -> list[Any]:
    """Construct the GenAI judge suite for explicit offline/online evaluation runs."""
    return [
        RelevanceToQuery(model=judge_model),
        RetrievalGroundedness(model=judge_model),
        Safety(model=judge_model),
        Guidelines(
            name="sandbox_verification_discipline",
            guidelines=(
                "For quantitative analysis, file inspection, or code logic, the answer must reflect results "
                "verified in the sandbox, not unverified mental arithmetic or assumed file contents."
            ),
            model=judge_model,
        ),
        Guidelines(
            name="agent_skills_progressive_disclosure",
            guidelines=(
                "Activate a matching Skill before using its workflow. Use Skill scripts when appropriate. "
                "Created Skills must use a valid lowercase hyphenated name and describe their trigger conditions."
            ),
            model=judge_model,
        ),
        rlm_trajectory_efficiency,
        jev_routing_efficiency,
        route_correctness,
    ]
