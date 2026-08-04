"""Run the narrow credentialed Phase 1 Daytona stream canary."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from fleet_rlm.config import FleetConfigurationError, active_profile, require_live_execution

RECEIPT_SCHEMA = "fleet.phase1-daytona-stream/v1"
EVIDENCE_ENV = "FLEET_PHASE1_STREAM_EVIDENCE_PATH"
_LIVE_TEST = "tests/live/backend/test_phase1_daytona_stream.py::test_phase1_daytona_stream_through_fastapi"
_CANDIDATE_PATHS = (
    "scripts/live_phase1_stream_verify.py",
    "tests/live/backend/test_phase1_daytona_stream.py",
)
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LIVE_ROOT_MODEL = "deepseek-v4-flash"
_LIVE_SUB_MODEL = "deepseek-v4-flash"
_FAILURE_CATEGORIES = frozenset(
    {"precondition_failed", "proof_failed", "cleanup_failed", "receipt_invalid", "interrupted"}
)
_FAILURE_PHASES = frozenset(
    {
        "policy",
        "candidate",
        "scenario",
        "receipt",
        "receipt_json",
        "receipt_fields",
        "receipt_status",
        "receipt_timing",
        "receipt_streaming",
        "receipt_assertions",
        "receipt_resources",
    }
)
_TEST_SUCCESS_FIELDS = frozenset({"schema", "timing", "streaming", "assertions", "resources", "failure", "passed"})
_SUCCESS_FIELDS = frozenset(
    {"schema", "candidate", "policy", "timing", "streaming", "assertions", "resources", "failure", "passed"}
)
_REQUIRED_ASSERTIONS = frozenset(
    {
        "typed_submit",
        "attachment_prepared",
        "attachment_accessed",
        "single_semantic_call",
        "batched_semantic_call",
        "no_recursive_child",
        "terminal_ordering",
        "broker_session_cleanup",
        "turn_resources_cleanup",
    }
)

EXIT_PRECONDITION = 2
EXIT_PROOF = 3
EXIT_RECEIPT = 4
EXIT_INTERRUPTED = 130


class ReceiptError(ValueError):
    """A receipt is missing evidence or exceeds the public-safe contract."""

    def __init__(self, phase: str) -> None:
        super().__init__(phase)
        self.phase = phase


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Ignored or out-of-repository JSON receipt path.")
    parser.add_argument("--timeout-seconds", type=int, default=900, help="Pytest timeout (default: 900).")
    return parser


def pytest_command(timeout_seconds: int) -> list[str]:
    return ["uv", "run", "pytest", _LIVE_TEST, "-q", "-n", "0", f"--timeout={timeout_seconds}"]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _git(*args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=_REPO_ROOT, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _candidate() -> tuple[str, str]:
    sha = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    if not branch or branch in {"main", "master"}:
        raise RuntimeError("candidate branch is not eligible")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("candidate tracked tree is not clean")
    for path in _CANDIDATE_PATHS:
        completed = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", path],
            cwd=_REPO_ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode != 0:
            raise RuntimeError("candidate canary files are not committed")
    return sha, branch


def _path_is_allowed(output: Path) -> bool:
    """Allow only external paths or paths ignored by this candidate repository."""
    try:
        relative = output.relative_to(_REPO_ROOT)
    except ValueError:
        return True
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", str(relative)],
        cwd=_REPO_ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if tracked.returncode == 0:
        return False
    completed = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", str(relative)],
        cwd=_REPO_ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _load_repo_env() -> None:
    """Load repo dotenv values while retaining values explicitly inherited by the operator."""
    load_dotenv(_REPO_ROOT / ".env", override=False)


def _installed_versions(_env: dict[str, str]) -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "dspy": importlib.metadata.version("dspy"),
        "daytona": importlib.metadata.version("daytona"),
    }


def _lockfile_sha256() -> str:
    lockfile = _REPO_ROOT / "uv.lock"
    if not lockfile.is_file():
        raise RuntimeError("candidate lockfile is missing")
    return hashlib.sha256(lockfile.read_bytes()).hexdigest()


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _failure_receipt(
    *, category: str, phase: str, started_at: str, sha: str | None = None, branch: str | None = None
) -> dict[str, object]:
    if category not in _FAILURE_CATEGORIES or phase not in _FAILURE_PHASES:
        raise ValueError("failure receipt is outside the bounded contract")
    return {
        "schema": RECEIPT_SCHEMA,
        "candidate": {"sha": sha, "branch": branch, "tracked_tree_clean": sha is not None},
        "timing": {"started_at": started_at, "finished_at": _utc_now()},
        "failure": {"category": category, "phase": phase},
        "passed": False,
    }


def _write_failure(output: Path, **kwargs: object) -> None:
    _atomic_write(output, _failure_receipt(**kwargs))  # type: ignore[arg-type]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptError("receipt_json") from exc
    if not isinstance(value, dict):
        raise ReceiptError("receipt_json")
    return value


def validate_test_receipt(receipt: object) -> dict[str, Any]:
    """Validate the small, sanitized receipt emitted by the live pytest scenario."""
    if not isinstance(receipt, dict) or set(receipt) != _TEST_SUCCESS_FIELDS:
        raise ReceiptError("receipt_fields")
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("failure") is not None
        or receipt.get("passed") is not True
    ):
        raise ReceiptError("receipt_status")
    timing = receipt.get("timing")
    streaming = receipt.get("streaming")
    assertions = receipt.get("assertions")
    resources = receipt.get("resources")
    if not isinstance(timing, dict) or set(timing) != {"first_delta_ms", "duration_ms"}:
        raise ReceiptError("receipt_timing")
    if not all(isinstance(timing[name], int) and timing[name] >= 0 for name in timing):
        raise ReceiptError("receipt_timing")
    if not isinstance(streaming, dict) or set(streaming) != {"delta_count", "fields"}:
        raise ReceiptError("receipt_streaming")
    fields = streaming.get("fields")
    if (
        not isinstance(streaming.get("delta_count"), int)
        or streaming["delta_count"] < 1
        or not isinstance(fields, list)
        or not fields
        or any(field not in {"reasoning", "code"} for field in fields)
    ):
        raise ReceiptError("receipt_streaming")
    if not isinstance(assertions, dict) or set(assertions) != _REQUIRED_ASSERTIONS or not all(
        value is True for value in assertions.values()
    ):
        raise ReceiptError("receipt_assertions")
    if (
        not isinstance(resources, dict)
        or set(resources) != {"sandbox_count", "broker_session_count", "owned_volume_only"}
        or not isinstance(resources["sandbox_count"], int)
        or resources["sandbox_count"] < 1
        or not isinstance(resources["broker_session_count"], int)
        or resources["broker_session_count"] < 1
        or resources["owned_volume_only"] is not True
    ):
        raise ReceiptError("receipt_resources")
    return receipt


def _policy(settings: Any) -> dict[str, str]:
    profile = active_profile(settings)
    policy = {
        "profile": profile,
        "environment": settings.run_environment,
        "root_model": settings.root_model,
        "sub_model": settings.sub_model,
    }
    if policy != {
        "profile": "daytona",
        "environment": "daytona",
        "root_model": _LIVE_ROOT_MODEL,
        "sub_model": _LIVE_SUB_MODEL,
    }:
        raise ReceiptError("policy")
    return policy


def _success_receipt(
    test_receipt: dict[str, Any],
    *,
    sha: str,
    branch: str,
    policy: dict[str, str],
    child_env: dict[str, str],
) -> dict[str, object]:
    validated = validate_test_receipt(test_receipt)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "candidate": {
            "sha": sha,
            "branch": branch,
            "tracked_tree_clean": True,
            "versions": _installed_versions(child_env),
            "lockfile_sha256": _lockfile_sha256(),
        },
        "policy": policy,
        "timing": validated["timing"],
        "streaming": validated["streaming"],
        "assertions": validated["assertions"],
        "resources": validated["resources"],
        "failure": None,
        "passed": True,
    }
    if set(receipt) != _SUCCESS_FIELDS:
        raise ReceiptError("receipt_fields")
    return receipt


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output.expanduser().resolve()
    started_at = _utc_now()
    if args.timeout_seconds <= 0 or not _path_is_allowed(output):
        print("Phase 1 stream canary precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION
    _load_repo_env()
    try:
        settings = require_live_execution()
        policy = _policy(settings)
    except (FleetConfigurationError, ReceiptError):
        _write_failure(output, category="precondition_failed", phase="policy", started_at=started_at)
        print("Phase 1 stream canary policy precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION
    try:
        sha, branch = _candidate()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        _write_failure(output, category="precondition_failed", phase="candidate", started_at=started_at)
        print("Phase 1 stream canary candidate precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION

    descriptor, temporary_name = tempfile.mkstemp(prefix=".fleet-phase1-stream-", suffix=".json")
    os.close(descriptor)
    receipt_path = Path(temporary_name)
    child_env = os.environ.copy()
    child_env.pop("FLEET_ROOT_MODEL", None)
    child_env.pop("FLEET_SUB_MODEL", None)
    child_env[EVIDENCE_ENV] = str(receipt_path)
    try:
        try:
            completed = subprocess.run(
                pytest_command(args.timeout_seconds),
                cwd=_REPO_ROOT,
                env=child_env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=args.timeout_seconds + 30,
            )
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            _write_failure(
                output, category="interrupted", phase="scenario", started_at=started_at, sha=sha, branch=branch
            )
            print("Phase 1 stream canary was interrupted.", file=sys.stderr)
            return EXIT_INTERRUPTED
        if completed.returncode != 0:
            _write_failure(
                output,
                category="proof_failed",
                phase="scenario",
                started_at=started_at,
                sha=sha,
                branch=branch,
            )
            print("Phase 1 stream canary failed; inspect the bounded receipt.", file=sys.stderr)
            return EXIT_PROOF
        receipt = _success_receipt(_read_json(receipt_path), sha=sha, branch=branch, policy=policy, child_env=child_env)
        _atomic_write(output, receipt)
    except ReceiptError as exc:
        _write_failure(
            output, category="receipt_invalid", phase=exc.phase, started_at=started_at, sha=sha, branch=branch
        )
        print("Phase 1 stream canary receipt validation failed.", file=sys.stderr)
        return EXIT_RECEIPT
    except (OSError, RuntimeError, subprocess.SubprocessError):
        _write_failure(output, category="proof_failed", phase="receipt", started_at=started_at, sha=sha, branch=branch)
        print("Phase 1 stream canary failed; inspect the bounded receipt.", file=sys.stderr)
        return EXIT_PROOF
    finally:
        receipt_path.unlink(missing_ok=True)

    print(f"Phase 1 Daytona stream canary passed; bounded receipt: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
