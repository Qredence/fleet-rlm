"""Scoring objectives, execution evidence, judge contracts, and scorer registration."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import dspy
import pytest
from dspy.utils import DummyLM
from mlflow.entities import SpanType

from fleet_rlm.execution_evidence import validate_structured, verification_entries
from fleet_rlm.optimize.objectives import (
    aggregate,
    artifact_correctness,
    delegation_correctness,
    efficiency,
    efficiency_metric,
    expected_values_metric,
    missing_values,
    route_metric,
    routing_efficiency,
)
from fleet_rlm.optimize.scorers import (
    JEV_ROUTE_THRESHOLD,
    TRACE_ASSESSMENT_VERSION,
    ScorerEvidenceError,
    build_mlflow_scorer_suite,
    execution_evidence_diagnostics,
    expected_values_present,
    fleet_metric,
    fleet_scorer,
    jev_routing_efficiency,
    rlm_trajectory_efficiency,
    route_correctness,
)

# Judge and diagnostic contracts


def _span(
    name: str,
    span_type: str = SpanType.CHAIN,
    outputs: object = None,
    *,
    inputs: object = None,
    span_id: str = "",
    parent_id: str | None = None,
    attributes: dict | None = None,
    status: object = None,
):
    return SimpleNamespace(
        name=name,
        span_type=span_type,
        inputs=inputs,
        outputs=outputs,
        span_id=span_id,
        parent_id=parent_id,
        attributes=attributes or {},
        status=status,
    )


def _trace(*spans):
    return SimpleNamespace(search_spans=lambda: list(spans))


def _execution_status(name: str = "OK"):
    return SimpleNamespace(status_code=SimpleNamespace(name=name))


def _judge_responses(monkeypatch, responses):
    class Calls(list):
        definitions: dict[str, dict]

    calls = Calls()
    calls.definitions = {}

    def make_judge(name, **_kwargs):
        calls.definitions[name] = _kwargs

        def judge(**context):
            calls.append((name, context))
            value, rationale = responses[name]
            return SimpleNamespace(value=value, rationale=rationale)

        return judge

    monkeypatch.setattr("fleet_rlm.optimize.scorers.make_judge", make_judge)
    return calls


def test_budget_units_come_from_host_outputs_not_wrapper_spans():
    # A span merely named "LMStudioAdapter" must not count as an LM call.
    trace = _trace(
        _span("fleet_turn", outputs={"lm_calls": 1}), _span("LMStudioAdapter"), _span("ChatCompletion", SpanType.LLM)
    )
    assert jev_routing_efficiency(trace=trace).value == 1.0
    assert "1 budget units" in jev_routing_efficiency(trace=trace).rationale


def test_flex_route_is_bounded_and_heavy_route_degrades():
    bounded = _trace(
        _span("fleet_turn", outputs={"lm_calls": 10}), _span("Flex.forward"), *[_span("lm", SpanType.LLM)] * 10
    )
    assert jev_routing_efficiency(trace=bounded).value == 0.95
    heavy = _trace(
        _span("fleet_turn", outputs={"lm_calls": 25}), _span("RLM.forward"), *[_span("lm", SpanType.LLM)] * 25
    )
    assert jev_routing_efficiency(trace=heavy).value == 0.75


def test_trajectory_penalizes_host_usage_without_counting_wrapper_errors():
    trace = _trace(
        _span("fleet_turn", outputs={"lm_calls": 5, "sandbox_errors": 1, "submit_rejections": 0}),
        *[_span("lm", SpanType.LLM)] * 5,
        _span("sandbox", outputs="Traceback (most recent call last)"),
    )
    assert rlm_trajectory_efficiency(trace=trace).value == 0.82


def test_missing_trace_is_unavailable_not_zero():
    assert rlm_trajectory_efficiency(trace=None) is None
    assert jev_routing_efficiency(trace=None) is None


@pytest.mark.parametrize("root_count", [0, 1, 2])
def test_trace_readers_require_one_host_root_and_ignore_nested_turns(root_count):
    from fleet_rlm.optimize.scorers import ScorerEvidenceError, _trace_evidence_context

    outputs = {"answer": "Done", "lm_calls": 3, "sandbox_errors": 0, "submit_rejections": 0}
    roots = [_span("fleet_turn", outputs=json.dumps(json.dumps(outputs))) for _ in range(root_count)]
    nested = _span("fleet_turn", outputs={"lm_calls": 999}, parent_id="parent")
    trace = _trace(*roots, nested)
    if root_count == 1:
        assert rlm_trajectory_efficiency(trace=trace).value == 1.0
        assert jev_routing_efficiency(trace=trace).value == 0.98
        assert _trace_evidence_context(trace)[0]["answer"] == "Done"
    else:
        assert rlm_trajectory_efficiency(trace=trace) is None
        assert jev_routing_efficiency(trace=trace) is None
        with pytest.raises(ScorerEvidenceError, match=f"found {root_count}"):
            _trace_evidence_context(trace)


def test_route_correctness_matches_gold_label():
    assert route_correctness(outputs={"route": "rlm"}, expectations={"route": "rlm"}).value is True
    assert route_correctness(outputs={"route": "direct"}, expectations={"route": "rlm"}).value is False
    assert route_correctness(outputs={"route": "direct"}, expectations={}) is None


def test_scorer_suite_includes_route_correctness():
    suite = build_mlflow_scorer_suite("databricks:/test-judge")
    names = [item.name for item in suite]
    assert "route_correctness" in names
    assert "jev_routing_efficiency_judge" in names
    assert "jev_routing_efficiency" in names
    assert "execution_evidence_diagnostics" in names
    assert "stopping_discipline" in names
    assert "output_instruction_compliance" in names
    assert "agent_skills_progressive_disclosure" in names


def _route_trace(**overrides):
    outputs = {"answer": "Done", "route": "flex", "lm_calls": 4, "sandbox_errors": 0, "submit_rejections": 0}
    outputs.update(overrides)
    return _trace(_span("fleet_turn", inputs={"task": "Inspect the file"}, outputs=outputs))


def _route_judge(jev_lm="jev-sentinel"):
    return {item.name: item for item in build_mlflow_scorer_suite("databricks:/test-judge", jev_lm=jev_lm)}[
        "jev_routing_efficiency_judge"
    ]


def _scorer(name: str, judge_model: str = "openai:/judge"):
    return next(item for item in build_mlflow_scorer_suite(judge_model) if item.name == name)


def _standard_judge_responses():
    return {
        "execution_evidence_diagnostics": (_execution_judge_payload(), "structured result"),
        "jev_routing_efficiency_judge": (True, "Flex was suitable."),
        "output_instruction_compliance": (True, "The answer followed the format."),
        "agent_skills_progressive_disclosure": (True, "Skill activity was reviewed."),
    }


@pytest.mark.parametrize("probability", [0.83, 0.4999, 0.5001])
def test_jev_route_judge_maps_probability_to_verdict_and_records_evidence(monkeypatch, probability):
    calls = []

    def judgment(jev_lm, state):
        calls.append((jev_lm, state))
        return {
            "reasonable": probability >= JEV_ROUTE_THRESHOLD,
            "probability": probability,
            "confidence": 0.66,
            "best_route": "flex",
            "best_route_probabilities": {"direct": 0.1, "flex": 0.71, "rlm": 0.19},
        }

    monkeypatch.setattr("fleet_rlm.optimize.scorers._jev_route_judgment", judgment)
    feedback = _route_judge()(trace=_route_trace())
    assert feedback.value is (probability >= JEV_ROUTE_THRESHOLD)
    assert calls == [
        ("jev-sentinel", {"task": "Inspect the file", "route": "flex", "lm_calls": 4, "sandbox_errors": 0})
    ]
    assert feedback.metadata["fleet.judge_backend"] == "typesafe"
    assert float(feedback.metadata["fleet.jev.route_probability"]) == probability
    assert feedback.metadata["fleet.trace_scorer_version"] == "fleet-trace-assessments-v9"
    assert feedback.metadata["mlflow.assessment.scorerVersion"] == "fleet-trace-assessments-v9"
    assert feedback.metadata["fleet.jev.best_route"] == "flex"
    assert feedback.metadata["fleet.jev.rubric_version"] == "jev-route-v3"
    assert f"p(route reasonable)={probability:.2f}" in feedback.rationale


def test_jev_route_judge_rejects_missing_usage_without_calling_jev(monkeypatch):
    calls = []
    monkeypatch.setattr("fleet_rlm.optimize.scorers._jev_route_judgment", lambda *args: calls.append(args))
    feedback = _route_judge()(trace=_route_trace(lm_calls=None))
    assert feedback.value is False
    assert "cannot be established" in feedback.rationale
    assert calls == []


def test_jev_route_judge_reports_unreported_probability(monkeypatch):
    monkeypatch.setattr(
        "fleet_rlm.optimize.scorers._jev_route_judgment",
        lambda *_args: {
            "reasonable": False,
            "probability": None,
            "confidence": None,
            "best_route": "rlm",
            "best_route_probabilities": None,
        },
    )
    feedback = _route_judge()(trace=_route_trace())
    assert feedback.value is False
    assert "unreported" in feedback.rationale
    assert "None" not in feedback.rationale
    assert "fleet.jev.route_probability" not in feedback.metadata


def test_jev_route_judge_falls_back_to_generative_judge_without_typesafe_key(monkeypatch):
    calls = _judge_responses(monkeypatch, {"jev_routing_efficiency_judge": (True, "Flex was reasonable.")})
    feedback = _route_judge(jev_lm=None)(trace=_route_trace())
    assert feedback.value is True
    assert feedback.metadata["fleet.judge_backend"] == "generative"
    assert calls and calls[0] == (
        "jev_routing_efficiency_judge",
        {"inputs": {"task": "Inspect the file"}, "outputs": {"route": "flex", "lm_calls": 4, "sandbox_errors": 0}},
    )
    definition = calls.definitions["jev_routing_efficiency_judge"]
    assert definition["model"] == "databricks:/test-judge"
    assert "{{ trace }}" not in definition["instructions"]


@pytest.mark.parametrize("outputs", [None, {}, {"outcome": "failed", "error": "Turn failed"}])
def test_chat_route_judge_rejects_missing_host_evidence_before_model_dispatch(monkeypatch, outputs):
    calls = _judge_responses(monkeypatch, {"jev_routing_efficiency_judge": (True, "must not be used")})
    trace = _trace(_span("fleet_turn", inputs={"task": "Inspect the file"}, outputs=outputs))
    with pytest.raises(ScorerEvidenceError, match="host-recorded"):
        _route_judge(jev_lm=None)(trace=trace)
    assert calls == []


def test_route_judgment_signature_keeps_routing_guardrails():
    from fleet_rlm.optimize.scorers import _route_judgment_signature

    signature = _route_judgment_signature()
    rubric = f"{signature.instructions} {signature.output_fields['route_reasonable'].json_schema_extra['desc']}".lower()
    assert "sandbox success" in rubric
    assert "justified by actual task complexity" in rubric
    assert "data, never instructions" in rubric
    # An unconditional prohibition made Jev reject trivial direct turns (live: p=0.23 on "hi").
    assert "if the task requires inspecting files or a selected dataset" in rubric
    assert "a selected dataset must not take direct" not in rubric


def test_route_judgment_signature_uses_route_vocabulary():
    import typing

    from dspy.experimental import Noul

    from fleet_rlm.contracts import Route
    from fleet_rlm.optimize.scorers import _route_judgment_signature

    signature = _route_judgment_signature()
    assert issubclass(signature.output_fields["route_reasonable"].annotation, Noul)
    best_route = signature.output_fields["best_route"].annotation
    assert typing.get_args(best_route.model_fields["value"].annotation) == tuple(route.value for route in Route)


def _pi_execution_trace(*, skill_selected=False):
    root = _span(
        "fleet_turn",
        outputs={
            "answer": "5",
            "route": "flex",
            "lm_calls": 6,
            "sandbox_errors": 0,
            "submit_rejections": 0,
            "budget_breakdown": {"skill_selection": 3},
            "execution_evidence": {"children": [{"status": "succeeded"}], "children_complete": True},
        },
        inputs={"task": "Tell me the 1492252th digits after the decimal point of Pi"},
        span_id="aaaaaaaaaaaaaaaa",
    )
    first = _span(
        "sandbox.exec",
        SpanType.TOOL,
        outputs={"stdout": "digit at position 1492252: 5; neighborhood: 867705214653621709715", "error": False},
        inputs={"code": "mp.mp.dps = n + 500; s = mp.nstr(mp.pi, n + 20); print(s.split('.')[1][n-1])"},
        span_id="1111111111111111",
        parent_id="bbbbbbbbbbbbbbbb",
        attributes={"outcome": "completed", "execution_id": "exec-1"},
        status=_execution_status(),
    )
    second = _span(
        "sandbox.exec",
        SpanType.TOOL,
        outputs={"stdout": "digit check 2: 5; matches step 1: True", "error": False},
        inputs={"code": "mp.mp.dps = n + 1000; s = mp.nstr(mp.pi, n + 20); print(s.split('.')[1][n-1])"},
        span_id="2222222222222222",
        parent_id="bbbbbbbbbbbbbbbb",
        attributes={"outcome": "completed", "execution_id": "exec-2"},
        status=_execution_status(),
    )
    skill = _span(
        "typesafe_skill_selection",
        outputs={"selected": ["calculation"] if skill_selected else [], "decisions": []},
        inputs={"task": "calculate Pi digit"},
        span_id="cccccccccccccccc",
        parent_id="bbbbbbbbbbbbbbbb",
    )
    return _trace(root, _span("Flex.forward"), first, second, skill)


def _execution_judge_payload(
    *,
    verification_applicable=True,
    verification_pass=True,
    integrity_pass=True,
    verification_rationale="Computed digit is supported by span_id=1111111111111111.",
    integrity_rationale="Both executions support the submitted digit; span_id=2222222222222222.",
):
    return json.dumps(
        {
            "verification_applicable": verification_applicable,
            "verification_pass": verification_pass,
            "verification_rationale": verification_rationale,
            "integrity_pass": integrity_pass,
            "integrity_rationale": integrity_rationale,
        }
    )


@pytest.mark.parametrize("rationale", ["No issues found.", "The agent used tools correctly.", "placeholder rationale"])
def test_execution_weak_rationales_cannot_support_a_verdict(monkeypatch, rationale):
    _judge_responses(
        monkeypatch,
        {
            "execution_evidence_diagnostics": (
                _execution_judge_payload(verification_rationale=rationale, integrity_rationale=rationale),
                "structured",
            )
        },
    )
    with pytest.raises(ScorerEvidenceError, match=r"citing|placeholder"):
        _scorer("execution_evidence_diagnostics")(trace=_pi_execution_trace())


def test_execution_diagnostics_reject_missing_task_and_unsupported_passing_receipts():
    from fleet_rlm.optimize.scorers import diagnose_execution_evidence

    def judge(**_kwargs):
        return SimpleNamespace(value=_execution_judge_payload())

    with pytest.raises(ScorerEvidenceError, match="task and answer"):
        diagnose_execution_evidence(_trace(_span("fleet_turn")), judge, judge_model="offline")
    no_receipts = _trace(_span("fleet_turn", inputs={"task": "Compute Pi"}, outputs={"answer": "5"}))
    with pytest.raises(ScorerEvidenceError, match="requires sandbox execution receipts"):
        diagnose_execution_evidence(no_receipts, judge, judge_model="offline")


def test_passing_verification_cannot_cite_only_failed_receipts():
    from fleet_rlm.optimize.scorers import diagnose_execution_evidence

    trace = _pi_execution_trace()
    for span in trace.search_spans():
        if span.name == "sandbox.exec":
            span.status = _execution_status("ERROR")
            span.attributes["outcome"] = "remote_code_error"
    with pytest.raises(ScorerEvidenceError, match="successful execution receipt"):
        diagnose_execution_evidence(
            trace, lambda **_kw: SimpleNamespace(value=_execution_judge_payload()), judge_model="offline"
        )


@pytest.mark.parametrize("passed", [True, False])
def test_native_boolean_map_uses_the_same_strict_evidence_validator(passed):
    from fleet_rlm.optimize.scorers import diagnose_execution_evidence, make_execution_evidence_judge

    # The pinned MLflow constructor must accept this schema without contacting a provider.
    assert make_execution_evidence_judge("openai:/offline") is not None
    result = SimpleNamespace(
        value={"verification_applicable": True, "verification_pass": passed, "integrity_pass": passed},
        rationale="Verification and tool claims are checked against span_id=1111111111111111.",
    )
    feedback = diagnose_execution_evidence(_pi_execution_trace(), lambda **_kw: result, judge_model="offline")
    assert [item.value for item in feedback] == [passed, passed]
    assert all(item.rationale == result.rationale for item in feedback)


@pytest.mark.parametrize("bad_value", ["true", 1, None])
def test_native_boolean_map_rejects_non_boolean_values(bad_value):
    from fleet_rlm.optimize.scorers import diagnose_execution_evidence

    result = SimpleNamespace(
        value={"verification_applicable": True, "verification_pass": bad_value, "integrity_pass": True},
        rationale="Verification and tool claims cite span_id=1111111111111111.",
    )
    with pytest.raises(ScorerEvidenceError, match="non-boolean"):
        diagnose_execution_evidence(_pi_execution_trace(), lambda **_kw: result, judge_model="offline")


@pytest.mark.parametrize("rationale", ["No problems found.", "Placeholder", "span_id=ffffffffffffffff is sufficient."])
def test_native_boolean_map_still_rejects_weak_or_fabricated_citations(rationale):
    from fleet_rlm.optimize.scorers import diagnose_execution_evidence

    result = SimpleNamespace(
        value={"verification_applicable": True, "verification_pass": True, "integrity_pass": True}, rationale=rationale
    )
    with pytest.raises(ScorerEvidenceError):
        diagnose_execution_evidence(_pi_execution_trace(), lambda **_kw: result, judge_model="offline")


@pytest.mark.parametrize("target", [None, "m-original-agent"])
def test_execution_evaluation_keeps_original_target_identity(monkeypatch, target):
    from fleet_rlm.optimize.scorers import diagnose_execution_evidence

    trace = _pi_execution_trace()
    trace.info = SimpleNamespace(trace_metadata={"mlflow.modelId": target} if target else {})
    monkeypatch.setattr("mlflow.active_run", lambda: SimpleNamespace(info=SimpleNamespace(run_id="eval-new")))
    monkeypatch.setattr("mlflow.update_current_trace", lambda **_kw: pytest.fail("Historical agent was relabeled"))
    result = diagnose_execution_evidence(
        trace, lambda **_kw: SimpleNamespace(value=_execution_judge_payload()), judge_model="openai:/judge"
    )
    for feedback in result:
        assert feedback.metadata["fleet.target_agent_model_id"] == (target or "unversioned")
        assert feedback.metadata["fleet.evaluation.run_id"] == "eval-new"
        assert feedback.metadata["fleet.diagnostic.judge_model"] == "openai:/judge"
        assert len(feedback.metadata["fleet.diagnostic.code_digest"]) == 64


def test_execution_assessments_use_bounded_sandbox_evidence_and_version(monkeypatch):
    calls = _judge_responses(
        monkeypatch,
        {
            "execution_evidence_diagnostics": (_execution_judge_payload(), "structured result"),
            "jev_routing_efficiency_judge": (True, "Flex was reasonable for this computation."),
            "output_instruction_compliance": (True, "The answer contains only the requested digit."),
            "agent_skills_progressive_disclosure": (True, "Selected Skill evidence was reviewed."),
        },
    )
    execution_scorer = _scorer("execution_evidence_diagnostics")
    feedback = execution_scorer(trace=_pi_execution_trace())
    assert {item.name: item.value for item in feedback} == {
        "sandbox_verification_discipline": True,
        "tool_execution_integrity": True,
    }
    assert all(item.metadata["fleet.trace_scorer_version"] == TRACE_ASSESSMENT_VERSION for item in feedback)
    execution_call = next(context for name, context in calls if name == "execution_evidence_diagnostics")
    evidence = execution_call["inputs"]["sandbox_exec_spans"]
    assert [item["span_id"] for item in evidence] == ["1111111111111111", "2222222222222222"]
    assert "n + 500" in evidence[0]["code"] and "n + 1000" in evidence[1]["code"]
    assert "independent verification" in calls.definitions["execution_evidence_diagnostics"]["instructions"].lower()


def test_execution_evidence_context_redacts_and_bounds_code_and_output():
    from fleet_rlm.optimize.scorers import _trace_evidence_context

    trace = _pi_execution_trace()
    spans = trace.search_spans()
    execution = next(span for span in spans if span.name == "sandbox.exec")
    execution.inputs = {"code": "API_KEY=private-value\n" + "x" * 5000}
    execution.outputs = {"stdout": "start" + "y" * 5000 + "end", "error": False}
    payload, _ = _trace_evidence_context(trace)
    evidence = payload["sandbox_exec_spans"][0]
    assert "private-value" not in evidence["code"]
    assert len(evidence["code"]) <= 1600
    assert len(evidence["stdout"]) <= 1600


@pytest.mark.parametrize("value", [(12, 3), {"answer": "36", "api_key": "private-value"}, 0, False])
def test_execution_judge_receives_return_values_without_stdout(monkeypatch, value):
    calls = _judge_responses(
        monkeypatch,
        {"execution_evidence_diagnostics": (_execution_judge_payload(), "structured result")},
    )
    trace = _pi_execution_trace()
    execution = next(span for span in trace.search_spans() if span.name == "sandbox.exec")
    execution.outputs = {"stdout": "", "value": value, "error": False}
    judge = _scorer("execution_evidence_diagnostics")
    judge(trace=trace)
    evidence = calls[0][1]["inputs"]["sandbox_exec_spans"][0]
    assert evidence["stdout"] == ""
    assert evidence["value"]
    assert "private-value" not in evidence["value"]
    if isinstance(value, dict):
        assert json.loads(evidence["value"])["answer"] == "36"
    else:
        assert json.loads(evidence["value"]) == (list(value) if isinstance(value, tuple) else value)


def test_execution_return_value_is_bounded_and_redacted():
    from fleet_rlm.optimize.scorers import _trace_evidence_context

    trace = _pi_execution_trace()
    execution = next(span for span in trace.search_spans() if span.name == "sandbox.exec")
    execution.outputs = {"stdout": "", "value": "API_KEY=private-value\n" + "x" * 5000 + "end"}
    payload, _ = _trace_evidence_context(trace)
    value = payload["sandbox_exec_spans"][0]["value"]
    assert "private-value" not in value
    assert len(value) <= 1600
    assert value.endswith("end")


def test_registered_suite_emits_each_trace_rubric_once_with_version_metadata(monkeypatch):
    responses = _standard_judge_responses()
    _judge_responses(monkeypatch, responses)
    suite = build_mlflow_scorer_suite("openai:/judge")
    trace = _pi_execution_trace(skill_selected=True)
    scorers_by_name = {item.name: item for item in suite}
    feedback = [
        scorers_by_name["jev_routing_efficiency_judge"](trace=trace),
        *scorers_by_name["execution_evidence_diagnostics"](trace=trace),
        scorers_by_name["output_instruction_compliance"](trace=trace),
        scorers_by_name["agent_skills_progressive_disclosure"](trace=trace),
    ]
    names = [item.name for item in feedback]
    assert len(names) == len(set(names)) == 5
    assert set(names) == {
        "jev_routing_efficiency_judge",
        "sandbox_verification_discipline",
        "tool_execution_integrity",
        "output_instruction_compliance",
        "agent_skills_progressive_disclosure",
    }
    for item in feedback:
        assert item.metadata["mlflow.assessment.scorerName"] == item.name
        assert item.metadata["mlflow.assessment.scorerVersion"] == "fleet-trace-assessments-v9"


def test_execution_assessments_preserve_judge_source_and_count_tokens_once(monkeypatch):
    from mlflow.entities import AssessmentSource, AssessmentSourceType, Feedback

    judge_source = AssessmentSource(source_type=AssessmentSourceType.LLM_JUDGE, source_id="openai:/judge")

    def make_judge(name, **_kwargs):
        if name == "execution_evidence_diagnostics":
            return lambda **_context: Feedback(
                value=_execution_judge_payload(),
                rationale="Structured execution result.",
                source=judge_source,
                metadata={
                    "mlflow.assessment.judgeInputTokens": "100",
                    "mlflow.assessment.judgeOutputTokens": "30",
                },
            )
        return lambda **_context: Feedback(
            value=True,
            rationale="Judge result.",
            source=judge_source,
        )

    monkeypatch.setattr("fleet_rlm.optimize.scorers.make_judge", make_judge)
    execution_scorer = _scorer("execution_evidence_diagnostics")
    verification, integrity = execution_scorer(trace=_pi_execution_trace())
    assert verification.source.source_type == AssessmentSourceType.LLM_JUDGE
    assert verification.source.source_id == "openai:/judge"
    assert verification.metadata["mlflow.assessment.scorerName"] == "sandbox_verification_discipline"
    assert verification.metadata["mlflow.assessment.scorerVersion"] == "fleet-trace-assessments-v9"
    assert verification.metadata["mlflow.assessment.judgeInputTokens"] == "100"
    assert integrity.source.source_id == "openai:/judge"
    assert integrity.metadata["mlflow.assessment.scorerName"] == "tool_execution_integrity"
    assert "mlflow.assessment.judgeInputTokens" not in integrity.metadata


def test_missing_execution_fails_required_verification_and_direct_answers_are_not_applicable(monkeypatch):
    responses = {
        "execution_evidence_diagnostics": (
            _execution_judge_payload(
                verification_pass=False,
                integrity_pass=False,
                verification_rationale="No sandbox.exec spans were present for the requested computation.",
                integrity_rationale="The computed answer has no execution receipt to support it.",
            ),
            "structured result",
        ),
        "jev_routing_efficiency_judge": (True, "Direct was reasonable for this simple question."),
        "output_instruction_compliance": (True, "The answer follows the requested format."),
        "agent_skills_progressive_disclosure": (True, "No Skill activity was selected."),
    }
    _judge_responses(monkeypatch, responses)
    execution_scorer = _scorer("execution_evidence_diagnostics")
    computation = _trace(
        _span("fleet_turn", inputs={"task": "Compute a large Pi digit"}, outputs={"answer": "5"}, span_id="a" * 16)
    )
    feedback = execution_scorer(trace=computation)
    assert {item.name: item.value for item in feedback} == {
        "sandbox_verification_discipline": False,
        "tool_execution_integrity": False,
    }

    direct = _trace(_span("fleet_turn", inputs={"task": "Say hello"}, outputs={"answer": "Hello"}, span_id="d" * 16))
    responses["execution_evidence_diagnostics"] = (
        _execution_judge_payload(
            verification_applicable=False,
            verification_pass=True,
            integrity_pass=True,
            verification_rationale="Not applicable: this conversational answer requires no execution.",
            integrity_rationale="No execution claim was made, so no tool receipt is required.",
        ),
        "structured result",
    )
    feedback = execution_scorer(trace=direct)
    assert {item.name: item.value for item in feedback} == {
        "sandbox_verification_discipline": True,
        "tool_execution_integrity": True,
    }


def test_failed_sandbox_span_is_visible_and_can_be_cited_for_a_negative_assessment(monkeypatch):
    failed_id = "3333333333333333"
    _judge_responses(
        monkeypatch,
        {
            "execution_evidence_diagnostics": (
                _execution_judge_payload(
                    verification_pass=False,
                    integrity_pass=False,
                    verification_rationale=f"The sandbox execution failed; span_id={failed_id}.",
                    integrity_rationale=f"No successful receipt supports the answer; span_id={failed_id}.",
                ),
                "structured result",
            ),
            "jev_routing_efficiency_judge": (True, "Flex was appropriate for the calculation."),
            "output_instruction_compliance": (True, "The requested format was followed."),
            "agent_skills_progressive_disclosure": (True, "Skill activity was reviewed."),
        },
    )
    execution_scorer = _scorer("execution_evidence_diagnostics")
    failed = _span(
        "sandbox.exec",
        SpanType.TOOL,
        outputs={"stdout": "", "stderr": "RuntimeError", "error": True},
        inputs={"code": "raise RuntimeError()"},
        span_id=failed_id,
        parent_id="bbbbbbbbbbbbbbbb",
        attributes={"outcome": "remote_code_error", "execution_id": "exec-failed"},
        status=_execution_status("ERROR"),
    )
    root = _span(
        "fleet_turn",
        inputs={"task": "Compute a value"},
        outputs={"answer": "5", "sandbox_errors": 1},
        span_id="aaaaaaaaaaaaaaaa",
    )
    feedback = execution_scorer(trace=_trace(root, failed))
    assert [item.value for item in feedback] == [False, False]


def test_truncated_execution_context_fails_scoring_instead_of_judging_partial_evidence(monkeypatch):
    calls = _judge_responses(
        monkeypatch,
        {
            "execution_evidence_diagnostics": (_execution_judge_payload(), "structured result"),
            "jev_routing_efficiency_judge": (True, "Flex was suitable."),
            "output_instruction_compliance": (True, "The answer followed the format."),
            "agent_skills_progressive_disclosure": (True, "Skill activity was reviewed."),
        },
    )
    execution_scorer = _scorer("execution_evidence_diagnostics")
    root = _span("fleet_turn", inputs={"task": "Compute a value"}, outputs={"answer": "5"}, span_id="a" * 16)
    execution_spans = [
        _span(
            "sandbox.exec",
            SpanType.TOOL,
            outputs={"stdout": str(index), "error": False},
            inputs={"code": f"print({index})"},
            span_id=f"{index:016x}",
            parent_id="b" * 16,
            attributes={"outcome": "completed"},
            status=_execution_status(),
        )
        for index in range(1, 10)
    ]
    with pytest.raises(ScorerEvidenceError, match="evidence context limit"):
        execution_scorer(trace=_trace(root, *execution_spans))
    assert not any(name == "execution_evidence_diagnostics" for name, _ in calls)


def test_output_compliance_judge_receives_only_request_and_final_answer(monkeypatch):
    calls = _judge_responses(
        monkeypatch,
        {
            "execution_evidence_diagnostics": (_execution_judge_payload(), "structured result"),
            "jev_routing_efficiency_judge": (True, "Flex was suitable."),
            "output_instruction_compliance": (True, "Only the requested digit was returned."),
            "agent_skills_progressive_disclosure": (True, "Skill activity was reviewed."),
        },
    )
    suite = build_mlflow_scorer_suite("openai:/judge")
    output_scorer = next(item for item in suite if item.name == "output_instruction_compliance")
    trace = _pi_execution_trace()
    feedback = output_scorer(trace=trace)
    assert feedback.name == "output_instruction_compliance" and feedback.value is True
    assert feedback.metadata["fleet.trace_scorer_version"] == "fleet-trace-assessments-v9"
    call = next(context for name, context in calls if name == "output_instruction_compliance")
    assert call == {
        "inputs": {"task": "Tell me the 1492252th digits after the decimal point of Pi"},
        "outputs": {"answer": "5"},
    }


def test_output_compliance_preserves_middle_constraints_and_violations(monkeypatch):
    calls = _judge_responses(monkeypatch, {"output_instruction_compliance": (False, "The answer violates the format.")})
    output_scorer = _scorer("output_instruction_compliance")
    task = "a" * 1800 + "Return exactly one line." + "z" * 1800
    answer = "b" * 1800 + "\nEXTRA LINE\n" + "y" * 1800
    result = output_scorer(trace=_trace(_span("fleet_turn", inputs={"task": task}, outputs={"answer": answer})))
    assert result.value is False
    assert calls == [("output_instruction_compliance", {"inputs": {"task": task}, "outputs": {"answer": answer}})]


@pytest.mark.parametrize("field", ["task", "answer"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "x" * 20_001,
        "[trace size limit]",
        "[trace depth limit]",
        "prefix… [truncated]",
        "head\n… [middle truncated] …\ntail",
    ],
)
def test_output_compliance_rejects_incomplete_evidence_without_a_judge_call(monkeypatch, field, value):
    calls = _judge_responses(monkeypatch, {"output_instruction_compliance": (True, "The output is compliant.")})
    output_scorer = _scorer("output_instruction_compliance")
    fields = {"task": "Return one line", "answer": "done", field: value}
    trace = _trace(_span("fleet_turn", inputs={"task": fields["task"]}, outputs={"answer": fields["answer"]}))
    with pytest.raises(ScorerEvidenceError, match=r"complete.*evidence"):
        output_scorer(trace=trace)
    assert calls == []


def test_output_compliance_redacts_complete_fields_and_accepts_the_limit(monkeypatch):
    calls = _judge_responses(monkeypatch, {"output_instruction_compliance": (True, "The output is compliant.")})
    output_scorer = _scorer("output_instruction_compliance")
    output_scorer(
        trace=_trace(
            _span("fleet_turn", inputs={"task": "Use api_key=private-value"}, outputs={"answer": "x" * 20_000})
        )
    )
    [(_, context)] = calls
    assert context["inputs"]["task"] == "Use api_key=[redacted]"
    assert context["outputs"]["answer"] == "x" * 20_000


@pytest.mark.parametrize(
    "rationale, message",
    [
        ("PLACEHOLDER", "placeholder rationale"),
        ("Supported by span_id=ffffffffffffffff.", "absent from the trace"),
        # Verbatim first-step rationale from trace tr-661be20e's online judge: no evidence was cited.
        ("I'll start by inspecting the trace metadata.", "without citing a sandbox span ID"),
    ],
)
def test_execution_assessments_reject_placeholder_and_unknown_span_citations(monkeypatch, rationale, message):
    response = _execution_judge_payload(
        verification_rationale=rationale,
        integrity_rationale="Supported by span_id=1111111111111111.",
    )
    _judge_responses(
        monkeypatch,
        {
            "execution_evidence_diagnostics": (response, "structured result"),
            "jev_routing_efficiency_judge": (True, "Route was reasonable."),
            "output_instruction_compliance": (True, "The format was followed."),
            "agent_skills_progressive_disclosure": (True, "Skill activity was reviewed."),
        },
    )
    execution_scorer = _scorer("execution_evidence_diagnostics")
    with pytest.raises(ScorerEvidenceError, match=message):
        execution_scorer(trace=_pi_execution_trace())


def test_skill_progressive_disclosure_skips_unselected_skills_and_uses_selected_evidence(monkeypatch):
    responses = {
        "execution_evidence_diagnostics": (_execution_judge_payload(), "structured result"),
        "jev_routing_efficiency_judge": (True, "Flex was reasonable."),
        "output_instruction_compliance": (True, "The answer followed the format."),
        "agent_skills_progressive_disclosure": (True, "Selected Skill evidence was reviewed."),
    }
    calls = _judge_responses(monkeypatch, responses)
    skill_scorer = _scorer("agent_skills_progressive_disclosure")
    assert skill_scorer(trace=_pi_execution_trace(skill_selected=False)) is None
    assert not any(name == "agent_skills_progressive_disclosure" for name, _ in calls)
    feedback = skill_scorer(trace=_pi_execution_trace(skill_selected=True))
    assert feedback.value is True
    assert feedback.metadata["fleet.trace_scorer_version"] == "fleet-trace-assessments-v9"
    skill_call = next(context for name, context in calls if name == "agent_skills_progressive_disclosure")
    assert skill_call["inputs"]["skill_activity"][0]["outputs"]


@pytest.mark.parametrize(
    "outputs",
    [{}, {"skill_activity": []}, {"skill_activity": [{"helper": "list_skills", "skill": None, "completed": True}]}],
)
def test_skill_scorer_skips_catalog_listing_and_code_mentions(outputs):
    from fleet_rlm.optimize.scorers import _skill_feedback_scorer

    trace = _trace(
        _span("fleet_turn", inputs={"task": "Process data"}, outputs={"answer": "Done"}),
        _span("sandbox.exec", inputs={"code": "# use_skill('example')"}, outputs=outputs, parent_id="root"),
        _span("skill.activate", parent_id="root"),  # No runtime producer emits this span.
    )
    assert _skill_feedback_scorer(lambda **_kwargs: pytest.fail("inapplicable judge"))(trace=trace) is None


def test_route_suitability_judge_is_separate_from_deterministic_budget_efficiency(monkeypatch):
    calls = _judge_responses(
        monkeypatch,
        {
            "execution_evidence_diagnostics": (_execution_judge_payload(), "structured result"),
            "jev_routing_efficiency_judge": (True, "Flex was suitable for this bounded calculation."),
            "output_instruction_compliance": (True, "The answer followed the format."),
            "agent_skills_progressive_disclosure": (True, "Skill activity was reviewed."),
        },
    )
    route_scorer = _scorer("jev_routing_efficiency_judge")
    trace = _pi_execution_trace()
    assert route_scorer(trace=trace).value is True
    assert jev_routing_efficiency(trace=trace).value == 0.95
    route_call = next(context for name, context in calls if name == "jev_routing_efficiency_judge")
    assert route_call["outputs"] == {"route": "flex", "lm_calls": 6, "sandbox_errors": 0}


def test_uc_wrapped_outputs_preserve_answer_route_and_execution_counts():
    inputs = json.dumps(json.dumps({"task": "Convert temperature"}))
    outputs = json.dumps(
        json.dumps({"answer": "22.2", "route": "flex", "lm_calls": 6, "sandbox_errors": 0, "submit_rejections": 0})
    )
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

    calls = []

    class Judge:
        def __init__(self, **_kwargs):
            pass

        def __call__(self, **kwargs):
            calls.append(kwargs)
            return [Feedback(value=True)]

    monkeypatch.setattr("fleet_rlm.optimize.scorers.RetrievalGroundedness", Judge)
    groundedness = _scorer("retrieval_groundedness")
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
    feedback = rlm_trajectory_efficiency(
        trace=_trace(
            _span("fleet_turn", outputs={"lm_calls": 1, "sandbox_errors": 1, "submit_rejections": 0}), execution
        )
    )
    assert feedback.value == 0.85
    assert "1 remote Python errors" in feedback.rationale


def _judge(is_correct: bool, critique: str = "checked") -> DummyLM:
    return DummyLM([{"plan": "r", "is_correct": is_correct, "is_safe": True, "critique": critique}])


def test_fleet_metric_does_not_let_route_or_efficiency_offset_wrong_answers():
    gold = dspy.Example(task="sum", answer="15,151.50", expected_values=["15151.50", "3787.875"], route="rlm")
    pred = dspy.Prediction(answer="Sum 15,151.50", route="rlm", lm_calls=13, sandbox_errors=1, submit_rejections=0)
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
    judge = DummyLM([{"plan": "r", "is_correct": True, "is_safe": True, "critique": "Correct and complete."}])
    result = fleet_metric(
        dspy.Example(task="2+2"),
        dspy.Prediction(answer="4", lm_calls=1, sandbox_errors=0, submit_rejections=0),
        judge_lm=judge,
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
    outputs = {
        "answer": "5xx: 3",
        "route": "flex",
        "lm_calls": 6,
        "sandbox_errors": 0,
        "submit_rejections": 0,
        "artifacts": [],
    }
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
    cheap = fleet_metric(
        gold, dspy.Prediction(answer="4", lm_calls=1, sandbox_errors=0, submit_rejections=0), judge_lm=_judge(True)
    )
    expensive = fleet_metric(
        gold, dspy.Prediction(answer="4", lm_calls=20, sandbox_errors=0, submit_rejections=0), judge_lm=_judge(True)
    )
    wrong = fleet_metric(
        gold, dspy.Prediction(answer="5", lm_calls=0, sandbox_errors=0, submit_rejections=0), judge_lm=_judge(False)
    )
    unknown = fleet_metric(gold, dspy.Prediction(answer="4"), judge_lm=_judge(True))
    assert cheap.score > expensive.score > wrong.score == 0
    assert expensive.objective_scores["quality"] == 1.0
    assert unknown.objective_scores["usage_available"] == 0.0 and "efficiency" not in unknown.objective_scores


def test_scorer_suite_adds_the_fleet_metric_only_with_a_dspy_judge():
    assert "fleet_metric" not in [getattr(item, "name", "") for item in build_mlflow_scorer_suite("openai:/j")]
    suite = build_mlflow_scorer_suite("openai:/j", judge_lm=_judge(True))
    assert suite[-1].name == "fleet_metric"


def test_should_share_host_recorded_recovery_penalties_with_trace_diagnostics():
    outputs = {"answer": "5", "lm_calls": 10, "sandbox_errors": 1, "submit_rejections": 1}
    trace = _trace(_span("fleet_turn", outputs=outputs), *[_span("wrapper", SpanType.LLM)] * 40)
    result = fleet_metric(dspy.Example(task="digit", answer="5"), dspy.Prediction(**outputs), judge_lm=_judge(True))
    assert result.objective_scores["efficiency"] == 0.595
    assert rlm_trajectory_efficiency(trace=trace).value == 0.595
    assert result.score == pytest.approx(0.9595)


@pytest.mark.parametrize("value", [None, True, -1, 1.5], ids=["missing", "bool", "negative", "fraction"])
def test_should_withhold_efficiency_credit_without_explicit_rejection_evidence(value):
    outputs = {"answer": "5", "lm_calls": 1, "sandbox_errors": 0, "submit_rejections": value}
    result = fleet_metric(dspy.Example(task="digit"), dspy.Prediction(**outputs), judge_lm=_judge(True))
    assert result.objective_scores["usage_available"] == 0
    assert "efficiency" not in result.objective_scores
    assert rlm_trajectory_efficiency(trace=_trace(_span("fleet_turn", outputs=outputs))) is None


@pytest.mark.parametrize("passes", [False, True], ids=["redundant_method", "requested_verification"])
def test_should_judge_stopping_from_execution_receipts_with_task_exceptions(monkeypatch, passes):
    calls = _judge_responses(
        monkeypatch, {"stopping_discipline": (passes, "The computation is in span_id=1111111111111111.")}
    )
    judge = _scorer("stopping_discipline")
    trace = _pi_execution_trace()
    spans = trace.search_spans()
    if passes:
        spans[0].inputs["task"] += "; verify with a second method"
    feedback = judge(trace=trace)
    assert feedback.value is passes
    context = calls[0][1]
    assert context["inputs"]["task"] == spans[0].inputs["task"]
    assert len(context["inputs"]["sandbox_exec_spans"]) == 2
    rules = calls.definitions["stopping_discipline"]["instructions"]
    assert "same code block" in rules
    assert "failed" in rules and "disagree" in rules and "requests verification" in rules
    assert feedback.metadata["fleet.trace_scorer_version"] == "fleet-trace-assessments-v9"


def test_should_refuse_stopping_judgment_when_computation_code_is_truncated(monkeypatch):
    calls = _judge_responses(monkeypatch, {})
    judge = _scorer("stopping_discipline")
    trace = _pi_execution_trace()
    trace.search_spans()[2].inputs["code"] = "x" * 13000
    with pytest.raises(ScorerEvidenceError, match="evidence context limit"):
        judge(trace=trace)
    assert not calls


@pytest.mark.parametrize("scenario", ["redundant_precision", "submit", "failed_check", "requested_verification"])
def test_stopping_review_receives_same_library_checks_in_execution_order(monkeypatch, scenario):
    # These scripted verdicts test evidence transport, not live judge accuracy.
    passes = scenario != "redundant_precision"
    calls = _judge_responses(
        monkeypatch, {"stopping_discipline": (passes, "Check and next action in span_id=2222222222222222.")}
    )
    task = "Compute a specified digit of pi"
    if scenario == "requested_verification":
        task += "; verify by increasing precision"
    first = _span(
        "sandbox.exec",
        span_id="1111111111111111",
        attributes={"phase": "task"},
        inputs={"code": "mp.dps = pos + 100\ns = str(mp.pi)\nprint(s[pos + 1])"},
        outputs={"stdout": "5", "error": False},
        status=_execution_status(),
    )
    check = "print(s[2:52] == known_prefix)\n"
    next_action = (
        "SUBMIT(answer=s[pos + 1], learned_skill_note='')"
        if scenario == "submit"
        else ("mp.dps = pos + 500\ns2 = str(mp.pi)\nprint(s2[pos + 1])")
    )
    second = _span(
        "sandbox.exec",
        span_id="2222222222222222",
        attributes={"phase": "task"},
        inputs={"code": check + next_action},
        outputs={"stdout": "False\n5" if scenario == "failed_check" else "True\n5", "error": False},
        status=_execution_status(),
    )
    first.start_time_ns, second.start_time_ns = 10, 20
    root = _span("fleet_turn", inputs={"task": task}, outputs={"answer": "5"})
    feedback = _scorer("stopping_discipline")(trace=_trace(root, second, first))
    assert feedback.value is passes
    evidence = calls[0][1]["inputs"]["sandbox_exec_spans"]
    assert [record["span_id"] for record in evidence] == [first.span_id, second.span_id]
    assert evidence[1]["code"] == check + next_action
    assert evidence[1]["stdout"] == second.outputs["stdout"]
    assert calls[0][1]["inputs"]["task"] == task
    rules = calls.definitions["stopping_discipline"]["instructions"]
    assert "same code block" in rules
    assert "known leading-digit" in rules and "failed" in rules and "requests verification" in rules


def test_should_preserve_both_methods_in_a_single_execution_for_stopping_review(monkeypatch):
    from pathlib import Path

    fixture = json.loads((Path(__file__).parents[1] / "fixtures/pi_verification_trace.json").read_text())
    calls = _judge_responses(
        monkeypatch, {"stopping_discipline": (False, "Redundant second method in span_id=1111111111111111.")}
    )
    root = _span("fleet_turn", inputs={"task": fixture["task"]}, outputs={"answer": fixture["answer"]})
    computation = _span(
        "sandbox.exec",
        inputs={"code": fixture["code"]},
        outputs={"stdout": fixture["stdout"], "error": False},
        span_id="1111111111111111",
        attributes={"phase": "task"},
        status=_execution_status(),
    )
    bootstrap = _span("sandbox.exec", inputs={"code": "x" * 20000}, attributes={"phase": "bootstrap"})
    judge = _scorer("stopping_discipline")
    assert judge(trace=_trace(root, bootstrap, computation)).value is False
    [evidence] = calls[0][1]["inputs"]["sandbox_exec_spans"]
    assert evidence["code"] == fixture["code"]
    assert "mp.nstr" in evidence["code"] and "def pi_int" in evidence["code"]
    assert evidence["stdout"] == fixture["stdout"]


def test_should_reject_a_stopping_verdict_with_an_unknown_execution_citation(monkeypatch):
    _judge_responses(monkeypatch, {"stopping_discipline": (True, "Verified span_id=9999999999999999.")})
    judge = _scorer("stopping_discipline")
    with pytest.raises(ScorerEvidenceError, match="absent from the trace"):
        judge(trace=_pi_execution_trace())


def test_metric_names_the_program_trace_without_changing_the_score():
    from fleet_rlm.optimize.scorers import SCORER_VERSION

    assert SCORER_VERSION == "fleet-metric-v11"
    gold = dspy.Example(task="Convert 72F", answer="22.2", expected_values=["22.2"], route="flex")
    pred = dspy.Prediction(answer="22.2", route="flex", lm_calls=4, sandbox_errors=0, submit_rejections=0)
    program_trace = [("FlexModule", {"task": "Convert 72F"}, {"answer": "22.2"})]

    with_trace = fleet_metric(gold, pred, program_trace=program_trace, judge_lm=_judge(True))
    without_trace = fleet_metric(gold, pred, judge_lm=_judge(True))

    # GEPA passes program_trace only while optimizing a Flex submodule; evidence must not move the score.
    assert "Program trace: " in with_trace.feedback
    assert "Program trace: " not in without_trace.feedback
    assert with_trace.score == without_trace.score == pytest.approx(0.9 + 0.1 * 0.985)


def test_metric_degrades_on_an_unexpected_program_trace_shape():
    gold = dspy.Example(task="t", answer="a", expected_values=["a"], route="rlm")
    pred = dspy.Prediction(answer="a", route="rlm", lm_calls=5, sandbox_errors=0, submit_rejections=0)
    result = fleet_metric(gold, pred, program_trace=["not-a-step"], judge_lm=_judge(True))
    assert "Program trace: unavailable" in result.feedback


def test_execution_identity_uses_explicit_worker_run_in_scorer_threads(monkeypatch):
    from fleet_rlm.optimize.scorers import execution_diagnostic_identity

    monkeypatch.setattr("mlflow.active_run", lambda: None)
    monkeypatch.setenv("FLEET_EVALUATION_RUN_ID", "worker-evaluation-run")
    assert execution_diagnostic_identity(None, "judge")["fleet.evaluation.run_id"] == "worker-evaluation-run"


# Optimization objectives


@pytest.mark.parametrize("used_rlm", [False, True])
def test_routing_efficiency_never_rewards_more_budget_units(used_rlm):
    scores = [routing_efficiency(used_rlm, calls)[0] for calls in range(0, 40)]
    assert scores == sorted(scores, reverse=True)


def test_routing_efficiency_keeps_its_bands():
    assert routing_efficiency(False, 1) == (1.0, "Fast path completed in 1 budget units.")
    assert routing_efficiency(True, 10) == (0.95, "Bounded RLM/Flex execution used 10 budget units.")


@pytest.mark.parametrize("calls,expected", [(2, 1.0), (3, 0.98), (15, 0.74)])
def test_direct_route_is_penalised_after_its_allowance_instead_of_scoring_one(calls, expected):
    # The previous fall-through scored any non-RLM Turn with 3-15 units as 1.0.
    assert routing_efficiency(False, calls)[0] == pytest.approx(expected)


@pytest.mark.parametrize("used_rlm", [False, True])
def test_routing_efficiency_floors_instead_of_going_negative(used_rlm):
    assert routing_efficiency(used_rlm, 10_000)[0] == 0.2


def test_efficiency_penalties_and_availability():
    assert efficiency(3, 0, 0) == 1.0
    assert efficiency(5, 1, 1) == 0.67
    pred = type("Pred", (), {"lm_calls": 4, "sandbox_errors": 0, "submit_rejections": 1})()
    assert efficiency_metric(pred) == (0.835, "4 budget units, 0 remote Python errors, 1 submission rejections")
    legacy = type("Pred", (), {"lm_calls": 4, "sandbox_errors": 0})()
    assert efficiency_metric(legacy) is None


def test_missing_values_respects_token_boundaries():
    assert missing_values("the total is 120 and 1.25", [12, 120, 1.25]) == [12]


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("12,34", 34),
        ("12,34", 1234),
        ("1,23,456", 23),
    ],
)
def test_partial_group_cannot_yield_a_number(answer, expected):
    """A comma between digits binds them, so a malformed group is not two numbers."""
    assert missing_values(answer, [expected]) == [expected]


@pytest.mark.parametrize(
    "answer,present",
    [
        ("2024-2025", 2024),
        ("2024-2025", 2025),
        ("10-20", 20),
        ("range 3-4.", 4),
    ],
)
def test_hyphenated_ranges_still_yield_both_numbers(answer, present):
    """A hyphen joins two separate numbers; each stays independently matchable."""
    assert missing_values(answer, [present]) == []


def test_unparseable_exponent_is_missing_instead_of_raising():
    """Decimal() raises beyond MAX_EMAX; a number that cannot be parsed cannot match."""
    huge = "1e" + "9" * 19
    assert missing_values(f"the value is {huge}", [1]) == [1]
    assert missing_values("the value is 1", [huge]) == [huge]


@pytest.mark.parametrize(
    "answer,expected,present",
    [
        ("-12", 12, False),
        ("12.5", 12, False),
        ("1.25", 1, False),
        ("+12", 12, True),
        ("-12.0", -12, True),
        ("1.2e1", 12, True),
        ("12e-1", 12, False),
        ("1,200.00", 1200, True),
        ("12,34", 1234, False),
        ("1.000001", 1, False),
        ("identifier12", 12, False),
        ("12kg", 12, False),
        ("0.5", ".5", True),
        ("EXACT wording", "exact", True),
    ],
)
def test_complete_numeric_tokens_and_text(answer, expected, present):
    assert (missing_values(answer, [expected]) == []) is present


def test_expected_values_and_route_metrics():
    gold = type("Gold", (), {"expected_values": ["42"], "route": "rlm"})()
    assert expected_values_metric(gold, "answer 42") == (1.0, "all present")
    assert expected_values_metric(gold, "answer 41") == (0.0, "missing ['42']")
    assert expected_values_metric(type("Gold", (), {"expected_values": []})(), "answer") is None
    assert route_metric(gold, type("Pred", (), {"route": "rlm"})()) == (1.0, "expected rlm, took rlm")
    assert route_metric(type("Gold", (), {"route": None})(), type("Pred", (), {"route": "rlm"})()) is None


def test_aggregate_zero_floor_keeps_efficiency_from_offsetting_errors():
    applied = {"answer_correctness": (0.0, "wrong"), "efficiency": (1.0, "cheap")}
    assert aggregate(applied) == (0.0, 0.0)
    correct = {"answer_correctness": (1.0, "right"), "efficiency": (0.5, "medium")}
    assert aggregate(correct) == pytest.approx((0.95, 1.0))


def test_objectives_stay_free_of_services():
    import inspect

    objectives = inspect.getmodule(aggregate)
    assert objectives is not None
    source = inspect.getsource(objectives)
    assert "mlflow" not in source
    assert "import dspy" not in source
    assert "open(" not in source


# Execution evidence


def artifact(raw: bytes) -> dict:
    return {"sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode()}


@pytest.mark.parametrize(
    "kind,raw,expected,keys",
    [
        ("text", b"North 200", "North 200", []),
        ("json", b'{"north":200,"south":240}', {"north": 200, "south": 240}, []),
        (
            "csv",
            b"region,total\nsouth,240\nnorth,200\n",
            [{"region": "north", "total": 200}, {"region": "south", "total": 240}],
            ["region"],
        ),
    ],
)
def test_artifact_checks_preserve_value_associations(kind, raw, expected, keys):
    gold = {
        "schema_version": 1,
        "artifacts": [{"path": "artifacts/result", "kind": kind, "expected": expected, "key_columns": keys}],
    }
    evidence = {"artifacts": {"complete": True, "files": {"artifacts/result": artifact(raw)}}}
    assert artifact_correctness(gold, evidence)[0] == 1
    evidence["artifacts"]["files"]["artifacts/result"] = artifact(raw.replace(b"200", b"240").replace(b"240", b"999"))
    assert artifact_correctness(gold, evidence)[0] == 0


@pytest.mark.parametrize("path", ["../bad", "files/result", "artifacts/../files/a", "artifacts//a", "/artifacts/a"])
def test_structured_gold_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        validate_structured({"artifacts": [{"path": path, "kind": "text", "expected": "a"}]})


def test_collected_artifacts_follow_the_workspace_limits_not_the_gold_limit():
    """A healthy Turn's output set must not be reported as incomplete evidence."""
    from fleet_rlm.execution_evidence import MAX_ARTIFACTS, MAX_EVIDENCE_BYTES, collect_artifacts

    paths = [f"artifacts/{index:03}.txt" for index in range(MAX_ARTIFACTS - 1)]
    blobs = {**{path: b"a" for path in paths}, "artifacts/large.bin": b"b" * (MAX_EVIDENCE_BYTES * 2)}
    observed = SimpleNamespace(
        list_workspace_files=lambda: list(blobs),
        read_workspace_file=lambda path, **_kwargs: blobs[path],
    )

    result = collect_artifacts(observed)

    assert result["complete"] is True
    assert len(result["files"]) == MAX_ARTIFACTS


