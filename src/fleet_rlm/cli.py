"""The `fleet` command: serve, cli (launch and connect the terminal client), optimize, and eval."""

from __future__ import annotations

import argparse
import ipaddress
import sys
import uuid
from typing import Any

import httpx
import uvicorn
from pydantic import ValidationError
from rich.console import Console

from fleet_rlm import launcher
from fleet_rlm.config import get_settings
from fleet_rlm.engine.routes import Route
from fleet_rlm.tui.app import run_interactive_cli

_CONSOLE = Console()


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def evaluation_statuses() -> tuple[str, ...]:
    from fleet_rlm.optimize.evaluation import REVIEW_STATUSES

    return REVIEW_STATUSES


def _eval_command(args: argparse.Namespace, settings: Any) -> None:
    """Build the eval dataset, label a trace, or run the synthetic cases against a live server."""
    import os

    import mlflow

    from fleet_rlm.optimize import evaluation

    target = settings.mlflow_target()
    mlflow.set_tracking_uri(target.tracking_uri)
    if target.mode == "databricks":
        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = settings.mlflow_tracing_sql_warehouse_id
    if args.eval_command == "label":
        evaluation.label_trace(
            args.trace_id,
            review_status=args.status,
            source_group=args.group,
            expected_answer=args.answer,
            expected_values=args.value,
            route=args.route,
            reviewer=args.reviewer,
        )
        _CONSOLE.print(f"Labeled {args.trace_id} as {args.status}")
        return
    if not target.eval_experiment_name:
        raise SystemExit("set FLEET_MLFLOW_EVAL_EXPERIMENT_NAME to the evaluation experiment")
    experiment = mlflow.set_experiment(target.eval_experiment_name)
    cases = evaluation.synthetic_cases(seed=args.seed)
    if args.eval_command == "dataset":
        name = args.name or (
            f"{settings.mlflow_trace_catalog}.{settings.mlflow_trace_schema}.fleet_rlm_eval_cases"
            if target.mode == "databricks"
            else "fleet_rlm_eval_cases"
        )
        from fleet_rlm.optimize import benchmarks

        if args.long_context:
            cases += benchmarks.long_context_cases()
        if args.oolong or args.longcot:
            with httpx.Client(follow_redirects=True) as hf:
                if args.oolong:
                    cases += benchmarks.oolong_cases(hf, limit=args.oolong)
                if args.longcot:
                    cases += benchmarks.longcot_cases(hf, per_domain=args.longcot)
        dataset = evaluation.build_dataset(name, experiment.experiment_id, cases)
        _CONSOLE.print(f"Dataset {dataset.name}: {len(cases)} cases")
        return
    for name, value in settings.judge_environment().items():
        os.environ.setdefault(name, value)
    result = evaluation.evaluate_cases(
        cases[: args.limit] if args.limit else cases,
        base_url=args.url,
        judge_model=settings.fleet_judge_model,
    )
    _CONSOLE.print(result.metrics)


