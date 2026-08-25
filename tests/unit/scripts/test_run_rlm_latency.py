from __future__ import annotations

import gzip
import json
import json as json_module
import sys
from types import SimpleNamespace

import pytest

from scripts.benchmarks.run_rlm_latency import (
    CORRECTNESS_DESCRIPTION,
    CORRECTNESS_INSTRUCTIONS,
    DEFAULT_JUDGE_MODEL,
    EVIDENCE_COVERAGE_DESCRIPTION,
    EVIDENCE_COVERAGE_INSTRUCTIONS,
    JUDGE_INFERENCE_PARAMS,
    QUALITY_RECORDS,
    BenchmarkError,
    _aggregate,
    _attach_trace_identity,
    _execution_trace_diagnostics,
    _termination_mode_from_chunk,
    _upload_corpus,
    _usage_totals,
    build_parser,
    latency_gate,
    main,
    percentile,
    quality_gate,
    run_benchmark,
    run_turn,
)


class _Response:
    def __init__(self, *, payload: object | None = None, lines: list[str] | None = None) -> None:
        self._payload = payload
        self._lines = lines or []

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload

    def iter_lines(self) -> list[str]:
        return self._lines


class _Stream:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def __enter__(self) -> _Response:
        return self.response

    def __exit__(self, *_args: object) -> None:
        return None


class _TurnClient:
    def __init__(self) -> None:
        self.turn_request: dict[str, object] | None = None

    def post(self, path: str, **_kwargs: object) -> _Response:
        assert path == "/api/sessions"
        return _Response(payload={"id": "session-1"})

    def stream(self, _method: str, _path: str, *, json: dict[str, object], headers: dict[str, str]) -> _Stream:
        self.turn_request = json
        assert headers["Idempotency-Key"].startswith("rlm-latency-")
        lines = [
            "data: " + json_module.dumps({"type": "data-attachment", "data": {"attachment_id": "attachment-1"}}),
            "data: "
            + json_module.dumps(
                {
                    "type": "data-rlm-code",
                    "data": {"code": "source = read_attachment(attachment_id=attachments[0]['id'])"},
                }
            ),
            "data: " + json_module.dumps({"type": "data-rlm-output", "data": {"output": "FINAL submitted"}}),
            "data: " + json_module.dumps({"type": "data-structured-result", "data": {"value": "{}"}}),
            "data: " + json_module.dumps({"type": "finish", "finishReason": "stop"}),
        ]
        return _Stream(_Response(lines=lines))


class _UploadClient:
    def __init__(self) -> None:
        self.path: str | None = None
        self.files: dict[str, object] | None = None

    def post(self, path: str, **kwargs: object) -> _Response:
        self.path = path
        self.files = kwargs["files"]
        return _Response(payload={"id": "attachment-1"})


def test_nearest_rank_percentiles_are_deterministic() -> None:
    values = list(range(1, 21))
    assert percentile(values, 50) == 10
    assert percentile(values, 95) == 19
    with pytest.raises(ValueError):
        percentile([], 50)


def test_run_turn_propagates_attachment_ids_and_captures_bounded_trajectory() -> None:
    client = _TurnClient()

    row = run_turn(client, "inspect the attachment", nonce="test", attachment_ids=("attachment-1",))

    assert client.turn_request == {
        "text": "inspect the attachment\n\nBenchmark nonce: test. It has no semantic meaning.",
        "attachment_ids": ["attachment-1"],
        "skill_selections": [],
    }
    assert row["attachment_accessed"] is True
    assert row["trajectory"] == {
        "codes": ["source = read_attachment(attachment_id=attachments[0]['id'])"],
        "outputs": ["FINAL submitted"],
    }
    assert row["termination_mode"] == "typed_submit"


