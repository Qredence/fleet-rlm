"""Offline domain regression coverage."""

from __future__ import annotations

import ast
import base64
import copy
import csv
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import dspy
import httpx
import pandas as pd
import pytest
from databricks.sdk.errors import NotFound
from dspy.utils import DummyLM
from mlflow.entities import SpanType
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import RESOURCE_DOES_NOT_EXIST

from fleet_rlm.execution_evidence import validate_structured, verification_entries
from fleet_rlm.optimize import evaluation
from fleet_rlm.optimize.evaluation import (
    EvalCase,
    label_trace,
    run_case,
    synthetic_cases,
)
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
    route_correctness,
)

# Consolidated from optimize/test_evaluation.py


# Evaluation and curation


def _rows(content: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content)))


def test_build_dataset_uses_databricks_dataset_integration(monkeypatch):
    import databricks.agents.datasets as agents_datasets
    import mlflow.genai.datasets as datasets

    calls = []
    dataset = SimpleNamespace(merge_records=lambda records: calls.append(records))
    monkeypatch.setattr(datasets, "get_tracking_uri", lambda: "databricks://test-profile")
    monkeypatch.setattr(datasets, "_databricks_profile_env", nullcontext)
    monkeypatch.setattr(datasets, "EvaluationDataset", lambda value: value)
    monkeypatch.setattr(agents_datasets, "get_dataset", lambda _name: dataset)
    case = EvalCase("c1", "task", ("42",), "direct", "group")

    assert evaluation.build_dataset("catalog.schema.cases", "123", [case]) is dataset
    assert calls == [[case.record()]]


@pytest.mark.parametrize("error", [NotFound("missing"), MlflowException("missing", RESOURCE_DOES_NOT_EXIST)])
def test_build_dataset_creates_only_when_missing(monkeypatch, error):
    calls = []
    dataset = SimpleNamespace(merge_records=lambda records: calls.append(records))

    def missing(**_kwargs):
        raise error

    monkeypatch.setattr(evaluation.mlflow.genai.datasets, "get_dataset", missing)
    monkeypatch.setattr(
        evaluation.mlflow.genai.datasets,
        "create_dataset",
        lambda **kwargs: calls.append(kwargs) or dataset,
    )
    assert evaluation.build_dataset("catalog.schema.cases", "123", []) is dataset
    assert calls == [{"name": "catalog.schema.cases", "experiment_id": "123"}, []]


@pytest.mark.parametrize("error", [PermissionError("denied"), MlflowException("denied", "PERMISSION_DENIED")])
def test_build_dataset_does_not_create_on_lookup_failure(monkeypatch, error):
    def denied(**_kwargs):
        raise error

    monkeypatch.setattr(evaluation.mlflow.genai.datasets, "get_dataset", denied)
    monkeypatch.setattr(
        evaluation.mlflow.genai.datasets,
        "create_dataset",
        lambda **_kwargs: pytest.fail("must not create a dataset after a failed lookup"),
    )
    with pytest.raises(type(error), match="denied"):
        evaluation.build_dataset("catalog.schema.cases", "123", [])


def test_synthetic_cases_are_deterministic_and_grouped():
    first, second = synthetic_cases(seed=3), synthetic_cases(seed=3)
    assert [c.record() for c in first] == [c.record() for c in second]
    assert [c.record() for c in first] != [c.record() for c in synthetic_cases(seed=4)]
    assert len({c.case_id for c in first}) == len(first)
    assert {c.review_status for c in first} == {"synthetic"}
    assert {c.category for c in first} == {"direct", "compute", "aggregate", "filter_count", "join"}


def _log_events(case) -> list[dict[str, str]]:
    rows = []
    for line in case.files["files/events.log"].splitlines():
        fields = dict(part.split("=", 1) for part in line.split()[1:] if "=" in part)
        if "action" in fields:
            rows.append(fields)
    return rows


def test_long_context_sizes_follow_tiers_and_gold_matches_recomputation():
    cases = evaluation.long_context_cases(seed=5, tiers=(20_000, 50_000), per_tier=4)
    assert len(cases) == 8
    for case in cases:
        tier = int(case.case_id.split("-")[1].rstrip("k")) * 1000
        assert tier <= case.context_chars < tier + 2_000
        rows = _log_events(case)
        if case.category.endswith("count"):
            assert case.expected_values == (str(sum(r["action"] == "refund" for r in rows)),)
        if case.category.endswith("list"):
            fails = Counter(r["user"] for r in rows if r["action"] == "failed_login")
            assert case.expected_values, "list cases must have a non-empty gold list"
            assert set(case.expected_values) == {u for u, n in fails.items() if n >= 2}
            assert case.answer_type == "list"
        if case.category.endswith("needle"):
            assert f"ALERT code={case.expected_values[0]} " in case.files["files/events.log"]


def test_long_context_is_deterministic():
    first = [case.record() for case in evaluation.long_context_cases(seed=2, per_tier=1)]
    second = [case.record() for case in evaluation.long_context_cases(seed=2, per_tier=1)]
    assert first == second


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
    assert routes == {"direct": "direct", "compute": "rlm", "aggregate": "rlm", "filter_count": None, "join": None}


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


@pytest.mark.parametrize("state", ["OK", "ERROR"])
def test_comparison_readback_waits_for_native_model_association(monkeypatch, state):
    location = SimpleNamespace(experiment_id="e1")
    early = _evaluation_trace(location, state=state)
    early.info.trace_metadata = {}
    ready = _evaluation_trace(location, state=state)
    ready.info.trace_metadata = {"mlflow.modelId": "m-original"}
    traces = iter([early, ready])
    monkeypatch.setattr(evaluation.mlflow, "get_trace", lambda *_args, **_kwargs: next(traces))
    monkeypatch.setattr(evaluation.time, "sleep", lambda _seconds: None)
    assert (
        evaluation.read_evaluation_trace("tr-1", experiment_id="e1", allow_error=True, require_model_id=True) is ready
    )


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
    failed = evaluation.read_evaluation_trace("tr-1", experiment_id="e1", allow_error=True)
    assert failed.info.state == "ERROR"
    with pytest.raises(evaluation.EvaluationError, match="outside the configured"):
        evaluation.read_evaluation_trace("tr-1", experiment_id="other", allow_error=True)

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
    result = SimpleNamespace(
        run_id="run-1",
        metrics={},
        result_df=pd.DataFrame(
            {
                "scorer/error_message": [None],
                "scorer/value": [True],
                "assessments": [
                    [{"metadata": {"mlflow.assessment.sourceRunId": "run-1"}, "feedback": {"value": True}}]
                ],
            }
        ),
    )
    received = []
    monkeypatch.setattr(evaluation, "build_mlflow_scorer_suite", lambda *args: received.append(args) or [])
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kwargs: result)
    sentinel = object()
    evaluation.score_evaluation_traces(
        [object()], judge_model="m", judge_lm="judge-sentinel", jev_lm=sentinel, report=lambda _message: None
    )
    assert received == [("m", "judge-sentinel", sentinel)]


def test_scoring_saved_traces_can_select_scorers_and_experiment(monkeypatch):
    result = SimpleNamespace(
        run_id="run-1",
        metrics={},
        result_df=pd.DataFrame(
            {
                "scorer/error_message": [None],
                "scorer/value": [True],
                "assessments": [
                    [{"metadata": {"mlflow.assessment.sourceRunId": "run-1"}, "feedback": {"value": True}}]
                ],
            }
        ),
    )
    calls = {}
    scorers = [object()]
    monkeypatch.setattr(evaluation.mlflow, "set_experiment", lambda **kwargs: calls.setdefault("experiment", kwargs))

    def start_run(**kwargs):
        calls["start_run"] = kwargs
        return nullcontext(SimpleNamespace(info=SimpleNamespace(run_id="run-1")))

    monkeypatch.setattr(evaluation.mlflow, "start_run", start_run)

    def evaluate(**kwargs):
        calls["evaluate"] = kwargs
        return result

    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", evaluate)
    monkeypatch.setattr(
        evaluation,
        "build_mlflow_scorer_suite",
        lambda *_args: pytest.fail("selected scorers must be used as supplied"),
    )

    scored = evaluation.score_evaluation_traces(
        [object()], judge_model="m", experiment_id="123", scorers=scorers, report=lambda _message: None
    )

    assert scored is result
    assert scored.run_id == "run-1"
    assert calls["experiment"] == {"experiment_id": "123"}
    assert calls["start_run"] == {"experiment_id": "123", "nested": False}
    assert calls["evaluate"]["scorers"] == scorers


def test_scoring_saved_traces_rejects_active_run_from_another_experiment(monkeypatch):
    monkeypatch.setattr(
        evaluation.mlflow,
        "active_run",
        lambda: SimpleNamespace(info=SimpleNamespace(experiment_id="other-experiment")),
    )
    monkeypatch.setattr(evaluation.mlflow, "set_experiment", lambda **_kwargs: pytest.fail("must reject first"))
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kwargs: pytest.fail("must reject first"))

    with pytest.raises(evaluation.EvaluationError, match="Scoring failed"):
        evaluation.score_evaluation_traces(
            [object()], judge_model="m", experiment_id="123", scorers=[object()], report=lambda _message: None
        )


def test_scoring_saved_traces_starts_distinct_nested_run_when_a_run_is_active(monkeypatch):
    result = SimpleNamespace(
        run_id="run-1",
        metrics={},
        result_df=pd.DataFrame(
            {"assessments": [[{"metadata": {"mlflow.assessment.sourceRunId": "run-1"}, "feedback": {"value": True}}]]}
        ),
    )
    calls = []
    monkeypatch.setattr(
        evaluation.mlflow,
        "active_run",
        lambda: SimpleNamespace(info=SimpleNamespace(experiment_id="123")),
    )
    monkeypatch.setattr(evaluation.mlflow, "set_experiment", lambda **_kwargs: None)
    monkeypatch.setattr(
        evaluation.mlflow,
        "start_run",
        lambda **kwargs: calls.append(kwargs) or nullcontext(SimpleNamespace(info=SimpleNamespace(run_id="run-1"))),
    )
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kwargs: result)

    evaluation.score_evaluation_traces(
        [object()], judge_model="m", experiment_id="123", scorers=[object()], report=lambda _message: None
    )

    assert calls == [{"experiment_id": "123", "nested": True}]


