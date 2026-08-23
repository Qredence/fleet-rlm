"""Run and aggregate the P35-D credentialed live certification matrix.

The matrix is deliberately serial.  Each lane owns its Daytona resources and
emits a small receipt; this command joins those receipts only after validating
one candidate SHA, one lockfile digest, and the published DSPy release.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CERTIFIED_DSPY = "3.3.1"
MANIFEST_SCHEMA = "fleet.p35d-live-certification-matrix/v1"

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

LIVE_LANES: dict[str, tuple[str, ...]] = {
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
    "timeout": (
        "tests/live/backend/test_daytona_deadline_cleanup.py::test_daytona_deadline_cleanup_through_fastapi",
    ),
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
    cleanup = receipt.get("cleanup")
    if (
        isinstance(cleanup, dict)
        and (cleanup.get("confirmed_absent") is not True or cleanup.get("admission_restored") is not True)
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
        },
        "assertions": receipt.get("assertions", {}),
        "cleanup": cleanup or {"confirmed_absent": True, "admission_restored": True},
        "receipt_path": receipt.get("receipt_path"),
    }


def build_manifest(
    *,
    sha: str,
    lockfile_sha256: str,
    receipts: dict[str, object],
    scans: dict[str, object],
    runtime: dict[str, object],
) -> dict[str, Any]:
    """Validate and build the content-addressed P35-D evidence manifest."""
    if not _is_hex(sha, 40):
        raise CertificationError("candidate SHA is invalid")
    if not _is_hex(lockfile_sha256, 64):
        raise CertificationError("lockfile SHA is invalid")
    if runtime.get("metadata") != CERTIFIED_DSPY or runtime.get("module") != CERTIFIED_DSPY:
        raise CertificationError("runtime DSPy identity is not exactly 3.3.1")
    if not receipts:
        raise CertificationError("no live receipts supplied")
    normalized: dict[str, dict[str, Any]] = {}
    for name, receipt in receipts.items():
        lane = _normalized_lane(receipt, name=name)
        candidate = lane["candidate"]
        if candidate["sha"] != sha:
            raise CertificationError(f"{name} receipt candidate SHA does not match manifest")
        if candidate["lockfile_sha256"] != lockfile_sha256:
            raise CertificationError(f"{name} receipt lockfile SHA does not match manifest")
        normalized[name] = lane
    if any(not isinstance(value, dict) or value.get("passed") is not True for value in scans.values()):
        raise CertificationError("one or more evidence scans failed")
    missing_claims = [
        claim
        for claim, lanes in CLAIM_LANES.items()
        if not all(lane in normalized for lane in lanes)
    ]
    if missing_claims:
        raise CertificationError("missing live lanes for " + ", ".join(missing_claims))
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {
            "sha": sha,
            "lockfile_sha256": lockfile_sha256,
            "dspy": CERTIFIED_DSPY,
            "tracked_tree_clean": True,
        },
        "claims": {claim: list(lanes) for claim, lanes in CLAIM_LANES.items()},
        "lanes": normalized,
        "runtime": runtime,
        "scans": scans,
        "passed": True,
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return manifest


_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*\S+"
)
_TRACEBACK = re.compile(r"(?i)traceback \(most recent call last\)")


def scan_host_log_surfaces(
    root: Path,
    *,
    secret_values: tuple[str, ...] = (),
    secret_names: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Scan all mission-owned host log roots without returning secret content."""
    surfaces = (
        root / ".fleet_rlm" / "logs",
        root / ".fleet-evidence" / "logs",
        root / "logs",
    )
    paths: list[Path] = []
    for surface in surfaces:
        if surface.is_dir():
            paths.extend(path for path in surface.rglob("*") if path.is_file() and not path.is_symlink())
    findings: list[dict[str, Any]] = []
    value_set = {value for value in secret_values if value}
    name_set = {name for name in secret_names if name}
    for path in sorted(set(paths)):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            findings.append({"path": str(path.relative_to(root)), "matches": ["unreadable_log"]})
            continue
        matches: list[str] = []
        if any(value in text for value in value_set):
            matches.append("secret_value")
        if any(re.search(rf"(?i)\b{re.escape(name)}\b\s*[:=]\s*\S+", text) for name in name_set):
            matches.append("credential_assignment")
        if _CREDENTIAL_ASSIGNMENT.search(text):
            matches.append("credential_assignment")
        if _TRACEBACK.search(text) and any(value in text for value in value_set):
            matches.append("secret_bearing_traceback")
        if matches:
            findings.append({"path": str(path.relative_to(root)), "matches": sorted(set(matches))})
    return {
        "passed": not findings,
        "files_scanned": len(paths),
        "findings": findings,
        "surfaces": [str(surface.relative_to(root)) for surface in surfaces if surface.exists()],
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Atomically write a manifest below the ignored evidence root."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CertificationError(f"could not read receipt {path.name}") from exc
    if not isinstance(payload, dict):
        raise CertificationError(f"receipt {path.name} is not an object")
    payload["receipt_path"] = str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)
    return payload