def main(argv: list[str] | None = None) -> None:
    """Run the backend or the interactive Rich client."""
    try:
        settings = get_settings()
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {str(error['msg']).removeprefix('Value error, ')}"
            for error in exc.errors(include_url=False, include_input=False)
        )
        sys.stderr.write(f"fleet: error: invalid configuration: {details}\n")
        raise SystemExit(2) from None
    parser = argparse.ArgumentParser(prog="fleet", description="Fleet-RLM Lite server and terminal client")
    commands = parser.add_subparsers(dest="command")

    serve = commands.add_parser("serve", help="run the FastAPI SSE server")
    serve.add_argument("--host", default=settings.host)
    serve.add_argument("--port", type=int, default=settings.port)

    cli = commands.add_parser(
        "cli",
        help="start the API server (and local MLflow) if needed, then open the interactive terminal client",
    )
    cli.add_argument("--url", default=None, help="connect to this server only; do not start anything")
    cli.add_argument("--session", default=f"sess_{uuid.uuid4().hex[:8]}")
    target = settings.mlflow_target()
    cli.add_argument(
        "--mlflow",
        choices=("local", "env"),
        default="local" if target.mode == "local" else "env",
        help="local: run a local MLflow server and trace to it; env: use the configured tracking URI "
        "(default follows FLEET_MLFLOW_MODE)",
    )
    cli.add_argument("--mlflow-port", type=int, default=5001)
    cli.add_argument("--mlflow-experiment", default=target.experiment_name)
    cli.add_argument("--startup-timeout", type=float, default=180.0, help="seconds to wait for each service")

    from fleet_rlm.optimize import gepa

    optimize = commands.add_parser(
        "optimize", help="evaluate a Session's reviewed examples and compile a GEPA candidate (live)"
    )
    gepa.add_arguments(optimize)

    evaluate = commands.add_parser("eval", help="evaluation datasets, gold labels, and runs")
    eval_commands = evaluate.add_subparsers(dest="eval_command", required=True)
    dataset = eval_commands.add_parser("dataset", help="create the synthetic evaluation dataset")
    dataset.add_argument(
        "--name", default="", help="dataset name (databricks mode: catalog.schema.table from the trace settings)"
    )
    dataset.add_argument("--seed", type=int, default=7)
    dataset.add_argument("--long-context", action="store_true", help="add synthetic 20K-200K character log cases")
    dataset.add_argument("--oolong", type=int, default=0, metavar="N", help="add N Oolong-synth benchmark cases")
    dataset.add_argument("--longcot", type=int, default=0, metavar="N", help="add N LongCoT cases per domain")
    run = eval_commands.add_parser("run", help="run synthetic cases against a live server and score them")
    run.add_argument("--url", default=f"http://{settings.host}:{settings.port}")
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--limit", type=int, default=0, help="only run the first N cases")
    label = eval_commands.add_parser("label", help="write gold labels onto a trace")
    label.add_argument("trace_id")
    label.add_argument("--status", required=True, choices=evaluation_statuses())
    label.add_argument("--group", required=True, help="source_group used for grouped train/dev/holdout splits")
    label.add_argument("--answer")
    label.add_argument("--value", action="append", help="expected value that must appear in the answer (repeatable)")
    label.add_argument("--route", choices=[route.value for route in Route])
    label.add_argument("--reviewer", default="human")

    args = parser.parse_args(argv)
    if args.command == "optimize":
        gepa.run(args, optimize, settings)
        return
    if args.command == "eval":
        _eval_command(args, settings)
        return
    if args.command == "serve":
        if not settings.allow_non_loopback_bind and not _is_loopback(args.host):
            parser.error("non-loopback binding requires FLEET_ALLOW_NON_LOOPBACK_BIND=true")
        from fleet_rlm.api.app import create_app

        bound = settings.with_explicit_overrides(host=args.host, port=args.port)
        uvicorn.run(create_app(settings=bound), host=bound.host, port=bound.port, reload=False)
        return

    session_id = getattr(args, "session", f"sess_{uuid.uuid4().hex[:8]}")
    url = getattr(args, "url", None)
    if not url and not settings.allow_non_loopback_bind and not _is_loopback(settings.host):
        parser.error("non-loopback binding requires FLEET_ALLOW_NON_LOOPBACK_BIND=true")
    plan = launcher.plan_launch(
        url=url,
        host=settings.host,
        port=settings.port,
        mlflow_mode=getattr(args, "mlflow", "local"),
        mlflow_port=getattr(args, "mlflow_port", 5001),
        mlflow_experiment=getattr(args, "mlflow_experiment", "fleet-rlm-dev"),
    )
    try:
        with launcher.running_services(
            plan,
            host=settings.host,
            port=settings.port,
            mlflow_port=getattr(args, "mlflow_port", 5001),
            startup_timeout=getattr(args, "startup_timeout", 180.0),
            log=lambda message: _CONSOLE.print(f"[dim]{message}[/dim]"),
        ):
            run_interactive_cli(base_url=plan.api_url, session_id=session_id)
    except launcher.LaunchError as exc:
        _CONSOLE.print(f"[bold red]Could not start services:[/bold red] {exc}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
