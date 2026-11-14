"""The `fleet` command: serve, cli (launch and connect the terminal client), optimize, and eval."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
import uuid
from typing import Any

import uvicorn
from pydantic import ValidationError
from rich.console import Console

from fleet_rlm import launcher
from fleet_rlm.config import get_settings
from fleet_rlm.contracts import Route
from fleet_rlm.optimization_runs import add_optimization_arguments, read_worker_settings
from fleet_rlm.replay import REVIEW_STATUSES
from fleet_rlm.tui.app import run_interactive_cli
from fleet_rlm.tui.web import serve_web_client, validate_api_url, validate_http_origin, web_auth_token
from fleet_rlm.workspace import validate_session_id

_CONSOLE = Console()


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _web_api_url(value: str) -> str:
    """Parse the API URL before it is embedded in Textual Serve's shell command."""
    try:
        return validate_api_url(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _web_public_url(value: str) -> str:
    """Validate and normalize the browser-facing URL used behind a proxy or wildcard bind."""
    try:
        return validate_http_origin(value, label="public URL")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _configuration_error(exc: ValueError) -> str:
    """Format configuration failures without echoing credential-bearing inputs."""
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {str(error['msg']).removeprefix('Value error, ')}"
            for error in exc.errors(include_url=False, include_input=False)
        )
    return str(exc)


def _uc_trace_location(location: Any) -> Any:
    """Return the Unity Catalog location across MLflow TraceLocation API shapes."""
    nested = getattr(location, "uc_table_prefix", None)
    return nested if nested is not None else location


