"""Run the narrow credentialed Phase 2 Daytona recursive-child canary."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from fleet_rlm.config import FleetConfigurationError, active_profile, require_live_execution

RECEIPT_SCHEMA = "fleet.phase2-daytona-recursive/v1"
EVIDENCE_ENV = "FLEET_PHASE2_RECURSIVE_EVIDENCE_PATH"
_LIVE_TEST = "tests/live/backend/test_phase2_daytona_recursive.py::test_phase2_daytona_recursive_through_fastapi"
_CANDIDATE_PATHS = (
    "scripts/live_phase2_recursive_verify.py",
    "tests/live/backend/test_phase2_daytona_recursive.py",
    "tests/unit/scripts/test_live_phase2_recursive_verify.py",
)
_REPO_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_ROOT = _REPO_ROOT / ".scratch" / "fleet-rlm-recursive-runtime" / "evidence"
_LIVE_ROOT_MODEL = "deepseek-v4-flash"
_LIVE_SUB_MODEL = "deepseek-v4-flash"
_FAILURE_CATEGORIES = frozenset(
    {
        "precondition_failed",
        "proof_failed",
        "cleanup_failed",
        "receipt_invalid",
        "interrupted",
    }
)
_FAILURE_PHASES = frozenset({"policy", "candidate", "scenario", "receipt", "receipt_json", "receipt_fields"})
_REQUIRED_ASSERTIONS = frozenset(
    {
        "dedicated_child_sandbox",
        "same_volume_sibling_scope",
        "root_marker_absent_in_child",
        "root_continuity",
        "child_typed_submit",
        "root_typed_submit",
        "strict_child_cleanup",
        "terminal_ordering",
        "no_grandchild_sandbox",
    }
)
_TEST_FIELDS = frozenset({"schema", "timing", "assertions", "failure", "passed"})
_SUCCESS_FIELDS = frozenset({"schema", "candidate", "policy", "timing", "assertions", "failure", "passed"})
EXIT_PRECONDITION = 2
EXIT_PROOF_FAILURE = 3
EXIT_INTERRUPTED = 130


class ReceiptError(ValueError):
    """The live scenario receipt is malformed or contains disallowed data."""


def build_parser() -> argparse.ArgumentParser:
    """
    Create the command-line parser for the receipt output path and execution timeout.

    Returns:
        argparse.ArgumentParser: Configured parser for the command-line arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="new JSON receipt below the ignored evidence directory",
    )
    parser.add_argument("--timeout-seconds", type=int, default=900)
    return parser


def pytest_command(timeout_seconds: int) -> list[str]:
    """Build the command used to run the designated live pytest scenario.

    Parameters:
        timeout_seconds (int): Maximum duration allowed for the test run.

    Returns:
        list[str]: The pytest command and its execution arguments.
    """
    return ["uv", "run", "pytest", _LIVE_TEST, "-q", "-n", "0", f"--timeout={timeout_seconds}"]


def _load_repo_env() -> None:
    load_dotenv(_REPO_ROOT / ".env", override=False)


def _path_is_allowed(path: Path) -> bool:
    """Determine whether a path is an eligible JSON receipt location within the evidence directory.

    Parameters:
        path (Path): Path to validate.

    Returns:
        `true` if the path is a JSON file below the evidence directory, `false` otherwise.
    """
    try:
        path.relative_to(_EVIDENCE_ROOT)
    except ValueError:
        return False
    return path.suffix == ".json" and path != _EVIDENCE_ROOT


def _git(*args: str) -> str:
    """Run a Git command from the repository root and return its trimmed standard output.

    Parameters:
        args (str): Arguments passed to Git.

    Returns:
        str: The command's standard output without leading or trailing whitespace.
    """
    return subprocess.check_output(["git", *args], cwd=_REPO_ROOT, text=True).strip()