def test_collected_artifacts_report_incompleteness_past_the_artifact_count():
    from fleet_rlm.execution_evidence import MAX_ARTIFACTS, collect_artifacts

    blobs = {f"artifacts/{index:03}.txt": b"a" for index in range(MAX_ARTIFACTS + 1)}
    observed = SimpleNamespace(
        list_workspace_files=lambda: list(blobs),
        read_workspace_file=lambda path, **_kwargs: blobs[path],
    )

    assert collect_artifacts(observed)["complete"] is False


def test_structured_gold_keeps_its_own_smaller_size_limit():
    """Raising the observed-artifact bound must not loosen reviewed gold validation."""
    from fleet_rlm.execution_evidence import MAX_EVIDENCE_BYTES

    gold = {"artifacts": [{"path": "artifacts/a.txt", "kind": "text", "expected": "x" * MAX_EVIDENCE_BYTES}]}
    with pytest.raises(ValueError, match="size limit"):
        validate_structured(gold)


def test_python_requires_execution_receipt_matching_observed_bytes():
    gold = {"artifacts": [{"path": "artifacts/report.py", "kind": "python", "execute": True, "stdout": "42\n"}]}
    row = artifact(b"print(42)")
    evidence = {
        "artifacts": {"complete": True, "files": {"artifacts/report.py": row}},
        "python": {
            "artifacts/report.py": {"complete": True, "sha256": row["sha256"], "exit_code": 0, "stdout": "42\n"}
        },
    }
    assert artifact_correctness(gold, evidence)[0] == 1
    evidence["python"]["artifacts/report.py"]["sha256"] = "different"
    assert artifact_correctness(gold, evidence)[0] == 0
    assert verification_entries(gold) == [{"path": "artifacts/report.py", "timeout_seconds": 30}]
    assert "expected" not in verification_entries(gold)[0]