def test_upload_corpus_uses_the_attachment_route_and_preserves_host_fixture(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.ndjson"
    corpus_path.write_text("entry-1\nentry-2\n", encoding="utf-8")
    client = _UploadClient()

    assert _upload_corpus(client, corpus_path, seed=1) == "attachment-1"

    assert client.path == "/api/attachments"
    assert client.files is not None
    filename, body, content_type = client.files["attachment"]
    assert filename == "fleet-corpus-1.ndjson.gz"
    assert gzip.decompress(body) == corpus_path.read_bytes()
    assert content_type == "application/gzip"


def test_corpus_quality_aggregate_requires_report_and_evidence() -> None:
    incomplete = _aggregate(
        [
            {
                "sample_kind": "measured",
                "duration_ms": 100,
                "first_event_ms": 10,
                "corpus_validation": {"passed": True},
                "corpus_evidence": {"passed": False},
                "corpus_quality_passed": False,
            }
        ],
        workload_id="corpus-chain-v1",
    )
    complete = _aggregate(
        [
            {
                "sample_kind": "measured",
                "duration_ms": 100,
                "first_event_ms": 10,
                "corpus_validation": {"passed": True},
                "corpus_evidence": {"passed": True},
                "corpus_quality_passed": True,
            }
        ],
        workload_id="corpus-chain-v1",
    )

    assert incomplete["corpus_report_complete"] is True
    assert incomplete["corpus_evidence_complete"] is False
    assert incomplete["quality_complete"] is False
    assert complete["corpus_report_complete"] is True
    assert complete["corpus_evidence_complete"] is True
    assert complete["quality_complete"] is True


def test_latency_gate_requires_improvement_tail_stability_and_quality() -> None:
    baseline = {"end_to_end_ms": {"p50": 100.0, "p95": 150.0}, "error_rate": 0.0}
    candidate = {
        "end_to_end_ms": {"p50": 50.0, "p95": 149.0},
        "error_rate": 0.0,
        "quality_complete": True,
    }
    assert latency_gate(baseline, candidate)["passed"] is True
    candidate["end_to_end_ms"]["p50"] = 51.0
    assert latency_gate(baseline, candidate)["passed"] is False
    candidate["end_to_end_ms"]["p50"] = 50.0
    candidate["end_to_end_ms"]["p95"] = 151.0
    assert latency_gate(baseline, candidate)["passed"] is False


def test_quality_dataset_is_five_bounded_json_records() -> None:
    assert len(QUALITY_RECORDS) == 5
    encoded = json.dumps(QUALITY_RECORDS)
    assert "required_evidence" in encoded
    assert "forbidden_claims" in encoded
    assert "provider_request_id" not in encoded


def test_default_judge_is_the_probe_verified_qwen_endpoint() -> None:
    assert DEFAULT_JUDGE_MODEL == "databricks:/databricks-qwen35-122b-a10b"
    assert JUDGE_INFERENCE_PARAMS == {"temperature": 0, "reasoning_effort": "low"}
    assert "expected_response" in CORRECTNESS_INSTRUCTIONS
    assert CORRECTNESS_DESCRIPTION.startswith("Check whether the response")
    assert "required_evidence" in EVIDENCE_COVERAGE_INSTRUCTIONS
    assert "required_uncertainty" in EVIDENCE_COVERAGE_INSTRUCTIONS
    assert "forbidden_claims" in EVIDENCE_COVERAGE_INSTRUCTIONS
    assert EVIDENCE_COVERAGE_DESCRIPTION.startswith("Check whether the response")


def test_quality_gate_requires_all_five_records_and_perfect_means() -> None:
    evaluation = {
        "dry_run": False,
        "records": 5,
        "metrics": {"correctness/mean": 1.0, "evidence_coverage/mean": 1.0},
    }
    assert quality_gate(evaluation) is True
    evaluation["records"] = 3
    assert quality_gate(evaluation) is False


def test_aggregate_excludes_failed_samples_from_latency_percentiles() -> None:
    rows = [
        {"sample_kind": "warmup", "duration_ms": 1.0, "first_event_ms": 1.0},
        {"sample_kind": "measured", "duration_ms": 100.0, "first_event_ms": 10.0},
        {"sample_kind": "measured", "duration_ms": 200.0, "first_event_ms": 20.0},
        {
            "sample_kind": "measured",
            "duration_ms": 5.0,
            "first_event_ms": -1.0,
            "error_category": "BenchmarkError",
        },
    ]

    summary = _aggregate(rows)

    assert summary["sample_count"] == 3
    assert summary["error_rate"] == pytest.approx(1 / 3)
    assert summary["end_to_end_ms"] == {"mean": 150.0, "p50": 100.0, "p95": 200.0}
    assert summary["first_runtime_event_ms"] == {"p50": 10.0, "p95": 20.0}


def test_execution_trace_diagnostics_exposes_wall_time_and_parse_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P38-RLM-006: provider-response timing was removed from engineering
    # spans; the benchmark reports wall-time diagnostics only.
    spans = [
        SimpleNamespace(
            name="RLM.root_lm",
            inputs={"context_chars": 12},
            outputs={"wall_time_ms": 100.0},
        ),
        SimpleNamespace(
            name="RLM.root_lm",
            inputs={"context_chars": 24},
            outputs={"wall_time_ms": 220.0},
        ),
        SimpleNamespace(
            name="RLM.execute",
            inputs={},
            outputs={
                "failure_category": "adapter_parse_error",
                "last_lm_call": {"response_keys": ()},
            },
        ),
    ]
    fake_mlflow = SimpleNamespace(
        set_tracking_uri=lambda _url: None,
        get_trace=lambda _trace_id: SimpleNamespace(data=SimpleNamespace(spans=spans)),
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

    diagnostics = _execution_trace_diagnostics("http://localhost:5001", "trace-1")

    assert diagnostics == {
        "root_lm_span_count": 2,
        "root_lm_wall_time_ms": 320.0,
        "root_lm_slowest_wall_time_ms": 220.0,
        "root_lm_max_context_chars": 24,
        "adapter_parse_error_count": 1,
        "last_lm_response_keys": [],
        "repair_error_count": 0,
        "detail_overflowed": False,
    }


def test_usage_totals_keep_only_approved_counters() -> None:
    assert _usage_totals(
        {
            "root": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "completion_tokens_details": {"reasoning_tokens": 3},
                "cache_read_input_tokens": 2,
                "cost": 99,
            }
        }
    ) == {"prompt_tokens": 10, "completion_tokens": 4, "reasoning_tokens": 3, "cache_read_tokens": 2}


def test_termination_mode_requires_explicit_stream_evidence() -> None:
    assert _termination_mode_from_chunk({"type": "data-rlm-output", "data": {"output": "FINAL submitted"}}) == (
        "typed_submit"
    )
    assert _termination_mode_from_chunk({"type": "reasoning-delta", "delta": "Extract forced final output"}) == (
        "native_extraction_fallback"
    )
    assert _termination_mode_from_chunk({"type": "finish", "finishReason": "stop"}) is None


def test_aggregate_excludes_failed_durations_from_latency_metrics() -> None:
    aggregate = _aggregate(
        [
            {"sample_kind": "measured", "duration_ms": 100, "first_event_ms": 10},
            {"sample_kind": "measured", "duration_ms": 10_000, "first_event_ms": -1, "error_category": "failed"},
            {"sample_kind": "warmup", "duration_ms": 1_000, "first_event_ms": 1},
        ]
    )

    assert aggregate["sample_count"] == 2
    assert aggregate["end_to_end_ms"] == {"mean": 100.0, "p50": 100.0, "p95": 100.0}
    assert aggregate["error_rate"] == 0.5


def test_trace_identity_must_match_public_and_execution_roots() -> None:
    row = {"trace_id": "tr-current"}

    _attach_trace_identity(row, "tr-current")

    assert row["stream_trace_id"] == "tr-current"
    assert row["execution_trace_id"] == "tr-current"
    assert row["trace_id_match"] is True

    with pytest.raises(BenchmarkError, match="did not match"):
        _attach_trace_identity({"trace_id": "tr-current"}, "tr-previous")


def test_parser_supports_seeded_corpus_workloads() -> None:
    args = build_parser().parse_args(
        [
            "benchmark",
            "--workload",
            "corpus-chain-v1",
            "--corpus-seed",
            "1",
            "--output",
            "receipt.json",
        ]
    )

    assert args.workload == "corpus-chain-v1"
    assert args.corpus_seed == 1


def test_cli_writes_bounded_failure_receipt(tmp_path) -> None:
    output = tmp_path / "failed.json"
    assert main(["compare", "--output", str(output)]) == 1
    payload = json.loads(output.read_text())
    generated_at = payload.pop("generated_at")
    assert generated_at
    assert payload == {
        "schema": "fleet.rlm-latency/v1",
        "command": "compare",
        "status": "failed",
        "error_category": "BenchmarkError",
    }


def test_failed_stream_retains_adapter_parse_error_count_via_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: failed streams preserve IDs and collect parse-error diagnostics."""

    class _FailingTurnClient:
        def __enter__(self) -> _FailingTurnClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, path: str, **_kwargs: object) -> _Response:
            if path == "/api/sessions":
                return _Response(payload={"id": "session-1"})
            return _Response(payload={})

        def get(self, path: str, **_kwargs: object) -> _Response:
            if path == "/api/settings":
                return _Response(
                    payload={
                        "active_profile": "default",
                        "scopes": [{"name": "default", "fields": []}],
                    }
                )
            return _Response(payload={})

        def stream(self, _method: str, _path: str, **_kwargs: object) -> _Stream:
            lines = [
                "data: "
                + json_module.dumps(
                    {
                        "type": "messageMetadata",
                        "messageMetadata": {"traceId": "tr-1", "runId": "run-1"},
                    }
                ),
                "data: " + json_module.dumps({"type": "error", "errorText": "Adapter parse failure"}),
            ]
            return _Stream(_Response(lines=lines))

    execution_trace_spans = [
        SimpleNamespace(
            name="RLM.execute",
            inputs={},
            outputs={
                "failure_category": "adapter_parse_error",
                "last_lm_call": {"response_keys": ["invalid"]},
            },
        ),
    ]

    fake_mlflow = SimpleNamespace(
        set_tracking_uri=lambda _url: None,
        get_trace=lambda _trace_id: SimpleNamespace(data=SimpleNamespace(spans=execution_trace_spans)),
        search_traces=lambda **_kwargs: [],
        MlflowClient=lambda: SimpleNamespace(set_trace_tag=lambda *_args: None),
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
    monkeypatch.setenv("FLEET_LIVE", "1")

    args = build_parser().parse_args(
        [
            "benchmark",
            "--warmups",
            "0",
            "--runs",
            "1",
            "--output",
            "receipt.json",
        ]
    )

    with monkeypatch.context() as m:
        m.setattr("httpx.Client", lambda **_kwargs: _FailingTurnClient())
        receipt = run_benchmark(args)

    aggregate = receipt["aggregate"]
    assert aggregate["sample_count"] == 1
    assert aggregate["error_rate"] == 1.0
    assert aggregate["adapter_parse_error_count"] == 1
