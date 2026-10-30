"""Offline acceptance gates for the opt-in comparison; no model calls."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from mlflow.entities import SpanType

SPEC = importlib.util.spec_from_file_location(
    "compare_generation_latency", Path(__file__).parents[2] / "scripts/compare_generation_latency.py"
)
latency = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(latency)


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