@pytest.mark.parametrize("failure", ["count", "failed", "inputs", "digest", "incomplete"])
def test_delegation_checks_host_receipts(failure):
    gold = {"children": [{"input_paths": ["files/sales.csv"], "artifacts": ["totals.json"]}]}
    evidence = {
        "children_complete": True,
        "children": [
            {
                "status": "succeeded",
                "inputs": {"files/sales.csv": "inputhash"},
                "artifacts": [{"path": "artifacts/random-id/totals.json", "sha256": "abc"}],
            }
        ],
        "artifacts": {"files": {"artifacts/random-id/totals.json": {"sha256": "abc"}}},
    }
    assert delegation_correctness(gold, evidence)[0] == 1
    if failure == "count":
        evidence["children"] *= 2
    elif failure == "failed":
        evidence["children"][0]["status"] = "failed"
    elif failure == "inputs":
        evidence["children"][0]["inputs"]["files/private.csv"] = "extra"
    elif failure == "digest":
        evidence["artifacts"]["files"]["artifacts/random-id/totals.json"]["sha256"] = "changed"
    else:
        evidence["children_complete"] = False
    assert delegation_correctness(gold, evidence)[0] == 0


def test_missing_artifact_zeroes_both_dspy_and_mlflow_quality(monkeypatch):
    from fleet_rlm.optimize import scorers

    monkeypatch.setattr(scorers, "answer_correctness", lambda *_args: (1.0, "correct"))
    gold = dspy.Example(
        task="produce report",
        answer="42",
        structured_expectations={"artifacts": [{"path": "artifacts/report.txt", "kind": "text", "expected": "42"}]},
    )
    prediction = dspy.Prediction(answer="42", lm_calls=1, sandbox_errors=0, execution_evidence={})
    result = fleet_metric(gold, prediction, judge_lm=object())
    assert result.score == 0 and result.objective_scores["artifact_correctness"] == 0
    feedback = fleet_scorer(object())(
        inputs={"task": gold.task},
        outputs=prediction.toDict(),
        expectations={"expected_answer": gold.answer, "structured_expectations": gold.structured_expectations},
    )
    assert next(row.value for row in feedback if row.name == "fleet_metric") == result.score


