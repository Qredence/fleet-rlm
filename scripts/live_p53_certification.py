#!/usr/bin/env python3
"""Run and aggregate the serial P53 live Session-runtime certification.

The P35-D matrix remains an independent transport/provider prerequisite.  This
runner adds the missing P53.2 continuation proof: a real Daytona root and
TurnLifecycle execute successful Turns around every taint/rotation trigger,
then the fresh runtime checks durable History and the absence of failed Python
state.  Native child lanes are consumed from their current, content-addressed
receipts.
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
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

try:
    import fcntl
except ImportError:  # pragma: no cover - certification runs on Unix CI hosts
    fcntl = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import p53_certification

CERTIFIED_DSPY = p53_certification.CERTIFIED_DSPY
RECEIPTS_ROOT = REPO_ROOT / ".fleet-evidence" / "receipts"
ARCHIVE_ROOT = REPO_ROOT / ".fleet-evidence" / "receipts-archive" / "p53"
DEFAULT_OUTPUT = p53_certification.DEFAULT_OUTPUT

# One explicit test owns each child claim.  The canonical receipt names are
# written by those tests themselves; the runner never treats a nearby receipt
# as evidence unless this exact command refreshed it.
CHILD_LANES: dict[str, str] = {
    "child_single": (
        "tests/live/backend/test_p39c_root_flow_live.py::test_live_root_flow_settles_child_ownership_before_publication"
    ),
    "child_batch": (
        "tests/live/backend/test_p39c_batch_live.py::test_live_batch_two_children_ordered_concurrent_leak_free"
    ),
    "child_failure": (
        "tests/live/backend/test_p39c_batch_live.py::test_live_batch_child_cleanup_failure_is_all_or_nothing"
    ),
    "child_timeout": (
        "tests/live/backend/test_p39c_cancel_deadline_live.py::"
        "test_live_deadline_with_in_flight_child_and_queued_sibling"
    ),
    "child_claim_loss": (
        "tests/live/backend/test_p39c_claim_loss_live.py::test_live_claim_loss_fencing_leaves_no_recursive_resources"
    ),
    "child_provider_absence": (
        "tests/live/backend/test_p39a_child_cleanup_ownership_live.py::"
        "test_live_child_cleanup_failure_fails_closed_with_explicit_classification"
    ),
    "child_volume_preservation": (
        "tests/live/backend/test_p39c_volume_preservation_live.py::"
        "test_live_volume_preservation_across_all_child_outcomes"
    ),
}

CHILD_CANONICAL_RECEIPTS = {
    "child_single": "p39c-root-flow.json",
    "child_batch": "p39c-batch-success.json",
    "child_failure": "p39c-batch-failure.json",
    "child_timeout": "p39c-cancel-deadline-deadline.json",
    "child_claim_loss": "p39c-claim-loss.json",
    "child_provider_absence": "p39a-child-cleanup-ownership-live-fault.json",
    "child_volume_preservation": "p39c-volume-preservation.json",
}


class LiveP53CertificationError(ValueError):
    """Raised when the serial P53 live run cannot be sealed safely."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity() -> tuple[str, str, dict[str, str | bool]]:
    try:
        sha = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        lockfile_sha256 = _sha256((REPO_ROOT / "uv.lock").read_bytes())
        dspy_version = importlib.metadata.version("dspy")
        import dspy

        dspy_module_version = getattr(dspy, "__version__", None)
    except (OSError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError, ImportError) as exc:
        raise LiveP53CertificationError("could not determine P53 candidate identity") from exc
    unexpected = [line for line in status.splitlines() if line and not line.startswith("?? .factory/")]
    if unexpected:
        raise LiveP53CertificationError("P53 live certification requires a clean worktree")
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise LiveP53CertificationError("candidate SHA is invalid")
    if dspy_version != CERTIFIED_DSPY or dspy_module_version != CERTIFIED_DSPY:
        raise LiveP53CertificationError("P53 live certification requires DSPy 3.3.1")
    snapshot = os.environ.get("FLEET_DAYTONA_SNAPSHOT")
    target = os.environ.get("DAYTONA_TARGET")
    if snapshot != p53_certification.CERTIFIED_DAYTONA_SNAPSHOT:
        raise LiveP53CertificationError("P53 live certification requires the authoritative Daytona v5 snapshot")
    if target != p53_certification.CERTIFIED_DAYTONA_TARGET:
        raise LiveP53CertificationError("P53 live certification requires unquoted DAYTONA_TARGET=us")
    return (
        sha,
        lockfile_sha256,
        {
            "sha": sha,
            "lockfile_sha256": lockfile_sha256,
            "dspy": dspy_version,
            "daytona_snapshot": snapshot,
            "daytona_target": target,
            "tracked_tree_clean": True,
        },
    )