def _candidate() -> tuple[str, str]:
    """
    Validate the repository candidate and return its commit SHA and branch name.

    Raises:
        RuntimeError: If the candidate is not on a non-main, non-master branch,
            the commit SHA is invalid, the worktree contains changes, or a
            required candidate file is not tracked.

    Returns:
        tuple[str, str]: The commit SHA and branch name.
    """
    sha = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    if len(sha) != 40 or not branch or branch in {"main", "master"}:
        raise RuntimeError("candidate must be a checked-out non-main commit")
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("candidate worktree is not clean")
    for path in _CANDIDATE_PATHS:
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", path],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    return sha, branch


def _installed_versions(env: dict[str, str]) -> dict[str, str]:
    """Return the installed versions of Python, DSPy, and Daytona.

    Parameters:
        env (dict[str, str]): Retained for compatibility and ignored.

    Returns:
        dict[str, str]: Version strings keyed by package name.
    """
    del env
    return {
        "python": sys.version.split()[0],
        "dspy": importlib.metadata.version("dspy"),
        "daytona": importlib.metadata.version("daytona"),
    }


def _lockfile_sha256() -> str:
    """Compute the SHA-256 digest of the repository's uv.lock file.

    Returns:
        str: The hexadecimal SHA-256 digest of uv.lock.
    """
    return hashlib.sha256((_REPO_ROOT / "uv.lock").read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """
    Write a JSON payload atomically to the specified path.

    Parameters:
        path (Path): Destination path for the JSON file.
        payload (dict[str, object]): JSON-serializable data to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
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


def _failure(*, category: str, phase: str) -> dict[str, object]:
    """
    Create a bounded failure receipt for the specified category and phase.

    Parameters:
        category (str): Failure category.
        phase (str): Execution phase associated with the failure.

    Returns:
        dict[str, object]: A failure receipt containing the schema, failure details, and a failed status.

    Raises:
        ValueError: If the category or phase is not allowed.
    """
    if category not in _FAILURE_CATEGORIES or phase not in _FAILURE_PHASES:
        raise ValueError("invalid bounded failure")
    return {
        "schema": RECEIPT_SCHEMA,
        "failure": {"category": category, "phase": phase},
        "passed": False,
    }


def _write_failure(path: Path, *, category: str, phase: str) -> None:
    """Write a failure receipt for the specified category and phase."""
    _write_json(path, _failure(category=category, phase=phase))


def validate_test_receipt(receipt: object) -> dict[str, Any]:
    """
    Validate a live test receipt against the required schema, assertions, timing, and content rules.

    Parameters:
        receipt (object): Receipt data to validate.

    Returns:
        dict[str, Any]: The validated receipt.

    Raises:
        ReceiptError: If the receipt is malformed, unsuccessful, contains invalid
            timing or assertions, or includes forbidden content.
    """
    if not isinstance(receipt, dict) or set(receipt) != _TEST_FIELDS:
        raise ReceiptError("receipt_fields")
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("failure") is not None
        or receipt.get("passed") is not True
    ):
        raise ReceiptError("receipt_fields")
    timing = receipt.get("timing")
    assertions = receipt.get("assertions")
    if (
        not isinstance(timing, dict)
        or set(timing) != {"turn_duration_ms", "child_duration_ms"}
        or not all(isinstance(value, int) and value >= 0 for value in timing.values())
    ):
        raise ReceiptError("receipt_fields")
    if (
        not isinstance(assertions, dict)
        or set(assertions) != _REQUIRED_ASSERTIONS
        or not all(value is True for value in assertions.values())
    ):
        raise ReceiptError("receipt_fields")
    serialized = json.dumps(receipt, sort_keys=True)
    forbidden = ("prompt", "answer", "code", "credential", "trace", "sandbox_id", "volume_id", "broker", "http")
    if any(token in serialized.lower() for token in forbidden):
        raise ReceiptError("receipt_fields")
    return receipt


def _policy(settings: Any) -> dict[str, str]:
    """
    Validate and return the execution policy required for the live recursive scenario.

    Parameters:
        settings (Any): Execution settings containing the active profile, environment,
                model identifiers, and recursion flag.

    Returns:
        dict[str, str]: The validated profile, environment, root model, and sub-model.

    Raises:
        ReceiptError: If the settings do not match the required policy or recursion is disabled.
    """
    policy = {
        "profile": active_profile(settings) or "",
        "environment": str(settings.run_environment),
        "root_model": str(settings.root_model),
        "sub_model": str(settings.sub_model),
    }
    expected = {
        "profile": "daytona-recursive",
        "environment": "daytona",
        "root_model": _LIVE_ROOT_MODEL,
        "sub_model": _LIVE_SUB_MODEL,
    }
    if policy != expected or not bool(getattr(settings, "rlm_recursion_enabled", False)):
        raise ReceiptError("policy")
    return policy


def _success_receipt(
    test_receipt: object,
    *,
    sha: str,
    branch: str,
    policy: dict[str, str],
    child_env: dict[str, str],
) -> dict[str, object]:
    """
    Build a successful verification receipt from validated test evidence.

    Parameters:
        test_receipt (object): Test evidence to validate and include in the receipt.
        sha (str): Candidate commit SHA.
        branch (str): Candidate branch name.
        policy (dict[str, str]): Validated execution policy.
        child_env (dict[str, str]): Environment used to determine installed package versions.

    Returns:
        dict[str, object]: A validated success receipt containing candidate metadata,
            policy, timing, assertions, and no failure.

    Raises:
        ReceiptError: If the resulting receipt does not contain the required fields.
    """
    evidence = validate_test_receipt(test_receipt)
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
        "timing": evidence["timing"],
        "assertions": evidence["assertions"],
        "failure": None,
        "passed": True,
    }
    if set(receipt) != _SUCCESS_FIELDS:
        raise ReceiptError("receipt_fields")
    return receipt


def main(argv: list[str] | None = None) -> int:
    """
    Run the Phase 2 recursive-child canary and write a validated JSON receipt.

    Parameters:
        argv (list[str] | None): Optional command-line arguments. If omitted, arguments
            are read from the process command line.

    Returns:
        int: The canary exit code, indicating success, a precondition failure, proof failure, or interruption.
    """
    args = build_parser().parse_args(argv)
    output = args.output.expanduser().resolve()
    if args.timeout_seconds <= 0 or not _path_is_allowed(output):
        print("Phase 2 recursive canary precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION
    _load_repo_env()
    try:
        settings = require_live_execution()
        policy = _policy(settings)
    except (FleetConfigurationError, ReceiptError):
        _write_failure(output, category="precondition_failed", phase="policy")
        print("Phase 2 recursive canary policy precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION
    try:
        sha, branch = _candidate()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        _write_failure(output, category="precondition_failed", phase="candidate")
        print("Phase 2 recursive canary candidate precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".test.json",
        text=True,
    )
    os.close(descriptor)
    test_receipt_path = Path(temporary_name)
    child_env = dict(os.environ)
    child_env[EVIDENCE_ENV] = str(test_receipt_path)
    try:
        try:
            completed = subprocess.run(
                pytest_command(args.timeout_seconds),
                cwd=_REPO_ROOT,
                env=child_env,
                check=False,
                timeout=args.timeout_seconds + 30,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            _write_failure(output, category="interrupted", phase="scenario")
            print("Phase 2 recursive canary was interrupted.", file=sys.stderr)
            return EXIT_INTERRUPTED
        if completed.returncode != 0:
            _write_failure(output, category="proof_failed", phase="scenario")
            return EXIT_PROOF_FAILURE
        try:
            evidence = json.loads(test_receipt_path.read_text(encoding="utf-8"))
            receipt = _success_receipt(evidence, sha=sha, branch=branch, policy=policy, child_env=child_env)
        except (OSError, json.JSONDecodeError):
            _write_failure(output, category="receipt_invalid", phase="receipt_json")
            return EXIT_PROOF_FAILURE
        except ReceiptError:
            _write_failure(output, category="receipt_invalid", phase="receipt_fields")
            return EXIT_PROOF_FAILURE
        _write_json(output, receipt)
        return 0
    finally:
        test_receipt_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
