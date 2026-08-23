#!/usr/bin/env python3
"""Build and verify the fail-closed P35-E certification manifest.

The gate is intentionally a consumer of evidence rather than a replacement
for the live matrix.  It records each command it runs, binds all receipts to
one Git SHA and lockfile digest, verifies behavior-golden baseline hashes, and
stores only content-addressed metadata under ``.fleet-evidence``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPO_ROOT))

validate_release = importlib.import_module("scripts.validate_release")

BASELINE_SCHEMA = "fleet.behavior-golden-baseline/v1"
MANIFEST_SCHEMA = "fleet.p35e-certification-manifest/v1"
DEFAULT_BASELINE = REPO_ROOT / "tests" / "fixtures" / "p35e-golden-baseline.json"
DEFAULT_LIVE_MANIFEST = REPO_ROOT / ".fleet-evidence" / "receipts" / "p35d-live-certification-matrix.json"
DEFAULT_OUTPUT = REPO_ROOT / ".fleet-evidence" / "receipts" / "p35e-certification-manifest.json"
DEFAULT_SERVICES = REPO_ROOT / ".factory" / "mission-services.yaml"

REQUIRED_GATES = (
    "deterministic",
    "security",
    "release-metadata",
    "package-build",
    "package-install-matrix",
    "whitespace",
    "live-daytona",
    "service-isolation",
)
OBSOLETE_RELEASE_TEXT = (
    "unreleased dspy",
    "unreleased DSPy",
    "gepa git override",
    "git source disappeared",
    "unsuitable for pypi",
    "unsuitable for PyPI",
    "git+https://",
    "OptimizeAnythingConfig",
    "optimize_anything",
)
_HEX = re.compile(r"^[0-9a-f]+$")


class CertificationGateError(ValueError):
    """Raised when certification evidence is absent, stale, or unsafe."""


@dataclass(frozen=True)
class GateResult:
    """Metadata-only result for one serial gate command."""

    name: str
    lane: str
    command: tuple[str, ...]
    returncode: int
    output_sha256: str
    output_clean: bool

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and self.output_clean


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_digest(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    return _sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def manifest_digest(manifest: dict[str, Any]) -> str:
    """Return the content digest used as the manifest's address."""
    return _json_digest(manifest)


