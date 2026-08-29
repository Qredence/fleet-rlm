"""Run and aggregate the P35-D credentialed live certification matrix.

The matrix is deliberately serial.  Each lane owns its Daytona resources and
emits a small receipt; this command joins those receipts only after validating
one candidate SHA, one lockfile digest, and the published DSPy release.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

try:
    import fcntl
except ImportError:  # pragma: no cover - certification runs on Unix CI hosts
    fcntl = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
CERTIFIED_DSPY = "3.3.1"
CERTIFIED_DAYTONA_SNAPSHOT = "fleet-rlm-python313-v5"
CERTIFIED_DAYTONA_TARGET = "us"
MANIFEST_SCHEMA = "fleet.p35d-live-certification-matrix/v1"
MAX_LOG_FILE_BYTES = 1_048_576
MAX_LOG_FILES = 10_000
MAX_LOG_TOTAL_BYTES = 64 * 1024 * 1024
MAX_LOG_DEPTH = 8
MAX_RECEIPT_BYTES = 4 * 1024 * 1024

# Claim -> concrete lane table consumed by p35e.  Keep this table explicit:
# a receipt is not evidence for an assertion merely because a test happened to
# execute nearby code.
CLAIM_LANES: dict[str, tuple[str, ...]] = {
    "VAL-RLM-001": ("runtime-version",),
    "VAL-RLM-059": ("stdout-reasoning", "root-child"),
    "VAL-RLM-060": ("stdout-reasoning",),
    "VAL-RLM-061": ("root-child", "root-batch"),
    "VAL-RLM-062": (
        "root-direct",
        "root-child",
        "root-batch",
        "stdout-reasoning",
        "cancel",
        "timeout",
        "workspace-memory",
        "attachment-artifact",
    ),
    "VAL-RLM-065": ("root-direct", "workspace-memory", "attachment-artifact", "fault-logs"),
    "VAL-RLM-071": ("fault-logs",),
}

REQUIRED_LIVE_SCHEMAS: dict[str, str] = {
    "runtime-version": "fleet.p35d-runtime-identity/v1",
    "root-direct": "fleet.p35d-root-direct/v1",
    "root-child": "fleet.rlm-callback-shadow-root-child/v1",
    "root-batch": "fleet.p35d-root-batch/v1",
    "stdout-reasoning": "fleet.p35d-stdout-reasoning/v1",
    "cancel": "fleet.p35d-cancel/v1",
    "timeout": "fleet.p35d-timeout/v1",
    "workspace-memory": "fleet.qre140-memory-candidate-proof/v1",
    "attachment-artifact": "fleet.p35d-attachment-artifact/v1",
    "fault-logs": "fleet.p35d-fault-logs/v1",
}
REQUIRED_LIVE_ASSERTIONS: dict[str, dict[str, object]] = {
    "runtime-version": {"exact_published_runtime": True},
    "root-direct": {
        "direct_root_completion": True,
        "typed_submit": True,
        "production_resources_secret_free": True,
    },
    "root-child": {
        "actual_root_interpreter_attached": True,
        "actual_child_interpreter_attached": True,
        "all_call_ids_paired": True,
        "child_under_recursive_parent": True,
        "root_depth_zero_child_depth_one": True,
        "no_grandchild_interpreter": True,
    },
    "root-batch": {"ordered_root_batch": True, "native_child_count": 2, "peak_child_concurrency": 2},
    "stdout-reasoning": {
        "reasoning_precedes_code": True,
        "stdout_incremental": True,
        "typed_submit_finalizes_stream": True,
        "cleanup_confirmed_absent": True,
    },
    "cancel": {"cancellation_observed": True, "admission_restored": True, "lease_released": True},
    "timeout": {"timeout_observed": True, "admission_restored": True, "lease_released": True},
    "workspace-memory": {
        "proposal_observed": True,
        "post_commit_v3_promoted": True,
        "searchable_on_next_turn": True,
        "injectable_on_next_turn": True,
        "secret_audit_passed": True,
        "cleanup_passed": True,
    },
    "attachment-artifact": {
        "attachment_readable": True,
        "artifact_survived_replacement": True,
        "shared_volume_checksum_verified": True,
    },
    "fault-logs": {"secret_free_fault_logs": True},
}


LIVE_LANES: dict[str, tuple[str, ...]] = {
    "runtime-version": ("tests/live/backend/test_p35d_live_matrix.py::test_p35d_runtime_identity",),
    "root-direct": ("tests/live/backend/test_p35d_live_matrix.py::test_p35d_live_root_direct",),
    "root-child": (
        "tests/live/backend/test_callback_shadow_root_child.py::test_live_callback_shadow_root_child_ancestry",
    ),
    "root-batch": (
        "tests/live/backend/test_daytona_recursive_batch.py::test_daytona_recursive_batch_two_children_through_fastapi",
    ),
    "stdout-reasoning": ("tests/live/backend/test_p35d_live_matrix.py::test_p35d_live_stdout_reasoning",),
    "cancel": (
        "tests/live/backend/test_daytona_cancel_during_execution.py::test_daytona_cancel_during_execution_through_fastapi",
    ),
    "timeout": ("tests/live/backend/test_daytona_deadline_cleanup.py::test_daytona_deadline_cleanup_through_fastapi",),
    "workspace-memory": (
        "tests/live/backend/test_memory_candidate_live.py::test_live_memory_candidate_promotes_after_commit_and_retrieves_on_next_turn",
    ),
    "attachment-artifact": (
        "tests/live/backend/test_attachment_artifact_durability.py::test_staged_attachment_is_readable_and_artifact_survives_replacement",
    ),
    "fault-logs": ("tests/live/backend/test_p35d_live_matrix.py::test_p35d_fault_logs_are_secret_free",),
}


class CertificationError(ValueError):
    """Raised when evidence cannot be joined into one safe certification."""


def _is_hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(char in "0123456789abcdef" for char in value)


def _candidate_from_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    candidate = receipt.get("candidate")
    if not isinstance(candidate, dict):
        candidate = {}
    identity = receipt.get("identity")
    if isinstance(identity, dict):
        candidate = {**candidate, **identity}
    return candidate


def _normalized_lane(receipt: object, *, name: str) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise CertificationError(f"{name} receipt is not an object")
    candidate = _candidate_from_receipt(receipt)
    sha = candidate.get("sha")
    lockfile_sha256 = candidate.get("lockfile_sha256")
    dspy = candidate.get("dspy")
    versions = candidate.get("versions")
    if dspy is None and isinstance(versions, dict):
        dspy = versions.get("dspy")
    if not _is_hex(sha, 40):
        raise CertificationError(f"{name} receipt has no candidate SHA")
    if not _is_hex(lockfile_sha256, 64):
        raise CertificationError(f"{name} receipt has no lockfile SHA")
    if dspy != CERTIFIED_DSPY:
        raise CertificationError(f"{name} receipt has uncertified DSPy")
    if receipt.get("passed") is not True:
        raise CertificationError(f"{name} receipt did not pass")
    run_id = receipt.get("run_id")
    if not _is_hex(run_id, 32):
        raise CertificationError(f"{name} receipt run ID is invalid")
    receipt_path = receipt.get("receipt_path")
    if receipt_path is not None:
        expected_path = Path(".fleet-evidence/receipts/p35d") / f"{name}.json"
        if receipt_path != str(expected_path):
            raise CertificationError(f"{name} receipt path is not canonical")
    expected_schema = REQUIRED_LIVE_SCHEMAS.get(name)
    if expected_schema is not None and receipt.get("schema") != expected_schema:
        raise CertificationError(f"{name} receipt schema is invalid")
    expected_assertions = REQUIRED_LIVE_ASSERTIONS.get(name)
    assertions = receipt.get("assertions")
    if expected_assertions is not None and (
        not isinstance(assertions, dict)
        or any(assertions.get(key) != expected for key, expected in expected_assertions.items())
    ):
        raise CertificationError(f"{name} receipt assertions are incomplete")
    cleanup = receipt.get("cleanup")
    if (
        not isinstance(cleanup, dict)
        or cleanup.get("confirmed_absent") is not True
        or cleanup.get("admission_restored") is not True
    ):
        raise CertificationError(f"{name} receipt is not cleanup-complete")
    return {
        "name": name,
        "schema": receipt.get("schema"),
        "passed": True,
        "candidate": {
            "sha": sha,
            "lockfile_sha256": lockfile_sha256,
            "dspy": dspy,
            "daytona_snapshot": candidate.get("daytona_snapshot"),
            "daytona_target": candidate.get("daytona_target"),
            "tracked_tree_clean": candidate.get("tracked_tree_clean"),
        },
        "assertions": receipt.get("assertions", {}),
        "cleanup": cleanup or {"confirmed_absent": True, "admission_restored": True},
        "receipt_path": receipt.get("receipt_path"),
        "run_id": run_id,
        "receipt_sha256": receipt.get("receipt_sha256"),
    }


def _validate_runtime_and_scans(runtime: object, scans: object) -> None:
    if not isinstance(runtime, dict):
        raise CertificationError("runtime identity is malformed")
    if (
        runtime.get("metadata") != CERTIFIED_DSPY
        or runtime.get("module") != CERTIFIED_DSPY
        or not isinstance(runtime.get("python"), str)
        or re.fullmatch(r"3\.13\.\d+", runtime["python"]) is None
        or runtime.get("doctor_identity") is not True
        or runtime.get("daytona_snapshot") != CERTIFIED_DAYTONA_SNAPSHOT
        or runtime.get("daytona_target") != CERTIFIED_DAYTONA_TARGET
    ):
        raise CertificationError("runtime identity is not certified")
    if not isinstance(scans, dict) or set(scans) != {"host_logs"}:
        raise CertificationError("evidence scans are incomplete")
    host_logs = scans["host_logs"]
    if (
        not isinstance(host_logs, dict)
        or host_logs.get("passed") is not True
        or type(host_logs.get("files_scanned")) is not int
        or host_logs["files_scanned"] <= 0
        or host_logs.get("findings") != []
        or not isinstance(host_logs.get("surfaces"), list)
        or not host_logs["surfaces"]
        or any(
            not isinstance(surface, str) or surface not in {".fleet_rlm/logs", ".fleet-evidence/logs", "logs"}
            for surface in host_logs["surfaces"]
        )
    ):
        raise CertificationError("host log evidence is incomplete")


def build_manifest(
    *,
    sha: str,
    lockfile_sha256: str,
    receipts: dict[str, object],
    scans: dict[str, object],
    runtime: dict[str, object],
    run_id: str | None = None,
) -> dict[str, Any]:
    """Validate and build the content-addressed P35-D evidence manifest."""
    if not _is_hex(sha, 40):
        raise CertificationError("candidate SHA is invalid")
    if not _is_hex(lockfile_sha256, 64):
        raise CertificationError("lockfile SHA is invalid")
    if run_id is not None and not _is_hex(run_id, 32):
        raise CertificationError("run ID is invalid")
    _validate_runtime_and_scans(runtime, scans)
    if not receipts:
        raise CertificationError("no live receipts supplied")
    if set(receipts) != set(LIVE_LANES):
        raise CertificationError("live lane coverage is incomplete")
    if not _is_hex(run_id, 32):
        raise CertificationError("run ID is required")
    normalized: dict[str, dict[str, Any]] = {}
    for name, receipt in receipts.items():
        lane = _normalized_lane(receipt, name=name)
        normalized[name] = lane
    for name, lane in normalized.items():
        if lane.get("run_id") != run_id:
            raise CertificationError(f"{name} receipt run ID does not match manifest")
        candidate = lane["candidate"]
        if candidate["sha"] != sha:
            raise CertificationError(f"{name} receipt candidate SHA does not match manifest")
        if candidate["lockfile_sha256"] != lockfile_sha256:
            raise CertificationError(f"{name} receipt lockfile SHA does not match manifest")
    for name, lane in normalized.items():
        candidate = lane["candidate"]
        if (
            candidate.get("daytona_snapshot") != CERTIFIED_DAYTONA_SNAPSHOT
            or candidate.get("daytona_target") != CERTIFIED_DAYTONA_TARGET
            or candidate.get("tracked_tree_clean") is not True
        ):
            raise CertificationError(f"{name} receipt Daytona provider identity is stale")
    missing_claims = [claim for claim, lanes in CLAIM_LANES.items() if not all(lane in normalized for lane in lanes)]
    if missing_claims:
        raise CertificationError("missing live lanes for " + ", ".join(missing_claims))
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        **({"run_id": run_id} if run_id is not None else {}),
        "candidate": {
            "sha": sha,
            "lockfile_sha256": lockfile_sha256,
            "dspy": CERTIFIED_DSPY,
            "daytona_snapshot": CERTIFIED_DAYTONA_SNAPSHOT,
            "daytona_target": CERTIFIED_DAYTONA_TARGET,
            "tracked_tree_clean": True,
        },
        "claims": {claim: list(lanes) for claim, lanes in CLAIM_LANES.items()},
        "lanes": normalized,
        "runtime": runtime,
        "scans": scans,
        "cleanup": {
            "confirmed_absent": all(lane["cleanup"]["confirmed_absent"] is True for lane in normalized.values()),
            "admission_restored": all(lane["cleanup"]["admission_restored"] is True for lane in normalized.values()),
        },
        "passed": True,
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return manifest


_CREDENTIAL_ASSIGNMENT = re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*(\S+)")
_TRACEBACK = re.compile(r"(?i)traceback \(most recent call last\)")
_REDACTED_VALUES = frozenset({"***", "...", "<redacted>", "[redacted]", "redacted", "none"})


def _is_non_redacted_credential(value: str) -> bool:
    normalized = value.rstrip(",;.)").strip("'\"").lower()
    return (
        bool(normalized)
        and not re.fullmatch(r"[a-z_][a-z0-9_]*=", normalized)
        and normalized not in _REDACTED_VALUES
        and not normalized.startswith(("http://", "https://"))
    )


def _has_credential_assignment(text: str, *, names: tuple[str, ...]) -> bool:
    """Return true only for non-redacted credential-like assignments."""
    if any(
        re.search(rf"(?i)\b{re.escape(name)}\b\s*[:=]\s*(\S+)", text)
        and _is_non_redacted_credential(re.search(rf"(?i)\b{re.escape(name)}\b\s*[:=]\s*(\S+)", text).group(1))
        for name in names
    ):
        return True
    return any(_is_non_redacted_credential(match.group(1)) for match in _CREDENTIAL_ASSIGNMENT.finditer(text))


def scan_host_log_surfaces(
    root: Path,
    *,
    secret_values: tuple[str, ...] = (),
    secret_names: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Scan mission-owned host files with bounded, no-follow traversal."""
    surfaces = (
        root / ".fleet_rlm" / "logs",
        root / ".fleet-evidence" / "logs",
        root / "logs",
    )
    paths: list[Path] = []
    findings: list[dict[str, Any]] = []
    visited = 0
    total_size = 0
    for surface in surfaces:
        if surface.is_symlink():
            findings.append({"path": str(surface.relative_to(root)), "matches": ["symlink_surface"]})
            continue
        if not surface.is_dir():
            continue
        stack: list[tuple[Path, int]] = [(surface, 0)]
        while stack:
            directory, depth = stack.pop()
            if depth > MAX_LOG_DEPTH:
                findings.append({"path": str(directory.relative_to(root)), "matches": ["log_depth_exceeded"]})
                continue
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        visited += 1
                        relative = str(Path(entry.path).relative_to(root))
                        if visited > MAX_LOG_FILES:
                            findings.append({"path": relative, "matches": ["log_file_count_exceeded"]})
                            stack.clear()
                            break
                        try:
                            if entry.is_symlink():
                                findings.append({"path": relative, "matches": ["symlink_file"]})
                            elif entry.is_dir(follow_symlinks=False):
                                stack.append((Path(entry.path), depth + 1))
                            elif entry.is_file(follow_symlinks=False):
                                paths.append(Path(entry.path))
                        except OSError:
                            findings.append({"path": relative, "matches": ["unreadable_log"]})
            except OSError:
                findings.append({"path": str(directory.relative_to(root)), "matches": ["unreadable_log"]})
    value_set = {value for value in secret_values if value}
    name_set = {name for name in secret_names if name}
    for path in sorted(set(paths)):
        descriptor: int | None = None
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
            )
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                findings.append({"path": str(path.relative_to(root)), "matches": ["non_regular_log"]})
                continue
            if metadata.st_size > MAX_LOG_FILE_BYTES:
                findings.append({"path": str(path.relative_to(root)), "matches": ["log_too_large"]})
                continue
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = None
                data = handle.read(MAX_LOG_FILE_BYTES + 1)
            if len(data) > MAX_LOG_FILE_BYTES:
                findings.append({"path": str(path.relative_to(root)), "matches": ["log_too_large"]})
                continue
            total_size += len(data)
            if total_size > MAX_LOG_TOTAL_BYTES:
                findings.append({"path": str(path.relative_to(root)), "matches": ["log_total_size_exceeded"]})
                break
            text = data.decode("utf-8", errors="replace")
        except OSError:
            findings.append({"path": str(path.relative_to(root)), "matches": ["unreadable_log"]})
            continue
        finally:
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
        matches: list[str] = []
        if any(value in text for value in value_set):
            matches.append("secret_value")
        if _has_credential_assignment(text, names=tuple(sorted(name_set))):
            matches.append("credential_assignment")
        if _TRACEBACK.search(text) and any(value in text for value in value_set):
            matches.append("secret_bearing_traceback")
        if matches:
            findings.append({"path": str(path.relative_to(root)), "matches": sorted(set(matches))})
    return {
        "passed": not findings and bool(paths),
        "files_scanned": len(paths),
        "findings": findings,
        "surfaces": [
            str(surface.relative_to(root)) for surface in surfaces if surface.exists() and not surface.is_symlink()
        ],
    }


