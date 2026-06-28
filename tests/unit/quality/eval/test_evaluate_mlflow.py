"""Tests for MLflow logging in evaluate.py (VAL-C-009, VAL-C-010, VAL-C-011)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_log_to_mlflow_creates_run_under_fleet_rlm_eval_experiment() -> None:
    """VAL-C-009: _log_to_mlflow should create a run under 'fleet-rlm-eval' experiment."""
    from fleet_rlm.quality.eval.evaluate import _log_to_mlflow
    from fleet_rlm.quality.eval.report import EvaluationReport

    mock_mlflow = MagicMock()
    mock_mlflow.set_experiment = MagicMock()
    mock_run = MagicMock()
    mock_run.info.run_id = "mlflow-run-123"
    mock_run.__enter__ = MagicMock(return_value=mock_run)
    mock_run.__exit__ = MagicMock(return_value=False)
    mock_mlflow.start_run = MagicMock(return_value=mock_run)
    mock_mlflow.log_metric = MagicMock()
    mock_mlflow.log_table = MagicMock()
    mock_mlflow.set_tag = MagicMock()

    # Create a simple report
    report = EvaluationReport.build(
        run_id="test-run-abc123",
        filters={"from_last_days": 1},
        per_trace=[{"trace_id": "tr-1", "answer_relevance": 0.8}],
    )

    with patch.dict("sys.modules", {"mlflow": mock_mlflow}):
        result = _log_to_mlflow(report)

    # Verify experiment was set correctly (VAL-C-009)
    mock_mlflow.set_experiment.assert_called_once_with("fleet-rlm-eval")
    assert result == "mlflow-run-123"


def test_log_to_mlflow_logs_aggregate_metrics() -> None:
    """VAL-C-010: _log_to_mlflow should log aggregate metrics via mlflow.log_metric."""
    from fleet_rlm.quality.eval.evaluate import _log_to_mlflow
    from fleet_rlm.quality.eval.report import EvaluationReport

    mock_mlflow = MagicMock()
    mock_mlflow.set_experiment = MagicMock()
    mock_run = MagicMock()
    mock_run.info.run_id = "mlflow-run-456"
    mock_mlflow.start_run = MagicMock(return_value=mock_run)
    mock_mlflow.__enter__ = MagicMock(return_value=mock_run)
    mock_mlflow.__exit__ = MagicMock(return_value=False)
    mock_mlflow.log_metric = MagicMock()
    mock_mlflow.log_table = MagicMock()
    mock_mlflow.set_tag = MagicMock()

    # Create a report with aggregates
    report = EvaluationReport.build(
        run_id="test-run-metrics",
        filters={"from_last_days": 1},
        per_trace=[
            {"trace_id": "tr-1", "answer_relevance": 0.8, "timeout_compliance": 1.0},
            {"trace_id": "tr-2", "answer_relevance": 0.9, "timeout_compliance": 0.5},
        ],
    )

    with patch.dict("sys.modules", {"mlflow": mock_mlflow}):
        _log_to_mlflow(report)

    # Verify log_metric was called for each aggregate (VAL-C-010)
    assert mock_mlflow.log_metric.call_count > 0
    logged_metrics = {call.args[0]: call.args[1] for call in mock_mlflow.log_metric.call_args_list}

    # Check that at least some expected metrics are logged
    assert "mean_answer_relevance" in logged_metrics or any("answer_relevance" in key for key in logged_metrics)


def test_log_to_mlflow_logs_per_trace_scores_as_table() -> None:
    """VAL-C-011: _log_to_mlflow should log per-trace scores via mlflow.log_table."""
    from fleet_rlm.quality.eval.evaluate import _log_to_mlflow
    from fleet_rlm.quality.eval.report import EvaluationReport

    mock_mlflow = MagicMock()
    mock_mlflow.set_experiment = MagicMock()
    mock_run = MagicMock()
    mock_run.info.run_id = "mlflow-run-789"
    mock_run.__enter__ = MagicMock(return_value=mock_run)
    mock_run.__exit__ = MagicMock(return_value=False)
    mock_mlflow.start_run = MagicMock(return_value=mock_run)
    mock_mlflow.log_metric = MagicMock()
    mock_mlflow.log_table = MagicMock()
    mock_mlflow.set_tag = MagicMock()

    per_trace_data = [
        {"trace_id": "tr-1", "answer_relevance": 0.8},
        {"trace_id": "tr-2", "answer_relevance": 0.9},
    ]

    report = EvaluationReport.build(
        run_id="test-run-table",
        filters={"from_last_days": 1},
        per_trace=per_trace_data,
    )

    with patch.dict("sys.modules", {"mlflow": mock_mlflow}):
        _log_to_mlflow(report)

    # Verify log_table was called with per-trace scores (VAL-C-011)
    mock_mlflow.log_table.assert_called_once()
    call_kwargs = mock_mlflow.log_table.call_args.kwargs
    assert call_kwargs["artifact_file"] == "per_trace_scores.json"
    # Data is transposed from list[dict] to dict[str, list] for mlflow.log_table
    expected_table_data = {
        "trace_id": ["tr-1", "tr-2"],
        "answer_relevance": [0.8, 0.9],
    }
    assert call_kwargs["data"] == expected_table_data


def test_log_to_mlflow_tags_run_with_report_run_id() -> None:
    """VAL-C-009: _log_to_mlflow should tag the MLflow run with the report's run_id for cross-referencing."""
    from fleet_rlm.quality.eval.evaluate import _log_to_mlflow
    from fleet_rlm.quality.eval.report import EvaluationReport

    mock_mlflow = MagicMock()
    mock_mlflow.set_experiment = MagicMock()
    mock_run = MagicMock()
    mock_run.info.run_id = "mlflow-run-tagged"
    mock_run.__enter__ = MagicMock(return_value=mock_run)
    mock_run.__exit__ = MagicMock(return_value=False)
    mock_mlflow.start_run = MagicMock(return_value=mock_run)
    mock_mlflow.log_metric = MagicMock()
    mock_mlflow.log_table = MagicMock()
    mock_mlflow.set_tag = MagicMock()

    report = EvaluationReport.build(
        run_id="report-run-id-xyz",
        filters={"from_last_days": 1},
        per_trace=[],
    )

    with patch.dict("sys.modules", {"mlflow": mock_mlflow}):
        _log_to_mlflow(report)

    # Verify the run was tagged with the report's run_id
    mock_mlflow.set_tag.assert_called_once_with("fleet_rlm.eval_run_id", "report-run-id-xyz")