def _read(path: Path, description: str) -> dict[str, Any]:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 8 * 1024 * 1024:
            raise LiveP53CertificationError(f"{description} is unsafe")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            value = json.loads(handle.read())
    except LiveP53CertificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LiveP53CertificationError(f"{description} is unreadable") from exc
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
    if not isinstance(value, dict):
        raise LiveP53CertificationError(f"{description} must be an object")
    return value


def _ensure_evidence_directory(path: Path) -> Path:
    """Create one evidence directory while rejecting symlinked components."""
    repo = Path(os.path.abspath(REPO_ROOT))
    if repo != repo.resolve():
        raise LiveP53CertificationError("P53 repository root is symlinked")
    root = repo / ".fleet-evidence"
    target = Path(os.path.abspath(path))
    if target != root and not target.is_relative_to(root):
        raise LiveP53CertificationError("P53 evidence path escapes the evidence root")
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise LiveP53CertificationError("P53 evidence root is unsafe")
    root.mkdir(parents=True, exist_ok=True)
    current = root
    for component in target.relative_to(root).parts if target != root else ():
        current = current / component
        if current.exists() and (current.is_symlink() or not current.is_dir()):
            raise LiveP53CertificationError("P53 evidence directory is unsafe")
        current.mkdir(exist_ok=True)
    return target


@contextlib.contextmanager
def _exclusive_evidence_lock() -> Any:
    """Serialize evidence writers and reject symlinked evidence roots."""
    evidence_root = _ensure_evidence_directory(REPO_ROOT / ".fleet-evidence")
    lock_path = evidence_root / ".p53-certification.lock"
    if fcntl is None:
        raise LiveP53CertificationError("P53 evidence locking is unavailable")
    if lock_path.exists() and (lock_path.is_symlink() or not lock_path.is_file()):
        raise LiveP53CertificationError("P53 evidence lock path is unsafe")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LiveP53CertificationError("another P53 certification run owns the evidence lock") from exc
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _archive(path: Path, run_tag: str, *, archive_name: str | None = None) -> None:
    if not path.is_file():
        return
    target = ARCHIVE_ROOT / run_tag / (archive_name or path.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(target))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
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


