from types import SimpleNamespace

from mlflow.entities import SpanType

from fleet_rlm.optimize.scorers import (
    build_mlflow_scorer_suite,
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
    assert rlm_trajectory_efficiency(trace=None).value is None
    assert jev_routing_efficiency(trace=None).value is None


def test_route_correctness_matches_gold_label():
    assert route_correctness(outputs={"route": "rlm"}, expectations={"route": "rlm"}).value is True
    assert route_correctness(outputs={"route": "direct"}, expectations={"route": "rlm"}).value is False
    assert route_correctness(outputs={"route": "direct"}, expectations={}).value is None
    # Gold labels and traces written before the rename still score.
    assert route_correctness(outputs={"route": "rlm"}, expectations={"route": "deep_rlm"}).value is True
    assert route_correctness(outputs={"route": "flex_pipeline"}, expectations={"route": "flex"}).value is True


def test_scorer_suite_includes_route_correctness():
    suite = build_mlflow_scorer_suite("databricks:/test-judge")
    assert route_correctness in suite


def test_structured_execution_error_is_counted_once():
    from mlflow.entities import SpanStatusCode

    execution = _span("sandbox.exec", SpanType.TOOL, {"error": True, "stderr": "Traceback"})
    execution.attributes = {"outcome": "remote_code_error"}
    execution.status = SimpleNamespace(status_code=SpanStatusCode.ERROR)
    feedback = rlm_trajectory_efficiency(trace=_trace(execution))
    assert feedback.value == 0.85
    assert "1 sandbox exceptions" in feedback.rationale
