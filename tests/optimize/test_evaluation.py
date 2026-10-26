import csv
import io
import json
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest

from fleet_rlm.optimize import evaluation
from fleet_rlm.optimize.evaluation import (
    EvalCase,
    label_trace,
    run_case,
    synthetic_cases,
)
from fleet_rlm.optimize.scorers import expected_values_present


def _rows(content: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content)))


def test_synthetic_cases_are_deterministic_and_grouped():
    first, second = synthetic_cases(seed=3), synthetic_cases(seed=3)
    assert [c.record() for c in first] == [c.record() for c in second]
    assert [c.record() for c in first] != [c.record() for c in synthetic_cases(seed=4)]
    assert len({c.case_id for c in first}) == len(first)
    assert {c.review_status for c in first} == {"synthetic"}
    assert {c.category for c in first} == {"direct", "compute", "aggregate", "filter_count", "join"}


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
    assert routes == {"direct": "direct", "compute": "flex", "aggregate": "flex", "filter_count": None, "join": None}


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


def test_critical_label_requires_exact_values_and_is_recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(evaluation.mlflow, "log_expectation", lambda **kw: calls.append(kw))
    with pytest.raises(ValueError, match="deterministic expected value"):
        label_trace("tr-critical-missing", review_status="approved", source_group="g", critical=True)
    with pytest.raises(ValueError, match="expected values must be nonempty"):
        label_trace(
            "tr-critical-empty", review_status="approved", source_group="g", expected_values=[" "], critical=True
        )

    label_trace(
        "tr-critical",
        review_status="approved",
        source_group="g",
        expected_values=["42"],
        critical=True,
    )
    assert any(item["name"] == "critical" and item["value"] is True for item in calls)


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
    assert (ok.value, bad.value) == (True, False)
    assert none is None


def test_scorer_uses_word_boundaries():
    cases = {"12 apples": True, "120 apples": False, "1.25 apples": False}
    for answer, expected in cases.items():
        result = expected_values_present(outputs={"answer": answer}, expectations={"expected_values": ["12"]})
        assert result.value is expected


def test_collect_only_labels_each_turn_with_its_gold_answer_and_skips_the_judge_suite(monkeypatch) -> None:
    import mlflow

    case = EvalCase(
        case_id="external-1",
        task="How many users failed?",
        expected_values=("3", "7"),
        category="external_counting",
        source_group="external-group-9",
        files={},
        review_status="approved",
        critical=True,
    )
    labels: list[dict] = []
    monkeypatch.setattr(
        evaluation, "run_case", lambda _client, _case, _sid: {"trace_id": "tr-1", "answer": "3, 7", "error": None}
    )
    monkeypatch.setattr(
        evaluation, "label_trace", lambda trace_id, **kwargs: labels.append({"trace_id": trace_id, **kwargs})
    )
    monkeypatch.setattr(evaluation, "read_evaluation_trace", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        evaluation,
        "labels_of",
        lambda _trace: {
            "review_status": "approved",
            "source_group": "external-group-9",
            # Several gold values are checked by the values scorer, not by a fabricated joined answer.
            "expected_answer": None,
            "expected_values": ["3", "7"],
            "route": None,
            "critical": True,
        },
    )
    monkeypatch.setattr(
        mlflow.genai, "evaluate", lambda **_: pytest.fail("the judge suite must not run in collect-only")
    )

    assert (
        evaluation.evaluate_cases(
            [case], base_url="http://test", judge_model="m", experiment_id="e1", collect_only=True
        )
        is None
    )
    assert labels == [
        {
            "trace_id": "tr-1",
            "review_status": "approved",
            "source_group": "external-group-9",
            "expected_answer": None,
            "expected_values": ["3", "7"],
            "route": None,
            "critical": True,
            "reviewer": "synthetic-generator",
        }
    ]


def _evaluation_trace(location, *, state="OK", spans=None):
    return SimpleNamespace(
        info=SimpleNamespace(trace_location=location, state=state),
        data=SimpleNamespace(spans=[object()] if spans is None else spans),
    )


@pytest.mark.parametrize("wrapped", [False, True])
def test_evaluation_trace_readback_checks_exact_uc_destination(monkeypatch, wrapped):
    location = SimpleNamespace(catalog_name="c", schema_name="s", table_prefix="eval")
    trace = _evaluation_trace(SimpleNamespace(uc_table_prefix=location) if wrapped else location)
    reads = []
    monkeypatch.setattr(evaluation.mlflow, "get_trace", lambda *args, **kwargs: reads.append((args, kwargs)) or trace)
    assert evaluation.read_evaluation_trace("tr-1", experiment_id="e1", uc_location=("c", "s", "eval")) is trace
    assert reads == [(("tr-1",), {"silent": True, "flush": True})]
    with pytest.raises(evaluation.EvaluationError, match="outside the configured"):
        evaluation.read_evaluation_trace("tr-1", experiment_id="e1", uc_location=("c", "s", "runtime"))