def _copy_atomic(source: Path, target: Path) -> None:
    """Copy one producer receipt without exposing a partial canonical file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as source_handle, os.fdopen(descriptor, "wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _command(test: str, timeout: int) -> tuple[str, ...]:
    # Keep quiet flags out of the command: pyproject addopts already carries
    # ``-q``.  A second ``-q`` (effective ``-qq``) suppresses the terminal
    # summary line that _pytest_exactly_one_passed requires.
    return ("uv", "run", "pytest", test, "-n", "0", f"--timeout={timeout}")


def _pytest_exactly_one_passed(output: str) -> bool:
    """Accept only a terminal pytest summary for one non-skipped test."""
    status_words = r"passed|failed|error(?:s)?|skipped|xfailed|xpassed|deselected"
    # pytest's format_session_duration appends a human-readable (H:MM:SS)
    # suffix for sessions >= 60s, so the summary need not end with the
    # seconds value.  Live Daytona lanes always cross that threshold.
    duration = r"\bin\s+[0-9.]+s(?:\s+\(\d+:\d{2}:\d{2}\))?\s*$"
    summaries = [
        line
        for line in output.splitlines()
        if re.search(duration, line) and re.search(rf"\d+\s+(?:{status_words})", line)
    ]
    if not summaries:
        return False
    counts = {word: 0 for word in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed", "deselected")}
    for count, word in re.findall(rf"(\d+)\s+({status_words})", summaries[-1]):
        normalized = "errors" if word == "error" else word
        counts[normalized] += int(count)
    return counts["passed"] == 1 and all(counts[key] == 0 for key in counts if key != "passed")


def _run_bounded(
    command: tuple[str, ...], *, env: dict[str, str], timeout: int, cap: int = 4 * 1024 * 1024
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
                        raise LiveP53CertificationError("P53 lane output exceeded its bound")
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


def _run_test(
    *,
    lane: str,
    test: str,
    timeout: int,
    env: dict[str, str],
    log_root: Path,
) -> None:
    returncode, output_bytes = _run_bounded(_command(test, timeout), env=env, timeout=timeout)
    output_text = output_bytes.decode("utf-8", errors="replace")
    pytest_passed = _pytest_exactly_one_passed(output_text)
    # Logs intentionally contain only command identity, status, and a digest.
    # Provider output and tracebacks are never copied into the evidence bundle.
    _write_json(
        log_root / f"{lane}.json",
        {
            "lane": lane,
            "test": test,
            "command": list(_command(test, timeout)),
            "returncode": returncode,
            "pytest_passed": pytest_passed,
            "output_sha256": _sha256(output_bytes),
        },
    )
    if returncode != 0 or not pytest_passed:
        raise LiveP53CertificationError(f"P53 live lane did not run exactly one passing test: {lane}")


def _child_receipt_path(lane: str, *, base: Path) -> Path:
    suffixes = {
        "child_single": "-p39c-root-flow",
        "child_batch": "-p39c-batch-success",
        "child_failure": "-p39c-batch-failure",
        "child_timeout": "-p39c-cd-deadline",
        "child_claim_loss": "-p39c-claim-loss",
        "child_provider_absence": "-p39a-child-fault",
        "child_volume_preservation": "-p39c-volume",
    }
    try:
        suffix = suffixes[lane]
    except KeyError as exc:
        raise LiveP53CertificationError(f"unknown P53 child lane: {lane}") from exc
    return base.with_name(f"{base.stem}{suffix}{base.suffix or '.json'}")


def run_certification(*, output: Path, timeout: int) -> int:
    with _exclusive_evidence_lock():
        return _run_certification(output=output, timeout=timeout)


def _run_certification(*, output: Path, timeout: int) -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        raise LiveP53CertificationError("FLEET_LIVE=1 is required")
    output = p53_certification.validate_manifest_path(output)
    sha, lockfile_sha256, identity = _identity()
    run_tag = uuid4().hex
    log_root = _ensure_evidence_directory(REPO_ROOT / ".fleet-evidence" / "logs" / "p53")
    _ensure_evidence_directory(RECEIPTS_ROOT / "p53")
    _ensure_evidence_directory(ARCHIVE_ROOT)
    rotation_path = RECEIPTS_ROOT / "p53" / "rotations.json"
    evidence_root = (REPO_ROOT / ".fleet-evidence").resolve()
    if output.is_file() and output.resolve().is_relative_to(evidence_root):
        _archive(output, run_tag)
    _archive(rotation_path, run_tag)
    env = os.environ.copy()
    env.update(
        {
            "FLEET_LIVE": "1",
            "FLEET_LIVE_EVIDENCE_PATH": str(rotation_path),
            "FLEET_P53_IDENTITY": json.dumps(identity, sort_keys=True),
            "FLEET_P53_RUN_ID": run_tag,
        }
    )
    rotation_test = (
        "tests/live/backend/test_p53_daytona_session_certification_live.py::"
        "test_live_p53_daytona_session_rotations_and_history"
    )
    _run_test(lane="session-rotations", test=rotation_test, timeout=timeout, env=env, log_root=log_root)
    if not rotation_path.is_file():
        raise LiveP53CertificationError("P53 session rotation lane did not write a receipt")
    rotation_receipt = _read(rotation_path, "P53 session rotation receipt")
    if rotation_receipt.get("run_id") != run_tag or rotation_receipt.get("candidate") != identity:
        raise LiveP53CertificationError("P53 rotation receipt provenance does not match this invocation")

    child_receipts: dict[str, tuple[Path, dict[str, Any]]] = {}
    for lane, test in CHILD_LANES.items():
        child_base = RECEIPTS_ROOT / "p53" / f"{lane}.json"
        child_env = env.copy()
        child_env["FLEET_LIVE_EVIDENCE_PATH"] = str(child_base)
        staging_path = _child_receipt_path(lane, base=child_base)
        canonical_path = RECEIPTS_ROOT / "p53" / CHILD_CANONICAL_RECEIPTS[lane]
        # Archive both the P39 producer's normal receipt and this runner's
        # staging/canonical copies. A prior run must never satisfy this lane.
        _archive(
            RECEIPTS_ROOT / CHILD_CANONICAL_RECEIPTS[lane],
            run_tag,
            archive_name=f"producer-{CHILD_CANONICAL_RECEIPTS[lane]}",
        )
        _archive(canonical_path, run_tag)
        _archive(staging_path, run_tag)
        _run_test(lane=lane, test=test, timeout=timeout, env=child_env, log_root=log_root)
        if not staging_path.is_file():
            raise LiveP53CertificationError(f"P53 child lane did not write a receipt: {lane}")
        child_receipt = _read(staging_path, f"P53 child receipt {lane}")
        child_candidate = child_receipt.get("candidate")
        if (
            not isinstance(child_candidate, dict)
            or child_candidate.get("sha") != sha
            or child_candidate.get("lockfile_sha256") != lockfile_sha256
            or child_candidate.get("dspy") != CERTIFIED_DSPY
            or child_candidate.get("daytona_snapshot") != p53_certification.CERTIFIED_DAYTONA_SNAPSHOT
            or child_candidate.get("daytona_target") != p53_certification.CERTIFIED_DAYTONA_TARGET
            or child_candidate.get("tracked_tree_clean") is not True
        ):
            raise LiveP53CertificationError(f"P53 child lane candidate is stale: {lane}")
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        _copy_atomic(staging_path, canonical_path)
        canonical_receipt = _read(canonical_path, f"P53 canonical child receipt {lane}")
        if canonical_receipt != child_receipt:
            raise LiveP53CertificationError(f"P53 canonical child receipt copy failed: {lane}")
        child_receipts[lane] = (canonical_path, canonical_receipt)

    final_sha, final_lockfile_sha256, final_identity = _identity()
    if (final_sha, final_lockfile_sha256, final_identity) != (sha, lockfile_sha256, identity):
        raise LiveP53CertificationError("P53 candidate identity changed during live certification")

    manifest = p53_certification.build_manifest(
        sha=sha,
        lockfile_sha256=lockfile_sha256,
        rotation_receipt_path=rotation_path,
        rotation_receipt=rotation_receipt,
        child_receipts=child_receipts,
    )
    _write_json(output, manifest)
    print(f"P53 live Session certification sealed: {output} ({manifest['manifest_sha256']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout-seconds", type=int, default=1500)
    args = parser.parse_args(argv)
    try:
        return run_certification(output=args.output.expanduser(), timeout=args.timeout_seconds)
    except (LiveP53CertificationError, OSError, subprocess.SubprocessError) as exc:
        print(f"P53 live certification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
