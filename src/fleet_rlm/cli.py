"""The `fleet` command: serve, cli (launch and connect the terminal client), optimize, and eval."""

from __future__ import annotations

import argparse
import ipaddress
import sys
import time
import uuid
from typing import Any

import httpx
import uvicorn
from pydantic import ValidationError
from rich.console import Console

from fleet_rlm import launcher
from fleet_rlm.config import get_settings
from fleet_rlm.engine.signatures import Route
from fleet_rlm.observability import trace_object as _trace_object
from fleet_rlm.replay import REVIEW_STATUSES
from fleet_rlm.tui.app import run_interactive_cli

_CONSOLE = Console()


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _trace_request(trace: Any) -> dict[str, Any]:
    """Decode the root request for selected replay curation."""
    return _trace_object(getattr(getattr(trace, "data", None), "request", None))


def _uc_trace_location(location: Any) -> Any:
    """Return the Unity Catalog location across MLflow TraceLocation API shapes."""
    nested = getattr(location, "uc_table_prefix", None)
    return nested if nested is not None else location


def _label_command(args: argparse.Namespace, settings: Any) -> None:
    """Resolve partial updates once, preflight the replay, then synchronize both stores."""
    import mlflow

    from fleet_rlm.optimize.dataset import labels_of
    from fleet_rlm.optimize.evaluation import label_trace
    from fleet_rlm.replay import selected_snapshot, validate_review_labels
    from fleet_rlm.store import Store

    trace = None
    for attempt in range(15):
        trace = mlflow.get_trace(args.trace_id, silent=True, flush=True)
        if trace is not None:
            break
        if attempt < 14:
            time.sleep(1)
    if trace is None:
        raise SystemExit(f"Trace {args.trace_id} is not readable; retry its saved trace ID")
    existing = labels_of(trace)
    labels = {
        "review_status": "approved",
        "source_group": trace.info.trace_metadata.get("mlflow.trace.session", ""),
        "expected_answer": None,
        "expected_values": [],
        "route": None,
        "critical": False,
    }
    labels.update({name: existing[name] for name in labels if name in existing})
    for name, value in (
        ("review_status", args.status),
        ("source_group", args.group),
        ("expected_answer", args.answer),
        ("expected_values", args.value),
        ("route", args.route),
        ("critical", args.critical),
    ):
        if value is not None:
            labels[name] = value
    request = _trace_request(trace)
    selected = request.get("replay_snapshot_selected") is True
    store = None
    mlflow_saved = False
    try:
        validate_review_labels(labels, require_gold=selected)
        if not args.reviewer.strip():
            raise ValueError("review labels require a reviewer")
        if selected:
            session_id = request.get("session_id", "")
            turn_id = request.get("turn_id", "")
            digest = request.get("replay_snapshot_sha256", "")
            if session_id != trace.info.trace_metadata.get("mlflow.trace.session"):
                raise ValueError("Trace session metadata does not match its replay reference")
            store = Store.open(
                settings.fleet_database_url,
                wait=True,
                lakebase_endpoint=settings.fleet_lakebase_endpoint,
                schema=settings.fleet_database_schema,
            )
            selected_snapshot(store, session_id, turn_id, digest)
        label_trace(args.trace_id, **labels, reviewer=args.reviewer)
        mlflow_saved = True
        if store is not None:
            store.save_review_labels(session_id, turn_id, digest, **labels, reviewer=args.reviewer)
    except Exception as exc:
        if mlflow_saved:
            raise SystemExit(
                f"MLflow labels were saved, but Lakebase curation failed ({type(exc).__name__}); "
                "rerun this label command to retry"
            ) from None
        raise SystemExit(
            f"Review was not synchronized ({type(exc).__name__}); check labels and replay reference, then retry"
        ) from None
    finally:
        if store is not None:
            store.close()
    _CONSOLE.print(f"Labeled {args.trace_id} as {labels['review_status']}")


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
        _label_command(args, settings)
        return
    if not target.eval_experiment_name:
        raise SystemExit("set FLEET_MLFLOW_EVAL_EXPERIMENT_NAME to the evaluation experiment")
    if target.mode == "databricks":
        from mlflow.entities.trace_location import UnityCatalog

        experiment = mlflow.set_experiment(
            target.eval_experiment_name,
            trace_location=UnityCatalog(
                catalog_name=settings.mlflow_trace_catalog,
                schema_name=settings.mlflow_trace_schema,
                table_prefix=settings.mlflow_eval_trace_table_prefix,
            ),
        )
    else:
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
        os.environ[name] = value
    if args.oolong:
        from fleet_rlm.optimize import benchmarks

        with httpx.Client(follow_redirects=True) as hf:
            cases = benchmarks.oolong_cases(hf, limit=args.oolong)
    try:
        result = evaluation.evaluate_cases(
            cases[: args.limit] if args.limit else cases,
            base_url=args.url,
            judge_model=settings.fleet_judge_model,
            judge_lm=settings.build_judge_lm() if settings.fleet_judge_model.startswith("openai:/") else None,
            collect_only=args.collect_only,
            experiment_id=experiment.experiment_id,
            uc_location=(
                (settings.mlflow_trace_catalog, settings.mlflow_trace_schema, settings.mlflow_eval_trace_table_prefix)
                if target.mode == "databricks"
                else None
            ),
            report=lambda message: _CONSOLE.print(message, markup=False, soft_wrap=True),
        )
    except evaluation.EvaluationError as exc:
        raise SystemExit(str(exc)) from None
    if result is not None:
        _CONSOLE.print(result.metrics)
    else:
        _CONSOLE.print(f"Collected labelled Turns for {len(cases[: args.limit] if args.limit else cases)} cases")