@pytest.mark.parametrize("wrapped", [False, True])
def test_local_evaluation_trace_checks_experiment(monkeypatch, wrapped):
    location = SimpleNamespace(experiment_id="e1")
    trace = _evaluation_trace(SimpleNamespace(mlflow_experiment=location) if wrapped else location)
    monkeypatch.setattr(evaluation.mlflow, "get_trace", lambda *_args, **_kwargs: trace)
    assert evaluation.read_evaluation_trace("tr-1", experiment_id="e1") is trace
    with pytest.raises(evaluation.EvaluationError, match="outside the configured"):
        evaluation.read_evaluation_trace("tr-1", experiment_id="other")


def test_evaluation_readback_waits_for_complete_nonempty_trace(monkeypatch):
    location = SimpleNamespace(experiment_id="e1")
    ready = _evaluation_trace(location)
    traces = iter(
        [None, _evaluation_trace(location, state="IN_PROGRESS"), _evaluation_trace(location, spans=[]), ready]
    )
    monkeypatch.setattr(evaluation.mlflow, "get_trace", lambda *_args, **_kwargs: next(traces))
    monkeypatch.setattr(evaluation.time, "sleep", lambda _seconds: None)
    assert evaluation.read_evaluation_trace("tr-1", experiment_id="e1") is ready


def test_evaluation_readback_waits_for_eventually_visible_expectations(monkeypatch):
    trace = _evaluation_trace(SimpleNamespace(experiment_id="e1"))
    labels = iter([{"route": "direct"}, {"route": "direct", "critical": True}])
    checks = []

    def probe(_trace):
        checks.append(True)
        return next(labels)

    monkeypatch.setattr(evaluation, "labels_of", probe)
    monkeypatch.setattr(evaluation.mlflow, "get_trace", lambda *_args, **_kwargs: trace)
    monkeypatch.setattr(evaluation.time, "sleep", lambda _seconds: None)
    assert (
        evaluation.read_evaluation_trace(
            "tr-1", experiment_id="e1", expected_labels={"route": "direct", "critical": True}
        )
        is trace
    )
    # It keeps checking until the late labels appear, instead of accepting the first non-empty trace.
    assert len(checks) >= 2


def test_evaluation_readback_bounds_a_blocked_sdk_call_without_duplicate_reads(monkeypatch):
    import threading
    import time

    release = threading.Event()
    reads = []

    def read(*_args, **_kwargs):
        reads.append(True)
        release.wait()
        return None

    monkeypatch.setattr(evaluation.mlflow, "get_trace", read)
    started = time.monotonic()
    try:
        with pytest.raises(evaluation.EvaluationError, match="without rerunning"):
            evaluation.read_evaluation_trace("tr-1", experiment_id="e1", wait_seconds=0.05)
        assert time.monotonic() - started < 1.0
        assert reads == [True]
    finally:
        release.set()


def test_evaluation_readback_rejects_failed_trace_and_sanitizes_sdk_errors(monkeypatch):
    monkeypatch.setattr(
        evaluation.mlflow,
        "get_trace",
        lambda *_args, **_kwargs: _evaluation_trace(SimpleNamespace(experiment_id="e1"), state="ERROR"),
    )
    with pytest.raises(evaluation.EvaluationError, match="completed with state ERROR"):
        evaluation.read_evaluation_trace("tr-1", experiment_id="e1")

    def fail(*_args, **_kwargs):
        raise RuntimeError("private credential detail")

    monkeypatch.setattr(evaluation.mlflow, "get_trace", fail)
    with pytest.raises(evaluation.EvaluationError, match=r"Trace readback failed \(RuntimeError\)") as exc:
        evaluation.read_evaluation_trace("tr-1", experiment_id="e1")
    assert "private credential" not in str(exc.value)


