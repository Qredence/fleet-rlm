import json
from functools import partial
from types import SimpleNamespace

import dspy
from dspy.utils import DummyLM
from mlflow.entities import SpanType

from fleet_rlm.optimize.evaluation import expected_values_present
from fleet_rlm.optimize.scorers import (
    build_mlflow_scorer_suite,
    fleet_metric,
    fleet_scorer,
    jev_routing_efficiency,
    rlm_trajectory_efficiency,
    route_correctness,
)


def _span(name: str, span_type: str = SpanType.CHAIN, outputs: object = None):
    return SimpleNamespace(name=name, span_type=span_type, outputs=outputs)


def _trace(*spans):
    return SimpleNamespace(search_spans=lambda: list(spans))


def test_lm_calls_are_counted_by_span_type_not_name():
    # A span merely named "LMStudioAdapter" must not count as an LM call.
    trace = _trace(_span("LMStudioAdapter"), _span("ChatCompletion", SpanType.LLM))
    assert jev_routing_efficiency(trace=trace).value == 1.0
    assert "1 LM calls" in jev_routing_efficiency(trace=trace).rationale


def test_flex_route_is_bounded_and_heavy_route_degrades():
    bounded = _trace(_span("Flex.forward"), *[_span("lm", SpanType.LLM)] * 10)
    assert jev_routing_efficiency(trace=bounded).value == 0.95
    heavy = _trace(_span("RLM.forward"), *[_span("lm", SpanType.LLM)] * 25)
    assert jev_routing_efficiency(trace=heavy).value == 0.8


def test_trajectory_penalizes_calls_and_tracebacks():
    trace = _trace(*[_span("lm", SpanType.LLM)] * 5, _span("sandbox", outputs="Traceback (most recent call last)"))
    assert rlm_trajectory_efficiency(trace=trace).value == 0.82


def test_missing_trace_is_unavailable_not_zero():
    assert rlm_trajectory_efficiency(trace=None) is None
    assert jev_routing_efficiency(trace=None) is None


def test_route_correctness_matches_gold_label():
    assert route_correctness(outputs={"route": "rlm"}, expectations={"route": "rlm"}).value is True
    assert route_correctness(outputs={"route": "direct"}, expectations={"route": "rlm"}).value is False
    assert route_correctness(outputs={"route": "direct"}, expectations={}) is None
    # Gold labels and traces written before the rename still score.
    assert route_correctness(outputs={"route": "rlm"}, expectations={"route": "deep_rlm"}).value is True
    assert route_correctness(outputs={"route": "flex_pipeline"}, expectations={"route": "flex"}).value is True


def test_scorer_suite_includes_route_correctness():
    suite = build_mlflow_scorer_suite("databricks:/test-judge")
    assert route_correctness in suite


def test_uc_wrapped_outputs_preserve_answer_route_and_execution_counts():
    inputs = json.dumps(json.dumps({"task": "Convert temperature"}))
    outputs = json.dumps(json.dumps({"answer": "22.2", "route": "flex", "lm_calls": 6, "sandbox_errors": 0}))
    expectations = {"expected_answer": "22.2", "expected_values": ["22.2"], "route": "direct"}
    assert expected_values_present(outputs=outputs, expectations=expectations).value is True
    assert route_correctness(outputs=outputs, expectations=expectations).value is False
    feedback = fleet_scorer(_judge(True))(inputs=inputs, outputs=outputs, expectations=expectations)
    values = {item.name: item.value for item in feedback}
    assert values["fleet_metric.answer_correctness"] == 1.0
    assert values["fleet_metric.route"] == 0.0
    assert values["fleet_metric.efficiency"] == 0.955
    assert values["fleet_metric"] == 0.0


def test_non_retrieval_tasks_skip_groundedness_without_null_feedback(monkeypatch):
    from mlflow.entities import Feedback

    import fleet_rlm.optimize.scorers as scorers

    calls = []

    class Judge:
        def __init__(self, **_kwargs):
            pass

        def __call__(self, **kwargs):
            calls.append(kwargs)
            return [Feedback(value=True)]

    monkeypatch.setattr(scorers, "RetrievalGroundedness", Judge)
    suite = build_mlflow_scorer_suite("openai:/judge")
    groundedness = next(item for item in suite if item.name == "retrieval_groundedness")
    assert groundedness(trace=_trace(_span("sandbox.exec", SpanType.TOOL))) is None
    assert calls == []
    trace = _trace(_span("retriever", SpanType.RETRIEVER))
    assert groundedness(trace=trace)[0].value is True
    assert calls == [{"trace": trace}]


def test_structured_execution_error_is_counted_once():
    from mlflow.entities import SpanStatusCode

    execution = _span("sandbox.exec", SpanType.TOOL, {"error": True, "stderr": "Traceback"})
    execution.attributes = {"outcome": "remote_code_error"}
    execution.status = SimpleNamespace(status_code=SpanStatusCode.ERROR)
    feedback = rlm_trajectory_efficiency(trace=_trace(execution))
    assert feedback.value == 0.85
    assert "1 sandbox exceptions" in feedback.rationale


