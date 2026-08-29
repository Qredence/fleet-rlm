"""Shared metadata-only evidence helpers for the P35-D live matrix."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"


def candidate_identity() -> dict[str, object]:
    """Return the candidate and certified provider identity without secrets."""
    load_dotenv(_REPO_ROOT / ".env", override=False)
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
    unexpected = [line for line in status if line and not line.startswith("?? .factory/")]
    return {
        "sha": sha,
        "lockfile_sha256": hashlib.sha256((_REPO_ROOT / "uv.lock").read_bytes()).hexdigest(),
        "dspy": importlib.metadata.version("dspy"),
        "daytona_snapshot": os.environ.get("FLEET_DAYTONA_SNAPSHOT"),
        "daytona_target": os.environ.get("DAYTONA_TARGET"),
        "tracked_tree_clean": not unexpected,
    }


def write_receipt(payload: dict[str, Any]) -> None:
    """Atomically write a matrix receipt when the runner requested one."""
    run_id = os.environ.get("FLEET_P35D_RUN_ID")
    if run_id:
        payload = {**payload, "run_id": run_id}
    raw_path = os.environ.get(_EVIDENCE_ENV)
    if not raw_path:
        return
    path = Path(raw_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
