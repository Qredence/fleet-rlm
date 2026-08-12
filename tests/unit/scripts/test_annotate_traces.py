from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from scripts.benchmarks.annotate_traces import (
    AnnotationError,
    annotate,
    build_parser,
    derive_attributes,
    main,
)


class _FakeSpan:
    def __init__(
        self,
        name: str,
        span_type: str,
        *,
        start_ns: int = 0,
        end_ns: int = 1_000_000,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.span_type = span_type
        self.start_time_ns = start_ns
        self.end_time_ns = end_ns
        self._attributes = dict(attributes or {})

    def get_attribute(self, key: str) -> Any:
        return self._attributes.get(key)


class _FakeTrace:
    def __init__(
        self,
        trace_id: str,
        *,
        state: str = "OK",
        execution_duration: int | None = 42,
        spans: list[_FakeSpan] | None = None,
    ) -> None:
        self.info = SimpleNamespace(trace_id=trace_id, state=state, execution_duration=execution_duration)
        self.data = SimpleNamespace(spans=list(spans or []))


def _install_fake_mlflow(
    monkeypatch: pytest.MonkeyPatch,
    *,
    traces: list[_FakeTrace] | None = None,
    experiment_name: str = "fleet-rlm",
) -> SimpleNamespace:
    monkeypatch.delenv("FLEET_MLFLOW_EXPERIMENT_NAME", raising=False)
    calls = SimpleNamespace(tags=[], searches=[])

    class _FakeClient:
        def set_trace_tag(self, trace_id: str, key: str, value: str) -> None:
            calls.tags.append((trace_id, key, value))

    client_mod = ModuleType("mlflow.tracking.client")
    client_mod.MlflowClient = _FakeClient  # type: ignore[attr-defined]
    tracking_mod = ModuleType("mlflow.tracking")
    tracking_mod.client = client_mod  # type: ignore[attr-defined]

    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    mlflow.get_experiment_by_name = (  # type: ignore[attr-defined]
        lambda name: SimpleNamespace(experiment_id="exp-1") if name == experiment_name else None
    )

    def search_traces(**kwargs: Any) -> list[_FakeTrace]:
        calls.searches.append(kwargs)
        return list(traces or [])

    mlflow.search_traces = search_traces  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking_mod)
    monkeypatch.setitem(sys.modules, "mlflow.tracking.client", client_mod)
    return calls


def _args(argv: list[str], tmp_path) -> object:
    return build_parser().parse_args([*argv, "--output", str(tmp_path / "receipt.json")])


def test_derive_attributes_extracts_llm_tool_latency_and_tokens() -> None:
    spans = [
        _FakeSpan(
            "fleet_turn",
            "CHAIN",
            start_ns=0,
            end_ns=5_000_000,
            attributes={"status": "OK"},
        ),
        _FakeSpan(
            "LM.model",
            "LLM",
            start_ns=0,
            end_ns=1_000_000,
            attributes={
                "model_name": "databricks:/databricks-qwen35-122b-a10b",
                "provider": "databricks",
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            },
        ),
        _FakeSpan("remember", "TOOL", attributes={}),
    ]
    trace = _FakeTrace("trace-1", state="ERROR", execution_duration=None, spans=spans)

    attributes = derive_attributes(trace)

    assert attributes["fleet.turn_status"] == "error"
    assert attributes["fleet.latency_ms"] == "5"
    assert attributes["fleet.models"] == "databricks:/databricks-qwen35-122b-a10b"
    assert attributes["fleet.providers"] == "databricks"
    assert attributes["fleet.tools"] == "remember"
    assert attributes["fleet.prompt_tokens"] == "12"
    assert attributes["fleet.completion_tokens"] == "3"
    assert attributes["fleet.total_tokens"] == "15"
    assert attributes["fleet.span_types"] == "chain:1,llm:1,tool:1"


def test_derive_attributes_uses_execution_duration_and_skips_empty() -> None:
    trace = _FakeTrace("trace-2", state="OK", execution_duration=120, spans=[])
    attributes = derive_attributes(trace)
    assert attributes == {"fleet.turn_status": "ok", "fleet.latency_ms": "120"}


def test_annotate_stamps_bounded_tags_and_reports_aggregates(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    traces = [
        _FakeTrace(
            "trace-a",
            spans=[_FakeSpan("LM.a", "LLM", attributes={"model_name": "model-a"})],
        ),
        _FakeTrace("trace-b", spans=[]),
        _FakeTrace("", spans=[]),
    ]
    calls = _install_fake_mlflow(monkeypatch, traces=traces)

    receipt = main(["annotate", "--experiment-id", "exp-1", "--output", str(tmp_path / "r.json")])

    assert receipt == 0
    payload = json.loads((tmp_path / "r.json").read_text())
    assert payload["status"] == "ok"
    assert payload["traces_seen"] == 3
    assert payload["traces_annotated"] == 2
    assert payload["traces_skipped"] == 1
    assert payload["tags_written"] > 0
    assert "fleet.turn_status" in payload["tag_counts"]
    assert calls.searches[0]["locations"] == ["exp-1"]
    tagged_trace_a = [tag for tag in calls.tags if tag[0] == "trace-a"]
    assert any(key == "fleet.models" and value == "model-a" for _tid, key, value in tagged_trace_a)
    assert not any(tid == "" for tid, _key, _value in calls.tags)


def test_annotate_applies_tag_filter(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch, traces=[_FakeTrace("trace-a")])

    main(
        [
            "annotate",
            "--experiment-id",
            "exp-1",
            "--tag",
            "fleet_eval_candidate",
            "--output",
            str(tmp_path / "r.json"),
        ]
    )

    assert calls.searches[0]["filter_string"] == "tag.fleet_eval_candidate = 'true'"


def test_annotate_resolves_experiment_by_name(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch, traces=[_FakeTrace("trace-a")])

    main(["annotate", "--experiment-name", "fleet-rlm", "--output", str(tmp_path / "r.json")])

    assert calls.searches[0]["locations"] == ["exp-1"]


def test_annotate_requires_live(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "0")
    _install_fake_mlflow(monkeypatch)
    with pytest.raises(AnnotationError, match="FLEET_LIVE"):
        annotate(_args(["annotate", "--experiment-id", "exp-1"], tmp_path))


def test_main_writes_failure_receipt_for_invalid_limit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "0")
    output = tmp_path / "failed.json"
    assert main(["annotate", "--limit", "0", "--output", str(output)]) == 1
    payload = json.loads(output.read_text())
    assert payload.pop("generated_at")
    assert payload == {
        "schema": "fleet.trace-annotation/v1",
        "command": "annotate",
        "status": "failed",
        "error_category": "AnnotationError",
    }
