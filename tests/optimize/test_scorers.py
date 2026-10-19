import json
from functools import partial
from types import SimpleNamespace

import dspy
import pytest
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
    import fleet_rlm.optimize.scorers as scorers

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

    monkeypatch.setattr(scorers, "make_judge", make_judge)
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
    assert jev_routing_efficiency(trace=heavy).value == 0.8


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
        assert jev_routing_efficiency(trace=trace).value == 1.0
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
    assert "output_instruction_compliance" in names
    assert "agent_skills_progressive_disclosure" in names


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


def test_execution_assessments_use_bounded_sandbox_evidence_and_version(monkeypatch):
    import fleet_rlm.optimize.scorers as scorers

    calls = _judge_responses(
        monkeypatch,
        {
            "execution_evidence_diagnostics": (_execution_judge_payload(), "structured result"),
            "jev_routing_efficiency_judge": (True, "Flex was reasonable for this computation."),
            "output_instruction_compliance": (True, "The answer contains only the requested digit."),
            "agent_skills_progressive_disclosure": (True, "Selected Skill evidence was reviewed."),
        },
    )
    suite = build_mlflow_scorer_suite("openai:/judge")
    execution_scorer = next(item for item in suite if item.name == "execution_evidence_diagnostics")
    feedback = execution_scorer(trace=_pi_execution_trace())
    assert {item.name: item.value for item in feedback} == {
        "sandbox_verification_discipline": True,
        "tool_execution_integrity": True,
    }
    assert all(item.metadata["fleet.trace_scorer_version"] == scorers.TRACE_ASSESSMENT_VERSION for item in feedback)
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
    suite = build_mlflow_scorer_suite("openai:/judge")
    judge = next(item for item in suite if item.name == "execution_evidence_diagnostics")
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
    responses = {
        "execution_evidence_diagnostics": (_execution_judge_payload(), "structured result"),
        "jev_routing_efficiency_judge": (True, "Flex was suitable."),
        "output_instruction_compliance": (True, "The answer followed the format."),
        "agent_skills_progressive_disclosure": (True, "Skill activity was reviewed."),
    }
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
        assert item.metadata["mlflow.assessment.scorerVersion"] == "fleet-trace-assessments-v5"


def test_execution_assessments_preserve_judge_source_and_count_tokens_once(monkeypatch):
    from mlflow.entities import AssessmentSource, AssessmentSourceType, Feedback

    import fleet_rlm.optimize.scorers as scorers

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

    monkeypatch.setattr(scorers, "make_judge", make_judge)
    suite = build_mlflow_scorer_suite("openai:/judge")
    execution_scorer = next(item for item in suite if item.name == "execution_evidence_diagnostics")
    verification, integrity = execution_scorer(trace=_pi_execution_trace())
    assert verification.source.source_type == AssessmentSourceType.LLM_JUDGE
    assert verification.source.source_id == "openai:/judge"
    assert verification.metadata["mlflow.assessment.scorerName"] == "sandbox_verification_discipline"
    assert verification.metadata["mlflow.assessment.scorerVersion"] == "fleet-trace-assessments-v5"
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
    suite = build_mlflow_scorer_suite("openai:/judge")
    execution_scorer = next(item for item in suite if item.name == "execution_evidence_diagnostics")
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
    suite = build_mlflow_scorer_suite("openai:/judge")
    execution_scorer = next(item for item in suite if item.name == "execution_evidence_diagnostics")
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
    from fleet_rlm.optimize.scorers import ScorerEvidenceError

    calls = _judge_responses(
        monkeypatch,
        {
            "execution_evidence_diagnostics": (_execution_judge_payload(), "structured result"),
            "jev_routing_efficiency_judge": (True, "Flex was suitable."),
            "output_instruction_compliance": (True, "The answer followed the format."),
            "agent_skills_progressive_disclosure": (True, "Skill activity was reviewed."),
        },
    )
    suite = build_mlflow_scorer_suite("openai:/judge")
    execution_scorer = next(item for item in suite if item.name == "execution_evidence_diagnostics")
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
    assert feedback.metadata["fleet.trace_scorer_version"] == "fleet-trace-assessments-v5"
    call = next(context for name, context in calls if name == "output_instruction_compliance")
    assert call == {
        "inputs": {"task": "Tell me the 1492252th digits after the decimal point of Pi"},
        "outputs": {"answer": "5"},
    }