def _label_command(args: argparse.Namespace, settings: Any) -> None:
    """Label one trace through the same function the HTTP route uses."""
    from fleet_rlm.optimize.evaluation import EvaluationError, label_turn
    from fleet_rlm.store import Store

    expectations = None
    if getattr(args, "expectations_file", None):
        from fleet_rlm.execution_evidence import parse_expectations_file

        expectations = parse_expectations_file(args.expectations_file)
    opened: list[Store] = []

    def open_store() -> Store:
        opened.append(
            Store.open(
                settings.fleet_database_url,
                wait=True,
                lakebase_endpoint=settings.fleet_lakebase_endpoint,
                schema=settings.fleet_database_schema,
            )
        )
        return opened[-1]

    try:
        labels = label_turn(
            open_store,
            args.trace_id,
            status=args.status,
            group=args.group,
            answer=args.answer,
            values=args.value,
            route=args.route,
            critical=args.critical,
            reviewer=args.reviewer,
            expectations=expectations,
        )
    except (EvaluationError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    finally:
        for store in opened:
            store.close()
    _CONSOLE.print(f"Labeled {args.trace_id} as {labels['review_status']}")


def _score_command(args: argparse.Namespace, settings: Any, experiment_id: str) -> None:
    """Score already collected, labelled traces without running their Turns again."""
    import mlflow

    from fleet_rlm.optimize import evaluation

    traces = []
    for trace_id in args.trace_ids:
        trace = mlflow.get_trace(trace_id, silent=True, flush=True)
        if trace is None:
            raise SystemExit(f"Trace {trace_id} is not readable; retry its saved trace ID")
        traces.append(trace)
    evaluation.apply_judge_environment(settings)
    diagnostic_options: dict[str, Any] = {}
    registered_names = getattr(args, "registered_scorer", None) or []
    scorer_experiment = getattr(args, "scorer_experiment_id", None)
    if bool(registered_names) != bool(scorer_experiment):
        raise SystemExit("--scorer-experiment-id and --registered-scorer must be supplied together")
    if registered_names and getattr(args, "execution_diagnostics_only", False):
        raise SystemExit("Registered selection cannot be combined with --execution-diagnostics-only")
    if registered_names:
        diagnostic_options["scorers"], diagnostic_options["registered_provenance"] = evaluation.load_registered_scorers(
            str(scorer_experiment), registered_names
        )
    elif getattr(args, "execution_diagnostics_only", False):
        from mlflow.genai.scorers import scorer

        from fleet_rlm.optimize.scorers import diagnose_execution_evidence, make_execution_evidence_judge

        judge = make_execution_evidence_judge(settings.fleet_judge_model)

        @scorer(name="execution_evidence_diagnostics")
        def diagnostics(trace):
            return diagnose_execution_evidence(trace, judge, judge_model=settings.fleet_judge_model)

        diagnostic_options["scorers"] = [diagnostics]
    else:
        diagnostic_options.update(
            judge_lm=settings.build_judge_lm(),
            judge_adapter=settings.resolved_model("judge").build_adapter(),
            jev_lm=settings.build_jev_lm(),
        )
    try:
        result = evaluation.score_evaluation_traces(
            traces,
            judge_model=settings.fleet_judge_model,
            **diagnostic_options,
            experiment_id=experiment_id,
            report=lambda message: _CONSOLE.print(message, markup=False, soft_wrap=True),
        )
    except evaluation.EvaluationError as exc:
        raise SystemExit(str(exc)) from None
    _CONSOLE.print(result.metrics)


def _eval_command(args: argparse.Namespace, settings: Any) -> None:
    """Build the eval dataset, label or score traces, or run the synthetic cases against a live server."""
    import mlflow

    from fleet_rlm.optimize import evaluation

    target = settings.mlflow_target()
    if args.eval_command == "run":
        try:
            launcher.verify_api_destination(args.url, settings.mlflow_descriptor(evaluation=True))
        except launcher.LaunchError as exc:
            raise SystemExit(str(exc)) from None
    mlflow.set_tracking_uri(target.tracking_uri)
    if target.mode == "databricks":
        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = settings.mlflow_tracing_sql_warehouse_id
    if args.eval_command == "label":
        _label_command(args, settings)
        return
    if not target.eval_experiment_name:
        raise SystemExit(f"set FLEET_MLFLOW_{target.mode.upper()}_EVAL_EXPERIMENT_NAME to the evaluation experiment")
    experiment = evaluation.evaluation_experiment(settings)
    if args.eval_command == "score":
        _score_command(args, settings, experiment.experiment_id)
        return
    cases = evaluation.synthetic_cases(seed=args.seed)
    if args.eval_command == "dataset":
        name = args.name or (
            f"{settings.mlflow_trace_catalog}.{settings.mlflow_trace_schema}.fleet_rlm_eval_cases"
            if target.mode == "databricks"
            else "fleet_rlm_eval_cases"
        )
        from fleet_rlm.optimize import evaluation

        if args.long_context:
            cases += evaluation.long_context_cases()
        dataset = evaluation.build_dataset(name, experiment.experiment_id, cases)
        _CONSOLE.print(f"Dataset {dataset.name}: {len(cases)} cases")
        return
    try:
        result = evaluation.run_cases(
            settings,
            cases[: args.limit] if args.limit else cases,
            base_url=args.url,
            experiment_id=experiment.experiment_id,
            collect_only=args.collect_only,
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
    """Run Fleet's server, terminal client, evaluation, tracing, or optimization commands."""
    parser = argparse.ArgumentParser(prog="fleet", description="Fleet-RLM Lite server and terminal client")
    commands = parser.add_subparsers(dest="command")

    serve = commands.add_parser("serve", help="run the FastAPI SSE server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    doctor = commands.add_parser(
        "doctor",
        help="validate configuration, models, database, MLflow, and Daytona readiness",
    )
    doctor.add_argument(
        "--probe-models",
        action="store_true",
        help="run a minimal live inference probe against the configured root model",
    )

    snapshot = commands.add_parser("snapshot", help="check, build, or activate the Daytona snapshot")
    snapshot_commands = snapshot.add_subparsers(dest="snapshot_command", required=True)
    snap_check = snapshot_commands.add_parser("check", help="verify snapshot availability and resource floors")
    snap_build = snapshot_commands.add_parser("build", help="build the Fleet Daytona snapshot")
    snap_activate = snapshot_commands.add_parser("activate", help="activate the Fleet Daytona snapshot")

    web = commands.add_parser("web", help="serve the Textual client in a browser")
    web.add_argument(
        "--url", type=_web_api_url, default=None, help="connect to this API only; do not start local services"
    )
    web.add_argument("--host", default="127.0.0.1", help="Textual Serve listener host (loopback by default)")
    web.add_argument("--port", type=int, default=8080, help="Textual Serve listener port")
    web.add_argument("--public-url", type=_web_public_url, help="browser-facing URL, e.g. behind a TLS proxy")
    web.add_argument("--session", help="use one Session ID for all browser clients (otherwise each gets its own)")
    web.add_argument("--mlflow-port", type=int, default=None)
    web.add_argument("--mlflow-experiment", default=None)
    web.add_argument("--startup-timeout", type=float, default=180.0, help="seconds to wait for each service")

    cli = commands.add_parser(
        "cli",
        help="start the API server and open the interactive terminal client",
    )
    cli.add_argument("--url", default=None, help="connect to this server only; do not start anything")
    cli.add_argument("--session", default=f"sess_{uuid.uuid4().hex[:8]}")
    cli.add_argument("backend", nargs="?", choices=("databricks",), help="use Databricks managed MLflow")
    cli.add_argument("--mlflow-port", type=int, default=None)
    cli.add_argument("--mlflow-experiment", default=None)
    cli.add_argument("--startup-timeout", type=float, default=180.0, help="seconds to wait for each service")

    optimize = commands.add_parser(
        "optimize", help="compile a GEPA candidate from every reviewed example (live), or --check readiness"
    )
    add_optimization_arguments(optimize)

    evaluate = commands.add_parser("eval", help="evaluation datasets, gold labels, and runs")
    eval_commands = evaluate.add_subparsers(dest="eval_command", required=True)
    dataset = eval_commands.add_parser("dataset", help="create the synthetic evaluation dataset")
    dataset.add_argument(
        "--name", default="", help="dataset name (databricks mode: catalog.schema.table from the trace settings)"
    )
    dataset.add_argument("--seed", type=int, default=7)
    dataset.add_argument("--long-context", action="store_true", help="add synthetic 20K-200K character log cases")
    run = eval_commands.add_parser("run", help="run synthetic cases against a live server and score them")
    run.add_argument("--url", default=None)
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--limit", type=int, default=0, help="only run the first N cases")
    run.add_argument(
        "--collect-only",
        action="store_true",
        help="run and label the Turns, skipping the MLflow judge suite",
    )
    score = eval_commands.add_parser("score", help="score already collected, labelled traces without rerunning them")
    score.add_argument("trace_ids", nargs="+", metavar="trace_id")
    score.add_argument("--scorer-experiment-id", help="source experiment for registered scorers")
    score.add_argument("--registered-scorer", action="append", help="registered scorer name (repeatable)")
    score.add_argument(
        "--execution-diagnostics-only", action="store_true", help="run only validated execution diagnostics"
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
    label.add_argument("--expectations-file", help="replace structured artifact/delegation gold with a JSON object")
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

    for command in (
        serve,
        web,
        cli,
        optimize,
        dataset,
        run,
        score,
        label,
        verify,
        doctor,
        snap_check,
        snap_build,
        snap_activate,
    ):
        command.add_argument(
            "--mlflow",
            choices=("local", "databricks"),
            default=None,
            help="MLflow backend (default: configured setting); replaces the retired --mlflow env option",
        )

    args = parser.parse_args(argv)
    if getattr(args, "backend", None) and args.mlflow == "local":
        parser.error("cli databricks conflicts with --mlflow local")
    backend = getattr(args, "backend", None) or getattr(args, "mlflow", None)
    managed_settings = None
    if args.command == "optimize" and getattr(args, "managed_run_id", None):
        try:
            managed_settings = read_worker_settings(sys.stdin)
        except (ValueError, TypeError):
            parser.error("invalid private worker configuration")
        if backend is None:
            backend = managed_settings.mlflow_mode
        if managed_settings.mlflow_mode != backend:
            parser.error("worker MLflow backend does not match its server configuration")
    if getattr(args, "mlflow_port", None) is not None:
        if backend == "databricks":
            parser.error("--mlflow-port is only supported with local MLflow")
        if not 1 <= args.mlflow_port <= 65535:
            parser.error("--mlflow-port must be between 1 and 65535")

    settings = managed_settings
    config_error: str | None = None
    if settings is None:
        try:
            settings = get_settings(backend)
        except (ValidationError, ValueError) as exc:
            config_error = _configuration_error(exc)
            if args.command != "doctor":
                parser.error(f"invalid configuration: {config_error} (run `fleet doctor` for details)")

    if args.command == "doctor":
        if settings is not None and not config_error:
            try:
                settings.prepare_mlflow_auth()
            except (ValidationError, ValueError) as exc:
                config_error = _configuration_error(exc)
        from fleet_rlm.doctor import run_doctor

        raise SystemExit(run_doctor(args, settings, config_error=config_error, console=_CONSOLE))

    assert settings is not None
    try:
        backend = settings.mlflow_mode
        if getattr(args, "mlflow_port", None) is not None and backend != "local":
            parser.error("--mlflow-port is only supported with local MLflow")
        overrides = {}
        if getattr(args, "mlflow_port", None) is not None:
            overrides["mlflow_local_port"] = args.mlflow_port
        if getattr(args, "mlflow_experiment", None) is not None:
            overrides[f"mlflow_{backend}_experiment_name"] = args.mlflow_experiment
        if args.command == "serve":
            if args.host is not None:
                overrides["host"] = args.host
            if args.port is not None:
                overrides["port"] = args.port
        if overrides:
            settings = settings.with_explicit_overrides(**overrides)
    except (ValidationError, ValueError) as exc:
        parser.error(f"invalid configuration: {_configuration_error(exc)} (run `fleet doctor` for details)")
    if args.command not in {None, "cli", "snapshot"}:
        settings.prepare_mlflow_auth()
    if args.command == "snapshot":
        from fleet_rlm.doctor import run_snapshot

        raise SystemExit(run_snapshot(args, settings, console=_CONSOLE))
    target = settings.mlflow_target()
    if args.command == "eval" and args.eval_command == "run" and args.url is None:
        args.url = f"http://{settings.host}:{settings.port}"

    if (
        args.command in {"eval", "trace", "optimize"}
        and not getattr(args, "approve", None)
        and target.mode == "local"
        and not launcher.mlflow_is_healthy(target.tracking_uri)
    ):
        parser.error(
            f"Local MLflow is not reachable at {target.tracking_uri}; start it with `fleet cli` "
            "or run an MLflow server separately before using standalone commands."
        )
    if args.command == "optimize":
        from fleet_rlm.optimize import gepa

        gepa.run(args, optimize, settings)
        return
    if args.command == "eval":
        _eval_command(args, settings)
        return
    if args.command == "trace":
        _trace_verify_command(args, settings)
        return
    if args.command == "serve":
        if not settings.allow_non_loopback_bind and not _is_loopback(settings.host):
            parser.error("non-loopback binding requires FLEET_ALLOW_NON_LOOPBACK_BIND=true")
        from fleet_rlm.api.app import create_app

        uvicorn.run(
            create_app(settings=settings),
            host=settings.host,
            port=settings.port,
            reload=False,
        )
        return

    if args.command == "web":
        if not 1 <= args.port <= 65535:
            parser.error("web --port must be between 1 and 65535")
        if not settings.allow_non_loopback_bind and not _is_loopback(args.host):
            parser.error("non-loopback binding requires FLEET_ALLOW_NON_LOOPBACK_BIND=true")
        if (args.host in {"0.0.0.0", "::"} or ":" in args.host) and args.public_url is None:
            parser.error("web --public-url is required when binding to a wildcard or IPv6 host")
        try:
            web_auth_token(args.host)
        except ValueError as exc:
            parser.error(str(exc))
        if args.session is not None:
            try:
                validate_session_id(args.session)
            except ValueError as exc:
                parser.error(str(exc))
        if not args.url and not settings.allow_non_loopback_bind and not _is_loopback(settings.host):
            parser.error("non-loopback binding requires FLEET_ALLOW_NON_LOOPBACK_BIND=true")
        try:
            plan = launcher.plan_launch(
                url=args.url,
                host=settings.host,
                port=settings.port,
                settings=settings,
            )
            _CONSOLE.print(f"MLflow: {target.mode} | {target.tracking_uri} | {target.experiment_name}", markup=False)
            with launcher.running_services(
                plan,
                host=settings.host,
                port=settings.port,
                mlflow_port=settings.mlflow_local_port,
                startup_timeout=args.startup_timeout,
                log=lambda message: _CONSOLE.print(f"[dim]{message}[/dim]"),
            ):
                launcher.verify_api_destination(plan.api_url, settings.mlflow_descriptor())
                browser_url = args.public_url or f"http://{args.host}:{args.port}"
                _CONSOLE.print(f"Fleet RLM Lite web app: {browser_url}", markup=False)
                serve_web_client(
                    api_url=plan.api_url,
                    mlflow_mode=backend,
                    host=args.host,
                    port=args.port,
                    session_id=args.session,
                    mlflow_port=args.mlflow_port,
                    mlflow_experiment=args.mlflow_experiment,
                    public_url=args.public_url,
                )
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        except launcher.LaunchError as exc:
            _CONSOLE.print(f"[bold red]Could not start services:[/bold red] {exc}")
            raise SystemExit(1) from None
        except OSError as exc:
            _CONSOLE.print(f"[bold red]Could not start web server:[/bold red] {type(exc).__name__}")
            raise SystemExit(1) from None
        return

    session_id = getattr(args, "session", f"sess_{uuid.uuid4().hex[:8]}")
    url = getattr(args, "url", None)
    if not url and not settings.allow_non_loopback_bind and not _is_loopback(settings.host):
        parser.error("non-loopback binding requires FLEET_ALLOW_NON_LOOPBACK_BIND=true")
    try:
        plan = launcher.plan_launch(url=url, host=settings.host, port=settings.port, settings=settings)
        _CONSOLE.print(f"MLflow: {target.mode} | {target.tracking_uri} | {target.experiment_name}", markup=False)
        with launcher.running_services(
            plan,
            host=settings.host,
            port=settings.port,
            mlflow_port=settings.mlflow_local_port,
            startup_timeout=getattr(args, "startup_timeout", 180.0),
            log=lambda message: _CONSOLE.print(f"[dim]{message}[/dim]"),
        ):
            launcher.verify_api_destination(plan.api_url, settings.mlflow_descriptor())
            run_interactive_cli(base_url=plan.api_url, session_id=session_id, settings=settings)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except launcher.LaunchError as exc:
        _CONSOLE.print(f"[bold red]Could not start services:[/bold red] {exc}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
