"""Run the bounded credentialed Daytona MVP proof and validate its receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from fleet_rlm.config import (
    FleetConfigurationError,
    ProfileEnvironmentContract,
    Settings,
    active_profile_contract,
    load_runtime_settings,
    require_live_execution,
)

RECEIPT_SCHEMA = "fleet.daytona-mvp-proof/v1"
EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
_LIVE_TEST = "tests/live/backend/test_fleet_rlm_daytona_mvp.py::test_complete_daytona_mvp_through_fastapi"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LIVE_ROOT_MODEL = os.environ.get("FLEET_LIVE_ROOT_MODEL", "deepseek-v4-flash")
_LIVE_SUB_MODEL = os.environ.get("FLEET_LIVE_SUB_MODEL", "deepseek-v4-flash")
_APPROVED_ROOT_MODELS = frozenset(
    name
    for base in {_LIVE_ROOT_MODEL, _LIVE_ROOT_MODEL.removesuffix("-0731"), _LIVE_ROOT_MODEL + "-0731"}
    for name in (base, f"openai/{base}")
)
_APPROVED_SUB_MODELS = frozenset(
    name
    for base in {_LIVE_SUB_MODEL, _LIVE_SUB_MODEL.removesuffix("-0731"), _LIVE_SUB_MODEL + "-0731"}
    for name in (base, f"openai/{base}")
)
_DURABILITY_TEST = "tests/live/backend/test_attachment_artifact_durability.py"
_SUCCESS_FIELDS = frozenset(
    {
        "schema",
        "candidate",
        "timing",
        "models",
        "resources",
        "counts",
        "streaming",
        "checksums",
        "assertions",
        "lanes",
        "external_promotion",
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


def _model_family(name: object) -> str:
    """Dated and undated spellings of one production model family."""
    if not isinstance(name, str):
        return ""
    base = name.removeprefix("openai/")
    return base.removesuffix("-0731")


EXIT_PRECONDITION = 2
EXIT_PROOF = 3
EXIT_RECEIPT = 4
EXIT_INTERRUPTED = 130


class ReceiptError(ValueError):
    """Raised when the live proof receipt is missing or outside its contract."""

    def __init__(self, phase: str) -> None:
        super().__init__(phase)
        self.phase = phase


@dataclass(frozen=True, slots=True)
class LaneResult:
    """Bounded outcome for one verifier lane."""

    name: str
    order: int
    passed: bool

    def as_dict(self) -> dict[str, object]:
        return {"order": self.order, "passed": self.passed}


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


def lane_command(lane: str, timeout_seconds: int) -> list[str]:
    """Return the one-shot pytest command for a named proof lane."""
    if lane == "attachment_artifact_durability":
        return [
            "uv",
            "run",
            "pytest",
            _DURABILITY_TEST,
            "-q",
            "-n",
            "0",
            f"--timeout={timeout_seconds}",
        ]
    if lane == "fastapi_dspy_daytona_mvp":
        return pytest_command(timeout_seconds)
    raise ValueError(f"unknown proof lane: {lane}")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        cwd=cwd,
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


def _installed_versions(worktree: Path, child_env: dict[str, str]) -> dict[str, str]:
    """Read proof dependency versions from the detached candidate environment."""
    try:
        completed = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-c",
                (
                    "import importlib.metadata as metadata, json, sys; "
                    "print(json.dumps({'python': sys.version.split()[0], "
                    "'dspy': metadata.version('dspy'), 'daytona': metadata.version('daytona')}))"
                ),
            ],
            cwd=worktree,
            env=child_env,
            check=True,
            capture_output=True,
            text=True,
        )
        versions = json.loads(completed.stdout)
    except (json.JSONDecodeError, subprocess.SubprocessError) as exc:
        raise RuntimeError("required proof package is not installed") from exc
    if (
        not isinstance(versions, dict)
        or set(versions) != {"python", "dspy", "daytona"}
        or not all(isinstance(value, str) and value for value in versions.values())
    ):
        raise RuntimeError("required proof package is not installed")
    return versions


def _lockfile_sha256(worktree: Path) -> str:
    lockfile = worktree / "uv.lock"
    if not lockfile.is_file():
        raise RuntimeError("candidate lockfile is missing")
    return hashlib.sha256(lockfile.read_bytes()).hexdigest()


def _create_detached_worktree(sha: str, repo_root: Path) -> Path:
    parent = Path(tempfile.mkdtemp(prefix=".fleet-live-proof-", dir=repo_root.parent))
    worktree = parent / "checkout"
    try:
        completed = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), sha],
            cwd=repo_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode != 0:
            raise RuntimeError("could not create detached proof worktree")
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
    if parent.name.startswith(".fleet-live-proof-") and parent.parent == repo_root.parent:
        parent.rmdir()


def _run_lane(
    *,
    lane: str,
    timeout_seconds: int,
    worktree: Path,
    child_env: dict[str, str],
) -> LaneResult:
    completed = subprocess.run(
        lane_command(lane, timeout_seconds),
        cwd=worktree,
        env=child_env,
        timeout=timeout_seconds + 60,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    order = 1 if lane == "attachment_artifact_durability" else 2
    return LaneResult(name=lane, order=order, passed=completed.returncode == 0)


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
    if not all(isinstance(value, str) and 0 < len(value) <= 64 for value in versions.values()):
        raise ReceiptError("candidate_versions")
    lockfile_checksum = candidate.get("lockfile_sha256")
    if (
        not isinstance(lockfile_checksum, str)
        or len(lockfile_checksum) != 64
        or any(character not in "0123456789abcdef" for character in lockfile_checksum)
    ):
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
        "streaming": {"first_delta_ms", "delta_count", "fields"},
        "checksums": {"snapshot_sha256", "workspace_sha256", "typed_result_sha256"},
    }
    for name, fields in required_fields.items():
        section = payload.get(name)
        if not isinstance(section, dict) or set(section) != fields:
            raise ReceiptError(f"receipt_{name}")
    timing = payload["timing"]
    if (
        any(
            not isinstance(timing[name], str) or not 0 < len(timing[name]) <= 64
            for name in ("started_at", "finished_at")
        )
        or not isinstance(timing["duration_ms"], int)
        or isinstance(timing["duration_ms"], bool)
        or not 0 <= timing["duration_ms"] <= 86_400_000
    ):
        raise ReceiptError("receipt_timing")
    resources = payload["resources"]
    if (
        not isinstance(resources["session_id"], str)
        or not 0 < len(resources["session_id"]) <= 128
        or not isinstance(resources["volume_id"], str)
        or not 0 < len(resources["volume_id"]) <= 128
        or not isinstance(resources["run_ids"], list)
        or not 1 <= len(resources["run_ids"]) <= 4
        or any(not isinstance(value, str) or not 0 < len(value) <= 128 for value in resources["run_ids"])
        or not isinstance(resources["sandbox_ids"], list)
        or not 1 <= len(resources["sandbox_ids"]) <= 4
        or any(not isinstance(value, str) or not 0 < len(value) <= 128 for value in resources["sandbox_ids"])
    ):
        raise ReceiptError("receipt_resources")
    checksums = payload["checksums"]
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in checksums.values()
    ):
        raise ReceiptError("receipt_checksums")
    counts = payload["counts"]
    if any(
        not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1_000_000
        for value in counts.values()
    ):
        raise ReceiptError("receipt_counts")
    streaming = payload["streaming"]
    if (
        not isinstance(streaming, dict)
        or set(streaming) != {"first_delta_ms", "delta_count", "fields"}
        or not isinstance(streaming["first_delta_ms"], int)
        or isinstance(streaming["first_delta_ms"], bool)
        or not 0 <= streaming["first_delta_ms"] <= 86_400_000
        or not isinstance(streaming["delta_count"], int)
        or isinstance(streaming["delta_count"], bool)
        or not 1 <= streaming["delta_count"] <= 1_000_000
        or not isinstance(streaming["fields"], list)
        or not streaming["fields"]
        or any(value not in {"reasoning", "code"} for value in streaming["fields"])
    ):
        raise ReceiptError("receipt_streaming")


def _validate_lane_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "gate",
        "staged_readable",
        "artifact_id",
        "artifact_checksum",
        "artifact_survived_replace",
        "sandbox_ids",
        "volume_id",
    }
    allowed = required | {"git_commit", "uv_lock_fingerprint", "workspace_id", "volume_subpath", "staged_path_prefix"}
    if not required <= set(payload) or not set(payload) <= allowed or payload.get("gate") != "B5":
        raise ReceiptError("durability_receipt")
    if payload.get("staged_readable") is not True or payload.get("artifact_survived_replace") is not True:
        raise ReceiptError("durability_assertions")
    artifact_id = payload.get("artifact_id")
    artifact_checksum = payload.get("artifact_checksum")
    volume_id = payload.get("volume_id")
    sandbox_ids = payload.get("sandbox_ids")
    if (
        not isinstance(artifact_id, str)
        or not artifact_id
        or len(artifact_id) > 128
        or not isinstance(volume_id, str)
        or not volume_id
        or len(volume_id) > 128
        or not isinstance(artifact_checksum, str)
        or len(artifact_checksum) != 64
        or any(character not in "0123456789abcdef" for character in artifact_checksum)
        or not isinstance(sandbox_ids, list)
        or not 1 <= len(sandbox_ids) <= 4
        or any(not isinstance(value, str) or not value or len(value) > 128 for value in sandbox_ids)
    ):
        raise ReceiptError("durability_evidence")
    return {
        "attachment_readable": True,
        "artifact_survived_replacement": True,
        "artifact_id": artifact_id,
        "artifact_checksum": artifact_checksum,
        "sandbox_ids": list(sandbox_ids),
        "volume_id": volume_id,
    }


def _load_durability_evidence(worktree: Path) -> dict[str, Any]:
    path = worktree / ".scratch/clean-backend-refoundation/assets/live-b5-attachment-artifact-durability-evidence.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptError("durability_receipt") from exc
    if not isinstance(payload, dict):
        raise ReceiptError("durability_receipt")
    return _validate_lane_evidence(payload)


def _build_success_receipt(
    payload: dict[str, Any],
    *,
    sha: str,
    branch: str,
    lockfile_sha256: str,
    versions: dict[str, str],
    models: dict[str, str],
    durability_evidence: dict[str, Any],
) -> dict[str, Any]:
    receipt = dict(payload)
    candidate = receipt.get("candidate")
    if not isinstance(candidate, dict):
        raise ReceiptError("candidate_fields")
    receipt["candidate"] = {
        "sha": sha,
        "branch": branch,
        "tracked_tree_clean": True,
        "versions": versions,
        "lockfile_sha256": lockfile_sha256,
    }
    if _model_family(receipt.get("models", {}).get("root")) != _model_family(models.get("root")) or _model_family(
        receipt.get("models", {}).get("sub")
    ) != _model_family(models.get("sub")):
        raise ReceiptError("receipt_models")
    receipt["lanes"] = {
        "attachment_artifact_durability": {
            **LaneResult("attachment_artifact_durability", 1, True).as_dict(),
            "evidence": durability_evidence,
        },
        "fastapi_dspy_daytona_mvp": {
            **LaneResult("fastapi_dspy_daytona_mvp", 2, True).as_dict(),
        },
    }
    receipt["external_promotion"] = {
        "candidate_sha": sha,
        "ci": "pending",
        "human_approval": "pending",
    }
    _validate_success_receipt_extended(receipt, sha=sha, branch=branch, lockfile_sha256=lockfile_sha256)
    return receipt


def _validate_success_receipt_extended(
    payload: dict[str, Any],
    *,
    sha: str,
    branch: str,
    lockfile_sha256: str,
) -> None:
    _validate_success_receipt(payload, sha=sha)
    candidate = payload["candidate"]
    if candidate.get("branch") != branch or candidate.get("lockfile_sha256") != lockfile_sha256:
        raise ReceiptError("candidate_fingerprint")
    models = payload.get("models")
    if not _models_are_approved(models):
        raise ReceiptError("receipt_models")
    lanes = payload.get("lanes")
    if not isinstance(lanes, dict) or set(lanes) != {
        "attachment_artifact_durability",
        "fastapi_dspy_daytona_mvp",
    }:
        raise ReceiptError("receipt_lanes")
    durability = lanes["attachment_artifact_durability"]
    mvp = lanes["fastapi_dspy_daytona_mvp"]
    if (
        not isinstance(durability, dict)
        or set(durability) != {"order", "passed", "evidence"}
        or durability.get("order") != 1
        or durability.get("passed") is not True
        or not isinstance(durability.get("evidence"), dict)
        or not isinstance(mvp, dict)
        or set(mvp) != {"order", "passed"}
        or mvp.get("order") != 2
        or mvp.get("passed") is not True
    ):
        raise ReceiptError("receipt_lanes")
    evidence = durability["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {
        "attachment_readable",
        "artifact_survived_replacement",
        "artifact_id",
        "artifact_checksum",
        "sandbox_ids",
        "volume_id",
    }:
        raise ReceiptError("durability_evidence")
    external = payload.get("external_promotion")
    if external != {"candidate_sha": sha, "ci": "pending", "human_approval": "pending"}:
        raise ReceiptError("external_promotion")


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


def _load_repo_env() -> None:
    """Load repo ``.env`` into the process without overriding exported values."""
    load_dotenv(_REPO_ROOT / ".env", override=False)


def _configured_models(settings: Settings | None = None) -> dict[str, str]:
    """Return the approved models resolved from the selected TOML profile."""
    resolved = settings or load_runtime_settings()
    return {"root": resolved.root_model, "sub": resolved.sub_model}


def _required_provider_environment(contract: ProfileEnvironmentContract) -> tuple[str, ...]:
    """Return policy-derived provider environment names without exposing values."""
    return contract.provider_environment_names


def _models_are_approved(models: object) -> bool:
    """Require the production Root/Sub pair while allowing DSPy normalization."""
    return bool(
        isinstance(models, dict)
        and set(models) == {"root", "sub"}
        and isinstance(models.get("root"), str)
        and models["root"] in _APPROVED_ROOT_MODELS
        and isinstance(models.get("sub"), str)
        and models["sub"] in _APPROVED_SUB_MODELS
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output.expanduser().resolve()
    started_at = _utc_now()
    if args.timeout_seconds <= 0 or not _path_is_allowed(output):
        print("Live proof precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION
    _load_repo_env()
    try:
        settings = require_live_execution()
        contract = active_profile_contract()
    except FleetConfigurationError:
        _write_failure(
            output,
            category="precondition_failed",
            phase="environment",
            started_at=started_at,
        )
        print("Live proof precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION
    required_environment = _required_provider_environment(contract)
    if any(not os.environ.get(name) for name in required_environment):
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
    models = _configured_models(settings)
    child_env = os.environ.copy()
    child_env.pop("FLEET_ROOT_MODEL", None)
    child_env.pop("FLEET_SUB_MODEL", None)
    if not _models_are_approved(models):
        _write_failure(
            output,
            category="precondition_failed",
            phase="models",
            started_at=started_at,
            sha=sha,
            branch=branch,
        )
        print("Live proof model precondition failed.", file=sys.stderr)
        return EXIT_PRECONDITION

    worktree: Path | None = None
    lockfile_sha256: str | None = None
    interrupted = False
    failure: tuple[str, str] | None = None
    try:
        repo_root = Path(_git("rev-parse", "--show-toplevel")).resolve()
        worktree = _create_detached_worktree(sha, repo_root)
        receipt_path = worktree / ".fleet-live-proof-receipt.json"
        child_env[EVIDENCE_ENV] = str(receipt_path)
        try:
            first_lane = _run_lane(
                lane="attachment_artifact_durability",
                timeout_seconds=args.timeout_seconds,
                worktree=worktree,
                child_env=child_env,
            )
            if not first_lane.passed:
                failure = ("proof_failed", "attachment_artifact_durability")
            else:
                durability_evidence = _load_durability_evidence(worktree)
                second_lane = _run_lane(
                    lane="fastapi_dspy_daytona_mvp",
                    timeout_seconds=args.timeout_seconds,
                    worktree=worktree,
                    child_env=child_env,
                )
                if not second_lane.passed:
                    failure = ("proof_failed", "fastapi_dspy_daytona_mvp")
                else:
                    receipt = _load_receipt(receipt_path)
                    lockfile_sha256 = _lockfile_sha256(worktree)
                    receipt = _build_success_receipt(
                        receipt,
                        sha=sha,
                        branch=branch,
                        lockfile_sha256=lockfile_sha256,
                        versions=_installed_versions(worktree, child_env),
                        models=models,
                        durability_evidence=durability_evidence,
                    )
                    _atomic_write(output, receipt)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            interrupted = True
        except (ReceiptError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            failure = (
                "receipt_invalid" if isinstance(exc, ReceiptError) else "proof_failed",
                getattr(exc, "phase", "lane"),
            )
    except (KeyboardInterrupt, subprocess.TimeoutExpired):
        interrupted = True
    except (OSError, RuntimeError, subprocess.SubprocessError):
        failure = ("proof_failed", "worktree")
    finally:
        if worktree is not None:
            try:
                _remove_detached_worktree(worktree, repo_root)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                failure = ("cleanup_failed", "worktree")

    if interrupted:
        _write_failure(
            output,
            category="interrupted",
            phase="lane",
            started_at=started_at,
            sha=sha,
            branch=branch,
        )
        print("Live proof was interrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED
    if failure is not None:
        category, phase = failure
        _write_failure(
            output,
            category=category,
            phase=phase,
            started_at=started_at,
            sha=sha,
            branch=branch,
        )
        if category == "receipt_invalid":
            print("Live proof receipt validation failed.", file=sys.stderr)
            return EXIT_RECEIPT
        if category == "cleanup_failed":
            print("Live proof cleanup failed.", file=sys.stderr)
            return EXIT_PROOF
        if phase == "worktree":
            print("Live proof worktree precondition failed.", file=sys.stderr)
            return EXIT_PRECONDITION
        print("Live proof failed; inspect the bounded receipt.", file=sys.stderr)
        return EXIT_PROOF

    if not output.exists():
        _write_failure(
            output,
            category="receipt_invalid",
            phase="receipt_json",
            started_at=started_at,
            sha=sha,
            branch=branch,
        )
        print("Live proof receipt validation failed.", file=sys.stderr)
        return EXIT_RECEIPT

    try:
        receipt = _load_receipt(output)
        if lockfile_sha256 is None:
            raise ReceiptError("candidate_fingerprint")
        _validate_success_receipt_extended(
            receipt,
            sha=sha,
            branch=branch,
            lockfile_sha256=lockfile_sha256,
        )
    except (ReceiptError, KeyError):
        _write_failure(
            output,
            category="receipt_invalid",
            phase="receipt_fields",
            started_at=started_at,
            sha=sha,
            branch=branch,
        )
        print("Live proof receipt validation failed.", file=sys.stderr)
        return EXIT_RECEIPT

    if _model_family(receipt["models"].get("root")) != _model_family(models.get("root")) or _model_family(
        receipt["models"].get("sub")
    ) != _model_family(models.get("sub")):
        _write_failure(
            output,
            category="receipt_invalid",
            phase="receipt_models",
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
