"""Evidence scorer regressions: no providers, databases, or live workers."""

from types import SimpleNamespace

from scripts.mlflow_evaluation import (
    independent_review_complete,
    monitoring_blockers,
    monitoring_control,
    score_cleanup,
    score_completion,
    score_execution,
    worker_assessments,
    worker_validation_passed,
)


def span(name, code="", output=None, parent=None, sid=None):
    return SimpleNamespace(
        name=name,
        span_id=sid or name,
        parent_id=parent,
        attributes={"mlflow.spanInputs": {"code_preview": code}, "mlflow.spanOutputs": output or {}},
        status=SimpleNamespace(status_code=SimpleNamespace(value="OK")),
    )


def trace(*spans, state="OK"):
    return SimpleNamespace(data=SimpleNamespace(spans=list(spans)), info=SimpleNamespace(state=state))


def test_server_worker_response_uses_native_assessment_name():
    statuses = [
        {
            "status": "SUCCEEDED",
            "result": {
                "tr-validation": {
                    "assessments": [{"assessment_name": "answer_correctness", "feedback": {"value": "yes"}}],
                    "failures": [],
                }
            },
        }
    ]
    assert worker_assessments(statuses)[0]["name"] == "answer_correctness"
    assert worker_validation_passed(statuses)


def test_successful_job_with_scorer_error_does_not_validate():
    statuses = [
        {
            "status": "SUCCEEDED",
            "result": {
                "tr-validation": {
                    "assessments": [
                        {
                            "assessment_name": "answer_correctness",
                            "feedback": {
                                "value": None,
                                "error": {"error_code": "SCORER_ERROR", "error_message": "provider unavailable"},
                            },
                        }
                    ],
                    "failures": [],
                }
            },
        }
    ]
    assert not worker_validation_passed(statuses)
    assert not worker_validation_passed([])


def test_ai_feedback_and_expectations_never_satisfy_human_calibration():
    from mlflow.entities import AssessmentSource, Feedback

    completed = SimpleNamespace(status="completed", completed_by="operator")
    ai = Feedback(
        name="fleet-v1-ai-review-answer_correctness",
        value=True,
        source=AssessmentSource(source_type="LLM_JUDGE", source_id="assistant"),
    )
    expectation = Feedback(
        name="expected_response", value=True, source=AssessmentSource(source_type="HUMAN", source_id="default")
    )
    assert not independent_review_complete(completed, [ai, expectation], "answer_correctness")


def test_pending_review_cannot_satisfy_calibration():
    from mlflow.entities import AssessmentSource, Feedback

    feedback = Feedback(
        name="fleet-v1-human-answer_correctness",
        value=True,
        source=AssessmentSource(source_type="HUMAN", source_id="operator"),
    )
    pending = SimpleNamespace(status="pending", completed_by=None)
    completed = SimpleNamespace(status="completed", completed_by="operator")
    assert not independent_review_complete(pending, [feedback], "answer_correctness")
    assert independent_review_complete(completed, [feedback], "answer_correctness")


def test_activation_rejects_missing_eligibility_and_execution_evidence():
    blockers = monitoring_blockers(
        "evidence_support", worker_passed=True, calibration_passed=True, eligibility_verified=False
    )
    assert len(blockers) == 2
    assert any("execution evidence" in b for b in blockers)
    assert any("Durable completion" in b for b in blockers)


def test_apply_activation_does_not_start_blocked_judges(monkeypatch):
    monkeypatch.setattr(
        "scripts.mlflow_evaluation.monitoring_status",
        lambda: {
            "judges": [{"name": "answer_correctness", "blockers": ["pending review"]}],
        },
    )
    monkeypatch.setattr(
        "scripts.mlflow_evaluation.get_scorer",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("Must not start a blocked judge")),
    )
    monitoring_control("activate", apply=True)


def test_stop_is_idempotent_and_does_not_require_calibration(monkeypatch):
    from scripts.mlflow_evaluation import MONITORED_JUDGES

    stopped = []
    judges = {name: SimpleNamespace(sample_rate=0.5, stop=lambda **_kw: None) for name in MONITORED_JUDGES}
    for name, item in judges.items():

        def stop(*, experiment_id, name=name, item=item):
            assert experiment_id == "1"
            stopped.append(name)
            item.sample_rate = 0

        item.stop = stop
    monkeypatch.setattr("scripts.mlflow_evaluation.get_scorer", lambda *, name, **_kw: judges[name])
    monkeypatch.setattr(
        "scripts.mlflow_evaluation.monitoring_status",
        lambda: (_ for _ in ()).throw(
            AssertionError("Stopping must work even when calibration evidence is unavailable")
        ),
    )
    monitoring_control("stop", apply=True)
    monitoring_control("stop", apply=True)
    assert stopped == list(MONITORED_JUDGES)


