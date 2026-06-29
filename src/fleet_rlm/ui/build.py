#!/usr/bin/env python3
"""Build the frontend and sync packaged UI assets."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    """Build and return an ArgumentParser for frontend build workflow.

    Returns:
        argparse.ArgumentParser: Configured parser for building and syncing UI assets.
    """
    repo_root = _repo_root()
    parser = argparse.ArgumentParser(description="Build the frontend and sync packaged UI assets")
    parser.add_argument(
        "--frontend-dir",
        type=Path,
        default=repo_root / "src" / "frontend",
        help="Frontend workspace to build",
    )
    parser.add_argument(
        "--target-ui-dir",
        type=Path,
        default=repo_root / "src" / "fleet_rlm" / "ui",
        help="Packaged UI directory that should receive the built dist assets",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip 'pnpm install --frozen-lockfile' and only run the frontend build",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build the frontend UI and sync assets to the packaged location.

    Args:
        argv: Command-line arguments (list[str] | None). Defaults to sys.argv.

    Returns:
        int: Exit code (0 for success, non-zero for failure).
    """
    args = build_parser().parse_args(argv)
    frontend_dir = args.frontend_dir.resolve()
    target_ui_dir = args.target_ui_dir.resolve()

    if not frontend_dir.exists():
        print(f"Error: Frontend directory not found at {frontend_dir}", file=sys.stderr)
        return 1

    print(f"Building frontend UI from {frontend_dir}...")

    if shutil.which("pnpm") is None:
        print(
            "Error: 'pnpm' command not found. Please install pnpm (https://pnpm.io).",
            file=sys.stderr,
        )
        return 1

    if not args.skip_install:
        print("Running 'pnpm install --frozen-lockfile'...")
        try:
            subprocess.run(["pnpm", "install", "--frozen-lockfile"], cwd=frontend_dir, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"Error running 'pnpm install': {exc}", file=sys.stderr)
            return 1

    build_cmd = ["pnpm", "run", "build"]

    print(f"Running '{' '.join(build_cmd)}'...")
    try:
        subprocess.run(build_cmd, cwd=frontend_dir, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Error running frontend build: {exc}", file=sys.stderr)
        return 1

    source_dist = frontend_dir / "dist"
    if not source_dist.exists():
        print(f"Error: Build failed, {source_dist} not found.", file=sys.stderr)
        return 1

    ensure_script = frontend_dir / "scripts" / "ensure-entrypoint.mjs"
    if ensure_script.exists():
        print(f"Running '{ensure_script}' on {source_dist}...")
        try:
            subprocess.run(
                ["node", str(ensure_script), "--dist-dir", str(source_dist)],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            print(f"Error running ensure-entrypoint.mjs: {exc}", file=sys.stderr)
            return 1
    else:
        print(f"Warning: {ensure_script} not found. Skipping entrypoint generation.")

    target_ui_dir.mkdir(parents=True, exist_ok=True)
    init_py = target_ui_dir / "__init__.py"
    if not init_py.exists():
        init_py.touch()

    target_dist = target_ui_dir / "dist"
    if target_dist.exists():
        shutil.rmtree(target_dist)

    print(f"Copying build output from {source_dist} to {target_dist}...")
    shutil.copytree(source_dist, target_dist)

    print("Frontend build complete and copied successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
