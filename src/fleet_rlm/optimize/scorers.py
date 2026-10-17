"""Scoring: MLflow trace scorers (trajectory, routing, judges) and the one Fleet metric."""

from __future__ import annotations

import re
from typing import Any

import dspy
from mlflow.entities import Feedback, SpanStatusCode, SpanType
from mlflow.genai.scorers import Guidelines, RelevanceToQuery, RetrievalGroundedness, scorer

from fleet_rlm.engine.signatures import AnswerJudge, TurnJudge, parse_route
from fleet_rlm.observability import activity_preview, trace_object

SCORER_VERSION = "fleet-metric-v4"


def _lm_call_count(spans: list[Any]) -> int:
    """Count model calls by MLflow span type, not by span-name substrings."""
    return sum(1 for span in spans if getattr(span, "span_type", None) == SpanType.LLM)


def _used_rlm_or_flex(spans: list[Any]) -> bool:
    return any((span.name or "").startswith(("RLM", "Flex")) for span in spans)


@scorer
def rlm_trajectory_efficiency(trace: Any) -> Feedback | None:
    """Penalize unnecessary LM calls and visible sandbox exceptions."""
    if trace is None:
        return None
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
    return Feedback(
        value=efficiency(lm_calls, len(error_spans)),
        rationale=f"Observed {lm_calls} LM calls and {len(error_spans)} sandbox exceptions.",
    )


@scorer
def jev_routing_efficiency(trace: Any) -> Feedback | None:
    """Score simple requests routed without starting a Flex/RLM execution."""
    if trace is None:
        return None
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
def route_correctness(outputs: Any, expectations: dict[str, Any]) -> Feedback | None:
    """Check that the turn took the labeled gold route (direct, flex, or rlm; legacy labels are read too)."""
    expected = parse_route((expectations or {}).get("route"))
    if expected is None:
        return None
    actual = parse_route(trace_object(outputs).get("route"))
    if actual is None:
        return None
    correct = str(actual) == str(expected)
    return Feedback(value=correct, rationale=f"Expected route {expected!r}, observed {actual!r}.")