def test_log_to_mlflow_returns_none_when_mlflow_unavailable() -> None:
    """_log_to_mlflow should return None gracefully when MLflow is not installed."""
    from fleet_rlm.quality.eval.evaluate import _log_to_mlflow
    from fleet_rlm.quality.eval.report import EvaluationReport

    report = EvaluationReport.build(
        run_id="test-run-no-mlflow",
        filters={},
        per_trace=[],
    )

    # Simulate MLflow not being installed
    with patch.dict("sys.modules", {"mlflow": None}):
        result = _log_to_mlflow(report)

    assert result is None


def test_log_to_mlflow_handles_empty_per_trace() -> None:
    """_log_to_mlflow should handle empty per_trace list gracefully."""
    from fleet_rlm.quality.eval.evaluate import _log_to_mlflow
    from fleet_rlm.quality.eval.report import EvaluationReport

    mock_mlflow = MagicMock()
    mock_mlflow.set_experiment = MagicMock()
    mock_run = MagicMock()
    mock_run.info.run_id = "mlflow-run-empty"
    mock_run.__enter__ = MagicMock(return_value=mock_run)
    mock_run.__exit__ = MagicMock(return_value=False)
    mock_mlflow.start_run = MagicMock(return_value=mock_run)
    mock_mlflow.log_metric = MagicMock()
    mock_mlflow.log_table = MagicMock()
    mock_mlflow.set_tag = MagicMock()

    report = EvaluationReport.build(
        run_id="test-run-empty",
        filters={"from_last_days": 1},
        per_trace=[],
    )

    with patch.dict("sys.modules", {"mlflow": mock_mlflow}):
        result = _log_to_mlflow(report)

    # Should succeed but not call log_table for empty per_trace
    assert result == "mlflow-run-empty"
    mock_mlflow.log_table.assert_not_called()
