"""FastAPI server launcher used by the Fleet CLI."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from fleet_rlm.cli.bind_safety import UnsafeBindError, require_safe_bind_host


def serve_api(
    *,
    host: str,
    port: int,
    reload: bool,
    allow_non_loopback: bool = False,
) -> None:
    """Run the configured application after enforcing the shared bind-safety gate."""
    require_safe_bind_host(host, allow_non_loopback=allow_non_loopback)

    import uvicorn

    uvicorn.run("fleet_rlm.main:app", host=host, port=port, reload=reload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the Fleet RLM FastAPI backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--allow-non-loopback-bind",
        action="store_true",
        help=(
            "allow binding to a non-loopback address; Fleet has no caller "
            "authentication and will expose the local API on the network"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        serve_api(
            host=args.host,
            port=args.port,
            reload=args.reload,
            allow_non_loopback=args.allow_non_loopback_bind,
        )
    except UnsafeBindError as exc:
        # Same policy and exit code as the `fleet` CLI bind gate.
        parser.exit(1, f"{parser.prog}: error: {exc}\n")
    return 0


__all__ = ["main", "serve_api"]


if __name__ == "__main__":
    raise SystemExit(main())