def _ensure_evidence_directory(path: Path) -> Path:
    """Create one evidence directory without following symlink components."""
    repo = Path(os.path.abspath(REPO_ROOT))
    if repo != repo.resolve():
        raise CertificationError("P35-D repository root is symlinked")
    root = repo / ".fleet-evidence"
    target = Path(os.path.abspath(path))
    if target != root and not target.is_relative_to(root):
        raise CertificationError("P35-D evidence path escapes the evidence root")
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise CertificationError("P35-D evidence root is unsafe")
    root.mkdir(parents=True, exist_ok=True)
    current = root
    for component in target.relative_to(root).parts if target != root else ():
        current = current / component
        if current.exists() and (current.is_symlink() or not current.is_dir()):
            raise CertificationError("P35-D evidence directory is unsafe")
        current.mkdir(exist_ok=True)
    return target


@contextlib.contextmanager
def _exclusive_evidence_lock() -> Any:
    evidence_root = _ensure_evidence_directory(REPO_ROOT / ".fleet-evidence")
    if fcntl is None:
        raise CertificationError("P35-D evidence locking is unavailable")
    lock_path = evidence_root / ".p35d-certification.lock"
    if lock_path.exists() and (lock_path.is_symlink() or not lock_path.is_file()):
        raise CertificationError("P35-D evidence lock path is unsafe")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise CertificationError("P35-D evidence lock path is unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CertificationError("P35-D evidence lock path is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CertificationError("another P35-D certification run owns the evidence lock") from exc
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _safe_output_path(path: Path) -> Path:
    expected = Path(os.path.abspath(REPO_ROOT / ".fleet-evidence" / "receipts" / "p35d-live-certification-matrix.json"))
    candidate = Path(os.path.abspath(path.expanduser()))
    if candidate != expected:
        raise CertificationError("P35-D output must use the canonical evidence path")
    _ensure_evidence_directory(candidate.parent)
    if candidate.exists() and (candidate.is_symlink() or not candidate.is_file()):
        raise CertificationError("P35-D output path is unsafe")
    return candidate


def _archive_existing(path: Path, archive_root: Path, run_id: str) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        raise CertificationError(f"P35-D evidence path is unsafe: {path.name}")
    destination = archive_root / run_id / path.name
    _ensure_evidence_directory(destination.parent)
    path.replace(destination)


def _write_atomic_text(path: Path, text: str) -> None:
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise CertificationError(f"P35-D output path is unsafe: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_bounded(
    command: list[str], *, env: dict[str, str], timeout: int, cap: int = 4 * 1024 * 1024
) -> tuple[int, bytes]:
    """Run one lane with bounded merged output and a process-group timeout."""
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    deadline = time.monotonic() + timeout + 60
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout + 60)
            events = selector.select(min(remaining, 0.25))
            if events:
                chunk = os.read(process.stdout.fileno(), 64 * 1024)
                if chunk:
                    output.extend(chunk)
                    if len(output) > cap:
                        raise CertificationError("P35-D lane output exceeded its bound")
                else:
                    selector.unregister(process.stdout)
                    break
            if process.poll() is not None and not events:
                break
        returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, 9)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        raise
    finally:
        selector.close()
        process.stdout.close()
    return returncode, bytes(output)


