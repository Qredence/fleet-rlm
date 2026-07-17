"""Run the bounded credentialed Daytona MVP proof and validate its receipt."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "fleet.daytona-mvp-proof/v1"
EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
_LIVE_TEST = "tests/live/backend/test_fleet_rlm_daytona_mvp.py::test_complete_daytona_mvp_through_fastapi"
_REQUIRED_ENV = ("FLEET_DAYTONA_API_KEY", "FLEET_LLM_API_KEY")
_SUCCESS_FIELDS = frozenset(
    {
        "schema",
        "candidate",
        "timing",
        "models",
        "resources",
        "counts",
        "checksums",
        "assertions",
        "failure",
        "passed",
    }
)
_FAILURE_CATEGORIES = frozenset(
    {
        "precondition_failed",
        "proof_failed",
        "cleanup_failed",
        "receipt_invalid",
        "interrupted",
    }
)

EXIT_PRECONDITION = 2
EXIT_PROOF = 3
EXIT_RECEIPT = 4
EXIT_INTERRUPTED = 130


class ReceiptError(ValueError):
    """Raised when the live proof receipt is missing or outside its contract."""

    def __init__(self, phase: str) -> None:
        super().__init__(phase)
        self.phase = phase


def _model_argument(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise argparse.ArgumentTypeError("model must not be empty")
    return cleaned


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Ignored or out-of-repository path for the bounded JSON receipt.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="Pytest timeout for the live proof (default: 900).",
    )
    parser.add_argument(
        "--root-model",
        type=_model_argument,
        help="Optional non-secret FLEET_ROOT_MODEL override for the child proof process.",
    )
    parser.add_argument(
        "--sub-model",
        type=_model_argument,
        help="Optional non-secret FLEET_SUB_MODEL override for the child proof process.",
    )
    return parser


def pytest_command(timeout_seconds: int) -> list[str]:
    return [
        "uv",
        "run",
        "pytest",
        _LIVE_TEST,
        "-q",
        "-n",
        "0",
        f"--timeout={timeout_seconds}",
    ]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _candidate() -> tuple[str, str]:
    sha = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    if not branch or branch in {"main", "master"}:
        raise RuntimeError("candidate branch is not eligible")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("candidate tracked tree is not clean")
    return sha, branch


def _path_is_allowed(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    try:
        root = Path(_git("rev-parse", "--show-toplevel")).resolve()
    except (OSError, subprocess.SubprocessError):
        return False
    try:
        resolved.relative_to(root)
    except ValueError:
        return True
    checked = subprocess.run(
        ["git", "check-ignore", "-q", "--", str(resolved)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return checked.returncode == 0


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _failure_receipt(
    *,
    category: str,
    phase: str,
    started_at: str,
    sha: str | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "candidate": None
        if sha is None or branch is None
        else {
            "sha": sha,
            "branch": branch,
            "tracked_tree_clean": True,
        },
        "timing": {
            "started_at": started_at,
            "finished_at": _utc_now(),
        },
        "failure": {"category": category, "phase": phase},
        "passed": False,
    }


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptError("receipt_json") from exc
    if not isinstance(payload, dict):
        raise ReceiptError("receipt_json")
    return payload


def _validate_success_receipt(payload: dict[str, Any], *, sha: str) -> None:
    if set(payload) != _SUCCESS_FIELDS:
        raise ReceiptError("receipt_fields")
    if payload.get("schema") != RECEIPT_SCHEMA:
        raise ReceiptError("receipt_schema")
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict) or set(candidate) != {
        "sha",
        "branch",
        "tracked_tree_clean",
        "versions",
        "lockfile_sha256",
    }:
        raise ReceiptError("candidate_fields")
    if candidate.get("sha") != sha:
        raise ReceiptError("candidate_fingerprint")
    if candidate.get("tracked_tree_clean") is not True:
        raise ReceiptError("candidate_fingerprint")
    versions = candidate.get("versions")
    if not isinstance(versions, dict) or set(versions) != {"python", "dspy", "daytona"}:
        raise ReceiptError("candidate_versions")
    if not all(isinstance(value, str) and value for value in versions.values()):
        raise ReceiptError("candidate_versions")
    lockfile_checksum = candidate.get("lockfile_sha256")
    if not isinstance(lockfile_checksum, str) or len(lockfile_checksum) != 64:
        raise ReceiptError("candidate_fingerprint")
    assertions = payload.get("assertions")
    required_assertions = {
        "typed_submit",
        "stateful_iterations",
        "fresh_replacement_context",
        "workspace_survived_replacement",
        "history_reload_identical",
        "secret_audit_passed",
        "cleanup_passed",
    }
    if not isinstance(assertions, dict) or set(assertions) != required_assertions:
        raise ReceiptError("receipt_assertions")
    if any(assertions[name] is not True for name in required_assertions):
        raise ReceiptError("receipt_assertions")
    if payload.get("passed") is not True or payload.get("failure") is not None:
        raise ReceiptError("receipt_result")
    required_fields = {
        "timing": {"started_at", "finished_at", "duration_ms"},
        "models": {"root", "sub"},
        "resources": {"session_id", "run_ids", "sandbox_ids", "volume_id"},
        "counts": {
            "iterations",
            "single_lm_calls",
            "batched_lm_calls",
            "host_tool_calls",
            "sse_start",
            "sse_finish",
            "sse_done",
        },
        "checksums": {"snapshot_sha256", "workspace_sha256", "typed_result_sha256"},
    }
    for name, fields in required_fields.items():
        section = payload.get(name)
        if not isinstance(section, dict) or set(section) != fields:
            raise ReceiptError(f"receipt_{name}")
    checksums = payload["checksums"]
    if any(not isinstance(value, str) or len(value) != 64 for value in checksums.values()):
        raise ReceiptError("receipt_checksums")
    counts = payload["counts"]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts.values()):
        raise ReceiptError("receipt_counts")


def _bounded_failure_is_valid(payload: dict[str, Any], *, sha: str) -> bool:
    if set(payload) != {"schema", "candidate", "timing", "failure", "passed"}:
        return False
    candidate = payload.get("candidate")
    timing = payload.get("timing")
    failure = payload.get("failure")
    return bool(
        payload.get("schema") == RECEIPT_SCHEMA
        and isinstance(candidate, dict)
        and set(candidate) == {"sha", "branch", "tracked_tree_clean"}
        and candidate.get("sha") == sha
        and candidate.get("tracked_tree_clean") is True
        and isinstance(timing, dict)
        and set(timing) == {"started_at", "finished_at"}
        and isinstance(failure, dict)
        and set(failure) == {"category", "phase"}
        and failure.get("category") in _FAILURE_CATEGORIES
        and isinstance(failure.get("phase"), str)
        and payload.get("passed") is False
    )


def _write_failure(
    output: Path,
    *,
    category: str,
    phase: str,
    started_at: str,
    sha: str | None = None,
    branch: str | None = None,
) -> None:
    _atomic_write(
        output,
        _failure_receipt(
            category=category,
            phase=phase,
            started_at=started_at,
            sha=sha,
            branch=branch,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if (args.root_model is None) != (args.sub_model is None):
        print("--root-model and --sub-model must be provided together.", file=sys.stderr)
        return EXIT_PRECONDITION
    output = args.output.expanduser().resolve()
    started_at = _utc_now()
    if args.timeout_seconds <= 0 or not _path_is_allowed(output):
        print("Live proof precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION
    live_enabled = os.environ.get("FLEET_LIVE", "").strip().lower() in {"1", "true", "yes"}
    if not live_enabled or any(not os.environ.get(name) for name in _REQUIRED_ENV):
        _write_failure(
            output,
            category="precondition_failed",
            phase="environment",
            started_at=started_at,
        )
        print("Live proof precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION
    try:
        sha, branch = _candidate()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        _write_failure(
            output,
            category="precondition_failed",
            phase="candidate",
            started_at=started_at,
        )
        print("Live proof candidate precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION

    output.unlink(missing_ok=True)
    child_env = os.environ.copy()
    child_env[EVIDENCE_ENV] = str(output)
    if args.root_model is not None:
        child_env["FLEET_ROOT_MODEL"] = args.root_model
        child_env["FLEET_SUB_MODEL"] = args.sub_model
    try:
        completed = subprocess.run(
            pytest_command(args.timeout_seconds),
            env=child_env,
            timeout=args.timeout_seconds + 60,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (KeyboardInterrupt, subprocess.TimeoutExpired):
        _write_failure(
            output,
            category="interrupted",
            phase="pytest",
            started_at=started_at,
            sha=sha,
            branch=branch,
        )
        print("Live proof was interrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED

    if completed.returncode != 0:
        try:
            retained = _load_receipt(output)
        except ReceiptError:
            retained = {}
        if not _bounded_failure_is_valid(retained, sha=sha):
            _write_failure(
                output,
                category="proof_failed",
                phase="pytest",
                started_at=started_at,
                sha=sha,
                branch=branch,
            )
        print("Live proof failed; inspect the bounded receipt.", file=sys.stderr)
        return EXIT_PROOF

    try:
        receipt = _load_receipt(output)
        _validate_success_receipt(receipt, sha=sha)
    except ReceiptError as exc:
        _write_failure(
            output,
            category="receipt_invalid",
            phase=exc.phase,
            started_at=started_at,
            sha=sha,
            branch=branch,
        )
        print("Live proof receipt validation failed.", file=sys.stderr)
        return EXIT_RECEIPT

    print(f"Live Daytona MVP proof passed; bounded receipt: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
