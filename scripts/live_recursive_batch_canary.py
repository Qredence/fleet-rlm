#!/usr/bin/env python3
"""Run the maintained two-child Daytona recursive-batch canary.

This wrapper creates no sessions or clients itself. The selected test owns the
FastAPI session, Daytona resources, assertions, receipt, and strict cleanup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST = "tests/live/backend/test_daytona_recursive_batch.py::test_daytona_recursive_batch_two_children_through_fastapi"


def _require_clean_candidate() -> str:
    """Require the run to start from a clean, named candidate branch."""
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("canary candidate identity is unavailable") from exc
    unexpected = [line for line in status if line and not line.startswith("?? .factory/")]
    if not branch or branch in {"main", "master"} or unexpected:
        raise RuntimeError("canary requires a clean tracked candidate branch")
    return sha


def _validate_receipt(output: Path) -> None:
    """Require the canary's complete, metadata-only evidence contract."""
    try:
        receipt = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("canary receipt is not valid JSON") from exc
    if not isinstance(receipt, dict) or receipt.get("schema") != "fleet.p35d-root-batch/v1":
        raise RuntimeError("canary receipt has an unexpected schema")
    candidate = receipt.get("candidate")
    try:
        expected_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        expected_lock = hashlib.sha256((_REPO_ROOT / "uv.lock").read_bytes()).hexdigest()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("canary candidate identity is unavailable") from exc
    if (
        not isinstance(candidate, dict)
        or candidate.get("sha") != expected_sha
        or candidate.get("tracked_tree_clean") is not True
        or candidate.get("lockfile_sha256") != expected_lock
    ):
        raise RuntimeError("canary receipt does not match the clean candidate")
    if receipt.get("passed") is not True:
        raise RuntimeError("canary receipt does not prove success")
    cleanup = receipt.get("cleanup")
    if (
        not isinstance(cleanup, dict)
        or cleanup.get("confirmed_absent") is not True
        or cleanup.get("admission_restored") is not True
    ):
        raise RuntimeError("canary receipt does not prove cleanup")
    trace = receipt.get("trace")
    if (
        not isinstance(trace, dict)
        or trace.get("root_span") != "fleet_turn"
        or not isinstance(trace.get("trace_id"), str)
        or not trace["trace_id"]
        or not isinstance(trace.get("child_spans"), int)
        or trace["child_spans"] < 2
    ):
        raise RuntimeError("canary receipt does not prove root and child traces")
    assertions = receipt.get("assertions")
    if (
        not isinstance(assertions, dict)
        or assertions.get("native_child_count") != 2
        or assertions.get("ordered_root_batch") is not True
        or assertions.get("peak_child_concurrency") != 2
        or assertions.get("retained_root_second_turn") is not True
    ):
        raise RuntimeError("canary receipt does not prove recursive execution and root reuse")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse an explicit evidence destination without loading credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New JSON receipt path outside the repository.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output.expanduser().resolve()
    if output.is_relative_to(_REPO_ROOT):
        raise SystemExit("--output must be outside the repository")
    if output.exists():
        raise SystemExit("--output must name a new receipt")
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        raise SystemExit("set FLEET_LIVE=1 to authorize the credentialed canary")
    try:
        _require_clean_candidate()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    environment = {**os.environ, "FLEET_LIVE_EVIDENCE_PATH": str(output)}
    result = subprocess.run(["uv", "run", "pytest", "-q", _TEST], cwd=_REPO_ROOT, env=environment, check=False)
    if result.returncode:
        return result.returncode
    if not output.is_file() or output.stat().st_size == 0:
        print("canary passed without a receipt", file=sys.stderr)
        return 1
    try:
        _validate_receipt(output)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
