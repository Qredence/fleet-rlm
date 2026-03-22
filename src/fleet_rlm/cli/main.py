"""Entry point for the standalone `fleet` interactive chat command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fleet_rlm.cli.terminal.chat import TerminalChatOptions, run_terminal_chat
from fleet_rlm.integrations.config.env import AppConfig

from .config import initialize_app_config, split_hydra_overrides


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet",
        description=(
            "Start standalone fleet interactive chat. "
            "Hydra overrides are supported as key=value tokens.\n"
            "Use 'fleet web' to launch the Web UI server."
        ),
    )
    # Support optional subcommands loosely
    parser.add_argument(
        "command",
        nargs="?",
        choices=["web"],
        help="Optional subcommand (e.g., 'web' to launch the Web UI).",
    )
    parser.add_argument(
        "--docs-path",
        type=Path,
        default=None,
        help="Optional document path to preload into the chat session.",
    )
    parser.add_argument(
        "--trace-mode",
        choices=("compact", "verbose", "off"),
        default="compact",
        help="Trace display mode.",
    )
    parser.add_argument(
        "--volume-name",
        type=str,
        default=None,
        help="Modal volume name for persistent storage.",
    )
    parser.add_argument(
        "--secret-name",
        type=str,
        default=None,
        help="Modal secret name for credentials.",
    )
    return parser


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _initialize_config(overrides: list[str]) -> AppConfig:
    """Compatibility shim for tests and callers patching the old helper name."""
    return initialize_app_config(overrides)


def main() -> None:
    # Quick check for 'web' subcommand before strict parsing
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        # Check if required Web UI/API dependencies are available.
        try:
            import fastapi  # noqa: F401
            import jwt  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError:
            print(
                "Error: Required Web UI dependencies not found. "
                "Reinstall/upgrade fleet-rlm (plain install should include Web UI support). "
                "Optional server extras remain available via `fleet-rlm[server]`.",
                file=sys.stderr,
            )
            raise SystemExit(1)

        print("Starting Web UI and API server on http://0.0.0.0:8000 ...")
        # Delegate to the fleet-rlm CLI's serve-api command
        # This reuses all the existing config initialization and uvicorn setup
        from .fleet_cli import main as cli_main

        # Rewrite sys.argv to simulate running `fleet-rlm serve-api --host 0.0.0.0`
        # Keep any hydra overrides that might have been passed
        hydra_args = [
            arg for arg in sys.argv[2:] if "=" in arg and not arg.startswith("-")
        ]
        sys.argv = [
            "fleet-rlm",
            "serve-api",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ] + hydra_args

        cli_main()
        return

    parser = _build_parser()
    args, remainder = parser.parse_known_args(sys.argv[1:])

    hydra_overrides, unknown_args = split_hydra_overrides(remainder)

    if unknown_args:
        parser.error(f"Unknown arguments: {' '.join(unknown_args)}")

    try:
        config = _initialize_config(hydra_overrides)
    except Exception as exc:
        print(f"Configuration Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    run_terminal_chat(
        config=config,
        options=TerminalChatOptions(
            docs_path=args.docs_path,
            trace_mode=args.trace_mode,
        ),
    )


if __name__ == "__main__":
    main()