def test_route_scorer_fails_when_a_gold_route_has_no_observed_route():
    assert route_correctness(outputs={"route": "flex"}, expectations={"route": "flex"}).value is True
    assert route_correctness(outputs={}, expectations={"route": "flex"}).value is False


def test_encoded_artifacts_are_redacted_before_export():
    from fleet_rlm.observability import trace_value

    receipt = artifact(b'api_key="very-secret-value"')
    cleaned = trace_value(receipt, nodes=[0])
    assert "base64" not in cleaned
    assert "very-secret-value" not in json.dumps(cleaned)
    assert "redacted" in cleaned["text"]


def test_python_verification_runs_only_via_daytona_transport(monkeypatch):
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    manager = DaytonaSandboxManager("key", "org", "volume", "verify")
    commands = []
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda *_args, **_kw: b"print(42)")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())

    def remote(_self, _sandbox, code, *, timeout, retry):
        assert retry is False
        commands.append((code, timeout))
        return json.dumps({"complete": True, "exit_code": 0, "stdout": "42\n"})

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", remote)
    manager.reset_turn_budget()
    try:
        result = manager.verify_python_artifact("artifacts/result.py", 30)
    finally:
        manager.end_turn()
    assert result["complete"] and result["stdout"] == "42\n"
    assert "subprocess.Popen" in commands[0][0] and "'/workspace/artifacts/result.py'" not in commands[0][0]
    assert commands[0][1] <= 31