@pytest.mark.parametrize("error", [None, "judge failed"])
def test_scoring_saved_traces_reports_run_and_rejects_scorer_errors(monkeypatch, error):
    feedback = {"error": {"error_message": error}} if error else {"value": True}
    result = SimpleNamespace(
        run_id="run-1",
        metrics={},
        result_df=pd.DataFrame(
            {
                "scorer/error_message": ["historical failure"],
                "scorer/value": [True],
                "assessments": [[{"metadata": {"mlflow.assessment.sourceRunId": "run-1"}, "feedback": feedback}]],
            }
        ),
    )
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


def test_scoring_allows_inapplicable_scorer_for_a_trace(monkeypatch):
    result = SimpleNamespace(
        run_id="run-1",
        metrics={},
        result_df=pd.DataFrame(
            {
                "trace_id": ["tr-1", "tr-2"],
                "assessments": [
                    [{"metadata": {"mlflow.assessment.sourceRunId": "run-1"}, "feedback": {"value": True}}],
                    [],
                ],
            }
        ),
    )
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kwargs: result)

    assert (
        evaluation.score_evaluation_traces(
            [object(), object()], judge_model="m", scorers=[object()], report=lambda _message: None
        )
        is result
    )


def test_scoring_rejects_empty_scorer_selection(monkeypatch):
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kwargs: pytest.fail("must not evaluate"))
    with pytest.raises(evaluation.EvaluationError, match="Scoring failed"):
        evaluation.score_evaluation_traces([object()], judge_model="m", scorers=[])


@pytest.mark.parametrize("columns", [{"trace_id": ["tr-1"]}, {"scorer/value": [None]}])
def test_scoring_rejects_absent_feedback(monkeypatch, columns):
    result = SimpleNamespace(run_id="run-1", metrics={}, result_df=pd.DataFrame(columns))
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kwargs: result)
    with pytest.raises(evaluation.EvaluationError, match="Scoring incomplete"):
        evaluation.score_evaluation_traces([object()], judge_model="m", scorers=[object()])


@pytest.mark.parametrize(
    "assessment",
    [
        {"metadata": {"mlflow.assessment.sourceRunId": "run-1"}, "expectation": {"value": True}},
        {"metadata": {"mlflow.assessment.sourceRunId": "older-run"}, "feedback": {"value": True}},
        {"metadata": {}, "feedback": {"value": True}},
    ],
)
def test_scoring_requires_feedback_from_current_run(monkeypatch, assessment):
    result = SimpleNamespace(
        run_id="run-1",
        metrics={},
        result_df=pd.DataFrame(
            {
                "gold/value": [True],
                "assessments": [[assessment]],
            }
        ),
    )
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kwargs: result)
    with pytest.raises(evaluation.EvaluationError, match="Scoring incomplete"):
        evaluation.score_evaluation_traces([object()], judge_model="m", scorers=[object()])


def _label_trace_fixture(monkeypatch, existing_answer=None):
    saved = {}
    trace = SimpleNamespace(
        info=SimpleNamespace(trace_metadata={"mlflow.trace.session": "session-1"}),
        data=SimpleNamespace(request={}),
        search_assessments=lambda **_kwargs: (
            [SimpleNamespace(name="expected_answer", value=existing_answer, create_time_ms=1)]
            if existing_answer
            else []
        ),
    )
    monkeypatch.setattr(evaluation.mlflow, "get_trace", lambda *_a, **_k: trace)
    monkeypatch.setattr(evaluation, "label_trace", lambda _id, **labels: saved.update(labels))
    return saved


def test_label_turn_defaults_the_expected_answer_to_the_stored_full_answer(monkeypatch) -> None:
    saved = _label_trace_fixture(monkeypatch)
    labels = evaluation.label_turn(SimpleNamespace(), "tr-1", values=["42"], default_answer="the full stored answer")
    assert labels["expected_answer"] == saved["expected_answer"] == "the full stored answer"


def test_label_turn_keeps_a_given_or_existing_answer_over_the_default(monkeypatch) -> None:
    saved = _label_trace_fixture(monkeypatch)
    evaluation.label_turn(SimpleNamespace(), "tr-1", answer="typed", default_answer="stored")
    assert saved["expected_answer"] == "typed"
    saved = _label_trace_fixture(monkeypatch, existing_answer="earlier gold")
    evaluation.label_turn(SimpleNamespace(), "tr-1", default_answer="stored")
    assert saved["expected_answer"] == "earlier gold"
    saved = _label_trace_fixture(monkeypatch)
    evaluation.label_turn(SimpleNamespace(), "tr-1", status="rejected", default_answer="stored")
    assert saved["expected_answer"] is None


def test_registered_scorer_drift_prevents_evaluation(monkeypatch):
    identity = {"experiment_id": "1", "name": "diagnostics", "version": 1, "digest": "before"}
    monkeypatch.setattr(
        evaluation, "load_registered_scorers", lambda *_a: ([object()], [{**identity, "digest": "after"}])
    )
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kw: pytest.fail("must not evaluate"))
    with pytest.raises(evaluation.EvaluationError, match="Scoring failed"):
        evaluation.score_evaluation_traces(
            [], judge_model="judge", scorers=[object()], registered_provenance=[identity]
        )


def test_label_turn_defaults_benchmark_traces_to_benchmark_and_refuses_approval(monkeypatch) -> None:
    import hashlib

    from fleet_rlm.replay import encode_snapshot

    snapshot = {
        "schema_version": 1,
        "session_id": "session-1",
        "turn_id": "turn-1",
        "task": "How many days in a leap year?",
        "workspace_manifest": "/workspace/files (empty)",
        "skills_catalog": "<available_skills/>",
        "memory_summary": "",
        "session_history": "",
        "context_files": {},
        "replay_files": {},
        "skill_revision_sha256": hashlib.sha256(b"{}").hexdigest(),
        "execution_mode": "hybrid",
        "turn_timeout_seconds": 900,
        "sandbox_transport": "daytona_code_interpreter",
    }
    digest, compressed = encode_snapshot(snapshot)
    trace = SimpleNamespace(
        info=SimpleNamespace(trace_metadata={"mlflow.trace.session": "session-1"}, tags={}),
        data=SimpleNamespace(
            request={
                "session_id": "session-1",
                "turn_id": "turn-1",
                "replay_snapshot_selected": True,
                "replay_snapshot_sha256": digest,
            },
            spans=[
                SimpleNamespace(parent_id="root", attributes={}),
                SimpleNamespace(parent_id=None, attributes={"fleet.benchmark.model_id": "m-bench"}),
            ],
        ),
        search_assessments=lambda **_kwargs: [],
    )
    mlflow_writes: list[dict] = []
    db_writes: list[dict] = []
    store = SimpleNamespace(
        replay_snapshot=lambda *_args: (digest, compressed),
        save_review_labels=lambda *_args, **kwargs: db_writes.append(kwargs),
    )
    monkeypatch.setattr(evaluation.mlflow, "get_trace", lambda *_a, **_k: trace)
    monkeypatch.setattr(evaluation, "label_trace", lambda _id, **labels: mlflow_writes.append(labels))

    with pytest.raises(ValueError, match="Benchmark traces cannot be approved"):
        evaluation.label_turn(store, "tr-bench", status="approved", answer="366")
    assert mlflow_writes == [] and db_writes == []

    labels = evaluation.label_turn(store, "tr-bench", answer="366", default_answer="ignored")
    assert labels["review_status"] == "benchmark"
    assert labels["expected_answer"] == "366"
    assert mlflow_writes[-1]["review_status"] == db_writes[-1]["review_status"] == "benchmark"


def test_score_evaluation_traces_tolerates_null_metadata_and_feedback_entries(monkeypatch) -> None:
    result = SimpleNamespace(
        run_id="run-1",
        metrics={"gold/mean": 1.0},
        result_df=pd.DataFrame(
            {
                "gold/value": [True],
                "assessments": [
                    [
                        {"metadata": None, "feedback": None},
                        {
                            "metadata": {"mlflow.assessment.sourceRunId": "run-1"},
                            "feedback": {"value": True, "error": None},
                        },
                    ]
                ],
            }
        ),
    )
    monkeypatch.setattr(evaluation.mlflow.genai, "evaluate", lambda **_kwargs: result)

    scored = evaluation.score_evaluation_traces([object()], judge_model="m", scorers=[object()])
    assert len(scored.result_df["assessments"].iloc[0]) == 1


# Benchmark tasks and replay isolation

DESTINATION = {"mode": "local", "tracking_uri": "http://tracking", "experiment_name": "runtime"}
ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("benchmark_tasks", ROOT / "scripts/benchmark_tasks.py")
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def freeze(tmp_path, count=2, task=None):
    cases = [
        {
            "case_id": f"task-{index}",
            "task": task if task is not None else f"Compute {index}",
            "expected_answer": "secret-gold",
            "question": {"answer": "secret-payload"},
            "files": {"context.txt": "unlabelled input"},
        }
        for index in range(count)
    ]
    bundle = tmp_path / "bundle"
    bench.freeze_tasks(bundle, cases, {"dataset": "external/tasks", "dataset_revision": "a" * 40})
    return bundle, tmp_path / "run"