def _read_json(path: Path, *, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CertificationGateError(f"{description} is unreadable") from exc
    if not isinstance(value, dict):
        raise CertificationGateError(f"{description} must be an object")
    return value


def _is_hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and bool(_HEX.fullmatch(value))


def _current_identity() -> tuple[str, str]:
    try:
        sha = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ("git", "status", "--porcelain", "--untracked-files=no"),
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        lockfile_sha256 = _sha256((REPO_ROOT / "uv.lock").read_bytes())
    except (OSError, subprocess.SubprocessError) as exc:
        raise CertificationGateError("could not determine candidate identity") from exc
    if not _is_hex(sha, 40):
        raise CertificationGateError("candidate SHA is invalid")
    if status:
        raise CertificationGateError("tracked worktree is not clean")
    return sha, lockfile_sha256


def _baseline_files(baseline: dict[str, Any]) -> list[dict[str, str]]:
    if baseline.get("schema") != BASELINE_SCHEMA:
        raise CertificationGateError("golden baseline schema is invalid")
    if not _is_hex(baseline.get("baseline_commit"), 40):
        raise CertificationGateError("golden baseline has no valid baseline commit")
    files = baseline.get("files")
    if not isinstance(files, list) or not files:
        raise CertificationGateError("golden baseline has no files")
    decision = baseline.get("human_decision")
    if decision is not None and (
        not isinstance(decision, dict)
        or not all(isinstance(decision.get(key), str) and decision[key].strip() for key in ("id", "rationale"))
    ):
        raise CertificationGateError("golden baseline human decision is incomplete")
    result: list[dict[str, str]] = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not _is_hex(item.get("sha256"), 64):
            raise CertificationGateError("golden baseline contains an invalid file digest")
        result.append({"path": item["path"], "sha256": item["sha256"]})
    return result


def verify_golden_baseline(baseline_path: Path, repo_root: Path) -> dict[str, Any]:
    """Verify every behavior-golden file against its recorded baseline digest."""
    baseline = _read_json(baseline_path, description="golden baseline")
    files = _baseline_files(baseline)
    checked: list[dict[str, str]] = []
    for item in files:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise CertificationGateError("golden baseline path escapes repository")
        target = repo_root / relative
        try:
            actual = _sha256(target.read_bytes())
        except OSError as exc:
            raise CertificationGateError(f"golden file is missing: {item['path']}") from exc
        if actual != item["sha256"]:
            decision = baseline.get("human_decision")
            if not decision:
                raise CertificationGateError(f"golden file digest changed: {item['path']}")
            checked.append({"path": item["path"], "sha256": actual, "baseline_sha256": item["sha256"]})
            continue
        checked.append({"path": item["path"], "sha256": actual, "baseline_sha256": item["sha256"]})
    return {
        "schema": baseline["schema"],
        "baseline_commit": baseline["baseline_commit"],
        "files": checked,
        "unchanged": all(item["sha256"] == item["baseline_sha256"] for item in checked),
        "human_decision": baseline.get("human_decision"),
    }


def _live_identity(manifest_path: Path, *, sha: str, lockfile_sha256: str) -> dict[str, Any]:
    live = _read_json(manifest_path, description="live certification manifest")
    if not str(live.get("schema", "")).startswith("fleet.p35d-"):
        raise CertificationGateError("live certification manifest schema is not P35-D")
    if live.get("passed") is not True:
        raise CertificationGateError("live certification manifest did not pass")
    live_digest = live.get("manifest_sha256")
    if not isinstance(live_digest, str):
        raise CertificationGateError("live certification manifest has no content digest")
    unsigned_live = {key: value for key, value in live.items() if key != "manifest_sha256"}
    if live_digest != _sha256(json.dumps(unsigned_live, sort_keys=True, separators=(",", ":")).encode("utf-8")):
        raise CertificationGateError("live certification manifest self-hash is invalid")
    candidate = live.get("candidate")
    if not isinstance(candidate, dict):
        raise CertificationGateError("live certification manifest has no candidate identity")
    if candidate.get("sha") != sha:
        raise CertificationGateError("live certification manifest SHA does not match candidate")
    if candidate.get("lockfile_sha256") != lockfile_sha256:
        raise CertificationGateError("live certification manifest lockfile SHA does not match candidate")
    if candidate.get("dspy") != "3.3.1":
        raise CertificationGateError("live certification manifest is not certified for DSPy 3.3.1")
    lanes = live.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        raise CertificationGateError("live certification manifest has no lanes")
    for lane_name, lane in lanes.items():
        if not isinstance(lane, dict) or lane.get("passed") is not True:
            raise CertificationGateError(f"live lane did not pass: {lane_name}")
        cleanup = lane.get("cleanup")
        if (
            not isinstance(cleanup, dict)
            or cleanup.get("confirmed_absent") is not True
            or cleanup.get("admission_restored") is not True
        ):
            raise CertificationGateError(f"live lane cleanup is incomplete: {lane_name}")
    return {
        "path": str(manifest_path),
        "schema": live["schema"],
        "sha": candidate["sha"],
        "lockfile_sha256": candidate["lockfile_sha256"],
        "lanes": sorted(str(name) for name in lanes),
        "manifest_sha256": live.get("manifest_sha256"),
    }


def _service_isolation_from_path(path: Path) -> dict[str, Any]:
    result = validate_release.validate_service_isolation(path)
    if result.get("passed") is not True:
        raise CertificationGateError("validator service isolation is not approved")
    return result


def build_certification_manifest(
    *,
    sha: str,
    lockfile_sha256: str,
    baseline_path: Path,
    repo_root: Path,
    live_manifest_path: Path,
    gate_results: list[GateResult],
    artifacts: list[dict[str, Any]],
    service_isolation: dict[str, Any],
) -> dict[str, Any]:
    """Validate inputs and build one content-addressed P35-E manifest."""
    if not _is_hex(sha, 40) or not _is_hex(lockfile_sha256, 64):
        raise CertificationGateError("candidate identity is invalid")
    golden = verify_golden_baseline(baseline_path, repo_root)
    live = _live_identity(live_manifest_path, sha=sha, lockfile_sha256=lockfile_sha256)
    result_names = {result.name for result in gate_results}
    if len(result_names) != len(gate_results):
        raise CertificationGateError("certification gate names are duplicated")
    missing = sorted(set(REQUIRED_GATES) - result_names)
    if missing:
        raise CertificationGateError("missing certification gates: " + ", ".join(missing))
    serialized_gates = [asdict(result) | {"passed": result.passed} for result in gate_results]
    if any(result["passed"] is not True for result in serialized_gates):
        raise CertificationGateError("one or more certification gates failed")
    if service_isolation.get("passed") is not True:
        raise CertificationGateError("validator service isolation evidence failed")
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {
            "sha": sha,
            "lockfile_sha256": lockfile_sha256,
            "dspy": "3.3.1",
            "tracked_tree_clean": True,
        },
        "gates": serialized_gates,
        "golden_baseline": golden,
        "live": live,
        "artifacts": artifacts,
        "service_isolation": service_isolation,
        "claims": {
            "deterministic": True,
            "live": True,
            "package": True,
            "release": True,
            "cleanup": all(
                lane.get("cleanup", {}).get("confirmed_absent") is True
                for lane in _read_json(live_manifest_path, description="live certification manifest")
                .get("lanes", {})
                .values()
                if isinstance(lane, dict)
            ),
        },
        "passed": True,
    }
    manifest["manifest_sha256"] = manifest_digest(manifest)
    return manifest


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Atomically write a certification manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_manifest(path: Path, *, expected_sha: str | None = None) -> dict[str, Any]:
    """Fail closed when a P35-E manifest is absent, stale, or incomplete."""
    manifest = _read_json(path, description="P35-E certification manifest")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise CertificationGateError("P35-E certification manifest schema is invalid")
    if manifest.get("manifest_sha256") != manifest_digest(manifest):
        raise CertificationGateError("P35-E certification manifest self-hash is invalid")
    candidate = manifest.get("candidate")
    if (
        not isinstance(candidate, dict)
        or not _is_hex(candidate.get("sha"), 40)
        or not _is_hex(candidate.get("lockfile_sha256"), 64)
        or candidate.get("dspy") != "3.3.1"
        or candidate.get("tracked_tree_clean") is not True
    ):
        raise CertificationGateError("P35-E certification manifest candidate is invalid")
    if expected_sha is not None and candidate["sha"] != expected_sha:
        raise CertificationGateError("P35-E certification manifest SHA does not match candidate")
    if manifest.get("passed") is not True:
        raise CertificationGateError("P35-E certification manifest is not sealed")
    golden = manifest.get("golden_baseline")
    if not isinstance(golden, dict):
        raise CertificationGateError("P35-E golden baseline evidence is missing")
    if golden.get("unchanged") is not True and not golden.get("human_decision"):
        raise CertificationGateError("P35-E behavior goldens are not unchanged")
    gates = manifest.get("gates")
    if not isinstance(gates, list) or {gate.get("name") for gate in gates} != set(REQUIRED_GATES):
        raise CertificationGateError("P35-E certification manifest omits required gates")
    if any(gate.get("passed") is not True for gate in gates):
        raise CertificationGateError("P35-E certification manifest contains a failed gate")
    service_isolation = manifest.get("service_isolation")
    if not isinstance(service_isolation, dict) or service_isolation.get("passed") is not True:
        raise CertificationGateError("P35-E service isolation evidence is not sealed")
    live = manifest.get("live")
    if not isinstance(live, dict) or not live.get("lanes"):
        raise CertificationGateError("P35-E live evidence is missing")
    return manifest