def test_structured_labels_change_dataset_digest_without_entering_inputs():
    from fleet_rlm.optimize.dataset import dataset_digest

    example = dspy.Example(task="compute", structured_expectations={}).with_inputs("task")
    original = dataset_digest({"train": [example]})
    example.structured_expectations = {"artifacts": [{"path": "artifacts/a.txt", "kind": "text", "expected": "42"}]}
    assert dataset_digest({"train": [example]}) != original
    assert example.inputs().toDict() == {"task": "compute"}


# Scorer portability and registration

SPEC = importlib.util.spec_from_file_location(
    "reconcile_mlflow_scorers", Path(__file__).parents[2] / "scripts/reconcile_mlflow_scorers.py"
)
reconcile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reconcile)


def test_registered_source_runs_in_fresh_process_without_closure_or_provider(tmp_path):
    definition = tmp_path / "definition.json"
    definition.write_text(json.dumps(execution_evidence_diagnostics.model_dump()))
    program = """
import json,sys
from types import SimpleNamespace as NS
from mlflow.genai.scorers import Scorer
import fleet_rlm.optimize.scorers as module
from mlflow.entities import SpanType
calls = []
def judge(**kwargs):
    calls.append(kwargs)
    return NS(value=dict(verification_applicable=True, verification_pass=True, integrity_pass=True),
        rationale="Verification and tool integrity: printed result span_id=1111111111111111.")
module.make_execution_evidence_judge = lambda model: judge
root = NS(name="fleet_turn", parent_id=None, inputs={"task":"Compute 17 * 23"}, outputs={"answer":"391"})
receipt = NS(name="sandbox.exec", parent_id="root", span_id="1111111111111111", span_type=SpanType.TOOL,
    inputs={"code":"print(17 * 23)"}, outputs={"stdout":"391"},
    attributes={"outcome":"completed"}, status=NS(status_code=NS(name="OK")))
trace = NS(search_spans=lambda: [root, receipt])
scorer = Scorer.model_validate(json.load(open(sys.argv[1])))
feedback = scorer(trace=trace)
assert len(calls) == 1 and len(feedback) == 2
assert {f.name for f in feedback} == {"sandbox_verification_discipline", "tool_execution_integrity"}
assert all(f.value is True and f.metadata["fleet.diagnostic.registration_version"] == "1" for f in feedback)
"""
    subprocess.run(
        [sys.executable, "-c", program, str(definition)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "MLFLOW_DISABLE_AGENT_HINT": "1",
            "MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS": "true",
            "FLEET_JUDGE_MODEL": "openai:/offline-judge",
        },
    )