def _pytest_exactly_one_passed(output: str) -> bool:
    status_words = r"passed|failed|error(?:s)?|skipped|xfailed|xpassed|deselected"
    summaries = [
        line
        for line in output.splitlines()
        if re.search(r"\bin\s+[0-9.]+s\s*$", line) and re.search(rf"\d+\s+(?:{status_words})", line)
    ]
    if not summaries:
        return False
    counts = {word: 0 for word in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed", "deselected")}
    for count, word in re.findall(rf"(\d+)\s+({status_words})", summaries[-1]):
        counts["errors" if word == "error" else word] += int(count)
    return counts["passed"] == 1 and all(value == 0 for key, value in counts.items() if key != "passed")


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Atomically write the canonical P35-D manifest."""
    target = _safe_output_path(path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_receipt_path(path: Path, *, lane: str) -> Path:
    expected = Path(os.path.abspath(REPO_ROOT / ".fleet-evidence" / "receipts" / "p35d" / f"{lane}.json"))
    candidate = Path(os.path.abspath(path.expanduser()))
    if candidate != expected:
        raise CertificationError(f"{lane} receipt path is not canonical")
    _ensure_evidence_directory(candidate.parent)
    if candidate.exists() and (candidate.is_symlink() or not candidate.is_file()):
        raise CertificationError(f"{lane} receipt path is unsafe")
    return candidate


def _load_receipt(path: Path, *, lane: str | None = None) -> dict[str, Any]:
    if lane is not None:
        path = _safe_receipt_path(path, lane=lane)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_RECEIPT_BYTES:
            raise CertificationError(f"receipt {path.name} is unsafe")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            data = handle.read(MAX_RECEIPT_BYTES + 1)
            if len(data) > MAX_RECEIPT_BYTES:
                raise CertificationError(f"receipt {path.name} is too large")
            payload = json.loads(data)
    except CertificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CertificationError(f"could not read receipt {path.name}") from exc
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
    if not isinstance(payload, dict):
        raise CertificationError(f"receipt {path.name} is not an object")
    payload["receipt_path"] = str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)
    payload["receipt_sha256"] = hashlib.sha256(data).hexdigest()
    return payload


def _identity() -> tuple[str, str, dict[str, object]]:
    load_dotenv(REPO_ROOT / ".env", override=False)
    if os.environ.get("FLEET_DAYTONA_SNAPSHOT") != CERTIFIED_DAYTONA_SNAPSHOT:
        raise CertificationError("P35-D requires the authoritative Daytona v5 snapshot")
    if os.environ.get("DAYTONA_TARGET") != CERTIFIED_DAYTONA_TARGET:
        raise CertificationError("P35-D requires unquoted DAYTONA_TARGET=us")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    if not _is_hex(sha, 40):
        raise CertificationError("candidate SHA is invalid")
    if subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip():
        raise CertificationError("tracked worktree is not clean")
    lockfile_sha256 = hashlib.sha256((REPO_ROOT / "uv.lock").read_bytes()).hexdigest()
    metadata_version = importlib.metadata.version("dspy")
    module_version = __import__("dspy").__version__
    runtime = {
        "metadata": metadata_version,
        "module": module_version,
        "python": sys.version.split()[0],
        "banner": f"Fleet RLM certified DSPy {metadata_version}",
        "doctor_identity": metadata_version == CERTIFIED_DSPY,
        "daytona_snapshot": CERTIFIED_DAYTONA_SNAPSHOT,
        "daytona_target": CERTIFIED_DAYTONA_TARGET,
    }
    return sha, lockfile_sha256, runtime


def _lane_command(test: str, timeout: int) -> list[str]:
    # Keep plugin autoload disabled for deterministic certification workers, but
    # load the two plugins required by the repository config and serial command.
    return [
        "uv",
        "run",
        "pytest",
        "-p",
        "pytest_timeout",
        "-p",
        "xdist.plugin",
        test,
        "-q",
        "-n",
        "0",
        f"--timeout={timeout}",
    ]


def run_matrix(*, timeout: int, output: Path) -> int:
    """Run every live lane serially, then write the joined manifest."""
    with _exclusive_evidence_lock():
        return _run_matrix(timeout=timeout, output=output)


def _run_matrix(*, timeout: int, output: Path) -> int:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        raise CertificationError("FLEET_LIVE=1 is required")
    output = _safe_output_path(output)
    sha, lockfile_sha256, runtime = _identity()
    receipt_root = _ensure_evidence_directory(REPO_ROOT / ".fleet-evidence" / "receipts" / "p35d")
    log_root = _ensure_evidence_directory(REPO_ROOT / ".fleet-evidence" / "logs" / "p35d")
    archive_root = _ensure_evidence_directory(REPO_ROOT / ".fleet-evidence" / "receipts-archive" / "p35d")
    run_id = uuid4().hex
    _archive_existing(output, archive_root, run_id)
    receipts: dict[str, object] = {}
    identity = {
        "sha": sha,
        "lockfile_sha256": lockfile_sha256,
        "dspy": CERTIFIED_DSPY,
        "daytona_snapshot": CERTIFIED_DAYTONA_SNAPSHOT,
        "daytona_target": CERTIFIED_DAYTONA_TARGET,
        "tracked_tree_clean": True,
    }
    for lane, tests in LIVE_LANES.items():
        if len(tests) != 1:
            raise CertificationError(f"{lane} must have exactly one serial test")
        receipt_path = _safe_receipt_path(receipt_root / f"{lane}.json", lane=lane)
        log_path = log_root / f"{lane}.log"
        _archive_existing(receipt_path, archive_root, run_id)
        _archive_existing(log_path, archive_root, run_id)
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "USER", "TMPDIR", "FLEET_LIVE", "FLEET_DAYTONA_SNAPSHOT", "DAYTONA_TARGET"}
            or key.endswith(("_API_KEY", "_TOKEN"))
        }
        env["FLEET_LIVE"] = "1"
        env["FLEET_P35D_RUN_ID"] = run_id
        env["FLEET_P35D_IDENTITY"] = json.dumps(identity, sort_keys=True)
        env["FLEET_LIVE_EVIDENCE_PATH"] = str(receipt_path)
        env["FLEET_PHASE1_STREAM_EVIDENCE_PATH"] = str(receipt_path)
        env["FLEET_PHASE2_RECURSIVE_EVIDENCE_PATH"] = str(receipt_path)
        env["FLEET_CALLBACK_SHADOW_EVIDENCE_PATH"] = str(receipt_path)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        returncode, output_bytes = _run_bounded(_lane_command(tests[0], timeout), env=env, timeout=timeout)
        output_text = output_bytes.decode("utf-8", errors="replace")
        _write_atomic_text(
            log_path,
            json.dumps({"lane": lane, "test": tests[0], "returncode": returncode}, sort_keys=True) + "\n",
        )
        if returncode != 0 or not _pytest_exactly_one_passed(output_text):
            raise CertificationError(f"live lane failed: {lane}")
        receipt = _load_receipt(receipt_path, lane=lane)
        if receipt.get("run_id") != run_id:
            raise CertificationError(f"live lane receipt run ID is stale: {lane}")
        # Persist the runner's candidate identity before taking the byte hash;
        # the outer gate can then re-open exactly the same canonical receipt.
        receipt.pop("receipt_path", None)
        receipt.pop("receipt_sha256", None)
        receipt["identity"] = identity
        _write_atomic_text(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        receipts[lane] = _load_receipt(receipt_path, lane=lane)
    final_sha, final_lockfile_sha256, final_runtime = _identity()
    if (final_sha, final_lockfile_sha256, final_runtime) != (sha, lockfile_sha256, runtime):
        raise CertificationError("candidate identity changed during P35-D matrix")
    scans = {
        "host_logs": scan_host_log_surfaces(
            REPO_ROOT,
            secret_values=tuple(
                value
                for name in (
                    "DAYTONA_API_KEY",
                    "GOOGLE_API_KEY",
                    "DEEPSEEK_API_KEY",
                    "KIMI_API_KEY",
                    "DATABRICKS_TOKEN",
                )
                if (value := os.environ.get(name))
            ),
            secret_names=("DAYTONA_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "KIMI_API_KEY", "DATABRICKS_TOKEN"),
        )
    }
    manifest = build_manifest(
        sha=sha,
        lockfile_sha256=lockfile_sha256,
        receipts=receipts,
        scans=scans,
        runtime=runtime,
        run_id=run_id,
    )
    write_manifest(output, manifest)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / ".fleet-evidence" / "receipts" / "p35d-live-certification-matrix.json",
    )
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args(argv)
    try:
        return run_matrix(timeout=args.timeout_seconds, output=args.output.expanduser().resolve())
    except (CertificationError, OSError, subprocess.SubprocessError) as exc:
        print(f"P35-D certification failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
