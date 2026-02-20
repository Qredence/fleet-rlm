"""Entry point for the standalone `fleet` interactive chat command."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .cli import _initialize_config
from .terminal_chat import TerminalChatOptions, run_terminal_chat


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
    parser.add_argument(
        "--ui",
        choices=("auto", "ink", "python"),
        default="auto",
        help="Choose UI runtime. Default auto prefers Ink and falls back to Python.",
    )
    return parser


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _find_ink_cli() -> Path | None:
    root = _repo_root()
    candidates = [
        root / "tui-cli" / "tui-ink" / "dist" / "cli.js",
        root / "tui-ink" / "dist" / "cli.js",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _run_ink_ui(
    *,
    ink_cli: Path,
    docs_path: Path | None,
    trace_mode: str,
    volume_name: str | None,
    secret_name: str | None,
    hydra_overrides: list[str],
) -> int:
    node_bin = shutil.which("node")
    if node_bin is None:
        raise RuntimeError("Node.js is required to run Ink UI.")
    cmd = [node_bin, str(ink_cli), "--bridge-python", sys.executable]
    if docs_path is not None:
        cmd.extend(["--docs-path", str(docs_path)])
    cmd.extend(["--trace-mode", trace_mode])
    if volume_name is not None:
        cmd.extend(["--volume-name", volume_name])
    if secret_name is not None:
        cmd.extend(["--secret-name", secret_name])
    if hydra_overrides:
        cmd.append("--")
        cmd.extend(hydra_overrides)
    completed = subprocess.run(cmd, check=False)
    return completed.returncode


def main() -> None:
    # Quick check for 'web' subcommand before strict parsing
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        # Check if the server extra is installed
        try:
            import uvicorn  # noqa: F401
            import fastapi  # noqa: F401
        except ImportError:
            print(
                'Error: Server dependencies not found. Please install with: uv pip install -e ".[server]"',
                file=sys.stderr,
            )
            raise SystemExit(1)

        print("Starting Web UI and API server on http://0.0.0.0:8000 ...")
        # Delegate to the fleet-rlm CLI's serve-api command
        # This reuses all the existing config initialization and uvicorn setup
        from .cli import main as cli_main

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

    hydra_overrides: list[str] = []
    unknown_args: list[str] = []
    for token in remainder:
        if "=" in token and not token.startswith("-"):
            hydra_overrides.append(token)
        else:
            unknown_args.append(token)

    if unknown_args:
        parser.error(f"Unknown arguments: {' '.join(unknown_args)}")

    if args.ui in {"auto", "ink"}:
        ink_cli = _find_ink_cli()
        if ink_cli is not None:
            try:
                exit_code = _run_ink_ui(
                    ink_cli=ink_cli,
                    docs_path=args.docs_path,
                    trace_mode=args.trace_mode,
                    volume_name=args.volume_name,
                    secret_name=args.secret_name,
                    hydra_overrides=hydra_overrides,
                )
            except RuntimeError as exc:
                if args.ui == "ink":
                    print(f"UI Error: {exc}", file=sys.stderr)
                    raise SystemExit(2) from exc
                print(
                    f"[fleet] Ink unavailable: {exc}. Falling back to Python UI.",
                    file=sys.stderr,
                )
            else:
                if exit_code == 0:
                    return
                if args.ui == "ink":
                    raise SystemExit(exit_code)
                print(
                    f"[fleet] Ink exited with code {exit_code}. Falling back to Python UI.",
                    file=sys.stderr,
                )
        elif args.ui == "ink":
            print(
                "UI Error: Ink bundle not found at tui-cli/tui-ink/dist/cli.js.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        else:
            print(
                "[fleet] Ink bundle not found. Falling back to Python UI.",
                file=sys.stderr,
            )

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