def _trace_verify_command(args: argparse.Namespace, settings: Any) -> None:
    """Flush pending export and verify one trace is readable from its configured destination."""
    import os

    import mlflow

    target = settings.mlflow_target()
    experiments = {
        "runtime": (target.experiment_name, settings.mlflow_trace_schema, settings.mlflow_trace_table_prefix),
        "evaluation": (
            target.eval_experiment_name,
            settings.mlflow_trace_schema,
            settings.mlflow_eval_trace_table_prefix,
        ),
        "optimization": (
            target.optimization_experiment_name,
            settings.mlflow_optimization_trace_schema,
            settings.mlflow_optimization_trace_table_prefix,
        ),
    }
    experiment_name, schema, table_prefix = experiments[args.scope]
    mlflow.set_tracking_uri(target.tracking_uri)
    if target.mode == "databricks":
        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = settings.mlflow_tracing_sql_warehouse_id
    try:
        experiment = mlflow.get_experiment_by_name(experiment_name)
    except Exception as exc:
        raise SystemExit(f"Could not read MLflow experiment ({type(exc).__name__})") from None
    if experiment is None:
        raise SystemExit(f"MLflow experiment {experiment_name!r} was not found")
    destination = target.tracking_uri
    expected_location = None
    if target.mode == "databricks":
        from mlflow.entities.trace_location import UnityCatalog

        location = experiment.trace_location
        expected = (settings.mlflow_trace_catalog, schema, table_prefix)
        actual = (
            getattr(location, "catalog_name", None),
            getattr(location, "schema_name", None),
            getattr(location, "table_prefix", None),
        )
        is_unity_catalog = (
            location is not None
            and type(location).__module__ == UnityCatalog.__module__
            and type(location).__name__ == "UnityCatalog"
        )
        if not is_unity_catalog or actual != expected:
            raise SystemExit(
                f"MLflow experiment trace destination mismatch for {args.scope}: "
                f"expected {expected[0]}.{expected[1]} prefix {expected[2]!r}"
            )
        expected_location = expected
        destination = f"{actual[0]}.{actual[1]}.{actual[2]}"
    try:
        trace = mlflow.get_trace(args.trace_id, silent=True, flush=True)
    except Exception as exc:
        raise SystemExit(f"Trace readback failed ({type(exc).__name__})") from None
    if trace is None:
        raise SystemExit(f"Trace {args.trace_id!r} was not found after flushing pending exports")
    if expected_location is not None:
        location = _uc_trace_location(trace.info.trace_location)
        actual_location = (
            getattr(location, "catalog_name", None),
            getattr(location, "schema_name", None),
            getattr(location, "table_prefix", None),
        )
        if actual_location != expected_location:
            raise SystemExit(f"Trace {args.trace_id!r} is stored outside the configured destination")
    span_count = len(trace.data.spans)
    if span_count == 0:
        raise SystemExit(f"Trace {args.trace_id!r} was found but contains no spans")
    _CONSOLE.print(f"Trace export verified: {args.trace_id} ({span_count} spans; {destination})")


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
        help="start the API server and open the interactive terminal client",
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
    run.add_argument("--oolong", type=int, default=0, metavar="N", help="run N Oolong-synth cases instead of synthetic")
    run.add_argument(
        "--collect-only",
        action="store_true",
        help="run and label the Turns, skipping the MLflow judge suite",
    )
    label = eval_commands.add_parser("label", help="write gold labels onto a trace")
    label.add_argument("trace_id")
    label.add_argument(
        "--status", choices=REVIEW_STATUSES, help="review status (new labels: approved; updates: preserved)"
    )
    label.add_argument(
        "--group",
        help="source_group for grouped train/dev/holdout splits (default: existing label, then the trace's session id)",
    )
    label.add_argument("--answer")
    label.add_argument("--value", action="append", help="expected value that must appear in the answer (repeatable)")
    label.add_argument("--route", choices=[route.value for route in Route])
    label.add_argument(
        "--critical",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="pin this deterministic example to GEPA's holdout and require a perfect candidate score",
    )
    label.add_argument("--reviewer", default="human")

    trace = commands.add_parser("trace", help="inspect and verify MLflow trace delivery")
    trace_commands = trace.add_subparsers(dest="trace_command", required=True)
    verify = trace_commands.add_parser("verify", help="flush pending exports and read one stored trace")
    verify.add_argument("trace_id", help="trace ID; use its fully qualified trace:/ reference for Unity Catalog")
    verify.add_argument("--scope", choices=("runtime", "evaluation", "optimization"), default="runtime")

    args = parser.parse_args(argv)
    if args.command == "optimize":
        gepa.run(args, optimize, settings)
        return
    if args.command == "eval":
        _eval_command(args, settings)
        return
    if args.command == "trace":
        _trace_verify_command(args, settings)
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
        mlflow_mode=getattr(args, "mlflow", "env"),
        mlflow_port=getattr(args, "mlflow_port", 5001),
        mlflow_experiment=getattr(args, "mlflow_experiment", target.experiment_name),
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
