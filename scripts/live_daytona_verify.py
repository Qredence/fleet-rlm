#!/usr/bin/env python3
"""Run the native semantic FastAPI and attachment durability live contracts.

This operator command checks one committed candidate with the configured
``daytona-native`` policy. It does not certify recursive execution, provider
containment, release readiness, or deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from fleet_rlm.config.loader import (
    ProfileEnvironmentContract,
    active_profile,
    load_profile_environment_contracts,
    require_live_execution,
)
from fleet_rlm.config.settings import FleetConfigurationError

RECEIPT_SCHEMA = "fleet.live-daytona-verification/v1"
EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
LIVE_AUTH_VALUES = frozenset({"1", "true", "yes"})
LIVE_PROFILE = "daytona-native"
ROOT_MODEL_ENV = "FLEET_LIVE_ROOT_MODEL"
SUB_MODEL_ENV = "FLEET_LIVE_SUB_MODEL"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_NATIVE_TEST = "tests/live/backend/test_fleet_rlm_daytona_mvp.py::test_native_semantic_calls_through_fastapi"
_DURABILITY_TEST = (
    "tests/live/backend/test_attachment_artifact_durability.py::"
    "test_staged_attachment_is_readable_and_artifact_survives_replacement"
)
DURABILITY_EVIDENCE_RELATIVE = Path(".fleet-evidence/receipts/p35d") / (
    "live-b5-attachment-artifact-durability-evidence.json"
)
_NATIVE_ASSERTIONS = frozenset(
    {
        "single_semantic_call_succeeded",
        "ordered_batch_results_verified",
        "one_budgeted_sub_lm_call_per_prompt",
        "typed_submit",
        "no_full_child_sandbox",
        "cleanup_passed",
    }
)
_DURABILITY_ASSERTIONS = frozenset(
    {"attachment_readable", "artifact_survived_replacement", "shared_volume_checksum_verified"}
)

EXIT_PRECONDITION = 2
EXIT_PROOF = 3
EXIT_RECEIPT = 4
EXIT_INTERRUPTED = 130


class ReceiptError(ValueError):
    """A lane did not produce evidence matching its active contract."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New ignored or out-of-repository JSON receipt path.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="Per-contract pytest timeout in seconds (default: 900).",
    )
    return parser


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
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


def _profile_contract() -> ProfileEnvironmentContract:
    contract = next(
        (item for item in load_profile_environment_contracts() if item.name == LIVE_PROFILE),
        None,
    )
    if contract is None:
        raise FleetConfigurationError(f"required live profile is missing: {LIVE_PROFILE}")
    return contract


def _valid_model_pair(models: object) -> bool:
    return bool(
        isinstance(models, dict)
        and set(models) == {"root", "sub"}
        and all(
            isinstance(value, str)
            and 0 < len(value) <= 256
            and not any(character.isspace() or ord(character) < 32 for character in value)
            for value in models.values()
        )
    )


def _candidate_models() -> dict[str, str]:
    models = {"root": os.environ.get(ROOT_MODEL_ENV, ""), "sub": os.environ.get(SUB_MODEL_ENV, "")}
    if not _valid_model_pair(models):
        raise ValueError("set bounded FLEET_LIVE_ROOT_MODEL and FLEET_LIVE_SUB_MODEL values")
    return models


def _path_is_allowed(path: Path) -> bool:
    try:
        root = Path(_git("rev-parse", "--show-toplevel")).resolve()
    except (OSError, subprocess.SubprocessError):
        return False
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return True
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--", str(path.resolve())],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return ignored.returncode == 0


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    """Atomically publish one receipt, refusing to replace an existing file."""
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
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _failure_receipt(
    *,
    started_at: str,
    category: str,
    phase: str,
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
        "timing": {"started_at": started_at, "finished_at": _utc_now()},
        "failure": {"category": category, "phase": phase},
        "passed": False,
    }