def test_output_compliance_preserves_middle_constraints_and_violations(monkeypatch):
    calls = _judge_responses(monkeypatch, {"output_instruction_compliance": (False, "The answer violates the format.")})
    output_scorer = next(
        item for item in build_mlflow_scorer_suite("openai:/judge") if item.name == "output_instruction_compliance"
    )
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
    from fleet_rlm.optimize.scorers import ScorerEvidenceError

    calls = _judge_responses(monkeypatch, {"output_instruction_compliance": (True, "The output is compliant.")})
    output_scorer = next(
        item for item in build_mlflow_scorer_suite("openai:/judge") if item.name == "output_instruction_compliance"
    )
    fields = {"task": "Return one line", "answer": "done", field: value}
    trace = _trace(_span("fleet_turn", inputs={"task": fields["task"]}, outputs={"answer": fields["answer"]}))
    with pytest.raises(ScorerEvidenceError, match=r"complete.*evidence"):
        output_scorer(trace=trace)
    assert calls == []


def test_output_compliance_redacts_complete_fields_and_accepts_the_limit(monkeypatch):
    calls = _judge_responses(monkeypatch, {"output_instruction_compliance": (True, "The output is compliant.")})
    output_scorer = next(
        item for item in build_mlflow_scorer_suite("openai:/judge") if item.name == "output_instruction_compliance"
    )
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
    from fleet_rlm.optimize.scorers import ScorerEvidenceError

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
    suite = build_mlflow_scorer_suite("openai:/judge")
    execution_scorer = next(item for item in suite if item.name == "execution_evidence_diagnostics")
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
    suite = build_mlflow_scorer_suite("openai:/judge")
    skill_scorer = next(item for item in suite if item.name == "agent_skills_progressive_disclosure")
    assert skill_scorer(trace=_pi_execution_trace(skill_selected=False)) is None
    assert not any(name == "agent_skills_progressive_disclosure" for name, _ in calls)
    feedback = skill_scorer(trace=_pi_execution_trace(skill_selected=True))
    assert feedback.value is True
    assert feedback.metadata["fleet.trace_scorer_version"] == "fleet-trace-assessments-v5"
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
    suite = build_mlflow_scorer_suite("openai:/judge")
    route_scorer = next(item for item in suite if item.name == "jev_routing_efficiency_judge")
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
    feedback = rlm_trajectory_efficiency(
        trace=_trace(
            _span("fleet_turn", outputs={"lm_calls": 1, "sandbox_errors": 1, "submit_rejections": 0}), execution
        )
    )
    assert feedback.value == 0.85
    assert "1 remote Python errors" in feedback.rationale


def _judge(is_correct: bool, critique: str = "checked") -> DummyLM:
    return DummyLM([{"reasoning": "r", "is_correct": is_correct, "is_safe": True, "critique": critique}])


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
    judge = DummyLM([{"reasoning": "r", "is_correct": True, "is_safe": True, "critique": "Correct and complete."}])
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
    suite = build_mlflow_scorer_suite("openai:/judge")
    judge = next(item for item in suite if item.name == "stopping_discipline")
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
    assert feedback.metadata["fleet.trace_scorer_version"] == "fleet-trace-assessments-v5"


def test_should_refuse_stopping_judgment_when_computation_code_is_truncated(monkeypatch):
    from fleet_rlm.optimize.scorers import ScorerEvidenceError

    calls = _judge_responses(monkeypatch, {})
    suite = build_mlflow_scorer_suite("openai:/judge")
    judge = next(item for item in suite if item.name == "stopping_discipline")
    trace = _pi_execution_trace()
    trace.search_spans()[2].inputs["code"] = "x" * 13000
    with pytest.raises(ScorerEvidenceError, match="evidence context limit"):
        judge(trace=trace)
    assert not calls


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
    judge = next(item for item in build_mlflow_scorer_suite("openai:/judge") if item.name == "stopping_discipline")
    assert judge(trace=_trace(root, bootstrap, computation)).value is False
    [evidence] = calls[0][1]["inputs"]["sandbox_exec_spans"]
    assert evidence["code"] == fixture["code"]
    assert "mp.nstr" in evidence["code"] and "def pi_int" in evidence["code"]
    assert evidence["stdout"] == fixture["stdout"]


def test_should_reject_a_stopping_verdict_with_an_unknown_execution_citation(monkeypatch):
    from fleet_rlm.optimize.scorers import ScorerEvidenceError

    _judge_responses(monkeypatch, {"stopping_discipline": (True, "Verified span_id=9999999999999999.")})
    judge = next(item for item in build_mlflow_scorer_suite("openai:/judge") if item.name == "stopping_discipline")
    with pytest.raises(ScorerEvidenceError, match="absent from the trace"):
        judge(trace=_pi_execution_trace())