def api(
    monkeypatch,
    *,
    failure=None,
    drift=False,
    unhealthy_after=False,
    tolerated_error=None,
    comparison=None,
    cleanup_status=200,
):
    requests = []
    turns = []
    configs = []
    modes = {}

    def handle(request):
        requests.append(request)
        path = request.url.path
        session = path.split("/")[2] if path.startswith("/sessions/") else None
        route = "rlm" if modes.get(session) == "rlm" else "flex"
        if path.endswith("/mode") and request.method == "PUT":
            modes[session] = json.loads(request.content)["mode"]
            return httpx.Response(200, json={"mode": modes[session]})
        if path == "/health":
            return httpx.Response(
                200,
                json={
                    "status": "failed" if unhealthy_after and turns else "ok",
                    "mlflow": {
                        "ready": True,
                        "mode": "local",
                        "experiment_name": "runtime",
                        "experiment_id": "runtime",
                        "tracking_uri": "http://tracking",
                    },
                },
            )
        if path.endswith("/config"):
            configs.append(path)
            return httpx.Response(
                200,
                json={
                    "mode": modes.get(session, "hybrid"),
                    "models": {"root": "configured-model", "sub": "configured-model"},
                    "configuration": {
                        "digest": "changed" if drift and len(configs) > 1 else modes.get(session, "d"),
                        **(
                            {"values": {**comparison, "execution_mode": modes.get(session, "hybrid")}}
                            if comparison
                            else {}
                        ),
                    },
                },
            )
        if path.endswith("/turn"):
            turns.append(json.loads(request.content))
            events = []
            if tolerated_error:
                events.append(
                    {
                        "event": "lm_end",
                        "model": "provider-model",
                        "ok": False,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "error_type": tolerated_error,
                    }
                )
            events.append(
                {
                    "event": "lm_end",
                    "model": "provider-model",
                    "ok": not failure,
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                    "error_type": failure,
                }
            )
            events.append(
                {
                    "event": "error" if failure else "turn_complete",
                    "turn_id": "turn-1",
                    "payload": {"answer": "42", "route": route, "trace_id": f"trace-{len(turns)}"},
                }
            )
            return httpx.Response(200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events))
        if "/turns/" in path:
            return httpx.Response(
                200,
                json={
                    "status": "failed" if failure else "completed",
                    "result": {"answer": "42", "route": route, "trace_id": f"trace-{len(turns)}"},
                },
            )
        if request.method == "DELETE":
            return httpx.Response(cleanup_status, json={})
        if path.endswith("/attach"):
            return httpx.Response(200, json={})
        return pytest.fail(f"Unexpected request: {request.method} {path}")

    original = httpx.Client
    monkeypatch.setattr(
        bench.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)
    )
    return requests, turns


def test_bundle_is_immutable_and_requires_pinned_source(tmp_path):
    bundle, _ = freeze(tmp_path)
    loaded = bench.load_tasks(bundle)
    with pytest.raises(FileExistsError):
        bench.freeze_tasks(bundle, loaded["cases"], loaded["metadata"])
    loaded["cases"][0]["task"] = "modified"
    bench.save_json(bundle / "bundle.json", loaded)
    with pytest.raises(ValueError, match="changed"):
        bench.load_tasks(bundle)
    with pytest.raises(ValueError, match="commit SHA"):
        bench.freeze_tasks(tmp_path / "other", [{"case_id": "x", "task": "x"}], {"dataset_revision": "main"})
    with pytest.raises(ValueError, match="name the dataset"):
        bench.freeze_tasks(tmp_path / "other2", [{"case_id": "x", "task": "x"}], {"dataset_revision": "a" * 40})


def test_runs_normal_api_without_gold_or_overrides_and_reuses_completed_receipts(tmp_path, monkeypatch):
    bundle, run = freeze(tmp_path)
    requests, turns = api(monkeypatch)
    bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert turns == [
        {"task": "Compute 0", "idempotency_key": "task-0"},
        {"task": "Compute 1", "idempotency_key": "task-1"},
    ]
    assert all("secret" not in request.content.decode() for request in requests)
    assert all(request.method not in {"PATCH", "PUT"} for request in requests)
    receipt = json.loads((run / "receipts/task-0.json").read_text())
    assert receipt["configuration"]["mode"] == "hybrid"
    assert receipt["configuration_digest"] == "d"
    assert receipt["status"] == "completed" and receipt["durable_outcome"]["status"] == "completed"
    assert receipt["output"]["trace_id"] == "trace-1" and receipt["cleanup"] == "released"
    bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert len(turns) == 2
    scores = bench.score_tasks(bundle, run, lambda _case, answer: float(answer == "42"))
    assert scores["complete"] and scores["completed_case_accuracy"] == 1
    assert scores["configuration_digest"] == "d"
    assert scores["reported_provider_usage"]["provider-model"]["input_tokens"] == 20
    assert scores["cases"][0]["trace_url"].startswith("http://tracking/#/experiments/runtime/traces")


@pytest.mark.parametrize("failure", ["LMRateLimitError", "ServiceUnavailableError"])
def test_provider_stop_prevents_subsequent_tasks_and_replay(tmp_path, monkeypatch, failure):
    bundle, run = freeze(tmp_path)
    _, turns = api(monkeypatch, failure=failure)
    with pytest.raises(RuntimeError, match="stopped"):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    with pytest.raises(RuntimeError, match="never replay"):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert len(turns) == 1
    scores = bench.score_tasks(bundle, run, lambda *_args: pytest.fail("failed turns must not be scored"))
    assert scores["status_counts"] == {"failed": 1, "unattempted": 1}
    assert not scores["complete"] and scores["all_selected_case_accuracy"] == 0


def test_tolerated_lm_failure_in_completed_turn_does_not_stop_the_run(tmp_path, monkeypatch):
    bundle, run = freeze(tmp_path)
    _, turns = api(monkeypatch, tolerated_error="ServiceUnavailableError")
    result = bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert result["status"] == "finished" and len(turns) == 2
    for case_id in ("task-0", "task-1"):
        receipt = json.loads((run / "receipts" / f"{case_id}.json").read_text())
        assert receipt["status"] == "completed" and "stop_reason" not in receipt
        assert receipt["cleanup"] == "released"
    bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert len(turns) == 2


def test_unknown_outcome_receipt_is_written_before_post_and_never_replayed(tmp_path, monkeypatch):
    bundle, run = freeze(tmp_path)
    original = bench._turn
    calls = []
    api(monkeypatch)

    def timeout(_client, _session, case):
        calls.append(case["case_id"])
        receipt = json.loads((run / "receipts/task-0.json").read_text())
        assert receipt["status"] == "uncertain"
        raise httpx.ReadTimeout("outcome unknown")

    monkeypatch.setattr(bench, "_turn", timeout)
    with pytest.raises(RuntimeError):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    monkeypatch.setattr(bench, "_turn", original)
    with pytest.raises(RuntimeError, match="never replay"):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert calls == ["task-0"]
    assert json.loads((run / "receipts/task-0.json").read_text())["status"] == "uncertain"


@pytest.mark.parametrize("option", ["drift", "unhealthy_after"])
def test_config_drift_or_post_turn_service_failure_stops_measurement(tmp_path, monkeypatch, option):
    bundle, run = freeze(tmp_path)
    _, turns = api(monkeypatch, **{option: True})
    with pytest.raises(RuntimeError):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert len(turns) == 1
    assert not bench.score_tasks(bundle, run, lambda *_args: 1)["complete"]
    with pytest.raises(RuntimeError):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert len(turns) == 1


def test_offline_scoring_keeps_wrong_answers_verifier_errors_and_missing_usage(tmp_path, monkeypatch):
    bundle, run = freeze(tmp_path)
    api(monkeypatch)
    bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)

    def verifier(case, _answer):
        if case["case_id"] == "task-1":
            raise ValueError("invalid gold")
        return 0

    score = bench.score_tasks(bundle, run, verifier)
    assert score["completed"] == 2 and score["scored"] == 1 and score["score_errors"] == 1
    assert score["completed_case_accuracy"] is None and score["mean_official_score_scored"] == 0
    assert not score["complete"]


def notebook_functions(name, namespace):
    document = json.loads((ROOT / "notebooks" / f"{name}.ipynb").read_text())
    for cell in document["cells"]:
        if cell["cell_type"] == "code":
            assert cell["execution_count"] is None and not cell["outputs"]
            compile("".join(cell["source"]), name, "exec")
        if cell["id"] == "definitions":
            tree = ast.parse("".join(cell["source"]))
            tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
            exec(compile(tree, name, "exec"), namespace)
    return namespace


@pytest.mark.parametrize("name", ["longcot_benchmark", "oolong_benchmark"])
@pytest.mark.parametrize("from_notebooks", [False, True])
def test_notebook_bootstrap_resolves_scripts_before_configuration(monkeypatch, name, from_notebooks):
    document = json.loads((ROOT / "notebooks" / f"{name}.ipynb").read_text())
    cells = {cell["id"]: cell for cell in document["cells"]}
    assert list(cells).index("setup") < list(cells).index("configuration")
    monkeypatch.chdir(ROOT / "notebooks" if from_notebooks else ROOT)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    namespace = {}
    exec(compile("".join(cells["setup"]["source"]), name, "exec"), namespace)
    assert namespace["ROOT"] == ROOT.resolve()
    assert sys.path[0] == str(ROOT.resolve() / "scripts")
    configuration = ast.parse("".join(cells["configuration"]["source"]))
    flags = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in configuration.body
        if isinstance(node, ast.Assign) and node.targets[0].id in {"ALLOW_LIVE", "ALLOW_JUDGES"}
    }
    assert flags == {"ALLOW_LIVE": False, "ALLOW_JUDGES": False}


