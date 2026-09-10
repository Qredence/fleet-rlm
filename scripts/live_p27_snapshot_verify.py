"""Seal one bounded P2.7 receipt for reduced Daytona snapshot candidates."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from fleet_rlm.config.loader import load_runtime_settings, require_live_execution
from fleet_rlm.config.settings import FleetConfigurationError
from fleet_rlm.daytona.platform import build_daytona_client
from fleet_rlm.daytona.provisioning import DaytonaEnvironmentProfile, environment_manifest
from fleet_rlm.snapshot_contract import validate_snapshot_name

if __package__:
    from . import daytona_snapshot
else:  # pragma: no cover - direct operator invocation
    import daytona_snapshot

RECEIPT_SCHEMA = "fleet.daytona-p27-snapshot-certification/v1"
_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_ROOT = _ROOT / ".fleet-evidence" / "receipts" / "adr006"
_RECURSIVE_EVIDENCE_ROOT = _ROOT / ".scratch" / "fleet-rlm-recursive-runtime" / "evidence"
_FORBIDDEN = ("prompt", "answer", "code", "credential", "sandbox_id", "volume_id", "broker", "trace", "http")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-snapshot", required=True)
    parser.add_argument("--child-snapshot", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    return parser


def _allowed_output(path: Path) -> bool:
    try:
        path.relative_to(_EVIDENCE_ROOT)
    except ValueError:
        return False
    return path.suffix == ".json" and path != _EVIDENCE_ROOT and not path.exists()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=_ROOT, text=True).strip()


def _candidate() -> tuple[str, str]:
    sha, branch = _git("rev-parse", "HEAD"), _git("branch", "--show-current")
    if len(sha) != 40 or not branch or branch in {"main", "master"}:
        raise RuntimeError("candidate must be a checked-out non-main commit")
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("candidate worktree is not clean")
    return sha, branch


def _write(path: Path, payload: dict[str, object]) -> None:
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


def _failure(category: str, phase: str, sha: str | None = None, branch: str | None = None) -> dict[str, object]:
    candidate = {"sha": sha, "branch": branch} if sha and branch else None
    return {
        "schema": RECEIPT_SCHEMA,
        "candidate": candidate,
        "failure": {"category": category, "phase": phase},
        "passed": False,
    }


async def _verify_snapshot_candidates(session: str, child: str) -> dict[str, dict[str, object]]:
    settings = load_runtime_settings()
    client = build_daytona_client(settings)
    try:
        verified: dict[str, dict[str, object]] = {}
        for profile, name in (
            (DaytonaEnvironmentProfile.SESSION, session),
            (DaytonaEnvironmentProfile.SEMANTIC_CHILD, child),
        ):
            spec = daytona_snapshot._spec(name, profile.value)
            await daytona_snapshot.check_snapshot(client, spec)
            await daytona_snapshot.verify_runtime(client, spec)
            manifest = environment_manifest(spec)
            verified[profile.value] = {
                "snapshot": name,
                "manifest_sha256": manifest.digest,
                "dependency_sha256": manifest.dependency_sha256,
                "resources": {"cpu": spec.cpu, "memory_gib": spec.memory_gib, "disk_gib": spec.disk_gib},
            }
        return verified
    finally:
        await client.close()


def _run(command: list[str], timeout_seconds: int) -> None:
    completed = subprocess.run(
        command,
        cwd=_ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout_seconds + 60,
    )
    if completed.returncode != 0:
        raise RuntimeError("live scenario failed")


def _assert_success_receipt(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("passed") is not True or payload.get("failure") is not None:
        raise RuntimeError("live scenario receipt failed")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = args.output.expanduser().resolve()
    if args.timeout_seconds <= 0 or not _allowed_output(output):
        print("P2.7 snapshot certification precondition failed.", file=sys.stderr)
        return 2
    try:
        session, child = validate_snapshot_name(args.session_snapshot), validate_snapshot_name(args.child_snapshot)
        load_dotenv(_ROOT / ".env", override=False)
        require_live_execution()
        sha, branch = _candidate()
    except (FleetConfigurationError, OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        _write(output, _failure("precondition_failed", "policy_or_candidate"))
        print("P2.7 snapshot certification precondition failed.", file=sys.stderr)
        return 2

    recursive_receipt: Path | None = None
    mvp_receipt: Path | None = None
    try:
        images = asyncio.run(_verify_snapshot_candidates(session, child))
        _RECURSIVE_EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
        descriptor, recursive_name = tempfile.mkstemp(dir=_RECURSIVE_EVIDENCE_ROOT, suffix=".json", text=True)
        os.close(descriptor)
        recursive_receipt = Path(recursive_name)
        descriptor, mvp_name = tempfile.mkstemp(dir=output.parent, suffix=".json", text=True)
        os.close(descriptor)
        mvp_receipt = Path(mvp_name)
        _run(
            [
                "uv",
                "run",
                "python",
                "scripts/live_daytona_verify.py",
                "--output",
                str(mvp_receipt),
                "--timeout-seconds",
                str(args.timeout_seconds),
                "--session-snapshot",
                session,
            ],
            args.timeout_seconds,
        )
        _assert_success_receipt(mvp_receipt)
        _run(
            [
                "uv",
                "run",
                "python",
                "scripts/live_phase2_recursive_verify.py",
                "--output",
                str(recursive_receipt),
                "--timeout-seconds",
                str(args.timeout_seconds),
                "--session-snapshot",
                session,
                "--child-snapshot",
                child,
            ],
            args.timeout_seconds,
        )
        _assert_success_receipt(recursive_receipt)
        payload: dict[str, object] = {
            "schema": RECEIPT_SCHEMA,
            "candidate": {
                "sha": sha,
                "branch": branch,
                "lockfile_sha256": hashlib.sha256((_ROOT / "uv.lock").read_bytes()).hexdigest(),
            },
            "images": images,
            "assertions": {
                "session_runtime_probe": True,
                "session_host_tool_rlm": True,
                "semantic_child_runtime_probe": True,
                "semantic_child_recursive_rlm": True,
                "all_disposable_cleanup_confirmed": True,
            },
            "finished_at": datetime.now(UTC).isoformat(),
            "failure": None,
            "passed": True,
        }
        rendered = json.dumps(payload, sort_keys=True).lower()
        if any(token in rendered for token in _FORBIDDEN):
            raise RuntimeError("receipt redaction failed")
        _write(output, payload)
    except (Exception, KeyboardInterrupt):
        _write(output, _failure("proof_failed", "snapshot_or_scenario", sha, branch))
        print("P2.7 snapshot certification failed; inspect the bounded receipt.", file=sys.stderr)
        return 3
    finally:
        for path in (recursive_receipt, mvp_receipt):
            if path is not None:
                path.unlink(missing_ok=True)
    print(f"P2.7 snapshot certification passed; bounded receipt: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
