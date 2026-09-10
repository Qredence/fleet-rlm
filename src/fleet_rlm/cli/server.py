"""Profile-aware FastAPI server launcher used by the Fleet CLI."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


class ProfileReloadError(ValueError):
    """Raised when an explicit profile is combined with Uvicorn reload."""


def serve_api(
    *,
    host: str,
    port: int,
    reload: bool,
    profile: str | None = None,
) -> None:
    """Run the FastAPI application with an optional explicit policy profile.

    The no-profile path intentionally retains the import-string launcher used
    by existing deployments.  Explicit profiles construct ``Settings`` before
    Uvicorn binds, so an invalid profile or missing policy fails before any
    provider, database, or Daytona resource is initialized.  Uvicorn's
    reloader cannot carry an in-memory ``Settings`` object safely, therefore
    explicit profile launches reject ``--reload`` rather than falling back to
    the committed default profile.
    """
    if profile is not None and reload:
        raise ProfileReloadError("--reload cannot be combined with an explicit --profile")

    import uvicorn

    if profile is None:
        uvicorn.run(
            "fleet_rlm.main:app",
            host=host,
            port=port,
            reload=reload,
        )
        return

    from fleet_rlm.app import create_app
    from fleet_rlm.config.loader import load_runtime_settings

    settings = load_runtime_settings(profile=profile)
    application = create_app(settings=settings)
    uvicorn.run(application, host=host, port=port, reload=False)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the Fleet RLM FastAPI backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--profile")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        serve_api(host=args.host, port=args.port, reload=args.reload, profile=args.profile)
    except ProfileReloadError as exc:
        _parser().error(str(exc))
    return 0


__all__ = ["ProfileReloadError", "main", "serve_api"]


if __name__ == "__main__":
    raise SystemExit(main())