def test_longcot_hf_join_preserves_all_domains_and_requires_prompt_identity():
    @dataclass
    class Question:
        question_id: str
        domain: str
        difficulty: str
        prompt: str
        problem: dict
        answer: str | None = None

    domains = ("logic", "cs", "chemistry", "chess", "math")
    namespace = notebook_functions(
        "longcot_benchmark",
        {
            "hashlib": hashlib,
            "json": json,
            "asdict": asdict,
            "DOMAINS": domains,
            "VERIFIER_COMMIT": "fb9649423f15f5b0091f8e988b100596cac592ca",
        },
    )
    rows = [
        {
            "question_id": domain,
            "domain": domain,
            "difficulty": "easy",
            "prompt": "published prompt",
            "template": "template",
        }
        for domain in domains
    ]
    canonical = {
        row["question_id"]: Question(
            row["question_id"], row["domain"], "easy", row["prompt"], {"template": "template", "hidden_state": [1, 2]}
        )
        for row in rows
    }
    selected = namespace["select_longcot"](rows, per_domain=1)
    assert selected == namespace["select_longcot"](list(reversed(rows)), per_domain=1)
    for row in selected:
        case = namespace["longcot_task"](row, canonical)
        assert case["task"] == row["prompt"]
        assert case["question"]["problem"]["hidden_state"] == [1, 2]
    with pytest.raises(ValueError, match="differs"):
        namespace["longcot_task"]({**rows[0], "prompt": "altered"}, canonical)
    namespace["distribution"] = lambda _name: SimpleNamespace(
        read_text=lambda _path: json.dumps({"vcs_info": {"commit_id": "installed-commit"}})
    )
    namespace["require_verifier"]("installed-commit")
    with pytest.raises(ValueError, match="required commit"):
        namespace["require_verifier"]("frozen-commit")
    seen, checked = [], []
    namespace.update(
        bundle={"metadata": {"verifier_commit": "frozen-commit"}},
        require_verifier=lambda expected: checked.append(expected),
        longcot=SimpleNamespace(
            Question=lambda **kwargs: kwargs,
            VerifyOptions=lambda **kwargs: kwargs,
            MathVerifyOptions=lambda **kwargs: kwargs,
            ChemistryVerifyOptions=lambda **kwargs: kwargs,
            verify=lambda _question, answer, options: seen.append(options) or answer == "yes",
        ),
    )
    assert namespace["official_score"]({"question": {}}, "yes") == 1
    assert checked == ["frozen-commit"]
    assert seen == [{"math": {"enable_fallback": False}, "chemistry": {"enable_fallback": False}}]