def test_evaluation_runs_use_distinct_sessions_and_keep_case_idempotency(monkeypatch):
    sessions = []
    case = synthetic_cases()[0]
    monkeypatch.setattr(
        evaluation,
        "run_case",
        lambda _client, _case, sid: sessions.append(sid) or {"answer": "22.2", "trace_id": "tr-1", "error": None},
    )
    monkeypatch.setattr(evaluation, "read_evaluation_trace", lambda *_args, **_kwargs: object())
    saved = {}
    monkeypatch.setattr(evaluation, "label_trace", lambda _trace_id, **kwargs: saved.update(kwargs))
    monkeypatch.setattr(evaluation, "labels_of", lambda _trace: saved)
    for _ in range(2):
        evaluation.evaluate_cases(
            [case], base_url="http://test", judge_model="m", experiment_id="e1", collect_only=True
        )
    assert sessions[0] != sessions[1]
    assert all(sid.startswith("eval-") and sid.endswith("-direct-00") for sid in sessions)

    # Within one Session, retries retain the stable case idempotency key.
    keys = []
    transport = httpx.MockTransport(
        lambda req: (
            keys.append(json.loads(req.content)["idempotency_key"])
            or httpx.Response(200, text=_sse({"event": "turn_complete", "payload": {"trace_id": "tr-1"}}))
        )
    )
    with httpx.Client(base_url="http://test", transport=transport) as client:
        run_case(client, case, sessions[0])
        run_case(client, case, sessions[0])
    assert keys == [case.case_id, case.case_id]


@pytest.mark.parametrize("failure", ["execution", "missing_trace", "destination", "assessment", "missing_labels"])
def test_incomplete_evaluation_never_reports_collection_success(monkeypatch, failure):
    case = synthetic_cases()[0]
    reports = []
    writes = []
    calls = []
    monkeypatch.setattr(
        evaluation,
        "run_case",
        lambda *_args: (
            calls.append(True)
            or {
                "trace_id": None if failure == "missing_trace" else "tr-1",
                "error": "failed" if failure == "execution" else None,
            }
        ),
    )

    def read(*_args, **_kwargs):
        if failure == "destination":
            raise evaluation.EvaluationError("outside the configured destination")
        return object()

    def label(*_args, **_kwargs):
        writes.append(True)
        if failure == "assessment":
            raise RuntimeError("private credential detail")

    monkeypatch.setattr(evaluation, "read_evaluation_trace", read)
    monkeypatch.setattr(evaluation, "label_trace", label)
    monkeypatch.setattr(evaluation, "labels_of", lambda _trace: {})
    with pytest.raises(evaluation.EvaluationError) as exc:
        evaluation.evaluate_cases(
            [case],
            base_url="http://test",
            judge_model="m",
            experiment_id="e1",
            collect_only=True,
            report=reports.append,
        )
    assert calls == [True]
    assert not any("collected and labelled" in message for message in reports)
    assert "private credential" not in str(exc.value)
    if failure in {"execution", "missing_trace", "destination"}:
        assert writes == []
    if failure == "assessment":
        assert any("trace tr-1" in message for message in reports)


def test_scoring_saved_traces_forwards_the_jev_lm(monkeypatch):
    result = SimpleNamespace(run_id="run-1", metrics={}, result_df=pd.DataFrame({"scorer/error_message": [None]}))
    received = []
    monkeypatch.setattr(evaluation, "build_mlflow_scorer_suite", lambda *args: received.append(args) or [])
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kwargs: result)
    sentinel = object()
    evaluation.score_evaluation_traces(
        [object()], judge_model="m", judge_lm="judge-sentinel", jev_lm=sentinel, report=lambda _message: None
    )
    assert received == [("m", "judge-sentinel", sentinel)]


@pytest.mark.parametrize("error", [None, "judge failed"])
def test_scoring_saved_traces_reports_run_and_rejects_scorer_errors(monkeypatch, error):
    result = SimpleNamespace(run_id="run-1", metrics={}, result_df=pd.DataFrame({"scorer/error_message": [error]}))
    monkeypatch.setattr(evaluation, "build_mlflow_scorer_suite", lambda *_args: [])
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kwargs: result)
    reports = []
    if error:
        with pytest.raises(evaluation.EvaluationError, match="Scoring incomplete"):
            evaluation.score_evaluation_traces([object()], judge_model="m", report=reports.append)
    else:
        assert evaluation.score_evaluation_traces([object()], judge_model="m", report=reports.append) is result
    assert reports == ["Evaluation scoring run: run-1"]


def test_scoring_requires_feedback_readback_even_when_metrics_were_computed(monkeypatch):
    result = SimpleNamespace(
        run_id="run-1", metrics={"fleet_metric/mean": 1.0}, result_df=pd.DataFrame({"trace_id": ["tr-1"]})
    )
    monkeypatch.setattr(evaluation, "build_mlflow_scorer_suite", lambda *_args: [])
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kwargs: result)
    with pytest.raises(evaluation.EvaluationError, match="Scoring incomplete"):
        evaluation.score_evaluation_traces([object()], judge_model="m")
