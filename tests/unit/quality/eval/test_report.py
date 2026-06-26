"""Unit tests for EvaluationReport serialization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleet_rlm.quality.eval.report import EvaluationReport


class TestEvaluationReportSerialization:
    """Tests for EvaluationReport JSON serialization."""

    def test_to_json_produces_valid_json(self) -> None:
        """Test that to_json produces valid JSON."""
        report = EvaluationReport.build(
            run_id="test-run-123",
            filters={"from_last_days": 1},
            per_trace=[
                {
                    "trace_id": "tr-1",
                    "answer_relevance": 0.9,
                    "faithfulness_to_context": 0.8,
                    "trajectory_coherence": 0.85,
                    "tool_selection_quality": 0.75,
                    "timeout_compliance": 1.0,
                    "trace_completeness": 1.0,
                    "token_cost": 150.0,
                    "latency_p95": 5.0,
                    "routing_correctness": 1.0,
                    "trajectory_redundancy": 0.0,
                }
            ],
        )

        json_str = report.to_json()
        parsed = json.loads(json_str)

        assert parsed["run_id"] == "test-run-123"
        assert parsed["filters"] == {"from_last_days": 1}
        assert len(parsed["per_trace"]) == 1
        assert "aggregates" in parsed

    def test_from_json_roundtrip(self) -> None:
        """Test that from_json correctly deserializes JSON."""
        report = EvaluationReport.build(
            run_id="test-run-456",
            filters={"limit": 10},
            per_trace=[
                {
                    "trace_id": "tr-1",
                    "answer_relevance": 0.9,
                }
            ],
        )

        json_str = report.to_json()
        restored = EvaluationReport.from_json(json_str)

        assert restored.run_id == report.run_id
        assert restored.filters == report.filters
        assert len(restored.per_trace) == len(report.per_trace)

    def test_write_and_read_from_disk(self, tmp_path: Path) -> None:
        """Test writing and reading report from disk."""
        report = EvaluationReport.build(
            run_id="test-run-789",
            filters={"trace_ids": ["tr-1", "tr-2"]},
            per_trace=[
                {
                    "trace_id": "tr-1",
                    "answer_relevance": 0.95,
                    "timeout_compliance": 1.0,
                },
                {
                    "trace_id": "tr-2",
                    "answer_relevance": 0.85,
                    "timeout_compliance": 0.5,
                },
            ],
        )

        # Write to disk
        report_path = report.write_to_disk(tmp_path)
        assert report_path.exists()
        assert report_path.name == "report.json"

        # Read from disk
        restored = EvaluationReport.read_from_disk(tmp_path)
        assert restored.run_id == report.run_id
        assert len(restored.per_trace) == 2


class TestEvaluationReportAggregates:
    """Tests for EvaluationReport aggregate computation."""

    def test_computes_mean_and_median(self) -> None:
        """Test that aggregates include mean and median."""
        report = EvaluationReport.build(
            run_id="test-run",
            filters={},
            per_trace=[
                {"trace_id": "tr-1", "answer_relevance": 0.8, "token_cost": 100.0},
                {"trace_id": "tr-2", "answer_relevance": 0.9, "token_cost": 200.0},
                {"trace_id": "tr-3", "answer_relevance": 1.0, "token_cost": 300.0},
            ],
        )

        assert "mean" in report.aggregates
        assert "median" in report.aggregates

        # Check mean calculations
        assert report.aggregates["mean"]["answer_relevance"] == pytest.approx(0.9)
        assert report.aggregates["mean"]["token_cost"] == pytest.approx(200.0)

        # Check median calculation
        assert report.aggregates["median"]["answer_relevance"] == pytest.approx(0.9)
        assert report.aggregates["median"]["token_cost"] == pytest.approx(200.0)

    def test_handles_empty_per_trace(self) -> None:
        """Test that aggregates handle empty per_trace gracefully."""
        report = EvaluationReport.build(
            run_id="test-run-empty",
            filters={},
            per_trace=[],
        )

        assert "mean" in report.aggregates
        assert "median" in report.aggregates
        # All values should be 0.0 for empty traces
        assert report.aggregates["mean"]["answer_relevance"] == 0.0

    def test_includes_all_10_metrics(self) -> None:
        """Test that aggregates include all 10 metrics (4 judges + 6 metrics)."""
        report = EvaluationReport.build(
            run_id="test-run",
            filters={},
            per_trace=[{"trace_id": "tr-1"}],
        )

        expected_metrics = [
            "answer_relevance",
            "faithfulness_to_context",
            "trajectory_coherence",
            "tool_selection_quality",
            "timeout_compliance",
            "trace_completeness",
            "token_cost",
            "latency_p95",
            "routing_correctness",
            "trajectory_redundancy",
        ]

        for metric in expected_metrics:
            assert metric in report.aggregates["mean"]
            assert metric in report.aggregates["median"]