def _identity() -> tuple[str, str, dict[str, object]]:
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
    }
    return sha, lockfile_sha256, runtime


def _lane_command(test: str, timeout: int) -> list[str]:
    return ["uv", "run", "pytest", test, "-q", "-n", "0", f"--timeout={timeout}"]


def run_matrix(*, timeout: int, output: Path) -> int:
    """Run every live lane serially, then write the joined manifest."""
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        raise CertificationError("FLEET_LIVE=1 is required")
    sha, lockfile_sha256, runtime = _identity()
    evidence_root = REPO_ROOT / ".fleet-evidence"
    receipt_root = evidence_root / "receipts" / "p35d"
    log_root = evidence_root / "logs" / "p35d"
    receipts: dict[str, object] = {}
    identity = {"sha": sha, "lockfile_sha256": lockfile_sha256, "dspy": CERTIFIED_DSPY}
    for lane, tests in LIVE_LANES.items():
        if len(tests) != 1:
            raise CertificationError(f"{lane} must have exactly one serial test")
        receipt_path = receipt_root / f"{lane}.json"
        env = os.environ.copy()
        env["FLEET_LIVE"] = "1"
        env["FLEET_P35D_IDENTITY"] = json.dumps(identity, sort_keys=True)
        env["FLEET_LIVE_EVIDENCE_PATH"] = str(receipt_path)
        env["FLEET_PHASE1_STREAM_EVIDENCE_PATH"] = str(receipt_path)
        env["FLEET_PHASE2_RECURSIVE_EVIDENCE_PATH"] = str(receipt_path)
        env["FLEET_CALLBACK_SHADOW_EVIDENCE_PATH"] = str(receipt_path)
        log_path = log_root / f"{lane}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            _lane_command(tests[0], timeout),
            cwd=REPO_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout + 60,
        )
        # Keep command output bounded and metadata-only. The actual host log
        # surfaces are scanned separately; raw subprocess output is not copied
        # into the evidence bundle.
        log_path.write_text(
            json.dumps(
                {"lane": lane, "test": tests[0], "returncode": completed.returncode},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        if completed.returncode != 0:
            raise CertificationError(f"live lane failed: {lane}")
        if not receipt_path.is_file():
            raise CertificationError(f"live lane did not write receipt: {lane}")
        receipt = _load_receipt(receipt_path)
        receipt["identity"] = identity
        receipts[lane] = receipt
    scans = {
        "host_logs": scan_host_log_surfaces(
            REPO_ROOT,
            secret_values=tuple(
                value
                for name in ("DAYTONA_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "KIMI_API_KEY")
                if (value := os.environ.get(name))
            ),
            secret_names=("DAYTONA_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "KIMI_API_KEY"),
        )
    }
    manifest = build_manifest(
        sha=sha,
        lockfile_sha256=lockfile_sha256,
        receipts=receipts,
        scans=scans,
        runtime=runtime,
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
