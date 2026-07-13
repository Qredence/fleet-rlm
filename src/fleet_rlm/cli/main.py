"""Thin launchers for the canonical Fleet RLM ASGI application."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def _serve_parser(*, program: str, command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=program, description="Serve the Fleet RLM backend")
    subcommands = parser.add_subparsers(dest="command", required=True)
    serve = subcommands.add_parser(command, help="start the FastAPI backend")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    return parser


def _run(*, program: str, command: str, argv: Sequence[str] | None = None) -> None:
    args = _serve_parser(program=program, command=command).parse_args(argv)
    import uvicorn

    uvicorn.run(
        "fleet_rlm.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def fleet_main(argv: Sequence[str] | None = None) -> None:
    """Run ``fleet web``."""
    _run(program="fleet", command="web", argv=argv)


def fleet_rlm_main(argv: Sequence[str] | None = None) -> None:
    """Run ``fleet-rlm serve-api``."""
    _run(program="fleet-rlm", command="serve-api", argv=argv)