def snapshot():
    return {
        "scorers": {
            name: {"version": 3, "sample_rate": 1.0, "filter_string": None, "definition": {"source": "old"}}
            for name in reconcile.OLD_NAMES
        },
        "experiment_id": "1",
        "tracking_uri": "offline",
        "replacement": {"source": "new"},
    }


def test_only_native_required_column_set_order_is_canonicalized():
    left = {
        "definition": {"builtin_scorer_pydantic_data": {"required_columns": ["inputs", "outputs"]}},
        "ordered": ["first", "second"],
    }
    right = copy.deepcopy(left)
    right["definition"]["builtin_scorer_pydantic_data"]["required_columns"].reverse()
    assert reconcile.digest(left) == reconcile.digest(right)
    assert left["definition"]["builtin_scorer_pydantic_data"]["required_columns"] == ["inputs", "outputs"]
    right["ordered"].reverse()
    assert reconcile.digest(left) != reconcile.digest(right)


@pytest.mark.parametrize(
    "change", ["version", "sample_rate", "filter_string", "definition", "tracking_uri", "replacement"]
)
def test_reconciliation_refuses_changed_review_before_any_write(change):
    review = snapshot()
    current = copy.deepcopy(review)
    if change in ("tracking_uri", "replacement"):
        current[change] = "changed"
    else:
        current["scorers"][reconcile.OLD_NAMES[0]][change] = "changed"
    with pytest.raises(ValueError, match="changed after review"):
        reconcile.require_review(review, current)


