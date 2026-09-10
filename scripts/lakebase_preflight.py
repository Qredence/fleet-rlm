"""Run a read-only, sanitized Lakebase PostgreSQL readiness preflight."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fleet_rlm.persistence.preflight import ManagedDatabasePreflightError, inspect_managed_postgres

_TARGET_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _resolve_mlflow_tracking_uri(explicit: str | None) -> str:
    """Resolve the selected MLflow endpoint without accepting an unknown backend."""
    if explicit is not None:
        value = explicit.strip()
    else:
        try:
            from fleet_rlm.config.loader import load_runtime_settings

            value = (load_runtime_settings().mlflow_tracking_uri or "").strip()
        except Exception as exc:
            raise ManagedDatabasePreflightError("configured MLflow tracking URI could not be resolved") from exc
    if not value:
        raise ManagedDatabasePreflightError("an MLflow tracking URI is required to prove storage separation")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--target", required=True, help="Non-secret target label")
    parser.add_argument("--database-url-env", default="FLEET_DATABASE_URL")
    parser.add_argument("--mlflow-tracking-uri", default=None)
    args = parser.parse_args(argv)
    if not _TARGET_PATTERN.fullmatch(args.target):
        print("target label must be 1-64 chars of [A-Za-z0-9._-], starting alphanumeric", file=sys.stderr)
        return 2
    if os.environ.get("FLEET_LIVE") != "1":
        print("set FLEET_LIVE=1 for the managed database preflight", file=sys.stderr)
        return 2
    database_url = os.environ.get(args.database_url_env, "")
    if not database_url:
        print("database URL is not configured", file=sys.stderr)
        return 2
    try:
        mlflow_tracking_uri = _resolve_mlflow_tracking_uri(args.mlflow_tracking_uri)
        observed = asyncio.run(
            inspect_managed_postgres(database_url, repo_root=ROOT, mlflow_tracking_uri=mlflow_tracking_uri)
        )
        if observed.mlflow_storage_separate is not True:
            raise ManagedDatabasePreflightError(
                "Fleet and MLflow storage separation could not be proven for the selected topology"
            )
        candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
        receipt = {
            "schema": "fleet.lakebase-preflight/v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "candidate": {"git_sha": candidate, "dirty": dirty},
            "target": {"label": args.target},
            "observed": observed.as_dict(),
        }
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        with args.receipt.open("x", encoding="utf-8") as destination:
            json.dump(receipt, destination, indent=2, sort_keys=True)
            destination.write("\n")
    except FileExistsError:
        print("receipt already exists; refusing replacement", file=sys.stderr)
        return 2
    except (ManagedDatabasePreflightError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, ManagedDatabasePreflightError) else "preflight support failed"
        print(message, file=sys.stderr)
        return 2
    print("Lakebase preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
