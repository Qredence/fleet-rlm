"""Evaluation orchestration for running GenAI evaluation on MLflow traces.

This module provides the run_evaluation() function that coordinates trace
retrieval, scoring, and report generation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .judges import JUDGE_CALLABLES, JUDGE_NAMES
from .metrics import METRIC_CALLABLES, METRIC_NAMES
from .report import EvaluationReport
from .trace_record import TraceRecord

logger = logging.getLogger(__name__)

# Default output directory for evaluation artifacts
_DEFAULT_OUTPUT_DIR = Path.cwd() / "mlartifacts" / "eval"


def _fetch_traces_from_mlflow(
    trace_ids: list[str] | None = None,
    limit: int | None = None,
    from_last_days: int = 1,
) -> list[dict[str, Any]]:
    """Fetch traces from MLflow tracking server.

    Args:
        trace_ids: Optional list of specific trace IDs to fetch.
        limit: Optional maximum number of traces to fetch.
        from_last_days: Number of days to look back (default: 1).

    Returns:
        List of trace dictionaries from MLflow.

    Raises:
        RuntimeError: If MLflow is unreachable or not configured.
    """
    try:
        import mlflow
        from mlflow.tracking import MlflowClient
    except ImportError as e:
        msg = "MLflow is not installed. Install with: uv pip install mlflow"
        raise RuntimeError(msg) from e

    # Get MLflow tracking URI
    tracking_uri = mlflow.get_tracking_uri()
    if not tracking_uri:
        tracking_uri = "http://127.0.0.1:5001"

    # Quick connectivity check to fail fast when MLflow is unreachable (VAL-C-058)
    try:
        from urllib.request import urlopen

        urlopen(tracking_uri, timeout=5)
    except Exception as conn_err:
        msg = f"MLflow tracking server unreachable at {tracking_uri}: {conn_err}"
        raise RuntimeError(msg) from conn_err

    client = MlflowClient(tracking_uri)

    # Calculate time window
    end_time = datetime.now(UTC)
    if from_last_days == 0:
        # VAL-C-052: from_last_days=0 means "today only" (current calendar day UTC)
        start_time = end_time.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_time = end_time - timedelta(days=from_last_days)

    # Convert to milliseconds for MLflow API
    start_time_ms = int(start_time.timestamp() * 1000)
    end_time_ms = int(end_time.timestamp() * 1000)

    try:
        if trace_ids:
            # Fetch specific traces
            traces = []
            for trace_id in trace_ids:
                try:
                    trace = client.get_trace(trace_id)
                    if trace:
                        traces.append(trace.to_dict())
                except Exception as e:
                    logger.warning("Failed to fetch trace %s: %s", trace_id, e)
            return traces

        # Search for traces in time window
        # Resolve the correct experiment - prefer "fleet-rlm", fall back to default "0"
        experiment_ids = ["0"]
        fleet_exp = mlflow.get_experiment_by_name("fleet-rlm")
        if fleet_exp:
            experiment_ids = [fleet_exp.experiment_id]

        # Note: MLflow search_traces returns Trace objects, we need to convert to dict
        traces = client.search_traces(
            experiment_ids=experiment_ids,
            filter_string=f"trace.timestamp >= {start_time_ms} AND trace.timestamp <= {end_time_ms}",
            max_results=limit or 100,
        )

        trace_dicts = []
        for trace in traces:
            trace_dict = trace.to_dict()
            trace_dicts.append(trace_dict)
            if limit and len(trace_dicts) >= limit:
                break

        return trace_dicts

    except Exception as e:
        msg = f"Failed to fetch traces from MLflow at {tracking_uri}: {e}"
        raise RuntimeError(msg) from e


def _score_trace(
    trace_record: TraceRecord,
    lm: Any,
) -> dict[str, Any]:
    """Score a single trace with all judges and metrics.

    Args:
        trace_record: The normalized trace to score.
        lm: Language model to use for judges.

    Returns:
        Dictionary with trace_id and all scores.
    """
    scores: dict[str, Any] = {
        "trace_id": trace_record.trace_id,
    }

    # Run judges (with error recovery)
    for judge_name in JUDGE_NAMES:
        try:
            judge_fn = JUDGE_CALLABLES[judge_name]
            score = judge_fn(trace_record, lm)
            scores[judge_name] = score
        except Exception as e:
            logger.warning("Judge %s failed for trace %s: %s", judge_name, trace_record.trace_id, e)
            # Record null/sentinel for failed judge
            scores[judge_name] = None

    # Run metrics (synchronous, no LLM needed)
    for metric_name in METRIC_NAMES:
        try:
            metric_fn = METRIC_CALLABLES[metric_name]
            score = metric_fn(trace_record)
            scores[metric_name] = score
        except Exception as e:
            logger.warning("Metric %s failed for trace %s: %s", metric_name, trace_record.trace_id, e)
            scores[metric_name] = None

    return scores


def _create_mlflow_run() -> str | None:
    """Create an MLflow run under 'fleet-rlm-eval' experiment and return its run_id.

    Returns:
        The MLflow run_id, or None if MLflow is unavailable.
    """
    try:
        import mlflow
    except ImportError:
        logger.warning("MLflow not installed, skipping MLflow run creation")
        return None

    try:
        # Set experiment (VAL-C-009)
        mlflow.set_experiment("fleet-rlm-eval")

        # Create a run and capture its run_id
        run = mlflow.start_run(run_name="eval-run")
        run_id = run.info.run_id
        # End the run temporarily; we'll log metrics and reopen it later
        mlflow.end_run()
        return run_id

    except Exception as e:
        logger.warning("Failed to create MLflow run: %s", e)
        return None


def _log_to_mlflow(report: EvaluationReport) -> str | None:
    """Log evaluation results to MLflow (VAL-C-009, VAL-C-010, VAL-C-011).

    Creates or resumes an MLflow run under the 'fleet-rlm-eval' experiment and logs:
    - Aggregate metrics (mean/median) via mlflow.log_metric (VAL-C-010)
    - Per-trace scores via mlflow.log_table (VAL-C-011)

    Args:
        report: The evaluation report to log.

    Returns:
        The MLflow run_id if logging succeeded, None otherwise.
    """
    try:
        import mlflow
    except ImportError:
        logger.warning("MLflow not installed, skipping MLflow logging")
        return None

    try:
        # Set experiment (VAL-C-009)
        mlflow.set_experiment("fleet-rlm-eval")

        # Create a run with the report's run_id embedded in the name for traceability
        with mlflow.start_run(run_name=f"eval-{report.run_id[:8]}") as run:
            mlflow_run_id = run.info.run_id

            # Tag the run with the report's run_id for cross-referencing
            mlflow.set_tag("fleet_rlm.eval_run_id", report.run_id)

            # Log aggregate metrics (VAL-C-010)
            if report.aggregates:
                for metric_name, stats in report.aggregates.items():
                    if isinstance(stats, dict):
                        for stat_type, value in stats.items():
                            if isinstance(value, (int, float)):
                                mlflow.log_metric(f"{metric_name}_{stat_type}", value)

            # Log per-trace scores as a table (VAL-C-011)
            if report.per_trace:
                # Convert list[dict] to dict[str, list] for mlflow.log_table
                table_data: dict[str, Any] = {}
                for trace_scores in report.per_trace:
                    for key, value in trace_scores.items():
                        if key not in table_data:
                            table_data[key] = []
                        table_data[key].append(value)

                mlflow.log_table(
                    data=table_data,
                    artifact_file="per_trace_scores.json",
                )

            logger.info("Logged evaluation results to MLflow run: %s", mlflow_run_id)
            return mlflow_run_id

    except Exception as e:
        logger.warning("Failed to log evaluation results to MLflow: %s", e)
        return None


def _resolve_judge_lm(lm: Any = None) -> Any:
    """Resolve the language model to use for judges.

    If an LM is explicitly provided, use it. Otherwise, resolve from
    the configured BYOK (Bring-Your-Own-Key) environment using the same
    resolver as the chat runtime (VAL-C-025).

    Args:
        lm: Optional pre-configured language model.

    Returns:
        A language model instance, or None if resolution fails.
    """
    if lm is not None:
        return lm

    # Try to resolve from BYOK environment using the same resolver as chat runtime
    try:
        from fleet_rlm.runtime.config import get_delegate_lm_from_env

        resolved_lm = get_delegate_lm_from_env()
        if resolved_lm is not None:
            logger.info("Resolved judge LM from BYOK environment configuration")
            return resolved_lm
    except ImportError:
        logger.debug("runtime.config not available for LM resolution")
    except Exception as e:
        logger.warning("Failed to resolve judge LM from environment: %s", e)

    # Fallback: try get_planner_lm_from_env which uses DSPY_LM_MODEL
    try:
        from fleet_rlm.runtime.config import get_planner_lm_from_env

        planner_lm = get_planner_lm_from_env()
        if planner_lm is not None:
            logger.info("Resolved judge LM from planner LM configuration (DSPY_LM_MODEL)")
            return planner_lm
    except ImportError:
        logger.debug("runtime.config not available for planner LM resolution")
    except Exception as e:
        logger.warning("Failed to resolve judge LM from planner configuration: %s", e)

    # Fallback: try build_bounded_chat_lm from the runtime
    try:
        from fleet_rlm.runtime.lm import build_bounded_chat_lm

        # Attempt to construct a bounded LM from any available credentials
        bounded_lm = build_bounded_chat_lm(
            base=None,
            max_tokens=4096,
            temperature=0.0,
            timeout=60.0,
        )
        if bounded_lm is not None:
            logger.info("Resolved judge LM via build_bounded_chat_lm fallback")
            return bounded_lm
    except ImportError:
        logger.debug("runtime.lm not available for LM resolution")
    except Exception as e:
        logger.warning("Failed to resolve judge LM via fallback: %s", e)

    logger.warning(
        "No judge LM available. Judges will return 0.0 for all traces. "
        "Ensure BYOK LM configuration is available (VAL-C-025)."
    )
    return None


def run_evaluation(
    trace_ids: list[str] | None = None,
    limit: int | None = None,
    from_last_days: int = 1,
    lm: Any = None,
    output_dir: Path | str | None = None,
) -> EvaluationReport:
    """Run GenAI evaluation on MLflow traces.

    This function coordinates the full evaluation pipeline:
    1. Fetch traces from MLflow
    2. Normalize traces into TraceRecords
    3. Score each trace with judges and metrics
    4. Build and write the evaluation report
    5. Log results to MLflow (VAL-C-009, VAL-C-010, VAL-C-011)

    Args:
        trace_ids: Optional list of specific trace IDs to evaluate.
        limit: Optional maximum number of traces to evaluate.
        from_last_days: Number of days to look back (default: 1).
        lm: Language model to use for judges. If None, resolves from BYOK
            environment using get_delegate_lm_from_env (VAL-C-025).
        output_dir: Directory to write report.json. Defaults to mlartifacts/eval/<run_id>/.

    Returns:
        An EvaluationReport with per-trace scores and aggregates.

    Raises:
        RuntimeError: If MLflow is unreachable or no traces found.
    """
    # Resolve the judge LM from BYOK if not explicitly provided (VAL-C-025)
    resolved_lm = _resolve_judge_lm(lm)

    # Generate unique run_id
    run_id = str(uuid.uuid4())

    # Prepare filters for report
    filters = {
        "trace_ids": trace_ids,
        "limit": limit,
        "from_last_days": from_last_days,
    }

    # Fetch traces
    logger.info("Fetching traces from MLflow...")
    traces = _fetch_traces_from_mlflow(
        trace_ids=trace_ids,
        limit=limit,
        from_last_days=from_last_days,
    )

    if not traces:
        logger.info("No traces found. Creating empty report.")
        # Return empty report
        report = EvaluationReport.build(
            run_id=run_id,
            filters=filters,
            per_trace=[],
        )
        # Write empty report to disk
        out_dir = Path(output_dir) if output_dir else _DEFAULT_OUTPUT_DIR / run_id
        report.write_to_disk(out_dir)
        # Log empty report to MLflow (VAL-C-009)
        _log_to_mlflow(report)
        return report

    logger.info("Found %d traces. Normalizing and scoring...", len(traces))

    # Normalize and score traces
    per_trace_scores: list[dict[str, Any]] = []
    for trace_dict in traces:
        try:
            # Normalize trace
            trace_record = TraceRecord.from_mlflow_trace(trace_dict)

            # Score trace using the resolved LM (VAL-C-025)
            scores = _score_trace(trace_record, resolved_lm)
            per_trace_scores.append(scores)

        except Exception as e:
            logger.warning("Failed to process trace: %s", e)
            continue

    # Build report
    report = EvaluationReport.build(
        run_id=run_id,
        filters=filters,
        per_trace=per_trace_scores,
    )

    # Write report to disk
    out_dir = Path(output_dir) if output_dir else _DEFAULT_OUTPUT_DIR / run_id
    report_path = report.write_to_disk(out_dir)
    logger.info("Report written to %s", report_path)

    # Log results to MLflow (VAL-C-009, VAL-C-010, VAL-C-011)
    _log_to_mlflow(report)

    return report