def test_reconciliation_requires_exact_v3_sampling_and_no_existing_combined_registration():
    review = snapshot()
    reconcile.require_review(review, review)
    for name, field, value in [
        (reconcile.OLD_NAMES[0], "version", 4),
        (reconcile.OLD_NAMES[1], "sample_rate", 0.5),
        (reconcile.OLD_NAMES[0], "filter_string", "tags.route = 'flex'"),
    ]:
        invalid = copy.deepcopy(review)
        invalid["scorers"][name][field] = value
        with pytest.raises(ValueError, match="Expected"):
            reconcile.require_review(invalid, invalid)
    review["scorers"][reconcile.NEW_NAME] = {}
    with pytest.raises(ValueError, match="already exists"):
        reconcile.require_review(review, review)


def test_apply_conflict_does_not_register_or_create_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: {})
    monkeypatch.setattr(
        reconcile, "execution_evidence_diagnostics", SimpleNamespace(register=lambda **_kw: pytest.fail("write"))
    )
    audit = tmp_path / "audit.json"
    with pytest.raises(ValueError, match="changed after review"):
        reconcile.apply_review(snapshot(), audit)
    assert not audit.exists()


def test_rollback_refuses_changed_registry(monkeypatch, tmp_path):
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"phase": "complete", "after": snapshot()}))
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: {})
    with pytest.raises(ValueError, match="Rollback identity conflict"):
        reconcile.rollback(audit)


@pytest.mark.parametrize("validation_fails", [False, True])
def test_reconciliation_orders_validation_readback_activation_and_stops_only_targets(
    monkeypatch, tmp_path, validation_fails
):
    review = snapshot()
    review["scorers"]["unrelated"] = {
        "version": 9,
        "definition": {"source": "unrelated"},
        "sample_rate": 0.25,
        "filter_string": "route = 'direct'",
    }
    state = copy.deepcopy(review)
    operations = []

    class Registered:
        def __init__(self, name):
            self.name = name

        @property
        def scorer_version(self):
            return state["scorers"][self.name]["version"]

        @property
        def sample_rate(self):
            return state["scorers"][self.name]["sample_rate"]

        @property
        def filter_string(self):
            return state["scorers"][self.name]["filter_string"]

        def model_dump(self):
            return state["scorers"][self.name]["definition"]

        def start(self, **_kw):
            operations.append("start:" + self.name)
            state["scorers"][self.name]["sample_rate"] = 1.0

        def stop(self, **_kw):
            operations.append("stop:" + self.name)
            state["scorers"][self.name]["sample_rate"] = 0.0

    def register(**_kw):
        operations.append("register")
        state["scorers"][reconcile.NEW_NAME] = {
            "version": 1,
            "sample_rate": None,
            "filter_string": None,
            "definition": review["replacement"],
        }
        return Registered(reconcile.NEW_NAME)

    def validate(_scorer, **_kwargs):
        operations.append("validate")
        if validation_fails:
            raise ValueError("negative control failed")
        return {"evaluation_run_id": "eval-1"}

    monkeypatch.setenv("FLEET_JUDGE_MODEL", "openai:/offline")
    monkeypatch.setenv("MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS", "true")
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: copy.deepcopy(state))
    monkeypatch.setattr(reconcile, "prove_portability", lambda _definition: operations.append("portability"))
    monkeypatch.setattr(reconcile, "validate_saved_trace", validate)
    monkeypatch.setattr(reconcile, "execution_evidence_diagnostics", SimpleNamespace(register=register))
    monkeypatch.setattr(reconcile.registry, "get_scorer", lambda *, name, **_kw: Registered(name))
    audit = tmp_path / "audit.json"
    if validation_fails:
        with pytest.raises(ValueError, match="negative control failed"):
            reconcile.apply_review(review, audit)
        assert not any(op.startswith(("start:", "stop:")) for op in operations)
        assert json.loads(audit.read_text())["phase"] == "registered"
    else:
        reconcile.apply_review(review, audit)
        assert operations == [
            "portability",
            "register",
            "validate",
            "start:" + reconcile.NEW_NAME,
            *("stop:" + name for name in reconcile.OLD_NAMES),
        ]
        reconcile.rollback(audit)
        assert json.loads(audit.read_text())["phase"] == "rolled_back"
    assert state["scorers"]["unrelated"] == review["scorers"]["unrelated"]


def test_owned_mlflow_explicitly_enables_custom_scorers_and_reused_server_is_untouched(tmp_path):
    from fleet_rlm.launcher import _mlflow_process

    process = _mlflow_process(5000, str(tmp_path))
    assert process.env == {"MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS": "true"}


@pytest.mark.parametrize("conflict", [None, "sampling", "entrypoint", "provider", "review"])
def test_resume_checks_existing_inactive_version_without_registering_again(monkeypatch, tmp_path, conflict):
    original = snapshot()
    review = copy.deepcopy(original)
    review.update(code_digest="fixed-code", rubric="execution-evidence-v2")
    review["scorers"][reconcile.NEW_NAME] = {
        "version": 1,
        "sample_rate": None,
        "filter_string": None,
        "definition": original["replacement"],
    }
    if conflict == "sampling":
        review["scorers"][reconcile.OLD_NAMES[0]]["sample_rate"] = 0.5
    elif conflict == "entrypoint":
        review["replacement"] = {"source": "unexpected"}
    elif conflict == "provider":
        review["judge_model"] = "another-judge"
    current = {} if conflict == "review" else copy.deepcopy(review)
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"phase": "registered", "review": original, "native_registration_version": 1}))
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: current)
    monkeypatch.setattr(reconcile, "prove_portability", lambda _definition: None)
    monkeypatch.setattr(reconcile.registry, "get_scorer", lambda **_kwargs: object())
    monkeypatch.setattr(
        reconcile,
        "execution_evidence_diagnostics",
        SimpleNamespace(register=lambda **_kw: pytest.fail("duplicate registration")),
    )

    def validate(_scorer, *, on_progress, saved_feedback=None):
        assert saved_feedback is None
        on_progress({"evaluation_run_id": "resume-run"})
        return {"evaluation_run_id": "resume-run"}

    activated = []
    monkeypatch.setattr(reconcile, "validate_saved_trace", validate)
    monkeypatch.setattr(reconcile, "_activate_review", lambda reviewed, *_args: activated.append(reviewed))
    if conflict:
        with pytest.raises(ValueError):
            reconcile.resume_registered_review(audit, review)
        assert not activated
    else:
        reconcile.resume_registered_review(audit, review)
        saved = json.loads(audit.read_text())
        assert saved["review"] == original and saved["resumption_review"] == review
        assert saved["validation_progress"]["evaluation_run_id"] == "resume-run"
        assert reconcile.NEW_NAME not in activated[0]["scorers"]


