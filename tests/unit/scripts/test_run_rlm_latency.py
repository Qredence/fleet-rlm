from __future__ import annotations

import json

import pytest

from scripts.benchmarks.run_rlm_latency import (
    QUALITY_RECORDS,
    _aggregate,
    _termination_mode_from_chunk,
    _usage_totals,
    latency_gate,
    main,
    percentile,
    quality_gate,
)


def test_nearest_rank_percentiles_are_deterministic() -> None:
    values = list(range(1, 21))
    assert percentile(values, 50) == 10
    assert percentile(values, 95) == 19
    with pytest.raises(ValueError):
        percentile([], 50)


def test_latency_gate_requires_improvement_tail_stability_and_quality() -> None:
    baseline = {"end_to_end_ms": {"p50": 100.0, "p95": 150.0}, "error_rate": 0.0}
    candidate = {
        "end_to_end_ms": {"p50": 80.0, "p95": 149.0},
        "error_rate": 0.0,
        "quality_complete": True,
    }
    assert latency_gate(baseline, candidate)["passed"] is True
    candidate["end_to_end_ms"]["p95"] = 151.0
    assert latency_gate(baseline, candidate)["passed"] is False


def test_quality_dataset_is_five_bounded_json_records() -> None:
    assert len(QUALITY_RECORDS) == 5
    encoded = json.dumps(QUALITY_RECORDS)
    assert "required_evidence" in encoded
    assert "forbidden_claims" in encoded
    assert "provider_request_id" not in encoded


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


def test_cli_writes_bounded_failure_receipt(tmp_path) -> None:
    output = tmp_path / "failed.json"
    assert main(["compare", "--output", str(output)]) == 1
    payload = json.loads(output.read_text())
    assert payload.pop("generated_at")
    assert payload == {
        "schema": "fleet.rlm-latency/v1",
        "command": "compare",
        "status": "failed",
        "error_category": "BenchmarkError",
    }
