"""Retain a Daytona SDK compatibility receipt without inferring live evidence.

The default lane runs the bounded local contract suite. Live surfaces remain
``not_exercised`` until a separately authorized operator campaign records them;
this command never contacts Daytona or loads dotenv.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "fleet.daytona-sdk-compatibility/v1"
MAX_RECEIPT_BYTES = 128 * 1024
UNIT_TESTS = (
    "tests/unit/backend/test_daytona_platform.py",
    "tests/unit/backend/daytona/test_native_sdk_contract.py",
    "tests/unit/backend/daytona/test_sdk_resource_errors.py",
    "tests/unit/scripts/test_daytona_snapshot.py",
)
LIVE_SURFACES = ("volume", "sandbox", "broker", "upload", "lifecycle", "public_events")


class CertificationError(RuntimeError):
    pass


def _git_identity() -> dict[str, object]:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
    except (OSError, subprocess.CalledProcessError):
        return {"git_sha": "unknown", "dirty": True}
    return {"git_sha": sha, "dirty": dirty}


def _unit_result(timeout: int) -> dict[str, object]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *UNIT_TESTS, "-q", "--tb=no"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "failed", "test_paths": list(UNIT_TESTS)}
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "test_paths": list(UNIT_TESTS),
    }


def receipt(*, unit: dict[str, object]) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": _git_identity(),
        "versions": {name: importlib.metadata.version(name) for name in ("daytona", "dspy", "mlflow")},
        "unit": unit,
        "live": {
            surface: {
                "status": "not_exercised",
                "reason": "requires explicit operator-authorized live campaign",
            }
            for surface in LIVE_SURFACES
        },
        "promotion_eligible": False,
        "promotion_reason": "live_surfaces_not_exercised",
    }


def write_once(path: Path, payload: dict[str, object]) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise CertificationError("compatibility receipt exceeds size bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(encoded)
    except FileExistsError as exc:
        raise CertificationError("compatibility receipt already exists") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180, choices=range(30, 601), metavar="30..600")
    args = parser.parse_args(argv)
    try:
        write_once(args.receipt, receipt(unit=_unit_result(args.timeout)))
    except CertificationError as error:
        print(str(error), file=sys.stderr)
        return 2
    print("Daytona SDK compatibility receipt retained; live surfaces remain not exercised.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