def test_oolong_selection_covers_groups_without_gold_or_labelled_context():
    namespace = notebook_functions(
        "oolong_benchmark", {"hashlib": hashlib, "Counter": Counter, "defaultdict": defaultdict}
    )
    rows = [
        {"id": index, "task_group": str(index % 3), "context_len": 1024, "context_window_id": index // 3}
        for index in range(30)
    ]
    chosen = namespace["select_oolong"](rows)
    assert chosen == namespace["select_oolong"](reversed(rows))
    assert len(chosen) == 12 and len({row["task_group"] for row in chosen}) == 3
    assert max(Counter(row["context_window_id"] for row in chosen).values()) <= 2
    case = namespace["oolong_task"](
        {
            **rows[0],
            "question": "count?",
            "context_window_text": "safe context",
            "context_window_text_with_labels": "leaked gold",
            "answer": "['gold']",
            "dataset": "test",
            "answer_type": "ANSWER_TYPE.LABEL",
        }
    )
    assert "gold" not in case["task"] and case["files"] == {"context.txt": "safe context"}


def test_live_and_judge_gates_precede_all_io(tmp_path):
    with pytest.raises(ValueError, match="allow_live"):
        bench.run_tasks(tmp_path, tmp_path, base_url="http://invalid", expected_destination=DESTINATION)
    with pytest.raises(ValueError, match="allow_judges"):
        bench.evaluate_saved_tasks(tmp_path, tmp_path, None)
    for descriptor in ({}, {"mode": "local", "tracking_uri": "http://tracking"}):
        with pytest.raises(ValueError, match="Expected destination"):
            bench.run_tasks(
                tmp_path, tmp_path, base_url="http://invalid", allow_live=True, expected_destination=descriptor
            )


def test_tracking_profile_mismatch_prevents_runtime_creation_and_paid_calls(tmp_path, monkeypatch):
    bundle, run_dir = freeze(tmp_path)
    requests, turns = api(monkeypatch)
    with pytest.raises(RuntimeError, match="stopped"):
        bench.run_tasks(
            bundle,
            run_dir,
            base_url="http://fleet",
            allow_live=True,
            expected_destination={"mode": "databricks", "tracking_uri": "databricks", "experiment_name": "runtime"},
        )
    assert [request.url.path for request in requests] == ["/health"]
    assert not turns
    receipt = json.loads((run_dir / "receipts/task-0.json").read_text())
    assert receipt["status"] == "preflight_failed" and "destination differs" in receipt["error"]


@pytest.mark.parametrize("backend", ["local", "databricks"])
@pytest.mark.parametrize(
    "judge_model", ["openai:/configured-judge", "databricks:/configured-judge", "anthropic:/judge"]
)
@pytest.mark.parametrize("scoring_error", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_evaluates_saved_traces_in_configured_experiment_without_turns(
    tmp_path, monkeypatch, backend, judge_model, scoring_error, partial
):
    import mlflow
    import pandas as pd

    from fleet_rlm import observability
    from fleet_rlm.optimize import evaluation, scorers

    bundle, run_dir = freeze(tmp_path, count=2 if partial else 1)
    _, turns = api(monkeypatch)
    bench.run_tasks(bundle, run_dir, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)

    def verifier(case, _answer):
        if partial and case["case_id"] == "task-1":
            raise ValueError("verifier unavailable")
        return 1

    bench.score_tasks(bundle, run_dir, verifier)
    descriptor = {
        "mode": backend,
        "tracking_uri": "databricks" if backend == "databricks" else "http://tracking",
        "trace_location": {"catalog": "catalog", "schema": "runtime", "table_prefix": "traces"}
        if backend == "databricks"
        else None,
    }
    run = json.loads((run_dir / "run.json").read_text())
    run["service"]["mlflow"].update(descriptor)
    bench.save_json(run_dir / "run.json", run)
    configured, reads, labels, inputs, logged = [], [], [], [], []

    def build_judge_lm():
        return "native-dspy-judge"

    settings = SimpleNamespace(
        mlflow_target=lambda: SimpleNamespace(
            eval_experiment_name="configured-eval", tracking_uri=descriptor["tracking_uri"]
        ),
        mlflow_descriptor=lambda: descriptor,
        prepare_mlflow_auth=lambda: None,
        judge_environment=lambda: {},
        build_judge_lm=build_judge_lm,
        resolved_model=lambda role: SimpleNamespace(build_adapter=lambda: "judge-adapter") if role == "judge" else None,
        build_jev_lm=lambda: "native-jev-lm",
        fleet_judge_model=judge_model,
        mlflow_trace_catalog="catalog",
        mlflow_trace_schema="runtime",
        mlflow_eval_trace_table_prefix="evaluations",
        mlflow_tracing_sql_warehouse_id="warehouse",
    )
    trace = SimpleNamespace(info=SimpleNamespace(trace_id="trace-1"))
    monkeypatch.setattr(
        observability, "configure_mlflow_observability", lambda *args, **kwargs: configured.append((args, kwargs))
    )
    monkeypatch.setattr(
        evaluation, "read_evaluation_trace", lambda identity, **kwargs: reads.append((identity, kwargs)) or trace
    )
    monkeypatch.setattr(evaluation, "label_trace", lambda identity, **kwargs: labels.append((identity, kwargs)))
    monkeypatch.setattr(
        scorers,
        "build_mlflow_scorer_suite",
        lambda model, lm, jev=None, judge_adapter=None: [f"native-suite:{model}:{lm}:{jev}:{judge_adapter}"],
    )
    monkeypatch.setattr(mlflow, "start_run", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(mlflow, "set_tags", lambda tags: logged.append(tags))
    monkeypatch.setattr(mlflow, "log_dict", lambda value, path: logged.append((path, value)))
    monkeypatch.setattr(mlflow, "log_input", lambda dataset, **kwargs: inputs.append((dataset, kwargs)))
    monkeypatch.setattr(mlflow.data, "from_pandas", lambda frame, **kwargs: (frame.to_dict("records"), kwargs))

    def evaluate(*, data, scorers):
        assert data == [{"trace": trace, "expectations": {"benchmark_official_score": 1}}]
        expected_lm = "native-dspy-judge"
        assert scorers[0] == f"native-suite:{judge_model}:{expected_lm}:native-jev-lm:judge-adapter"
        assert scorers[-1](expectations=data[0]["expectations"]).value == 1
        frame = pd.DataFrame({"benchmark_correctness/value": [1]})
        if scoring_error:
            frame["fleet_metric/error_message"] = ["judge failure"]
        return SimpleNamespace(run_id="saved-eval", metrics={"benchmark_correctness/mean": 1}, result_df=frame)

    monkeypatch.setattr(mlflow.genai, "evaluate", evaluate)
    if scoring_error:
        with pytest.raises(RuntimeError, match="Evaluation incomplete"):
            bench.evaluate_saved_tasks(bundle, run_dir, settings, allow_judges=True)
    else:
        assert bench.evaluate_saved_tasks(bundle, run_dir, settings, allow_judges=True).run_id == "saved-eval"
    assert len(turns) == (2 if partial else 1)  # Evaluation never sends another Turn.
    assert configured[0][0] == ("configured-eval", descriptor["tracking_uri"])
    assert configured[0][1]["trace_table_prefix"] == "evaluations"
    assert reads[-1][1]["expected_labels"]["review_status"] == "benchmark"
    assert reads[-1][1]["expected_labels"]["expected_answer"] == "secret-gold"
    assert reads[-1][1]["uc_location"] == (("catalog", "runtime", "traces") if backend == "databricks" else None)
    assert labels[0][1]["review_status"] == "benchmark"
    assert inputs[0][0][0] == [
        {"case_id": case["case_id"], "task": case["task"]} for case in bench.load_tasks(bundle)["cases"]
    ]
    assert inputs[0][1] == {"context": "benchmark"}
    saved = json.loads((run_dir / "evaluation.json").read_text())
    assert saved["run_id"] == "saved-eval"
    assert saved["complete"] is (not scoring_error and not partial)
    assert saved["scoring_complete"] is not scoring_error
    assert (saved["expected"], saved["completed"], saved["scored"]) == ((2, 2, 1) if partial else (1, 1, 1))
    assert logged[0]["benchmark.expected"] == str(saved["expected"])


@pytest.mark.parametrize("official_only", [False, True])
def test_official_scores_survive_real_mlflow_cross_experiment_cloning(
    tmp_path, monkeypatch, mlflow_test_uri, official_only
):
    import mlflow

    from fleet_rlm.optimize import scorers

    bundle, run_dir = freeze(tmp_path, task="identical prompt")
    with monkeypatch.context() as api_patch:
        api(api_patch)
        bench.run_tasks(bundle, run_dir, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    source = mlflow.set_experiment("benchmark-serving")
    source_trace_ids = set()
    expected_scores = {"task-0": 0.75, "task-1": 0.0}
    for case_id in expected_scores:
        with mlflow.start_span(name=case_id) as span:
            span.set_inputs({"task": case_id})
            span.set_outputs({"answer": "42"})
        trace = mlflow.get_trace(span.trace_id, flush=True)
        source_trace_ids.add(trace.info.trace_id)
        receipt_path = run_dir / "receipts" / f"{case_id}.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["output"]["trace_id"] = trace.info.trace_id
        bench.save_json(receipt_path, receipt)
    descriptor = {"mode": "local", "tracking_uri": mlflow_test_uri, "experiment_name": source.name}
    run = json.loads((run_dir / "run.json").read_text())
    run["service"]["mlflow"].update(descriptor, experiment_id=source.experiment_id)
    bench.save_json(run_dir / "run.json", run)
    bench.score_tasks(bundle, run_dir, lambda case, _answer: expected_scores[case["case_id"]])
    settings = SimpleNamespace(
        mlflow_target=lambda: SimpleNamespace(
            eval_experiment_name="benchmark-evaluation", tracking_uri=mlflow_test_uri
        ),
        mlflow_descriptor=lambda: descriptor,
        prepare_mlflow_auth=lambda: None,
        judge_environment=lambda: (
            pytest.fail("Official-only scoring must not prepare judge credentials") if official_only else {}
        ),
        build_judge_lm=lambda: None,
        resolved_model=lambda _role: SimpleNamespace(build_adapter=lambda: None),
        build_jev_lm=lambda: None,
        fleet_judge_model="unused",
        mlflow_trace_catalog="",
        mlflow_trace_schema="",
        mlflow_eval_trace_table_prefix="",
        mlflow_tracing_sql_warehouse_id="",
    )

    # Exercise native trace readback, labeling, cloning and scoring without model judges.
    def suite(*_args, **_kwargs):
        if official_only:
            pytest.fail("Official-only scoring must not build model judges")
        return []

    monkeypatch.setattr(scorers, "build_mlflow_scorer_suite", suite)
    result = bench.evaluate_saved_tasks(
        bundle, run_dir, settings, allow_judges=not official_only, official_only=official_only
    )
    frame = result.result_df
    cloned_traces = [mlflow.entities.Trace.from_json(value) for value in frame["trace"]]
    assert {trace.info.trace_id for trace in cloned_traces}.isdisjoint(source_trace_ids)
    assert {
        trace.data.spans[0].name: value
        for trace, value in zip(cloned_traces, frame["benchmark_correctness/value"], strict=True)
    } == expected_scores
    assert not frame.filter(regex="error_message$").notna().any().any()
    saved = json.loads((run_dir / "evaluation.json").read_text())
    assert saved["complete"] and saved["scoring_complete"]
    if official_only:
        assert saved["dataset_id"] and saved["official_only"]
        from mlflow.genai.datasets import search_datasets

        assert len(search_datasets()) == 1
        bench.evaluate_saved_tasks(bundle, run_dir, settings, official_only=True)
        assert len(search_datasets()) == 1  # Re-score receipts, reusing the native frozen dataset.


def test_evaluation_refuses_a_run_without_recorded_destination(tmp_path):
    bundle, run_dir = freeze(tmp_path, count=1)
    digest = bench.load_tasks(bundle)["digest"]
    bench.save_json(run_dir / "run.json", {"bundle_digest": digest, "run_id": "r", "status": "incomplete"})
    bench.save_json(
        run_dir / "scores.json",
        {"bundle_digest": digest, "complete": False, "expected": 1, "completed": 0, "scored": 0, "cases": []},
    )
    with pytest.raises(ValueError, match="recorded Fleet destination"):
        bench.evaluate_saved_tasks(bundle, run_dir, SimpleNamespace(), allow_judges=True)


def test_oolong_official_parser_retains_numeric_partial_credit():
    spec = importlib.util.spec_from_file_location("oolong_scoring", ROOT / "scripts/oolong_scoring.py")
    official = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(official)
    case = {
        "id": 1,
        "context_window_id": 1,
        "dataset": "test",
        "answer": "['12']",
        "answer_type": "ANSWER_TYPE.NUMERIC",
    }
    assert official.synth_process_response(case, "Answer: 12", "fleet")["score"] == 1
    assert official.synth_process_response(case, "Answer: 13", "fleet")["score"] == 0.75


def comparison_settings():
    values = {
        "execution_mode": "hybrid",
        "fleet_root_model": "openai/configured-model",
        "fleet_sub_model": "openai/configured-model",
        "fleet_shared_notes_subpath": "",
        "fleet_root_cache": False,
        "fleet_sub_cache": False,
        "fleet_root_max_tokens": 16384,
        "fleet_sub_max_tokens": None,
    }
    lm = SimpleNamespace(model="configured-model", cache=False, num_retries=3, kwargs={"max_tokens": 16384})
    return values, lm


@pytest.mark.parametrize("arm", ["hybrid", "rlm"])
@pytest.mark.parametrize("deadline", [0, 600])
def test_comparison_mode_deadline_prompt_and_evidence(tmp_path, monkeypatch, arm, deadline):
    values, lm = comparison_settings()
    bundle = tmp_path / "bundle"
    bench.freeze_tasks(
        bundle,
        [{"case_id": "task", "task": "published prompt\nsolution = ...", "question": {"answer": "secret-gold"}}],
        {"dataset": "external/tasks", "dataset_revision": "a" * 40},
    )
    requests, turns = api(monkeypatch, comparison=values)
    run = tmp_path / arm
    bench.run_tasks(
        bundle,
        run,
        base_url="http://fleet",
        expected_destination=DESTINATION,
        allow_live=True,
        arm=arm,
        direct_lm=lm,
        expected_configuration=values,
        timeout_seconds=deadline,
    )
    assert turns == [
        {"task": "published prompt\nsolution = ...", "idempotency_key": "task", "timeout_seconds": deadline}
    ]
    assert "secret-gold" not in b"".join(request.content for request in requests).decode()
    assert json.loads((run / "evidence/task/request.json").read_text()) == turns[0]
    assert "turn_complete" in (run / "evidence/task/response.sse").read_text()
    receipt = json.loads((run / "receipts/task.json").read_text())
    assert receipt["cleanup"] == "released"
    assert receipt["output"]["route"] == ("rlm" if arm == "rlm" else "flex")
    request_count = len(requests)
    with pytest.raises(ValueError, match="different frozen tasks or API"):
        bench.run_tasks(
            bundle,
            run,
            base_url="http://fleet",
            expected_destination=DESTINATION,
            allow_live=True,
            arm=arm,
            direct_lm=lm,
            expected_configuration=values,
            timeout_seconds=600 if deadline == 0 else 0,
        )
    assert len(requests) == request_count  # A changed deadline cannot rewrite or replay prior evidence.


@pytest.mark.parametrize("change", ["cache", "notes", "model", "budget"])
def test_comparison_configuration_mismatch_stops_before_paid_call(tmp_path, monkeypatch, change):
    expected, lm = comparison_settings()
    values = dict(expected)
    if change == "cache":
        values["fleet_root_cache"] = True
    elif change == "notes":
        values["fleet_shared_notes_subpath"] = "shared/learned-notes"
    elif change == "model":
        lm.model = "another-model"
    else:
        values["fleet_sub_max_tokens"] = 999
    bundle = tmp_path / "bundle"
    bench.freeze_tasks(bundle, [{"case_id": "task", "task": "prompt"}], {"dataset": "x", "dataset_revision": "a" * 40})
    _, turns = api(monkeypatch, comparison=values)
    with pytest.raises(RuntimeError, match="Benchmark stopped"):
        bench.run_tasks(
            bundle,
            tmp_path / "run",
            base_url="http://fleet",
            expected_destination=DESTINATION,
            allow_live=True,
            arm="rlm",
            direct_lm=lm,
            expected_configuration=expected,
        )
    assert turns == []
    assert json.loads((tmp_path / "run/receipts/task.json").read_text())["status"] == "preflight_failed"


@pytest.mark.parametrize("missing_usage", [False, True])
@pytest.mark.parametrize("deadline", [0, 600])
def test_direct_uses_native_request_and_response_without_gold(tmp_path, monkeypatch, missing_usage, deadline):
    import anyio
    import mlflow
    from dspy._vendor.lm15.transports import AsyncTransportResponse, StdlibAsyncTransport, Timeouts

    from fleet_rlm.config import FleetSettings

    monkeypatch.setenv("DATABRICKS_TOKEN", "test-only")
    requests = []

    @asynccontextmanager
    async def transport(_self, request):
        assert (anyio.current_effective_deadline() == float("inf")) is (deadline == 0)
        requests.append(request)

        async def chunks():
            yield json.dumps(
                {
                    "id": "provider-response",
                    "model": "reported-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "solution = 42"},
                            "finish_reason": "length",
                        }
                    ],
                    **(
                        {}
                        if missing_usage
                        else {"usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
                    ),
                }
            ).encode()

        async def release(_consumed):
            pass

        yield AsyncTransportResponse(
            status=200, reason="OK", headers=[], http_version="HTTP/1.1", chunks=chunks(), release=release
        )

    mlflow.set_experiment("direct-benchmark")
    mlflow.dspy.autolog(log_traces=True)
    monkeypatch.setattr(StdlibAsyncTransport, "stream", transport)
    settings = FleetSettings(
        fleet_root_api_base="https://gateway.invalid/ai-gateway/mlflow/v1",
        fleet_root_api_key_env="DATABRICKS_TOKEN",
        databricks_token="test-only",
        fleet_root_model="openai/system.ai.deepseek-v4-1-flash-1017",
        fleet_root_max_tokens=16384,
        fleet_root_temperature=0.2,
        fleet_root_cache=False,
        fleet_root_adapter="json",
        fleet_root_response_format="json_object",
    )
    from fleet_rlm.models import build_lm

    lm = build_lm(settings, settings.fleet_root_model, "root")
    case = {"task": "published prompt", "expected_answer": "secret-gold", "question": {"answer": "secret-payload"}}
    receipt = bench._direct_turn(lm, case, timeout_seconds=deadline, evidence_dir=tmp_path / "evidence")
    assert len(requests) == 1
    wire = json.loads(requests[0].body)
    assert wire["messages"] == [{"role": "user", "content": "published prompt"}]
    assert wire["response_format"] == {"type": "json_object"}
    assert not wire.get("tools")
    assert wire["model"] == "system.ai.deepseek-v4-1-flash-1017"
    assert wire["max_tokens"] == 16384 and wire["temperature"] == 0.2
    assert "max_completion_tokens" not in wire
    assert (
        lm.kwargs.get("timeout") is None and Timeouts().read == 600
    )  # Native SDK idle limit, not a benchmark deadline.
    assert receipt["output"]["answer"] == "solution = 42"
    assert receipt["output"]["finish_reason"] == "length"
    assert receipt["usage"][0]["usage"] == (
        {} if missing_usage else {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    )
    trace = mlflow.get_trace(receipt["output"]["trace_id"])
    assert trace.data.spans[0].inputs == {"task": "published prompt"}
    assert trace.data.spans[0].span_type == "CHAIN"
    assert len([span for span in trace.data.spans if span.span_type in {"LLM", "CHAT_MODEL"}]) == 1
    assert "mlflow.chat.tokenUsage" not in trace.data.spans[0].attributes
    assert "secret-gold" not in trace.to_json() and "secret-payload" not in trace.to_json()
    assert json.loads((tmp_path / "evidence/response.json").read_text())["id"] == "provider-response"
    delivered = bench._benchmark_trace(
        receipt,
        {"source_group": "cs:template"},
        experiment_id=mlflow.get_experiment_by_name("direct-benchmark").experiment_id,
    )
    assert delivered.info.trace_id == trace.info.trace_id


def test_direct_deadline_cancels_native_async_call_and_saves_failure(tmp_path):
    import anyio
    import dspy
    import mlflow

    cancelled = []

    class Engine:
        async def complete(self, _request):
            try:
                await anyio.sleep_forever()
            finally:
                cancelled.append(True)

    mlflow.set_experiment("direct-deadline")
    lm = dspy.LM(
        "test/model", engine=SimpleNamespace(complete=lambda _request: None), async_engine=Engine(), cache=False
    )
    with pytest.raises(TimeoutError):
        bench._direct_turn(lm, {"task": "prompt"}, timeout_seconds=0.01, evidence_dir=tmp_path / "evidence")
    assert cancelled == [True]
    assert json.loads((tmp_path / "evidence/response.json").read_text())["error_type"] == "TimeoutError"


@pytest.mark.parametrize(
    "outcome", ["complete", "rate_limit", "cleanup", "verifier_error", "trace_error", "duplicate_model", "wrong_route"]
)
def test_pilot_is_gated_rotates_arms_and_never_exceeds_ten_slots(tmp_path, monkeypatch, outcome):
    from fleet_rlm import observability
    from fleet_rlm.optimize import artifacts

    bundle = tmp_path / "bundle"
    cases = [
        {"case_id": domain, "task": f"{domain} prompt", "source_group": f"{domain}:template"}
        for domain in ("logic", "cs", "chemistry", "chess", "math")
    ]
    bench.freeze_tasks(bundle, cases, {"dataset": "x", "dataset_revision": "a" * 40, "hf_pin_verified": True})
    values, lm = comparison_settings()
    settings = SimpleNamespace(
        fleet_root_model="root-model",
        fleet_sub_model="sub-model",
        mlflow_target=lambda: SimpleNamespace(mode="local", experiment_name="runtime", tracking_uri="http://tracking"),
        mlflow_descriptor=lambda: DESTINATION,
        configuration=lambda: {"digest": "settings", "values": values},
        mlflow_trace_catalog="",
        mlflow_trace_schema="",
        mlflow_trace_table_prefix="",
        mlflow_tracing_sql_warehouse_id="",
    )
    monkeypatch.setattr(bench, "build_lm", lambda _settings, _model_id, _role: lm, raising=False)
    slots = []
    direct_calls = []
    requests, turns = api(
        monkeypatch,
        comparison=values,
        failure="LMRateLimitError" if outcome == "rate_limit" else None,
        cleanup_status=409 if outcome == "cleanup" else 200,
    )
    original_run = bench.run_tasks

    def record_slot(*args, **kwargs):
        assert kwargs["timeout"] is None and kwargs["timeout_seconds"] == 0
        slots.append((kwargs["case_ids"], kwargs["arm"]))
        return original_run(*args, **kwargs)

    def direct(_lm, case, **_kwargs):
        assert _kwargs["timeout_seconds"] == 0
        direct_calls.append(case["task"])
        return {
            "status": "completed",
            "output": {"answer": "42", "route": "direct", "trace_id": "direct-trace"},
            "usage": [{"model": "configured-model", "usage": {"input_tokens": 3}, "finish_reason": "length"}],
        }

    def verify(_case, _answer):
        if outcome == "verifier_error":
            raise ValueError("offline verifier failed")
        return 0  # A wrong answer is still a valid completed observation.

    def trace(_receipt, _case, **_kwargs):
        if outcome == "trace_error":
            raise RuntimeError("required trace unavailable")
        if outcome == "wrong_route":
            _receipt["output"]["route"] = "flex"
        return SimpleNamespace(
            data=SimpleNamespace(
                spans=[SimpleNamespace(span_type="CHAT_MODEL") for _ in range(2 if outcome == "duplicate_model" else 1)]
            )
        )

    monkeypatch.setattr(bench, "run_tasks", record_slot)
    monkeypatch.setattr(bench, "_direct_turn", direct)
    monkeypatch.setattr(bench, "version", lambda _name: "test")
    monkeypatch.setattr(observability, "configure_mlflow_observability", lambda *_args, **_kwargs: "runtime")
    monkeypatch.setattr(artifacts, "optimization_code_digest", lambda: "code")
    monkeypatch.setattr(bench, "_benchmark_trace", trace)
    with pytest.raises(ValueError, match="allow_live"):
        bench.run_comparison_pilot(bundle, tmp_path / "run", settings, base_url="http://fleet")
    if outcome != "complete":
        for _ in range(2):
            with pytest.raises(RuntimeError):
                bench.run_comparison_pilot(
                    bundle, tmp_path / "run", settings, base_url="http://fleet", allow_live=True, official_score=verify
                )
        assert len(direct_calls) == (1 if outcome in {"verifier_error", "duplicate_model"} else 0)
        assert len(turns) == 1
        assert not (tmp_path / "run/hybrid").exists()
        scores = bench.score_comparison_pilot(bundle, tmp_path / "run", lambda *_args: 1)
        assert not scores["arms"]["rlm"]["complete"]
        return
    run = tmp_path / "run"
    report = bench.run_comparison_pilot(
        bundle, run, settings, base_url="http://fleet", allow_live=True, official_score=verify
    )
    assert report["status"] == "finished" and len(slots) == 10
    assert [arm for _, arm in slots[:6]] == ["rlm", "direct", "direct", "rlm", "rlm", "direct"]
    assert [case_ids for case_ids, _arm in slots[::2]] == [["cs"], ["chemistry"], ["chess"], ["math"], ["logic"]]
    assert Counter(arm for _, arm in slots) == {"direct": 5, "rlm": 5}
    assert report["max_task_attempts"] == 10 and report["timeout_seconds"] == 0
    assert report["native_transport_timeouts"]["read"] == 600
    assert len(turns) == 5 and len(direct_calls) == 5
    assert set(json.loads((run / "first-pair.json").read_text())) == {"direct", "rlm"}
    assert all(turn["timeout_seconds"] == 0 for turn in turns)
    assert all(
        request.extensions["timeout"]["read"] is None for request in requests if request.url.path.endswith("/turn")
    )
    assert len({request.url.path for request in requests if request.method == "DELETE"}) == 10
    request_count = len(requests)
    bench.run_comparison_pilot(bundle, run, settings, base_url="http://fleet", allow_live=True, official_score=verify)
    assert len(requests) == request_count and len(direct_calls) == 5  # No paid replay.
    scores = bench.score_comparison_pilot(bundle, run, lambda *_args: 1)
    assert all(report["correct"] == 5 and report["complete"] for report in scores["arms"].values())
    assert scores["paired_vs_direct"] == {"rlm": {"ties": 5}}
    assert scores["arms"]["direct"]["truncated_cases"] == 5
    assert scores["arms"]["rlm"]["cases_with_unknown_truncation"] == 5


def test_pilot_requires_hf_verification_before_service_access(tmp_path):
    bundle, run = freeze(tmp_path)
    with pytest.raises(ValueError, match="Hugging Face MCP"):
        bench.run_comparison_pilot(bundle, run, SimpleNamespace(), base_url="http://fleet", allow_live=True)
    assert not run.exists()


def test_direct_postflight_failure_preserves_the_completed_answer_and_trace(tmp_path, monkeypatch):
    values, lm = comparison_settings()
    bundle = tmp_path / "bundle"
    bench.freeze_tasks(bundle, [{"case_id": "task", "task": "prompt"}], {"dataset": "x", "dataset_revision": "a" * 40})
    api(monkeypatch, comparison=values)
    original_health = bench._health
    health_calls = []

    def health(client):
        health_calls.append(True)
        if len(health_calls) == 2:
            raise RuntimeError("postflight unavailable")
        return original_health(client)

    output = {"answer": "saved answer", "trace_id": "saved-trace", "route": "direct"}

    def direct(_lm, _case, *, evidence_dir, **_kwargs):
        bench.save_json(evidence_dir / "response.json", {"id": "provider-response"})
        return {"status": "completed", "output": output, "usage": []}

    monkeypatch.setattr(bench, "_health", health)
    monkeypatch.setattr(bench, "_direct_turn", direct)
    run = tmp_path / "run"
    with pytest.raises(RuntimeError, match="Benchmark stopped"):
        bench.run_tasks(
            bundle,
            run,
            base_url="http://fleet",
            expected_destination=DESTINATION,
            allow_live=True,
            arm="direct",
            direct_lm=lm,
            expected_configuration=values,
        )
    receipt = json.loads((run / "receipts/task.json").read_text())
    assert receipt["status"] == "completed" and receipt["output"] == output
    assert receipt["stop_reason"] == "transport_or_service_failure" and receipt["cleanup"] == "released"


def test_partial_stream_keeps_evidence_and_requests_disconnect_cleanup(tmp_path, monkeypatch):
    values, lm = comparison_settings()
    bundle = tmp_path / "bundle"
    bench.freeze_tasks(bundle, [{"case_id": "task", "task": "prompt"}], {"dataset": "x", "dataset_revision": "a" * 40})
    requests, turns = api(monkeypatch, comparison=values)
    make_client = bench.httpx.Client

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"event":"turn_status","turn_id":"turn-interrupted"}\n\n'
            raise httpx.ReadTimeout("stream interrupted")

    def client(**kwargs):
        instance = make_client(**kwargs)
        stream = instance.stream

        def interrupted(method, url, **options):
            if url.endswith("/turn"):
                return nullcontext(
                    httpx.Response(200, request=httpx.Request(method, "http://fleet" + url), stream=BrokenStream())
                )
            return stream(method, url, **options)

        instance.stream = interrupted
        return instance

    monkeypatch.setattr(bench.httpx, "Client", client)
    run = tmp_path / "run"
    for _ in range(2):
        with pytest.raises(RuntimeError):
            bench.run_tasks(
                bundle,
                run,
                base_url="http://fleet",
                expected_destination=DESTINATION,
                allow_live=True,
                arm="rlm",
                direct_lm=lm,
                expected_configuration=values,
            )
    receipt = json.loads((run / "receipts/task.json").read_text())
    assert receipt["status"] == "uncertain" and receipt["cancellation"] == "stream_closed"
    assert receipt["turn_id"] == "turn-interrupted" and receipt["cleanup"] == "released"
    assert "turn-interrupted" in (run / "evidence/task/response.sse").read_text()
    assert any(request.method == "DELETE" for request in requests)
    assert turns == []  # No replacement of the interrupted request.


def test_comparison_requires_expected_configuration_before_any_request(tmp_path, monkeypatch):
    _, lm = comparison_settings()
    monkeypatch.setattr(bench, "load_tasks", lambda *_args: pytest.fail("must reject before loading tasks"))
    with pytest.raises(ValueError, match="comparison arm requires"):
        bench.run_tasks(
            tmp_path / "bundle",
            tmp_path / "run",
            base_url="http://fleet",
            expected_destination=DESTINATION,
            allow_live=True,
            arm="hybrid",
            direct_lm=lm,
            expected_configuration=None,
        )


# Runtime report comparison

REPORTS_SPEC = importlib.util.spec_from_file_location(
    "compare_runtime_reports", Path(__file__).parents[2] / "scripts/compare_runtime_reports.py"
)
reports = importlib.util.module_from_spec(REPORTS_SPEC)
REPORTS_SPEC.loader.exec_module(reports)


def _report():
    return {
        "status": "passed",
        "metrics": {
            case: {
                "route": case.removeprefix("route_"),
                "answer": answer,
                "lm_calls": 3,
                "wall_clock_s": 1.0,
                "outcome": "turn_complete",
                "event_kinds": ["turn_complete", "done"],
            }
            for case, answer in reports.EXPECTED_ANSWERS.items()
        },
    }


def _compare(baseline, after):
    return reports.compare([(Path("baseline.json"), baseline)], after)


def test_identical_correct_reports_pass():
    assert _compare(_report(), _report())["passed"]


@pytest.mark.parametrize("case", ["route_rlm", "route_flex", "route_hybrid"])
@pytest.mark.parametrize("side", ["baseline", "after"])
def test_wrong_nonempty_route_answer_fails(case, side):
    baseline, after = _report(), _report()
    (baseline if side == "baseline" else after)["metrics"][case]["answer"] = "wrong"
    verdict = _compare(baseline, after)
    assert not verdict["passed"]
    assert any("answer" in finding for finding in verdict["cases"][case]["findings"])


@pytest.mark.parametrize("omit_after", [False, True])
def test_case_missing_from_one_baseline_cannot_be_excluded(omit_after):
    baseline = _report()
    incomplete = copy.deepcopy(baseline)
    del incomplete["metrics"]["route_flex"]
    after = _report()
    if omit_after:
        del after["metrics"]["route_flex"]
    verdict = reports.compare([(Path("one"), baseline), (Path("two"), incomplete)], after)
    assert not verdict["passed"]
    if omit_after:
        assert verdict["missing_in_after"] == ["route_flex"]
    else:
        assert "case missing from a baseline report" in verdict["cases"]["route_flex"]["findings"]


@pytest.mark.parametrize("calls", [None, "3", True, -1, 4])
def test_missing_invalid_or_increased_after_budget_fails(calls):
    after = _report()
    after["metrics"]["route_rlm"]["lm_calls"] = calls
    assert not _compare(_report(), after)["passed"]


def test_omitted_after_budget_fails():
    after = _report()
    del after["metrics"]["route_rlm"]["lm_calls"]
    assert not _compare(_report(), after)["passed"]


# Generation-latency acceptance gates

LATENCY_SPEC = importlib.util.spec_from_file_location(
    "compare_generation_latency", Path(__file__).parents[2] / "scripts/compare_generation_latency.py"
)
latency = importlib.util.module_from_spec(LATENCY_SPEC)
LATENCY_SPEC.loader.exec_module(latency)


def completed_report():
    report = latency.experiment_plan()
    report["evaluation_run_ids"] = {arm: "eval-" + arm for arm in latency.ARMS}
    report["controls"] = {"fleet_root_model": "same-model", "fleet_root_cache": False}
    for index, row in enumerate(report["observations"]):
        route = {"direct": "direct", "children": "rlm"}.get(row["case"], "flex")
        row.update(
            measurement_version=latency.MEASUREMENT_VERSION,
            status="measured",
            correct=True,
            fresh_session=True,
            truncated=False,
            agent_model_id=f"m-{row['arm']}",
            agent_fingerprint=row["arm"],
            source_digest="source",
            dependency_digest="deps",
            program_digest="program",
            configuration_digest=row["arm"],
            expected_configuration_digest=row["arm"],
            controls_digest=latency._controls_digest(report["controls"]),
            root_max_tokens=latency.ARMS[row["arm"]][0],
            sub_max_tokens=latency.ARMS[row["arm"]][1],
            trace_id=f"trace-{index}",
            turn_id=f"turn-{index}",
            root_sandbox_id=f"sandbox-{index}",
            end_to_end_seconds=10 if row["arm"] == "baseline" else 8,
            model_call_seconds=5,
            input_tokens=200,
            output_tokens=100,
            generation_calls=2,
            parse_errors=0,
            parsing_recoveries=0,
            successful_children=2,
            child_sandbox_ids=[f"child-{index}-1", f"child-{index}-2"] if row["case"] == "children" else [],
            terminal={
                "outcome": "completed",
                "route": route,
                "answer": latency.GOLD[row["case"]],
                "lm_calls": 3,
                "budget_breakdown": {"generation_query": 3},
            },
        )
    return report


def test_exact_plan_keeps_gold_outside_inputs_and_caps_at_39():
    report = latency.experiment_plan()
    assert len(report["observations"]) == 39
    assert len({row["session_id"] for row in report["observations"]}) == 39
    for arm in latency.ARMS:
        rows = [row for row in report["observations"] if row["arm"] == arm]
        assert len(rows) == 13 and sum(row["case"] == "pi" for row in rows) == 1
    assert not any("expected" in case or "gold" in case for case in latency.CASES.values())


def test_measured_speedup_passes_without_changing_defaults():
    verdict = latency.compare(completed_report())
    assert not verdict["defaults_changed"]
    assert all(result["promotable"] for result in verdict["candidates"].values())


@pytest.mark.parametrize(
    "field,value",
    [
        ("correct", False),
        ("truncated", True),
        ("truncated", None),
        ("generation_calls", 100),
        ("agent_model_id", None),
        ("input_tokens", None),
        ("end_to_end_seconds", float("nan")),
        ("fresh_session", False),
        ("status", "attempted"),
        ("controls_digest", "changed"),
        ("root_max_tokens", 1000),
        ("agent_model_id", "m-different"),
        ("configuration_digest", "different"),
    ],
)
def test_incomplete_or_regressed_candidate_is_not_promoted(field, value):
    report = completed_report()
    row = next(row for row in report["observations"] if row["arm"] == "candidate_a")
    row[field] = value
    assert not latency.compare(report)["candidates"]["candidate_a"]["promotable"]


def test_bad_pi_canary_prevents_promotion_without_disqualifying_other_candidate():
    report = completed_report()
    next(row for row in report["observations"] if row["arm"] == "candidate_b" and row["case"] == "pi")["correct"] = (
        False
    )
    results = latency.compare(report)["candidates"]
    assert results["candidate_a"]["promotable"] and not results["candidate_b"]["promotable"]


def test_requires_ten_percent_median_short_task_improvement():
    report = completed_report()
    for row in report["observations"]:
        if row["arm"] == "candidate_a":
            row["end_to_end_seconds"] = 9.01
    assert not latency.compare(report)["candidates"]["candidate_a"]["promotable"]


def test_comparison_requires_retained_controls():
    report = completed_report()
    del report["controls"]
    assert not any(value["promotable"] for value in latency.compare(report)["candidates"].values())


@pytest.mark.parametrize("fault", ["duplicate_session", "missing_row", "changed_program", "unverified_children"])
def test_comparison_identity_and_isolation_gates(fault):
    report = completed_report()
    if fault == "duplicate_session":
        report["observations"][1]["session_id"] = report["observations"][0]["session_id"]
    elif fault == "missing_row":
        report["observations"].pop()
    elif fault == "changed_program":
        report["observations"][0]["program_digest"] = "different"
    else:
        for row in report["observations"]:
            row["successful_children"] = 0
    assert not any(value["promotable"] for value in latency.compare(report)["candidates"].values())


def test_missing_provider_finish_reason_is_unknown_and_wrapper_usage_is_not_double_counted():
    root = SimpleNamespace(
        name="fleet_turn",
        parent_id=None,
        span_id="root",
        span_type=SpanType.CHAIN,
        attributes={},
        outputs={"execution_evidence": {}},
    )
    wrapper = SimpleNamespace(
        name="Predict.forward",
        parent_id="root",
        span_id="wrapper",
        span_type=SpanType.LLM,
        attributes={"mlflow.chat.tokenUsage": {"input_tokens": 9999, "output_tokens": 9999}},
    )
    adapter = SimpleNamespace(name="ChatAdapter", parent_id="wrapper", span_id="adapter", span_type=SpanType.CHAIN)
    model = SimpleNamespace(
        name="LM.__call__",
        parent_id="adapter",
        span_id="model",
        span_type=SpanType.LLM,
        attributes={"mlflow.chat.tokenUsage": {"input_tokens": 10, "output_tokens": 20}},
        start_time_ns=1_000_000_000,
        end_time_ns=2_000_000_000,
        outputs=["391"],
    )
    trace = SimpleNamespace(
        search_spans=lambda: [root, wrapper, adapter, model], info=SimpleNamespace(trace_metadata={})
    )
    values = latency.trace_measurements(trace)
    assert values["input_tokens"] == 10 and values["output_tokens"] == 20
    assert values["generation_calls"] == 1 and values["model_call_seconds"] == 1
    assert values["truncated"] is None
    model.attributes["finish_reason"] = "length"
    assert latency.trace_measurements(trace)["truncated"] is True
    model.attributes["finish_reason"] = "stop"
    assert latency.trace_measurements(trace)["truncated"] is False
    trace.info.trace_metadata["mlflow.modelId"] = "m-native"
    root.attributes["fleet.agent.model_id"] = "m-different"
    with pytest.raises(ValueError, match="does not match the Turn identity"):
        latency.trace_measurements(trace)
    root.attributes["fleet.agent.model_id"] = "m-native"
    assert latency.trace_measurements(trace)["agent_model_id"] == "m-native"


@pytest.mark.parametrize("fault", ["none", "caps", "notes", "provider_failure"])
def test_collection_journals_intent_and_never_sends_gold_or_repeats_an_attempt(monkeypatch, tmp_path, fault):
    from contextlib import nullcontext

    report = latency.experiment_plan()
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(report))
    calls = []
    monkeypatch.setattr(latency.mlflow, "get_tracking_uri", lambda: "offline")
    monkeypatch.setattr(
        latency.mlflow, "start_run", lambda **_kw: nullcontext(SimpleNamespace(info=SimpleNamespace(run_id="eval")))
    )
    monkeypatch.setattr(latency, "read_evaluation_trace", lambda *_a, **_kw: object())
    monkeypatch.setattr(latency, "trace_measurements", lambda _trace: {"agent_model_id": "m-agent"})
    monkeypatch.setattr(
        latency.mlflow,
        "log_expectation",
        lambda **kwargs: (
            None
            if kwargs["name"] == "review_status" and kwargs["value"] == "benchmark"
            else pytest.fail("curation label")
        ),
    )

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, method, url, **kwargs):
            assert method in {"GET", "PUT", "POST"}
            if url == "/health":
                result = {"mlflow": {"ready": True, "experiment_id": "1"}}
            elif url.endswith("/history"):
                result = {"turns": []}
            elif url.endswith("/config"):
                result = {
                    "configuration": {
                        "digest": "config",
                        "mlflow_target": {"tracking_uri": "offline"},
                        "values": {
                            "fleet_root_max_tokens": 4096 if fault == "caps" else 16384,
                            "fleet_sub_max_tokens": None,
                            "fleet_shared_notes_subpath": "notes" if fault == "notes" else "",
                        },
                    }
                }
            elif url.endswith("/files"):
                result = {"files": []}
            else:
                result = {}
            if "files" in kwargs:
                assert "expected" not in str(kwargs)
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: result)

        def post(self, url, **kwargs):
            payload = kwargs["json"]
            calls.append((url, payload))
            saved = json.loads(path.read_text())
            session = url.split("/")[2]
            row = next(row for row in saved["observations"] if row["session_id"] == session)
            assert row["status"] == "attempted"
            assert set(payload) == {"task", "idempotency_key"}
            assert payload["task"] == latency.CASES[row["case"]]["task"]
            if fault == "provider_failure":
                raise RuntimeError("interrupted")
            outcome = {
                "answer": latency.GOLD[row["case"]],
                "trace_id": f"trace-{len(calls)}",
                "turn_id": f"turn-{len(calls)}",
            }
            events = [{"event": "turn_complete", "payload": outcome}, {"event": "done"}]
            text = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
            return SimpleNamespace(raise_for_status=lambda: None, text=text)

        def delete(self, _url):
            return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(latency.httpx, "Client", lambda **_kw: Client())
    if fault == "none":
        latency.collect(path, "baseline", "http://offline.invalid", "1")
        assert len(calls) == 13
        with pytest.raises(ValueError, match="already measured"):
            latency.collect(path, "baseline", "http://offline.invalid", "1")
        assert len(calls) == 13
    elif fault == "provider_failure":
        with pytest.raises(RuntimeError, match="interrupted"):
            latency.collect(path, "baseline", "http://offline.invalid", "1")
        with pytest.raises(ValueError, match="unresolved attempt"):
            latency.collect(path, "baseline", "http://offline.invalid", "1")
        assert len(calls) == 1
    else:
        with pytest.raises(ValueError, match=r"caps|learned notes"):
            latency.collect(path, "baseline", "http://offline.invalid", "1")
        assert calls == []


