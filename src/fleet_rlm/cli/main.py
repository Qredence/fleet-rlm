"""Thin launchers for the canonical Fleet RLM ASGI application."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import Any

from fleet_rlm.cli.bind_safety import UnsafeBindError, require_safe_bind_host


def _add_serve_command(
    subcommands: Any,
    command: str,
    *,
    help_text: str,
    run_environment: str | None = None,
    supervise_tui: bool = False,
) -> None:
    serve = subcommands.add_parser(command, help=help_text)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument(
        "--allow-non-loopback-bind",
        action="store_true",
        help=(
            "allow binding to a non-loopback address; Fleet has no caller "
            "authentication and will expose the local API on the network"
        ),
    )
    if supervise_tui:
        serve.add_argument("tui_args", nargs=argparse.REMAINDER)
    serve.set_defaults(
        run_environment=run_environment,
        supervise_tui=supervise_tui,
    )


def _fleet_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet", description="Run and diagnose Fleet RLM")
    subcommands = parser.add_subparsers(dest="command", required=True)
    _add_serve_command(subcommands, "web", help_text="start the configured FastAPI backend")
    _add_serve_command(
        subcommands,
        "cli",
        help_text="start the Daytona backend and pi-tui terminal",
        run_environment="daytona",
        supervise_tui=True,
    )
    doctor = subcommands.add_parser("doctor", help="run opt-in provider diagnostics")
    doctor_providers = doctor.add_subparsers(dest="doctor_provider", required=True)
    daytona = doctor_providers.add_parser(
        "daytona",
        help="verify Daytona, database, scoped mount, and interpreter access",
    )
    daytona.set_defaults(command="doctor", doctor_provider="daytona")
    return parser


def _single_command_parser(*, program: str, command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=program, description="Serve the Fleet RLM backend")
    subcommands = parser.add_subparsers(dest="command", required=True)
    _add_serve_command(subcommands, command, help_text="start the FastAPI backend")
    return parser


def _run(parser: argparse.ArgumentParser, argv: Sequence[str] | None = None) -> None:
    args = parser.parse_args(argv)
    # Certified-runtime guard: fail closed with a bounded public error before
    # any provider, database, or Daytona resource is constructed and before any
    # listener binds. Argument parsing (and --help) stay reachable on any
    # runtime; every serving/diagnostic command is guarded.
    from fleet_rlm.rlm.dspy_contract import UncertifiedDSpyVersionError, assert_dspy_version

    try:
        assert_dspy_version()
    except UncertifiedDSpyVersionError as exc:
        parser.exit(1, f"{parser.prog}: error: {exc}\n")
    if args.command == "doctor":
        _run_doctor(parser, args.doctor_provider)
        return

    try:
        require_safe_bind_host(
            args.host,
            allow_non_loopback=bool(args.allow_non_loopback_bind),
        )
    except UnsafeBindError as exc:
        parser.exit(1, f"fleet: error: {exc}\n")
    if args.supervise_tui:
        from fleet_rlm.cli.supervisor import SupervisorError, supervise

        tui_args = tuple(args.tui_args)
        if tui_args[:1] == ("--",):
            tui_args = tui_args[1:]
        try:
            supervise(
                host=args.host,
                port=args.port,
                reload=args.reload,
                run_environment=args.run_environment,
                tui_args=tui_args,
            )
        except SupervisorError as exc:
            parser.exit(1, f"fleet: error: {exc}\n")
        return
    import uvicorn

    uvicorn.run(
        "fleet_rlm.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


_DOCTOR_ACTIONS = {
    "auth": "verify FLEET_DAYTONA_API_KEY and Daytona account access.",
    "quota": "verify Daytona account capacity and quota.",
    "network_timeout": "verify network access to Daytona and retry.",
    "provider_5xx": "retry after the Daytona service recovers.",
    "request_validation": "update the Fleet Daytona adapter to the pinned SDK contract.",
    "mount_mismatch": "correct the Fleet scoped Volume mount contract.",
    "rlm_provider": "verify the configured Root LM follows the pinned DSPy RLM JSON action contract.",
    "database": "verify FLEET_DATABASE_URL and upgrade the database to Alembic head.",
    "settings": "configure the required FLEET_DAYTONA_API_KEY and FLEET_DATABASE_URL settings.",
    "interpreter": "inspect the disposable Sandbox interpreter capability.",
    "cleanup": "check Daytona for a labelled fleet-daytona-doctor Sandbox and delete it if present.",
    "unknown": "inspect sanitized server diagnostics and retry.",
}


def _run_doctor(parser: argparse.ArgumentParser, provider: str) -> None:
    if provider != "daytona":
        parser.error(f"unsupported doctor provider: {provider}")
    from fleet_rlm.config import active_profile, load_runtime_settings, redacted_policy_summary
    from fleet_rlm.daytona.diagnostics import run_daytona_doctor

    try:
        settings = load_runtime_settings()
    except Exception:
        _emit("[failed] settings: Required Fleet Daytona settings are missing or invalid.")
        _emit(f"action: {_DOCTOR_ACTIONS['settings']}")
        raise SystemExit(1) from None
    profile = active_profile(settings)
    _emit(f"[ok] policy: {redacted_policy_summary(settings, profile=profile or 'unknown')}")
    result = asyncio.run(run_daytona_doctor(settings))
    for step in result.steps:
        state = "ok" if step.ok else "failed"
        _emit(f"[{state}] {step.name}: {step.message}")
    if not result.ok:
        categories = [step.category for step in result.steps if not step.ok and step.category]
        if not categories:
            categories = [result.failure_category or "unknown"]
        for category in dict.fromkeys(categories):
            action = _DOCTOR_ACTIONS.get(category, _DOCTOR_ACTIONS["unknown"])
            _emit(f"action: {action}")
        raise SystemExit(1)


def _emit(message: str) -> None:
    """Write one public CLI message to stdout."""
    sys.stdout.write(f"{message}\n")


def fleet_main(argv: Sequence[str] | None = None) -> None:
    """Run the configured Daytona Fleet backend."""
    _run(_fleet_parser(), argv)


def fleet_rlm_main(argv: Sequence[str] | None = None) -> None:
    """Run ``fleet-rlm serve-api``."""
    _run(_single_command_parser(program="fleet-rlm", command="serve-api"), argv)