def _load_json(path: Path, *, phase: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(phase) from exc
    if not isinstance(value, dict):
        raise ReceiptError(phase)
    return value


def _validate_durability_receipt(
    path: Path,
    *,
    worktree: Path,
    sha: str,
    lockfile_sha256: str,
) -> dict[str, Any]:
    receipt = _load_json(path, phase="durability_receipt")
    candidate = receipt.get("candidate")
    assertions = receipt.get("assertions")
    cleanup = receipt.get("cleanup")
    if (
        receipt.get("schema") != "fleet.p35d-attachment-artifact/v1"
        or receipt.get("passed") is not True
        or not isinstance(candidate, dict)
        or candidate.get("sha") != sha
        or candidate.get("lockfile_sha256") != lockfile_sha256
        or candidate.get("tracked_tree_clean") is not True
        or not isinstance(assertions, dict)
        or any(assertions.get(name) is not True for name in _DURABILITY_ASSERTIONS)
        or not isinstance(cleanup, dict)
        or cleanup.get("confirmed_absent") is not True
        or cleanup.get("admission_restored") is not True
    ):
        raise ReceiptError("durability_receipt_contract")

    evidence_path = worktree / DURABILITY_EVIDENCE_RELATIVE
    evidence = _load_json(evidence_path, phase="durability_evidence")
    artifact_id = evidence.get("artifact_id")
    checksum = evidence.get("artifact_checksum")
    sandbox_ids = evidence.get("sandbox_ids")
    volume_id = evidence.get("volume_id")
    if (
        evidence.get("git_commit") != sha
        or evidence.get("uv_lock_fingerprint") != lockfile_sha256[:16]
        or evidence.get("staged_readable") is not True
        or evidence.get("artifact_survived_replace") is not True
        or not isinstance(artifact_id, str)
        or not artifact_id
        or not isinstance(checksum, str)
        or len(checksum) != 64
        or any(char not in "0123456789abcdef" for char in checksum)
        or not isinstance(sandbox_ids, list)
        or len(sandbox_ids) < 2
        or not all(isinstance(item, str) and item for item in sandbox_ids)
        or not isinstance(volume_id, str)
        or not volume_id
    ):
        raise ReceiptError("durability_evidence_contract")
    return {
        "attachment_readable": True,
        "artifact_survived_replacement": True,
        "artifact_id": artifact_id,
        "artifact_checksum": checksum,
        "sandbox_ids": sandbox_ids,
        "volume_id": volume_id,
    }


def _validate_native_receipt(
    path: Path,
    *,
    sha: str,
    lockfile_sha256: str,
    models: dict[str, str],
) -> dict[str, Any]:
    receipt = _load_json(path, phase="native_receipt")
    candidate = receipt.get("candidate")
    assertions = receipt.get("assertions")
    counts = receipt.get("counts")
    resources = receipt.get("resources")
    if (
        receipt.get("schema") != "fleet.daytona-mvp-proof/v2"
        or receipt.get("passed") is not True
        or receipt.get("failure") is not None
        or not isinstance(candidate, dict)
        or candidate.get("sha") != sha
        or candidate.get("lockfile_sha256") != lockfile_sha256
        or candidate.get("tracked_tree_clean") is not True
        or receipt.get("models") != models
        or not isinstance(assertions, dict)
        or set(assertions) != _NATIVE_ASSERTIONS
        or any(assertions.get(name) is not True for name in _NATIVE_ASSERTIONS)
        or not isinstance(counts, dict)
        or counts.get("single_lm_calls") != 1
        or counts.get("batched_lm_prompts") != 3
        or counts.get("recursive_calls") != 0
        or counts.get("sse_done") != 1
        or not isinstance(resources, dict)
        or not all(isinstance(resources.get(name), str) and resources[name] for name in ("session_id", "run_id"))
        or not isinstance(resources.get("sandbox_ids"), list)
        or not resources["sandbox_ids"]
        or not all(isinstance(item, str) and item for item in resources["sandbox_ids"])
    ):
        raise ReceiptError("native_receipt_contract")
    return {
        "passed": True,
        "assertions": {name: True for name in sorted(_NATIVE_ASSERTIONS)},
        "counts": counts,
        "resources": resources,
    }


def _create_detached_worktree(sha: str, repo_root: Path) -> Path:
    parent = Path(tempfile.mkdtemp(prefix=".fleet-live-daytona-", dir=repo_root.parent))
    worktree = parent / "checkout"
    try:
        result = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), sha],
            cwd=repo_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode:
            raise RuntimeError("could not create candidate worktree")
        return worktree
    except BaseException:
        parent.rmdir()
        raise


def _remove_detached_worktree(worktree: Path, repo_root: Path) -> None:
    parent = worktree.parent
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=repo_root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if parent.name.startswith(".fleet-live-daytona-") and parent.parent == repo_root.parent:
        parent.rmdir()


def _pytest_command(test: str, timeout_seconds: int) -> list[str]:
    return ["uv", "run", "pytest", "-q", "-n", "0", f"--timeout={timeout_seconds}", test]