def test_native_guidelines_omits_execution_spans(monkeypatch):
    from mlflow.entities import Feedback
    from mlflow.genai.scorers import Guidelines

    captured = {}

    def judge(**kwargs):
        captured.update(kwargs)
        return Feedback(value=False)

    monkeypatch.setattr("mlflow.genai.judges.meets_guidelines", judge)
    Guidelines(guidelines="Claims need evidence", model="gateway:/test")(
        inputs={"request": "Run two children and aggregate"}, outputs="Completed aggregation"
    )
    assert captured["context"] == {
        "request": "{'request': 'Run two children and aggregate'}",
        "response": "Completed aggregation",
    }
    assert "trace" not in captured


def test_actual_python_counting_passes():
    t = trace(
        span(
            "sandbox.execute",
            "counts = Counter(labels)",
            {"phase_status": "completed", "output_preview": "counts = {'spam': 6, 'ham': 4}"},
        )
    )
    assert score_execution(t, {"execution_requirements": ["aggregation"]}).value is True


def test_correct_answer_and_child_results_do_not_establish_root_aggregation():
    child = span("RLM.recursive_call", output={"child_outcome": {"answer": "counts"}}, sid="child")
    t = trace(
        child,
        span(
            "sandbox.execute",
            "counts = Counter(labels)",
            {"phase_status": "completed", "output_preview": "counts = {'spam': 6, 'ham': 4}"},
            parent="child",
        ),
        span(
            "sandbox.execute",
            "spam_total = sum(parsed)",
            {"phase_status": "completed", "output_preview": "INVALID child output(s); not submitting"},
        ),
    )
    assert score_execution(t, {"execution_requirements": ["aggregation"]}).value is False


def test_marker_with_literal_answer_does_not_pass():
    t = trace(
        span(
            "sandbox.execute",
            "print('FLEET_AGGREGATION_VERIFIED ...')",
            {"phase_status": "completed", "output_preview": 'FLEET_AGGREGATION_VERIFIED {"spam": 6, "ham": 4}'},
        )
    )
    assert score_execution(t, {"execution_requirements": ["aggregation"]}).value is False


def test_wrong_total_fails():
    t = trace(
        span(
            "sandbox.execute",
            "counts = Counter(labels)",
            {"phase_status": "completed", "output_preview": "counts = {'spam': 3, 'ham': 2}"},
        )
    )
    assert score_execution(t, {"execution_requirements": ["aggregation"], "expected_records": 10}).value is False


def test_missing_and_invalid_evidence_are_errors():
    assert score_execution(None, {"execution_requirements": ["python"]}).error.error_code == "MISSING_TRACE"
    assert score_execution(None, {"invalid_input": True}).error.error_code == "INVALID_TEST_INPUT"
    assert score_cleanup(trace()).error.error_code == "NOT_APPLICABLE"


def test_cleanup_requires_confirmation():
    t = trace(span("RLM.child.acquire"), span("RLM.child.cleanup", output={"status": "pending"}))
    assert score_cleanup(t).value is False


def test_open_trace_can_have_durable_completed_turn():
    t = trace(span("Turn.settlement"), span("database.commit", output={"outcome": "completed"}), state="IN_PROGRESS")
    assert score_completion(t, {"durable_state": "completed"}).value is True
    assert score_completion(t, {"durable_state": "cancelled"}).value is False


def test_recursive_total_receipt_matches_actual_child_counts():
    t = trace(
        span("RLM.recursive_call", output={"child_outcome": '{"spam": 3, "ham": 2}'}, sid="a"),
        span("RLM.recursive_call", output={"child_outcome": '{"spam": 3, "ham": 2}'}, sid="b"),
        span(
            "sandbox.execute",
            "spam_total = sum(c['spam'] for c in parsed)",
            {"phase_status": "completed", "output_preview": "total spam=6 ham=4 records=10"},
        ),
    )
    assert score_execution(t, {"execution_requirements": ["aggregation"]}).value is True
    t.data.spans[-1].attributes["mlflow.spanOutputs"]["output_preview"] = "total spam=7 ham=3 records=10"
    assert score_execution(t, {"execution_requirements": ["aggregation"]}).value is False


def test_record_list_receipt_requires_labels_matching_counts():
    receipt = '{"counts": {"spam": 1, "ham": 1}, "records": [{"label": "spam"}, {"label": "ham"}]}'
    t = trace(
        span(
            "sandbox.execute",
            "counts = Counter(labels)",
            {"phase_status": "completed", "output_preview": "FLEET_AGGREGATION_VERIFIED " + receipt},
        )
    )
    assert score_execution(t, {"execution_requirements": ["aggregation"], "expected_records": 2}).value is True
    assert score_execution(t, {"execution_requirements": ["aggregation"], "expected_records": 10}).value is False