def build_mlflow_scorer_suite(
    judge_model: str = "openai:/system.ai.deepseek-v4-1-flash", judge_lm: dspy.LM | None = None
) -> list[Any]:
    """Construct the GenAI judge suite; with ``judge_lm`` it also scores the same ``fleet_metric`` GEPA uses."""
    groundedness = RetrievalGroundedness(model=judge_model)

    @scorer(name="retrieval_groundedness")
    def retrieval_groundedness(trace: Any) -> list[Feedback] | None:
        # MLflow's built-in raises on non-retrieval tasks. An absent metric is inapplicable,
        # not a failed judge and not null feedback (which Databricks rejects without an error).
        if trace is None or not any(span.span_type == SpanType.RETRIEVER for span in trace.search_spans()):
            return None
        return groundedness(trace=trace)

    suite = [
        RelevanceToQuery(model=judge_model),
        retrieval_groundedness,
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
    return [*suite, fleet_scorer(judge_lm)] if judge_lm is not None else suite


def efficiency(lm_calls: int, sandbox_errors: int) -> float:
    """1.0 for up to three LM calls; each extra call costs 0.015 and each failed sandbox execution 0.15."""
    return round(max(0.0, 1.0 - 0.015 * max(0, lm_calls - 3) - 0.15 * sandbox_errors), 3)


def missing_values(answer: str, expected: list[Any]) -> list[Any]:
    """Gold values absent from the answer; thousands separators are ignored."""
    text = answer.lower().replace(",", "")
    # Boundaries stop "12" from matching inside "120" or "1.25".
    return [value for value in expected if not re.search(rf"(?<![\w.]){re.escape(str(value).lower())}(?![\w])", text)]


class FleetJudge(dspy.Module):
    """LLM-as-judge for one Turn answer: against the gold answer when labelled, reference-free otherwise."""

    def __init__(self) -> None:
        super().__init__()
        self.with_reference = dspy.ChainOfThought(AnswerJudge)
        self.without_reference = dspy.ChainOfThought(TurnJudge)

    def forward(self, task: str, answer: str, expected_answer: str = "") -> dspy.Prediction:
        if expected_answer:
            verdict = self.with_reference(task=task, expected_answer=expected_answer, candidate_answer=answer)
        else:
            verdict = self.without_reference(task=task, candidate_answer=answer)
        is_correct = bool(getattr(verdict, "is_correct", False))
        is_safe = bool(getattr(verdict, "is_safe", False))
        return dspy.Prediction(
            score=float(is_correct and is_safe),
            feedback=str(verdict.critique)[:1000],
            is_correct=is_correct,
            is_safe=is_safe,
        )


# Each metric returns (score, detail), or None when it does not apply to the example.
Metric = tuple[float, str] | None


def answer_correctness(gold: Any, answer: str, judge_lm: dspy.LM) -> Metric:
    """The judge's verdict; uses the gold answer when one is labelled."""
    judge = FleetJudge()
    judge.set_lm(judge_lm)
    verdict = judge(
        task=str(getattr(gold, "task", "")), answer=answer, expected_answer=str(getattr(gold, "answer", "") or "")
    )
    return verdict.score, verdict.feedback


def expected_values_metric(gold: Any, answer: str) -> Metric:
    """Every labelled gold value appears in the answer."""
    expected = getattr(gold, "expected_values", None)
    if not expected:
        return None
    missing = missing_values(answer, list(expected))
    return (0.0, f"missing {missing}") if missing else (1.0, "all present")


def route_metric(gold: Any, pred: Any) -> Metric:
    """The Turn took the labelled gold route."""
    expected = parse_route(getattr(gold, "route", None))
    if expected is None:
        return None
    actual = parse_route(getattr(pred, "route", None))
    return float(actual == expected), f"expected {expected.value}, took {actual and actual.value}"


def efficiency_metric(pred: Any) -> Metric:
    """LM calls and failed sandbox executions the engine reported for the Turn."""
    lm_calls = getattr(pred, "lm_calls", None)
    sandbox_errors = getattr(pred, "sandbox_errors", None)
    if not isinstance(lm_calls, int) or not isinstance(sandbox_errors, int):
        return None
    if any(type(value) is not int or value < 0 for value in (lm_calls, sandbox_errors)):
        return None
    return efficiency(lm_calls, sandbox_errors), f"{lm_calls} LM calls, {sandbox_errors} sandbox errors"


def fleet_metric(
    gold: Any,
    pred: Any,
    trace: Any = None,  # noqa: ARG001
    pred_name: str | None = None,
    pred_trace: Any = None,
    *,
    judge_lm: dspy.LM,
) -> dspy.Prediction:
    """Incorrect answers score zero; correct answers compete on measured execution efficiency."""
    answer = str(getattr(pred, "answer", "") or "").strip()
    if not answer:
        return dspy.Prediction(
            score=0.0,
            feedback="FAIL answer: the Turn produced no answer.",
            objective_scores={"answer_correctness": 0.0, "quality": 0.0, "usage_available": 0.0},
        )
    metrics: dict[str, Metric] = {
        "answer_correctness": answer_correctness(gold, answer, judge_lm),
        "expected_values": expected_values_metric(gold, answer),
        "route": route_metric(gold, pred),
        "efficiency": efficiency_metric(pred),
    }
    applied = {name: result for name, result in metrics.items() if result is not None}
    quality = {name: result for name, result in applied.items() if name != "efficiency"}
    correctness = min(value for value, _ in quality.values())
    score = correctness * (0.9 + 0.1 * (metrics["efficiency"][0] if metrics["efficiency"] else 0.0))
    lines = [
        f"{'PASS' if value == 1.0 else 'FAIL'} {name} ({value:.2f}): {detail}"
        for name, (value, detail) in applied.items()
    ]
    expected_answer = str(getattr(gold, "answer", "") or "")
    lines.append(
        f"Expected answer: {expected_answer[:500]}" if expected_answer else "No gold answer; judged reference-free."
    )
    if pred_name:
        lines.insert(0, f"Feedback for {pred_name}; the score covers the whole Turn.")
        if pred_trace:
            lines.append(
                "Target step: "
                + activity_preview(
                    [{"inputs": inputs, "outputs": dict(outputs)} for _, inputs, outputs in pred_trace], limit=2000
                )
            )
    if metrics["efficiency"] is None:
        lines.append("Execution usage unavailable; no efficiency credit or promotion evidence.")
    return dspy.Prediction(
        score=score,
        feedback="\n".join(lines),
        objective_scores={name: value for name, (value, _) in applied.items()}
        | {"quality": correctness, "usage_available": float(metrics["efficiency"] is not None)},
    )


def gold_and_prediction(inputs: Any, outputs: Any, expectations: dict[str, Any] | None) -> tuple[Any, Any]:
    """Rebuild the metric's ``gold`` and ``pred`` from a ``fleet_turn`` trace's inputs, outputs, and labels."""
    labels = expectations or {}
    inputs = trace_object(inputs) or inputs
    outputs = trace_object(outputs) or outputs
    gold = dspy.Example(
        task=str(inputs.get("task", "")) if isinstance(inputs, dict) else str(inputs or ""),
        answer=labels.get("expected_answer") or "",
        expected_values=labels.get("expected_values") or [],
        route=labels.get("route"),
    )
    pred = dspy.Prediction(**outputs) if isinstance(outputs, dict) else dspy.Prediction(answer=str(outputs or ""))
    return gold, pred


def metric_feedbacks(result: dspy.Prediction) -> list[Feedback]:
    """``fleet_metric`` as MLflow feedback: the overall score, then one entry per objective."""
    overall = Feedback(name="fleet_metric", value=result.score, rationale=result.feedback)
    return [overall] + [
        Feedback(name=f"fleet_metric.{name}", value=value) for name, value in (result.objective_scores or {}).items()
    ]


def fleet_scorer(judge_lm: dspy.LM) -> Any:
    """``fleet_metric`` and each of its objectives as an MLflow scorer over a ``fleet_turn`` trace."""

    @scorer(name="fleet_metric")
    def fleet_score(inputs: Any, outputs: Any, expectations: dict[str, Any]) -> list[Feedback]:
        gold, pred = gold_and_prediction(inputs, outputs, expectations)
        return metric_feedbacks(fleet_metric(gold, pred, judge_lm=judge_lm))

    return fleet_score
