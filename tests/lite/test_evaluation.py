import csv
import io
import json

import httpx
import pytest

from fleet_rlm import evaluation
from fleet_rlm.evaluation import (
    EvalCase,
    expected_values_present,
    label_trace,
    run_case,
    synthetic_cases,
)


def _rows(content: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content)))


def test_synthetic_cases_are_deterministic_and_grouped():
    first, second = synthetic_cases(seed=3), synthetic_cases(seed=3)
    assert [c.record() for c in first] == [c.record() for c in second]
    assert [c.record() for c in first] != [c.record() for c in synthetic_cases(seed=4)]
    assert len({c.case_id for c in first}) == len(first)
    assert {c.review_status for c in first} == {"synthetic"}
    assert {c.category for c in first} == {"direct", "aggregate", "filter_count", "join"}


def test_gold_values_match_independent_recomputation():
    for case in synthetic_cases():
        if case.category == "aggregate":
            totals: dict[str, int] = {}
            for row in _rows(case.files["files/sales.csv"]):
                totals[row["region"]] = totals.get(row["region"], 0) + int(row["revenue"])
            assert set(case.expected_values) == set(totals) | {str(v) for v in totals.values()}
        if case.category == "filter_count":
            rows = _rows(case.files["files/shipments.csv"])
            assert case.expected_values == (str(sum(r["status"] == "late" for r in rows)),)
        if case.category == "join":
            tiers = {r["customer_id"]: r["tier"] for r in _rows(case.files["files/customers.csv"])}
            by_tier: dict[str, int] = {}
            for row in _rows(case.files["files/orders.csv"]):
                by_tier[tiers[row["customer_id"]]] = by_tier.get(tiers[row["customer_id"]], 0) + int(row["amount"])
            assert set(case.expected_values) == set(by_tier) | {str(v) for v in by_tier.values()}


def test_only_confident_routes_are_labeled():
    routes = {c.category: c.route for c in synthetic_cases()}
    assert routes == {"direct": "direct", "aggregate": "flex", "filter_count": None, "join": None}


def test_label_trace_writes_each_expectation(monkeypatch):
    calls = []
    monkeypatch.setattr(evaluation.mlflow, "log_expectation", lambda **kw: calls.append(kw))
    label_trace("tr-1", review_status="approved", source_group="g1", expected_answer="42", route="rlm")
    assert {c["name"]: c["value"] for c in calls} == {
        "review_status": "approved",
        "source_group": "g1",
        "expected_answer": "42",
        "route": "rlm",
    }
    assert all(c["trace_id"] == "tr-1" and c["source"].source_type == "HUMAN" for c in calls)


def test_label_trace_marks_synthetic_as_code_source(monkeypatch):
    calls = []
    monkeypatch.setattr(evaluation.mlflow, "log_expectation", lambda **kw: calls.append(kw))
    label_trace("tr-2", review_status="synthetic", source_group="g")
    assert calls[0]["source"].source_type == "CODE"


@pytest.mark.parametrize(
    "kwargs", [{"review_status": "maybe", "source_group": "g"}, {"review_status": "approved", "source_group": " "}]
)
def test_label_trace_rejects_invalid_labels(kwargs):
    with pytest.raises(ValueError):
        label_trace("tr-3", **kwargs)


def _sse(*events: dict) -> str:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


def test_run_case_attaches_files_then_parses_turn_complete():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/attach"):
            return httpx.Response(200, json={"rel_path": "files/sales.csv"})
        body = json.loads(request.content)
        assert body == {"task": "t", "idempotency_key": "c1"}
        return httpx.Response(
            200,
            text=_sse(
                {"event": "turn_status", "payload": {}},
                {
                    "event": "turn_complete",
                    "payload": {"answer": "ok 200", "route": "flex", "trace_id": "tr-9"},
                },
            ),
        )

    case = EvalCase("c1", "t", ("200",), "aggregate", "c1", files={"files/sales.csv": "a,b\n1,2\n"})
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler)) as client:
        out = run_case(client, case, "s1")
    assert seen == [("POST", "/sessions/s1/attach"), ("POST", "/sessions/s1/turn")]
    assert out == {"answer": "ok 200", "route": "flex", "trace_id": "tr-9", "error": None}