def test_collection_rejects_tampered_session_before_contacting_server(tmp_path, monkeypatch):
    report = latency.experiment_plan()
    report["observations"][0]["session_id"] = "../../other-session"
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    monkeypatch.setattr(latency.httpx, "Client", lambda **_kwargs: pytest.fail("unexpected network access"))
    with pytest.raises(ValueError, match="observation identity"):
        latency.collect(path, "baseline", "http://offline.invalid", "1")


# Consolidated from optimize/test_scorers.py


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


@pytest.mark.parametrize("root_count", [0, 1, 2])
def test_trace_readers_require_one_host_root_and_ignore_nested_turns(root_count):
    from fleet_rlm.optimize.scorers import ScorerEvidenceError, _trace_evidence_context

    outputs = {"answer": "Done", "lm_calls": 3, "sandbox_errors": 0, "submit_rejections": 0}
    roots = [_span("fleet_turn", outputs=json.dumps(json.dumps(outputs))) for _ in range(root_count)]
    nested = _span("fleet_turn", outputs={"lm_calls": 999}, parent_id="parent")
    trace = _trace(*roots, nested)
    if root_count == 1:
        assert _trace_evidence_context(trace)[0]["answer"] == "Done"
    else:
        with pytest.raises(ScorerEvidenceError, match=f"found {root_count}"):
            _trace_evidence_context(trace)