def _run_contract(
    test: str,
    *,
    timeout_seconds: int,
    worktree: Path,
    environment: dict[str, str],
    receipt_path: Path,
) -> None:
    child_environment = {**environment, EVIDENCE_ENV: str(receipt_path)}
    result = subprocess.run(
        _pytest_command(test, timeout_seconds),
        cwd=worktree,
        env=child_environment,
        timeout=timeout_seconds + 60,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        raise RuntimeError(test.rsplit("::", 1)[-1])


def _load_repo_env() -> None:
    load_dotenv(_REPO_ROOT / ".env", override=False)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output.expanduser().resolve()
    started_at = _utc_now()

    if not 1 <= args.timeout_seconds <= 86_400 or not _path_is_allowed(output):
        print("Live verification output or timeout precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION
    if output.exists():
        print("Live verification output must be a new file.", file=sys.stderr)
        return EXIT_PRECONDITION

    if os.environ.get("FLEET_LIVE", "").strip().lower() not in LIVE_AUTH_VALUES:
        _write_once(
            output, _failure_receipt(started_at=started_at, category="precondition_failed", phase="live_authorization")
        )
        print("Set FLEET_LIVE=1 to authorize the credentialed live verification.", file=sys.stderr)
        return EXIT_PRECONDITION

    _load_repo_env()
    try:
        require_live_execution(profile=LIVE_PROFILE)
        if active_profile(require_live_execution()) != LIVE_PROFILE:
            raise FleetConfigurationError(f"the configured default profile must be {LIVE_PROFILE}")
        contract = _profile_contract()
        models = _candidate_models()
        missing = [name for name in contract.provider_environment_names if not os.environ.get(name)]
        if missing:
            raise FleetConfigurationError("live profile credentials are incomplete")
        sha, branch = _candidate()
        repo_root = Path(_git("rev-parse", "--show-toplevel")).resolve()
        lockfile_sha256 = hashlib.sha256((repo_root / "uv.lock").read_bytes()).hexdigest()
    except (FleetConfigurationError, OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        _write_once(
            output, _failure_receipt(started_at=started_at, category="precondition_failed", phase="policy_or_candidate")
        )
        print("Live verification policy or candidate precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION

    worktree: Path | None = None
    failure: tuple[str, str] | None = None
    native_evidence: dict[str, Any] | None = None
    durability_evidence: dict[str, Any] | None = None
    try:
        worktree = _create_detached_worktree(sha, repo_root)
        environment = os.environ.copy()
        environment[ROOT_MODEL_ENV] = models["root"]
        environment[SUB_MODEL_ENV] = models["sub"]
        environment.pop("FLEET_ROOT_MODEL", None)
        environment.pop("FLEET_SUB_MODEL", None)
        durability_receipt_path = worktree.parent / "durability-receipt.json"
        _run_contract(
            _DURABILITY_TEST,
            timeout_seconds=args.timeout_seconds,
            worktree=worktree,
            environment=environment,
            receipt_path=durability_receipt_path,
        )
        durability_evidence = _validate_durability_receipt(
            durability_receipt_path,
            worktree=worktree,
            sha=sha,
            lockfile_sha256=lockfile_sha256,
        )
        durability_receipt_path.unlink(missing_ok=True)

        native_receipt_path = worktree.parent / "native-receipt.json"
        _run_contract(
            _NATIVE_TEST,
            timeout_seconds=args.timeout_seconds,
            worktree=worktree,
            environment=environment,
            receipt_path=native_receipt_path,
        )
        native_evidence = _validate_native_receipt(
            native_receipt_path,
            sha=sha,
            lockfile_sha256=lockfile_sha256,
            models=models,
        )
        native_receipt_path.unlink(missing_ok=True)
    except KeyboardInterrupt:
        failure = ("interrupted", "contract")
    except subprocess.TimeoutExpired:
        failure = ("proof_failed", "timeout")
    except ReceiptError as exc:
        failure = ("receipt_invalid", str(exc))
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        failure = ("proof_failed", str(exc))
    finally:
        if worktree is not None:
            try:
                _remove_detached_worktree(worktree, repo_root)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                failure = ("cleanup_failed", "candidate_worktree")

    candidate = {
        "sha": sha,
        "branch": branch,
        "tracked_tree_clean": True,
        "lockfile_sha256": lockfile_sha256,
    }
    if failure is not None:
        category, phase = failure
        try:
            _write_once(
                output,
                _failure_receipt(
                    started_at=started_at,
                    category=category,
                    phase=phase,
                    sha=sha,
                    branch=branch,
                ),
            )
        except FileExistsError:
            print("Live verification receipt path was already claimed.", file=sys.stderr)
            return EXIT_RECEIPT
        if category == "interrupted":
            print("Live verification was interrupted.", file=sys.stderr)
            return EXIT_INTERRUPTED
        print("Live verification failed; inspect its bounded receipt.", file=sys.stderr)
        return EXIT_RECEIPT if category == "receipt_invalid" else EXIT_PROOF

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "candidate": candidate,
        "policy": {"profile": LIVE_PROFILE, "models": models},
        "timing": {"started_at": started_at, "finished_at": _utc_now()},
        "contracts": {
            "attachment_artifact_durability": {"passed": True, "evidence": durability_evidence},
            "native_semantic_fastapi": native_evidence,
        },
        "scope": [
            "staged attachment is readable during a Run",
            "artifact bytes remain readable with the shared volume after sandbox replacement",
            "native single and ordered batch semantic calls pass through FastAPI",
            "the native contract reports zero recursive child calls",
            "owned Daytona resources settle through the tested cleanup paths",
        ],
        "passed": True,
    }
    try:
        _write_once(output, receipt)
    except FileExistsError:
        print("Live verification receipt path was already claimed.", file=sys.stderr)
        return EXIT_RECEIPT
    print(f"Native Daytona contracts passed; receipt: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