def verify_command(args: argparse.Namespace) -> int:
    """Verify a manifest against the current clean candidate by default."""
    expected_sha = args.sha
    if expected_sha is None:
        expected_sha, _ = _current_identity()
    verify_manifest(args.manifest.resolve(), expected_sha=expected_sha)
    print(f"P35-E certification verified: {args.manifest}")
    return 0


def _run_gate(name: str, lane: str, command: tuple[str, ...], *, timeout: int) -> GateResult:
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("FLEET_") or key.endswith(("_API_KEY", "_TOKEN")):
            env.pop(key, None)
    process = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (process.stdout + process.stderr).encode("utf-8", errors="replace")
    output_text = output.decode("utf-8", errors="replace")
    output_clean = not any(phrase in output_text for phrase in OBSOLETE_RELEASE_TEXT)
    return GateResult(name, lane, command, process.returncode, _sha256(output), output_clean)


def run_gate(args: argparse.Namespace) -> int:
    """Run serial deterministic/package gates and consume P35-D evidence."""
    sha, lockfile_sha256 = _current_identity()
    baseline_path = args.baseline.resolve()
    live_path = args.live_manifest.resolve()
    service_result = _service_isolation_from_path(args.services.resolve())
    verify_golden_baseline(baseline_path, REPO_ROOT)
    live = _live_identity(live_path, sha=sha, lockfile_sha256=lockfile_sha256)
    commands = (
        ("deterministic", "deterministic", ("make", "check", "PYTEST_XDIST_MAX_WORKERS=2")),
        ("security", "security", ("make", "check-security")),
        ("release-metadata", "package", ("make", "check-release")),
        ("package-build", "package", ("make", "build-release")),
        (
            "package-install-matrix",
            "package",
            ("uv", "run", "pytest", "tests/unit/backend/packaging", "-q", "-n", "0"),
        ),
        ("whitespace", "release", ("git", "diff", "--check")),
    )
    results = [_run_gate(name, lane, command, timeout=args.timeout_seconds) for name, lane, command in commands]
    results.append(
        GateResult(
            "live-daytona",
            "live",
            ("receipt", str(live_path)),
            0,
            str(live.get("manifest_sha256") or _sha256(live_path.read_bytes())),
            True,
        )
    )
    results.append(
        GateResult(
            "service-isolation",
            "cross",
            ("services.yaml", str(args.services)),
            0,
            _sha256(args.services.read_bytes()),
            True,
        )
    )
    artifact_manifest_path = args.dist / "artifact-manifest.json"
    if not artifact_manifest_path.is_file():
        raise CertificationGateError("package build did not emit an artifact manifest")
    artifact_manifest = validate_release.verify_artifact_manifest(artifact_manifest_path, args.dist)
    artifacts = artifact_manifest["artifacts"]
    manifest = build_certification_manifest(
        sha=sha,
        lockfile_sha256=lockfile_sha256,
        baseline_path=baseline_path,
        repo_root=REPO_ROOT,
        live_manifest_path=live_path,
        gate_results=results,
        artifacts=artifacts,
        service_isolation=service_result,
    )
    write_manifest(args.output.resolve(), manifest)
    print(f"P35-E certification sealed: {args.output} ({manifest['manifest_sha256']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    run_parser.add_argument("--live-manifest", type=Path, default=DEFAULT_LIVE_MANIFEST)
    run_parser.add_argument("--services", type=Path, default=DEFAULT_SERVICES)
    run_parser.add_argument("--dist", type=Path, default=REPO_ROOT / "dist")
    run_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    run_parser.add_argument("--timeout-seconds", type=int, default=1800)
    run_parser.set_defaults(func=run_gate)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT)
    verify_parser.add_argument("--sha")
    verify_parser.set_defaults(func=verify_command)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (CertificationGateError, OSError, subprocess.SubprocessError) as exc:
        print(f"P35-E certification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