def test_route_correctness_matches_gold_label():
    assert route_correctness(outputs={"route": "rlm"}, expectations={"route": "rlm"}).value is True
    assert route_correctness(outputs={"route": "direct"}, expectations={"route": "rlm"}).value is False
    assert route_correctness(outputs={"route": "direct"}, expectations={}) is None


def test_scorer_suite_includes_route_correctness():
    suite = build_mlflow_scorer_suite("databricks:/test-judge")
    names = [item.name for item in suite]
    assert set(names) == {
        "relevance_to_query",
        "retrieval_groundedness",
        "jev_routing_efficiency_judge",
        "execution_evidence_diagnostics",
        "route_correctness",
    }


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
    ]
    names = [item.name for item in feedback]
    assert len(names) == len(set(names)) == 3
    assert set(names) == {
        "jev_routing_efficiency_judge",
        "sandbox_verification_discipline",
        "tool_execution_integrity",
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
        },
    )
    execution_scorer = _scorer("execution_evidence_diagnostics")
    with pytest.raises(ScorerEvidenceError, match=message):
        execution_scorer(trace=_pi_execution_trace())


def test_route_suitability_judge_is_separate_from_deterministic_budget_efficiency(monkeypatch):
    calls = _judge_responses(
        monkeypatch,
        {
            "execution_evidence_diagnostics": (_execution_judge_payload(), "structured result"),
            "jev_routing_efficiency_judge": (True, "Flex was suitable for this bounded calculation."),
        },
    )
    route_scorer = _scorer("jev_routing_efficiency_judge")
    trace = _pi_execution_trace()
    assert route_scorer(trace=trace).value is True
    assert routing_efficiency(True, 6)[0] == 0.95
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


def test_should_share_host_recorded_recovery_penalties_with_fleet_metric():
    outputs = {"answer": "5", "lm_calls": 10, "sandbox_errors": 1, "submit_rejections": 1}
    result = fleet_metric(dspy.Example(task="digit", answer="5"), dspy.Prediction(**outputs), judge_lm=_judge(True))
    assert result.objective_scores["efficiency"] == 0.595
    assert result.score == pytest.approx(0.9595)


@pytest.mark.parametrize("value", [None, True, -1, 1.5], ids=["missing", "bool", "negative", "fraction"])
def test_should_withhold_efficiency_credit_without_explicit_rejection_evidence(value):
    outputs = {"answer": "5", "lm_calls": 1, "sandbox_errors": 0, "submit_rejections": value}
    result = fleet_metric(dspy.Example(task="digit"), dspy.Prediction(**outputs), judge_lm=_judge(True))
    assert result.objective_scores["usage_available"] == 0
    assert "efficiency" not in result.objective_scores


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
