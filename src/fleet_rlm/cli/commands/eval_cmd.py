"""Eval CLI subcommand for fleet-rlm.

This module implements the `fleet-rlm eval` subcommand that runs GenAI
evaluation on MLflow traces. All heavy imports (DSPy, MLflow, quality/eval)
are deferred to the command callback to avoid import-time side effects.

Flags:
    --trace-ids: Specific trace IDs to evaluate (repeatable).
    --limit N: Maximum number of traces to evaluate.
    --from-last-days N: Look-back window in days (default: 1, 0 = today only).
"""

from __future__ import annotations

import logging

import typer

logger = logging.getLogger(__name__)


def eval_command(
    trace_ids: list[str] | None = typer.Option(
        None,
        "--trace-ids",
        help="Specific trace ID(s) to evaluate. Repeat for multiple.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Maximum number of traces to evaluate.",
    ),
    from_last_days: int = typer.Option(
        1,
        "--from-last-days",
        help="Look-back window in days. 0 = today only, 1 = last 24h (default: 1).",
    ),
) -> None:
    """Run GenAI evaluation on MLflow traces.

    Fetches traces from the MLflow tracking server, scores them with
    4 LLM-as-judge scorers and 6 programmatic metrics, and writes a
    report to mlartifacts/eval/<run_id>/report.json.
    """
    # Lazy imports — avoid DSPy/MLflow/quality at module top-level (VAL-C-047)
    try:
        from fleet_rlm.quality.eval import run_evaluation
    except ImportError as e:
        typer.echo(f"Error: quality/eval package not available: {e}", err=True)
        raise typer.Exit(code=1) from e

    try:
        report = run_evaluation(
            trace_ids=trace_ids if trace_ids else None,
            limit=limit,
            from_last_days=from_last_days,
        )
    except RuntimeError as e:
        # Unrecoverable failure (e.g. MLflow unreachable) — exit 1 (VAL-C-058)
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    except Exception as e:
        typer.echo(f"Unexpected error during evaluation: {e}", err=True)
        raise typer.Exit(code=1) from e

    # Print summary to stdout
    trace_count = len(report.per_trace)
    typer.echo(f"run_id={report.run_id}")
    typer.echo(f"traces_evaluated={trace_count}")
    typer.echo(f"report_path=mlartifacts/eval/{report.run_id}/report.json")

    if trace_count == 0:
        typer.echo("No traces found. Empty report generated.")
    else:
        # Print per-judge means
        if report.aggregates and "mean" in report.aggregates:
            means = report.aggregates["mean"]
            typer.echo("\nJudge scores (mean):")
            for name, value in means.items():
                typer.echo(f"  {name}: {value:.4f}")