def _judge(is_correct: bool, critique: str = "checked") -> DummyLM:
    return DummyLM([{"reasoning": "r", "is_correct": is_correct, "is_safe": True, "critique": critique}])


def test_fleet_metric_does_not_let_route_or_efficiency_offset_wrong_answers():
    gold = dspy.Example(task="sum", answer="15,151.50", expected_values=["15151.50", "3787.875"], route="rlm")
    pred = dspy.Prediction(answer="Sum 15,151.50", route="rlm", lm_calls=13, sandbox_errors=1)
    result = fleet_metric(gold, pred, judge_lm=_judge(False, "mean is missing"))
    # efficiency = 1 - 0.015*10 - 0.15 = 0.7
    assert result.objective_scores == {
        "answer_correctness": 0.0,
        "expected_values": 0.0,
        "route": 1.0,
        "efficiency": 0.7,
        "quality": 0.0,
        "usage_available": 1.0,
    }
    assert result.score == 0.0
    assert "FAIL expected_values (0.00): missing ['3787.875']" in result.feedback
    assert "PASS route" in result.feedback and "FAIL answer_correctness (0.00): mean is missing" in result.feedback
    assert "Expected answer: 15,151.50" in result.feedback


def test_fleet_metric_judges_reference_free_without_a_gold_answer():
    judge = DummyLM([{"reasoning": "r", "is_correct": True, "is_safe": True, "critique": "Correct and complete."}])
    result = fleet_metric(
        dspy.Example(task="2+2"), dspy.Prediction(answer="4", lm_calls=1, sandbox_errors=0), judge_lm=judge
    )
    assert result.objective_scores == {
        "answer_correctness": 1.0,
        "efficiency": 1.0,
        "quality": 1.0,
        "usage_available": 1.0,
    }
    assert "No gold answer; judged reference-free." in result.feedback
    assert "expected_answer" not in judge.history[-1]["messages"][-1]["content"]


def test_fleet_metric_scores_an_empty_answer_zero_without_calling_the_judge():
    result = fleet_metric(dspy.Example(task="t", answer="a"), dspy.Prediction(answer="  "), judge_lm=DummyLM([]))
    assert result.score == 0.0 and "no answer" in result.feedback


def test_fleet_metric_names_the_component_gepa_is_tuning():
    result = fleet_metric(
        dspy.Example(task="t", answer="a"),
        dspy.Prediction(answer="a"),
        None,
        "program.primary_rlm.generate_action",
        None,
        judge_lm=_judge(True),
    )
    assert result.feedback.startswith("Feedback for program.primary_rlm.generate_action")


def test_mlflow_scorer_and_gepa_metric_give_the_same_scores():
    inputs = {"task": "count 5xx", "session_id": "s1"}
    outputs = {"answer": "5xx: 3", "route": "flex", "lm_calls": 6, "sandbox_errors": 0, "artifacts": []}
    expectations = {"expected_answer": "3", "expected_values": ["3"], "route": "rlm", "review_status": "approved"}
    feedbacks = fleet_scorer(_judge(True))(inputs=inputs, outputs=outputs, expectations=expectations)
    gold = dspy.Example(task="count 5xx", answer="3", expected_values=["3"], route="rlm")
    direct = fleet_metric(gold, dspy.Prediction(**outputs), judge_lm=_judge(True))
    by_name = {feedback.name: feedback.value for feedback in feedbacks}
    assert by_name.pop("fleet_metric") == direct.score == 0.0
    assert by_name == {f"fleet_metric.{name}": value for name, value in direct.objective_scores.items()}


def test_fleet_metric_satisfies_the_gepa_metric_arity_check():
    dspy.GEPA(metric=partial(fleet_metric, judge_lm=_judge(True)), auto="light", reflection_lm=_judge(True))


def test_efficiency_breaks_correctness_ties_and_missing_usage_is_unavailable():
    gold = dspy.Example(task="sum", answer="4")
    cheap = fleet_metric(gold, dspy.Prediction(answer="4", lm_calls=1, sandbox_errors=0), judge_lm=_judge(True))
    expensive = fleet_metric(gold, dspy.Prediction(answer="4", lm_calls=20, sandbox_errors=0), judge_lm=_judge(True))
    wrong = fleet_metric(gold, dspy.Prediction(answer="5", lm_calls=0, sandbox_errors=0), judge_lm=_judge(False))
    unknown = fleet_metric(gold, dspy.Prediction(answer="4"), judge_lm=_judge(True))
    assert cheap.score > expensive.score > wrong.score == 0
    assert expensive.objective_scores["quality"] == 1.0
    assert unknown.objective_scores["usage_available"] == 0.0 and "efficiency" not in unknown.objective_scores


def test_scorer_suite_adds_the_fleet_metric_only_with_a_dspy_judge():
    assert "fleet_metric" not in [getattr(item, "name", "") for item in build_mlflow_scorer_suite("openai:/j")]
    suite = build_mlflow_scorer_suite("openai:/j", judge_lm=_judge(True))
    assert suite[-1].name == "fleet_metric"