def test_run_case_reports_failed_turn_without_trace():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text=_sse({"event": "error", "payload": {"error": "x"}}))
    )
    with httpx.Client(base_url="http://test", transport=transport) as client:
        out = run_case(client, EvalCase("c", "t", ("1",), "direct", "c"), "s")
    assert out["trace_id"] is None and "x" in out["error"]


def test_expected_values_scorer_ignores_thousands_separators():
    ok = expected_values_present(outputs={"answer": "East: 1,200"}, expectations={"expected_values": ["east", "1200"]})
    bad = expected_values_present(outputs={"answer": "East: 1,100"}, expectations={"expected_values": ["east", "1200"]})
    none = expected_values_present(outputs={"answer": "x"}, expectations={})
    assert (ok.value, bad.value, none.value) == (True, False, None)


def test_scorer_uses_word_boundaries():
    cases = {"12 apples": True, "120 apples": False, "1.25 apples": False}
    for answer, expected in cases.items():
        result = expected_values_present(outputs={"answer": answer}, expectations={"expected_values": ["12"]})
        assert result.value is expected


def test_stopping_evidence_excludes_bootstrap_and_private_predictors():
    from types import SimpleNamespace

    from fleet_rlm.evaluation import stopping_evidence

    def span(name, inputs=None, outputs=None, phase="task", start=0):
        return SimpleNamespace(
            name=name, inputs=inputs, outputs=outputs, attributes={"phase": phase}, start_time_ns=start
        )

    root = span("fleet_turn", {"task": "compute"})
    task = span("sandbox.exec", {"code": "print(391)"}, {"stdout": "391"}, start=2)
    trace = SimpleNamespace(
        search_spans=lambda: [
            task,
            root,
            span("sandbox.exec", {"code": "private bootstrap"}, {}, phase="bootstrap"),
            span("Predict.forward", {"reasoning": "private thoughts"}),
        ]
    )
    assert stopping_evidence(trace) == {
        "task": "compute",
        "steps": [{"code": "print(391)", "observation": '{"stdout": "391"}'}],
    }
    task.outputs = None
    assert stopping_evidence(trace) is None


@pytest.mark.parametrize(
    "case,expected",
    [
        ("supported_submit", True),
        ("unnecessary_recompute", False),
        ("failed_check_corrected", True),
        ("requested_independent_check", True),
    ],
)
def test_stopping_regression_suite_projects_evidence_and_uses_explicit_judge(monkeypatch, case, expected):
    from types import SimpleNamespace

    from mlflow.entities import Feedback
    from mlflow.genai.scorers import Guidelines

    from fleet_rlm.evaluation import evaluate_stopping_traces

    tasks = {
        "supported_submit": ("compute", ["print(391)", "assert 391 == 17*23", "SUBMIT(answer='391')"]),
        "unnecessary_recompute": ("compute", ["print(391)", "assert 391 == 17*23", "print(sum([391]))"]),
        "failed_check_corrected": ("compute", ["assert 390 == 17*23", "print(391)", "SUBMIT(answer='391')"]),
        "requested_independent_check": ("compute and independently verify", ["print(391)", "print(sum([391]))"]),
    }
    task, codes = tasks[case]
    spans = [
        SimpleNamespace(
            name="fleet_turn", inputs={"task": task}, outputs={"answer": "391"}, attributes={}, start_time_ns=0
        )
    ]
    spans.extend(
        SimpleNamespace(
            name="sandbox.exec",
            inputs={"code": code},
            outputs={"stdout": "391"},
            attributes={"phase": "task"},
            start_time_ns=i + 1,
        )
        for i, code in enumerate(codes)
    )
    trace = SimpleNamespace(search_spans=lambda: spans, info=SimpleNamespace(state="OK"))
    captured = []

    def judge(_self, *, inputs, outputs):
        captured.append((inputs, outputs))
        # Mocking verifies plumbing, not live semantic agreement with the labels.
        return Feedback(value=expected, rationale=case)

    def evaluate(*, data, scorers):
        assert data == [trace]
        assert scorers[0](trace=trace).value is True
        return scorers[-1](trace=trace)

    monkeypatch.setattr(Guidelines, "__call__", judge)
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", evaluate)
    assert evaluate_stopping_traces([trace], judge_model="databricks:/test-judge").value is expected
    assert captured[0][0]["task"] == task
    assert [step["code"] for step in captured[0][1]["execution_steps"]] == codes