def test_validation_persists_run_and_completed_feedback_before_a_negative_control_failure(monkeypatch):
    from contextlib import nullcontext

    from mlflow.entities import Feedback

    trace = SimpleNamespace(info=SimpleNamespace(trace_metadata={}), data=SimpleNamespace(spans=[]))
    monkeypatch.setattr(reconcile.mlflow, "get_trace", lambda _trace_id: trace)
    monkeypatch.setattr(
        reconcile.mlflow,
        "start_run",
        lambda **_kw: nullcontext(SimpleNamespace(info=SimpleNamespace(run_id="run-before-call"))),
    )
    progress = []

    def score(**_kwargs):
        assert progress[0]["evaluation_run_id"] == "run-before-call"
        if len(progress) > 1:
            raise ValueError("negative control failed")
        return [Feedback(name=name, value=True, rationale="receipt citation") for name in reconcile.OLD_NAMES]

    with pytest.raises(ValueError, match="negative control failed"):
        reconcile.validate_saved_trace(score, on_progress=progress.append)
    assert len(progress[1]["saved_feedback"]) == 2


def test_negative_control_adds_a_false_claim_without_altering_the_original():
    from mlflow.entities import Span
    from mlflow.tracing.constant import SpanAttributeKey

    def span(name, span_id, parent):
        return Span.from_dict(
            {
                "trace_id": "tr-" + "a" * 32,
                "span_id": span_id,
                "parent_span_id": parent,
                "name": name,
                "start_time_unix_nano": "1",
                "end_time_unix_nano": "2",
                "status": {"code": "STATUS_CODE_OK"},
                "attributes": {
                    SpanAttributeKey.REQUEST_ID: "tr-" + "a" * 32,
                    SpanAttributeKey.OUTPUTS: json.dumps({"answer": "5"}),
                },
            }
        )

    root = span("fleet_turn", "1" * 16, "")
    execution = span("sandbox.exec", "2" * 16, "1" * 16)
    trace = SimpleNamespace(data=SimpleNamespace(spans=[root, execution]))
    control = reconcile._unsupported_execution_claim(trace)
    assert len(control.data.spans) == 1
    assert "executed Python" in control.data.spans[0].outputs["answer"]
    assert root.outputs["answer"] == "5" and len(trace.data.spans) == 2


def test_retirement_refuses_review_drift_before_judge_or_write(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    review = snapshot()
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review))
    changed = copy.deepcopy(review)
    changed["tracking_uri"] = "changed"
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: changed)
    with pytest.raises(ValueError, match="review changed"):
        reconcile.retire_legacy(path, tmp_path / "audit.json")
    assert not (tmp_path / "audit.json").exists()


def test_retirement_validates_then_stops_only_legacy_and_recovers(monkeypatch, tmp_path):
    from contextlib import nullcontext

    from fleet_rlm.optimize import scorers

    monkeypatch.chdir(tmp_path)

    state = snapshot()
    state["judge_model"] = "openai:/offline"
    state["scorers"][reconcile.NEW_NAME] = {"version": 1, "sample_rate": None}
    operations = []
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: copy.deepcopy(state))
    monkeypatch.setattr(reconcile.mlflow, "get_trace", lambda trace_id: trace_id)
    monkeypatch.setattr(
        reconcile.mlflow,
        "start_run",
        lambda **_kw: nullcontext(SimpleNamespace(info=SimpleNamespace(run_id="evaluation"))),
    )
    monkeypatch.setattr(scorers, "make_execution_evidence_judge", lambda _model: object())
    monkeypatch.setattr(reconcile, "_unsupported_execution_claim", lambda _trace: "negative")

    def diagnose(trace, *_args, **_kwargs):
        operations.append(trace)
        return [
            SimpleNamespace(name=name, value=trace != "negative", to_dictionary=lambda: {"recorded": True})
            for name in reconcile.OLD_NAMES
        ]

    monkeypatch.setattr(scorers, "diagnose_execution_evidence", diagnose)

    def get_scorer(*, name, **_kwargs):
        def stop(**_kw):
            assert "negative" in operations
            operations.append(name)
            state["scorers"][name]["sample_rate"] = 0.0
            if state["scorers"][name].get("filter_string") == "":
                state["scorers"][name]["filter_string"] = None

        return SimpleNamespace(
            stop=stop,
            sample_rate=state["scorers"][name]["sample_rate"],
            filter_string=state["scorers"][name].get("filter_string"),
        )

    monkeypatch.setattr(reconcile.registry, "get_scorer", get_scorer)
    for name in reconcile.OLD_NAMES:
        state["scorers"][name]["filter_string"] = ""
    initial = copy.deepcopy(state)
    path, audit = tmp_path / "review.json", tmp_path / "audit.json"
    path.write_text(json.dumps(state))
    reconcile.retire_legacy(path, audit)
    assert operations[-2:] == list(reconcile.OLD_NAMES)
    assert all(state["scorers"][name]["filter_string"] is None for name in reconcile.OLD_NAMES)
    assert state["scorers"][reconcile.NEW_NAME]["sample_rate"] is None
    saved = json.loads(audit.read_text())
    assert saved["phase"] == "retired"
    before = list(operations)
    path.write_text(json.dumps(state))
    reconcile.retire_legacy(path, audit)
    assert operations == before

    state["scorers"][reconcile.OLD_NAMES[0]]["sample_rate"] = 0.0
    state["scorers"][reconcile.OLD_NAMES[0]]["filter_string"] = None
    state["scorers"][reconcile.OLD_NAMES[1]]["sample_rate"] = 1.0
    state["scorers"][reconcile.OLD_NAMES[1]]["filter_string"] = ""
    partial_review, partial_audit = tmp_path / "partial_review.json", tmp_path / "partial_audit.json"
    partial_review.write_text(json.dumps(state))
    partial_audit.write_text(
        json.dumps(
            {
                "operation": "retire_legacy",
                "review": initial,
                "stopped": [],
                "pending_stop": reconcile.OLD_NAMES[0],
                "validations": saved["validations"],
            }
        )
    )
    reconcile.retire_legacy(partial_review, partial_audit)
    assert operations == [*before, reconcile.OLD_NAMES[1]]
    recovered = json.loads(partial_audit.read_text())
    assert recovered["phase"] == "retired" and recovered["stopped"] == list(reconcile.OLD_NAMES)
    assert "pending_stop" not in recovered


def test_operator_paths_reject_working_directory_escape(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    review = tmp_path / "review.json"
    review.write_text("{}")
    with pytest.raises(ValueError, match="working directory"):
        reconcile.retire_legacy(Path("../review.json"), tmp_path / "audit.json")
    with pytest.raises(ValueError, match="working directory"):
        reconcile.filter_online_schedules(review, Path("../audit.json"))
    assert not (tmp_path / "audit.json").exists()


def test_filter_online_schedules_updates_only_active_targets_and_recovers(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    state = snapshot()
    for name, version in reconcile.ONLINE_SCHEDULE_VERSIONS.items():
        state["scorers"][name] = {
            "version": version,
            "sample_rate": 1.0,
            "filter_string": None,
            "definition": {"source": name},
        }
    operations = []
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: copy.deepcopy(state))

    def get_scorer(*, name, **_kwargs):
        def update(*, sampling_config, **_kw):
            operations.append((name, sampling_config.sample_rate, sampling_config.filter_string))
            state["scorers"][name]["filter_string"] = sampling_config.filter_string

        return SimpleNamespace(
            update=update,
            scorer_version=state["scorers"][name]["version"],
            sample_rate=state["scorers"][name]["sample_rate"],
            filter_string=state["scorers"][name]["filter_string"],
        )

    monkeypatch.setattr(reconcile.registry, "get_scorer", get_scorer)
    initial = copy.deepcopy(state)
    review_path, audit_path = tmp_path / "filter_review.json", tmp_path / "filter_audit.json"
    review_path.write_text(json.dumps(state))
    reconcile.filter_online_schedules(review_path, audit_path)
    assert [name for name, _, _ in operations] == list(reconcile.ONLINE_SCHEDULE_VERSIONS)
    assert all(
        state["scorers"][name]["filter_string"] == reconcile.ONLINE_TRACE_FILTER
        for name in reconcile.ONLINE_SCHEDULE_VERSIONS
    )
    saved = json.loads(audit_path.read_text())
    assert saved["phase"] == "filtered" and saved["filtered"] == list(reconcile.ONLINE_SCHEDULE_VERSIONS)

    # Recovery after a partial update of the first schedule
    names = list(reconcile.ONLINE_SCHEDULE_VERSIONS)
    for name in names[1:]:
        state["scorers"][name]["filter_string"] = None
    partial_review, partial_audit = tmp_path / "partial_filter_review.json", tmp_path / "partial_filter_audit.json"
    partial_review.write_text(json.dumps(state))
    partial_audit.write_text(
        json.dumps(
            {
                "operation": "filter_online",
                "review": initial,
                "filtered": [],
                "pending_filter": names[0],
            }
        )
    )
    operations.clear()
    reconcile.filter_online_schedules(partial_review, partial_audit)
    assert [name for name, _, _ in operations] == names[1:]
    recovered = json.loads(partial_audit.read_text())
    assert recovered["phase"] == "filtered" and recovered["filtered"] == names
    assert "pending_filter" not in recovered
