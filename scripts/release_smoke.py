#!/usr/bin/env python3
"""Smoke-test installed release bytes without starting provider lifespans."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import subprocess
import sys
from importlib import resources
from pathlib import Path
from shutil import which
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _run_help(executable: str) -> str:
    sibling = Path(sys.executable).with_name(executable)
    path = str(sibling) if sibling.is_file() else which(executable)
    if path is None:
        raise RuntimeError(f"installed entry point is unavailable: {executable}")
    completed = subprocess.run(
        (path, "--help"),
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"installed entry point failed: {executable}")
    return completed.stdout


async def _check_openapi() -> dict[str, Any]:
    import httpx

    from fleet_rlm.app import create_app
    from fleet_rlm.config import Settings

    settings = Settings(run_environment="daytona", data_root=str(Path.cwd() / ".release-smoke-data"))
    transport = httpx.ASGITransport(app=create_app(settings=settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://release-smoke") as client:
        response = await client.get("/openapi.json")
    if response.status_code != 200:
        raise RuntimeError("installed OpenAPI route did not return 200")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("installed OpenAPI response is not an object")
    info = payload.get("info")
    paths = payload.get("paths")
    if not isinstance(info, dict) or not isinstance(paths, dict):
        raise RuntimeError("installed OpenAPI response is incomplete")
    return payload


def run(expected_version: str | None) -> int:
    import fleet_rlm

    package_path = Path(fleet_rlm.__file__).resolve()
    if package_path.is_relative_to(ROOT / "src"):
        raise RuntimeError("release smoke imported Fleet from the checkout")
    actual_version = importlib.metadata.version("fleet-rlm")
    if expected_version is not None and actual_version != expected_version:
        raise RuntimeError("installed Fleet version does not match the release artifact")
    if fleet_rlm.__version__ != actual_version:
        raise RuntimeError("installed package metadata and runtime version differ")
    package = resources.files("fleet_rlm")
    required_assets = (
        package / "py.typed",
        package / "daytona" / "snapshot-requirements.txt",
        package / "skills" / "bundled" / "dspy-rlm" / "SKILL.md",
    )
    if not all(asset.is_file() for asset in required_assets):
        raise RuntimeError("installed Fleet package assets are incomplete")
    if importlib.metadata.version("dspy") != "3.3.1":
        raise RuntimeError("installed DSPy is not the certified final release")
    fleet_help = _run_help("fleet")
    fleet_rlm_help = _run_help("fleet-rlm")
    if not {"web", "cli", "doctor"} <= set(fleet_help.split()):
        raise RuntimeError("fleet help is missing the supported command inventory")
    if "serve-api" not in fleet_rlm_help:
        raise RuntimeError("fleet-rlm help is missing serve-api")
    openapi = asyncio.run(_check_openapi())
    info = openapi["info"]
    paths = openapi["paths"]
    if info.get("title") != "fleet-rlm" or "/api/sessions/{session_id}/turns" not in paths:
        raise RuntimeError("installed OpenAPI contract is incomplete")
    print(f"installed release smoke passed: fleet-rlm {actual_version}, {len(paths)} OpenAPI paths")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version")
    args = parser.parse_args(argv)
    try:
        return run(args.expected_version)
    except (OSError, RuntimeError, importlib.metadata.PackageNotFoundError) as exc:
        print(f"release smoke failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
