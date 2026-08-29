#!/usr/bin/env python3
"""Build and verify the fail-closed P35-E certification manifest.

The gate is intentionally a consumer of evidence rather than a replacement
for the live matrix.  It records each command it runs, binds all receipts to
one Git SHA and lockfile digest, verifies behavior-golden baseline hashes, and
stores only content-addressed metadata under ``.fleet-evidence``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPO_ROOT))

validate_release = importlib.import_module("scripts.validate_release")
p53_certification = importlib.import_module("scripts.p53_certification")
p35d_certification = importlib.import_module("scripts.live_p35d_certification")

BASELINE_SCHEMA = "fleet.behavior-golden-baseline/v1"
MANIFEST_SCHEMA = "fleet.p35e-certification-manifest/v1"
DEFAULT_BASELINE = REPO_ROOT / "tests" / "fixtures" / "p35e-golden-baseline.json"
DEFAULT_LIVE_MANIFEST = REPO_ROOT / ".fleet-evidence" / "receipts" / "p35d-live-certification-matrix.json"
DEFAULT_P53_LIVE_MANIFEST = REPO_ROOT / ".fleet-evidence" / "receipts" / "p53-live-session-certification.json"
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
    "live-session",
    "service-isolation",
)
REQUIRED_LIVE_LANES = (
    "runtime-version",
    "root-direct",
    "root-child",
    "root-batch",
    "stdout-reasoning",
    "cancel",
    "timeout",
    "workspace-memory",
    "attachment-artifact",
    "fault-logs",
)

REQUIRED_CERTIFICATION_CLAIMS = {
    "deterministic": True,
    "live": True,
    "live_session": True,
    "package": True,
    "release": True,
    "cleanup": True,
}
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


_MAX_JSON_BYTES = 8 * 1024 * 1024


def _read_json_bytes(path: Path, *, description: str) -> tuple[dict[str, Any], bytes]:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_JSON_BYTES:
            raise CertificationGateError(f"{description} is unsafe")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            data = handle.read(_MAX_JSON_BYTES + 1)
        if len(data) > _MAX_JSON_BYTES:
            raise CertificationGateError(f"{description} is too large")
        value = json.loads(data)
    except CertificationGateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CertificationGateError(f"{description} is unreadable") from exc
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
    if not isinstance(value, dict):
        raise CertificationGateError(f"{description} must be an object")
    return value, data


def _read_json(path: Path, *, description: str) -> dict[str, Any]:
    return _read_json_bytes(path, description=description)[0]


_MAX_REGULAR_BYTES = 64 * 1024 * 1024


def _read_regular_bytes(path: Path, *, description: str) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_REGULAR_BYTES:
            raise CertificationGateError(f"{description} is unsafe")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            data = handle.read(_MAX_REGULAR_BYTES + 1)
        if len(data) > _MAX_REGULAR_BYTES:
            raise CertificationGateError(f"{description} is too large")
        return data
    except CertificationGateError:
        raise
    except OSError as exc:
        raise CertificationGateError(f"{description} is unreadable") from exc
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _is_hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and bool(_HEX.fullmatch(value))


def _require_certified_provider_configuration() -> None:
    """Require the exact Daytona provider identity used by certification."""
    load_dotenv(REPO_ROOT / ".env", override=False)
    if os.environ.get("FLEET_DAYTONA_SNAPSHOT") != p53_certification.CERTIFIED_DAYTONA_SNAPSHOT:
        raise CertificationGateError("certification requires the authoritative Daytona v5 snapshot")
    if os.environ.get("DAYTONA_TARGET") != p53_certification.CERTIFIED_DAYTONA_TARGET:
        raise CertificationGateError("certification requires unquoted DAYTONA_TARGET=us")


def _current_identity() -> tuple[str, str]:
    try:
        installed_dspy = importlib.metadata.version("dspy")
        import dspy

        module_dspy = getattr(dspy, "__version__", None)
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
    except (OSError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError) as exc:
        raise CertificationGateError("could not determine candidate identity") from exc
    if not _is_hex(sha, 40):
        raise CertificationGateError("candidate SHA is invalid")
    if installed_dspy != "3.3.1" or module_dspy != "3.3.1":
        raise CertificationGateError("installed DSPy is not the certified 3.3.1 release")
    unexpected = [
        line for line in status.splitlines() if line and not (line.startswith("?? .factory/") or line == "?? .factory")
    ]
    if unexpected:
        raise CertificationGateError("tracked worktree is not clean")
    return sha, lockfile_sha256


def _safe_repo_file(path: Path, repo_root: Path, *, description: str) -> Path:
    """Resolve one certification input without allowing symlink escape."""
    lexical = Path(os.path.abspath(path.expanduser()))
    root = Path(os.path.abspath(repo_root.expanduser()))
    resolved_root = root.resolve()
    resolved = lexical.resolve()
    if (
        root != resolved_root
        or not resolved.is_relative_to(resolved_root)
        or lexical != resolved
        or not lexical.is_file()
    ):
        raise CertificationGateError(f"{description} path is unsafe")
    return resolved


def _safe_exact_path(
    path: Path,
    expected: Path,
    *,
    description: str,
    directory: bool = False,
    allow_missing: bool = False,
) -> Path:
    """Accept only one canonical path, rejecting symlink aliases."""
    lexical = Path(os.path.abspath(path.expanduser()))
    canonical = Path(os.path.abspath(expected.expanduser()))
    if lexical != canonical or lexical != canonical.resolve():
        raise CertificationGateError(f"{description} path is unsafe")
    if allow_missing and not lexical.exists():
        return lexical
    if directory:
        valid = lexical.is_dir() and not lexical.is_symlink()
    else:
        valid = lexical.is_file() and not lexical.is_symlink()
    if not valid:
        raise CertificationGateError(f"{description} path is unsafe")
    return lexical


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
    baseline_path = _safe_repo_file(baseline_path, repo_root, description="golden baseline")
    baseline = _read_json(baseline_path, description="golden baseline")
    files = _baseline_files(baseline)
    checked: list[dict[str, str]] = []
    for item in files:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise CertificationGateError("golden baseline path escapes repository")
        target = _safe_repo_file(repo_root / relative, repo_root, description="golden file")
        try:
            actual = _sha256(_read_regular_bytes(target, description="golden file"))
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


def _live_identity(
    manifest_path: Path,
    *,
    sha: str,
    lockfile_sha256: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    evidence_repo = repo_root or REPO_ROOT
    manifest_path = _safe_repo_file(manifest_path, evidence_repo, description="live certification manifest")
    live = _read_json(manifest_path, description="live certification manifest")
    if live.get("schema") != "fleet.p35d-live-certification-matrix/v1":
        raise CertificationGateError("live certification manifest schema is not P35-D")
    if live.get("passed") is not True:
        raise CertificationGateError("live certification manifest did not pass")
    live_run_id = live.get("run_id")
    if not _is_hex(live_run_id, 32):
        raise CertificationGateError("live certification manifest run ID is invalid")
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
    if (
        candidate.get("daytona_snapshot") != p53_certification.CERTIFIED_DAYTONA_SNAPSHOT
        or candidate.get("daytona_target") != p53_certification.CERTIFIED_DAYTONA_TARGET
    ):
        raise CertificationGateError("live certification manifest Daytona provider identity is stale")
    if candidate.get("tracked_tree_clean") is not True:
        raise CertificationGateError("live certification manifest candidate is not a clean tree")
    runtime = live.get("runtime")
    if (
        not isinstance(runtime, dict)
        or runtime.get("metadata") != "3.3.1"
        or runtime.get("module") != "3.3.1"
        or not isinstance(runtime.get("python"), str)
        or re.fullmatch(r"3\.13\.\d+", runtime["python"]) is None
        or runtime.get("doctor_identity") is not True
        or runtime.get("daytona_snapshot") != p53_certification.CERTIFIED_DAYTONA_SNAPSHOT
        or runtime.get("daytona_target") != p53_certification.CERTIFIED_DAYTONA_TARGET
    ):
        raise CertificationGateError("live certification runtime identity is invalid")
    scans = live.get("scans")
    host_logs = scans.get("host_logs") if isinstance(scans, dict) else None
    if (
        not isinstance(scans, dict)
        or set(scans) != {"host_logs"}
        or not isinstance(host_logs, dict)
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
        raise CertificationGateError("live certification host-log scan is invalid")
    cleanup = live.get("cleanup")
    if (
        not isinstance(cleanup, dict)
        or cleanup.get("confirmed_absent") is not True
        or cleanup.get("admission_restored") is not True
    ):
        raise CertificationGateError("live certification cleanup is incomplete")
    claims = live.get("claims")
    expected_claims = {claim: list(lanes) for claim, lanes in p35d_certification.CLAIM_LANES.items()}
    if claims != expected_claims:
        raise CertificationGateError("live certification claims are invalid")
    lanes = live.get("lanes")
    if not isinstance(lanes, dict) or set(lanes) != set(REQUIRED_LIVE_LANES):
        raise CertificationGateError("live certification manifest has incomplete lane coverage")
    for lane_name, lane in lanes.items():
        if not isinstance(lane, dict) or lane.get("passed") is not True:
            raise CertificationGateError(f"live lane did not pass: {lane_name}")
        if lane.get("run_id") != live_run_id:
            raise CertificationGateError(f"live lane run ID is stale: {lane_name}")
        expected_schema = p35d_certification.REQUIRED_LIVE_SCHEMAS.get(lane_name)
        if expected_schema is None or lane.get("schema") != expected_schema:
            raise CertificationGateError(f"live lane schema is invalid: {lane_name}")
        assertions = lane.get("assertions")
        expected_assertions = p35d_certification.REQUIRED_LIVE_ASSERTIONS.get(lane_name, {})
        if not isinstance(assertions, dict) or any(
            assertions.get(key) != expected for key, expected in expected_assertions.items()
        ):
            raise CertificationGateError(f"live lane assertions are incomplete: {lane_name}")
        lane_candidate = lane.get("candidate")
        receipt_path_value = lane.get("receipt_path")
        receipt_sha256 = lane.get("receipt_sha256")
        expected_receipt_path = Path(".fleet-evidence/receipts/p35d") / f"{lane_name}.json"
        if receipt_path_value != str(expected_receipt_path) or not _is_hex(receipt_sha256, 64):
            raise CertificationGateError(f"live lane receipt reference is invalid: {lane_name}")
        try:
            raw_receipt_path = _safe_repo_file(
                evidence_repo / receipt_path_value,
                evidence_repo,
                description="live lane receipt",
            )
            raw_receipt, raw_receipt_bytes = _read_json_bytes(
                raw_receipt_path, description=f"live lane receipt {lane_name}"
            )
            if _sha256(raw_receipt_bytes) != receipt_sha256:
                raise CertificationGateError(f"live lane receipt reference is stale: {lane_name}")
            raw_receipt["receipt_path"] = str(expected_receipt_path)
            raw_normalized = p35d_certification._normalized_lane(raw_receipt, name=lane_name)
            expected_normalized = dict(lane)
            expected_normalized.pop("receipt_sha256", None)
            raw_normalized.pop("receipt_sha256", None)
            if raw_normalized != expected_normalized:
                raise CertificationGateError(f"live lane receipt claims are stale: {lane_name}")
        except (p35d_certification.CertificationError, OSError) as exc:
            if isinstance(exc, CertificationGateError):
                raise
            raise CertificationGateError(f"live lane receipt is invalid: {lane_name}") from exc
        if (
            not isinstance(lane_candidate, dict)
            or lane_candidate.get("sha") != sha
            or lane_candidate.get("lockfile_sha256") != lockfile_sha256
            or lane_candidate.get("dspy") != "3.3.1"
            or lane_candidate.get("daytona_snapshot") != p53_certification.CERTIFIED_DAYTONA_SNAPSHOT
            or lane_candidate.get("daytona_target") != p53_certification.CERTIFIED_DAYTONA_TARGET
            or lane_candidate.get("tracked_tree_clean") is not True
        ):
            raise CertificationGateError(f"live lane candidate identity is stale: {lane_name}")
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
        "run_id": live_run_id,
        "cleanup": {"confirmed_absent": True, "admission_restored": True},
    }


_GATE_LANES = {
    "deterministic": "deterministic",
    "security": "security",
    "release-metadata": "package",
    "package-build": "package",
    "package-install-matrix": "package",
    "whitespace": "release",
    "live-daytona": "live",
    "live-session": "live",
    "service-isolation": "cross",
}
_FIXED_GATE_COMMANDS: dict[str, tuple[str, ...]] = {
    "deterministic": ("make", "check", "PYTEST_XDIST_MAX_WORKERS=2"),
    "security": ("make", "check-security"),
    "release-metadata": ("make", "check-release"),
    "package-build": ("make", "build-release"),
    "whitespace": ("git", "diff", "--check"),
}


def _validate_gate_records(gates: object) -> list[dict[str, Any]]:
    if not isinstance(gates, list) or len(gates) != len(REQUIRED_GATES):
        raise CertificationGateError("P35-E certification gates are incomplete")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for gate in gates:
        if not isinstance(gate, dict):
            raise CertificationGateError("P35-E certification gate entry is malformed")
        name = gate.get("name")
        lane = gate.get("lane")
        command = gate.get("command")
        if (
            not isinstance(name, str)
            or name not in _GATE_LANES
            or name in seen
            or lane != _GATE_LANES[name]
            or not isinstance(command, (list, tuple))
            or not command
            or any(not isinstance(part, str) or not part for part in command)
            or type(gate.get("returncode")) is not int
            or gate["returncode"] != 0
            or not _is_hex(gate.get("output_sha256"), 64)
            or gate.get("output_clean") is not True
            or gate.get("passed") is not True
        ):
            raise CertificationGateError("P35-E certification gate entry is invalid")
        expected = _FIXED_GATE_COMMANDS.get(name)
        if expected is not None and tuple(command) != expected:
            raise CertificationGateError(f"P35-E certification gate command is invalid: {name}")
        if name == "package-install-matrix" and (
            len(command) < 6
            or command[0] != "env"
            or not command[1].startswith("FLEET_RELEASE_DIST=")
            or tuple(command[2:6]) != ("uv", "run", "pytest", "tests/unit/backend/packaging")
            or tuple(command[-3:]) != ("-q", "-n", "0")
        ):
            raise CertificationGateError(f"P35-E certification gate command is invalid: {name}")
        if name in {"live-daytona", "live-session"} and command[0] != "receipt":
            raise CertificationGateError(f"P35-E certification gate command is invalid: {name}")
        if name == "service-isolation" and command[0] != "services.yaml":
            raise CertificationGateError(f"P35-E certification gate command is invalid: {name}")
        seen.add(name)
        normalized.append(dict(gate))
    if seen != set(REQUIRED_GATES):
        raise CertificationGateError("P35-E certification gates are incomplete")
    return normalized


def _safe_dist_dir(path: Path, *, repo_root: Path = REPO_ROOT) -> Path:
    """Resolve a distribution directory without following a directory alias."""
    lexical = Path(os.path.abspath(path.expanduser()))
    root = Path(os.path.abspath(repo_root.expanduser()))
    resolved_root = root.resolve()
    resolved = lexical.resolve()
    if root != resolved_root or lexical != resolved or not resolved.is_relative_to(resolved_root):
        raise CertificationGateError("release distribution path is unsafe")
    if not lexical.is_dir() or lexical.is_symlink():
        raise CertificationGateError("release distribution path is unsafe")
    return lexical


def _read_artifact_bytes(path: Path, *, description: str) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_REGULAR_BYTES:
            raise CertificationGateError(f"{description} is unsafe")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            data = handle.read(_MAX_REGULAR_BYTES + 1)
        if len(data) > _MAX_REGULAR_BYTES:
            raise CertificationGateError(f"{description} is too large")
        return data
    except CertificationGateError:
        raise
    except OSError as exc:
        raise CertificationGateError(f"{description} is unreadable") from exc
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _verify_artifact_bytes(dist: Path, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bind every sealed artifact entry to one no-follow byte snapshot."""
    dist = _safe_dist_dir(dist, repo_root=REPO_ROOT)
    actual: list[dict[str, Any]] = []
    for artifact in artifacts:
        filename = str(artifact["filename"])
        target = dist / filename
        data = _read_artifact_bytes(target, description=f"release artifact {filename}")
        if len(data) != artifact["size"] or _sha256(data) != artifact["sha256"]:
            raise CertificationGateError(f"P35-E release artifact bytes do not match: {filename}")
        actual.append(dict(artifact))
    names = {str(item["filename"]) for item in artifacts}
    try:
        entries = tuple(dist.iterdir())
    except OSError as exc:
        raise CertificationGateError("release distribution is unreadable") from exc
    expected_kind_files = {
        entry.name
        for entry in entries
        if entry.name.startswith("fleet_rlm-") and (entry.name.endswith(".whl") or entry.name.endswith(".tar.gz"))
    }
    if expected_kind_files != names:
        raise CertificationGateError("release distribution contains unexpected artifacts")
    return sorted(actual, key=lambda item: str(item["filename"]))


def _validate_artifact_evidence(artifacts: object) -> list[dict[str, Any]]:
    """Validate immutable wheel/sdist evidence embedded in a certification."""
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise CertificationGateError("P35-E artifact evidence must contain one wheel and one sdist")
    normalized: list[dict[str, Any]] = []
    kinds: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise CertificationGateError("P35-E artifact evidence entry is invalid")
        filename = artifact.get("filename")
        kind = artifact.get("kind")
        sha256 = artifact.get("sha256")
        version = artifact.get("version")
        if (
            not isinstance(filename, str)
            or filename != Path(filename).name
            or "\\" in filename
            or ".." in Path(filename).parts
            or kind not in {"wheel", "sdist"}
            or not _is_hex(sha256, 64)
            or type(artifact.get("size")) is not int
            or artifact["size"] <= 0
            or not isinstance(version, str)
            or not version
        ):
            raise CertificationGateError("P35-E artifact evidence entry is incomplete")
        suffix = "-py3-none-any.whl" if kind == "wheel" else ".tar.gz"
        if re.fullmatch(rf"fleet_rlm-[0-9]+\.[0-9]+\.[0-9]+{re.escape(suffix)}", filename) is None:
            raise CertificationGateError("P35-E artifact filename is unsafe")
        expected_version = filename.removeprefix("fleet_rlm-").removesuffix(suffix)
        if version != expected_version:
            raise CertificationGateError("P35-E artifact version does not match its filename")
        if kind in kinds:
            raise CertificationGateError("P35-E artifact evidence contains duplicate kinds")
        kinds.add(kind)
        normalized.append(dict(artifact))
    if kinds != {"wheel", "sdist"}:
        raise CertificationGateError("P35-E artifact evidence is missing a wheel or sdist")
    return normalized


def _verify_manifest_golden_evidence(golden: dict[str, Any], repo_root: Path) -> None:
    """Verify the file bytes represented by a sealed golden evidence block."""
    if golden.get("schema") != BASELINE_SCHEMA or not _is_hex(golden.get("baseline_commit"), 40):
        raise CertificationGateError("P35-E golden baseline evidence is malformed")
    files = golden.get("files")
    if not isinstance(files, list) or not files:
        raise CertificationGateError("P35-E golden baseline has no file evidence")
    unchanged = True
    for item in files:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not _is_hex(item.get("sha256"), 64)
            or not _is_hex(item.get("baseline_sha256"), 64)
        ):
            raise CertificationGateError("P35-E golden file evidence is malformed")
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise CertificationGateError("P35-E golden evidence path escapes repository")
        try:
            target = _safe_repo_file(repo_root / relative, repo_root, description="golden evidence file")
            actual = _sha256(_read_regular_bytes(target, description="golden file"))
        except OSError as exc:
            raise CertificationGateError(f"golden file is missing: {item['path']}") from exc
        if actual != item["sha256"]:
            raise CertificationGateError(f"golden file evidence is stale: {item['path']}")
        unchanged = unchanged and item["sha256"] == item["baseline_sha256"]
    decision = golden.get("human_decision")
    if not unchanged and (
        not isinstance(decision, dict)
        or not all(isinstance(decision.get(key), str) and decision[key].strip() for key in ("id", "rationale"))
    ):
        raise CertificationGateError("P35-E golden drift has no human decision")
    if golden.get("unchanged") is not unchanged:
        raise CertificationGateError("P35-E golden unchanged claim is invalid")


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
    p53_live_manifest_path: Path,
) -> dict[str, Any]:
    """Validate inputs and build one content-addressed P35-E manifest."""
    if not _is_hex(sha, 40) or not _is_hex(lockfile_sha256, 64):
        raise CertificationGateError("candidate identity is invalid")
    try:
        installed_dspy = importlib.metadata.version("dspy")
    except importlib.metadata.PackageNotFoundError as exc:
        raise CertificationGateError("certified DSPy is not installed") from exc
    if installed_dspy != "3.3.1":
        raise CertificationGateError("installed DSPy is not the certified 3.3.1 release")
    golden = verify_golden_baseline(baseline_path, repo_root)
    live = _live_identity(live_manifest_path, sha=sha, lockfile_sha256=lockfile_sha256, repo_root=repo_root)
    try:
        p53_live = p53_certification.verify_manifest(
            p53_live_manifest_path,
            expected_sha=sha,
            expected_lockfile_sha256=lockfile_sha256,
        )
    except p53_certification.P53CertificationError as exc:
        raise CertificationGateError("P53 live Session evidence is invalid") from exc
    result_names = {result.name for result in gate_results}
    if len(result_names) != len(gate_results):
        raise CertificationGateError("certification gate names are duplicated")
    missing = sorted(set(REQUIRED_GATES) - result_names)
    if missing:
        raise CertificationGateError("missing certification gates: " + ", ".join(missing))
    serialized_gates = [asdict(result) | {"passed": result.passed} for result in gate_results]
    _validate_gate_records(serialized_gates)
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
            "daytona_snapshot": p53_certification.CERTIFIED_DAYTONA_SNAPSHOT,
            "daytona_target": p53_certification.CERTIFIED_DAYTONA_TARGET,
            "tracked_tree_clean": True,
        },
        "gates": serialized_gates,
        "golden_baseline": golden,
        "live": live,
        "p53_live": {
            "path": str(p53_live_manifest_path),
            "manifest_sha256": p53_live["manifest_sha256"],
            "run_id": p53_live["run_id"],
        },
        "artifacts": _validate_artifact_evidence(artifacts),
        "service_isolation": service_isolation,
        "claims": dict(REQUIRED_CERTIFICATION_CLAIMS),
        "passed": True,
    }
    manifest["manifest_sha256"] = manifest_digest(manifest)
    return manifest


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Atomically write a certification manifest without following symlinks."""
    target = _safe_exact_path(
        path,
        DEFAULT_OUTPUT,
        description="certification manifest output",
        allow_missing=True,
    )
    parent = target.parent
    if parent.resolve() != parent:
        raise CertificationGateError("certification manifest output path is unsafe")
    parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def verify_manifest(
    path: Path,
    *,
    expected_sha: str | None = None,
    expected_lockfile_sha256: str | None = None,
    require_p53: bool = True,
    dist: Path | None = None,
) -> dict[str, Any]:
    """Fail closed when a P35-E manifest is absent, stale, or incomplete.

    The nested P35-D receipt is re-read instead of trusting the identity summary
    copied into the outer manifest.  This keeps verification content-addressed
    when a receipt is replaced after the outer manifest was sealed.
    """
    path = _safe_repo_file(path, REPO_ROOT, description="P35-E certification manifest")
    manifest = _read_json(path, description="P35-E certification manifest")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise CertificationGateError("P35-E certification manifest schema is invalid")
    if manifest.get("manifest_sha256") != manifest_digest(manifest):
        raise CertificationGateError("P35-E certification manifest self-hash is invalid")
    try:
        installed_dspy = importlib.metadata.version("dspy")
    except importlib.metadata.PackageNotFoundError as exc:
        raise CertificationGateError("certified DSPy is not installed") from exc
    if installed_dspy != "3.3.1":
        raise CertificationGateError("installed DSPy is not the certified 3.3.1 release")
    candidate = manifest.get("candidate")
    if (
        not isinstance(candidate, dict)
        or not _is_hex(candidate.get("sha"), 40)
        or not _is_hex(candidate.get("lockfile_sha256"), 64)
        or candidate.get("dspy") != "3.3.1"
        or candidate.get("daytona_snapshot") != p53_certification.CERTIFIED_DAYTONA_SNAPSHOT
        or candidate.get("daytona_target") != p53_certification.CERTIFIED_DAYTONA_TARGET
        or candidate.get("tracked_tree_clean") is not True
    ):
        raise CertificationGateError("P35-E certification manifest candidate is invalid")
    if expected_sha is not None and candidate["sha"] != expected_sha:
        raise CertificationGateError("P35-E certification manifest SHA does not match candidate")
    if expected_lockfile_sha256 is not None and candidate["lockfile_sha256"] != expected_lockfile_sha256:
        raise CertificationGateError("P35-E certification manifest lockfile SHA does not match candidate")
    if manifest.get("passed") is not True:
        raise CertificationGateError("P35-E certification manifest is not sealed")
    golden = manifest.get("golden_baseline")
    if not isinstance(golden, dict):
        raise CertificationGateError("P35-E golden baseline evidence is missing")
    _verify_manifest_golden_evidence(golden, REPO_ROOT)
    _validate_gate_records(manifest.get("gates"))
    service_isolation = manifest.get("service_isolation")
    if not isinstance(service_isolation, dict) or service_isolation.get("passed") is not True:
        raise CertificationGateError("P35-E service isolation evidence is not sealed")
    live = manifest.get("live")
    if not isinstance(live, dict):
        raise CertificationGateError("P35-E live evidence is missing")
    live_path_value = live.get("path")
    if not isinstance(live_path_value, str) or not live_path_value.strip():
        raise CertificationGateError("P35-E live evidence path is missing")
    nested_live = _live_identity(
        Path(live_path_value).expanduser(),
        sha=candidate["sha"],
        lockfile_sha256=candidate["lockfile_sha256"],
    )
    for key in ("schema", "sha", "lockfile_sha256", "lanes", "manifest_sha256", "run_id", "cleanup"):
        if live.get(key) != nested_live.get(key):
            raise CertificationGateError(f"P35-E nested live evidence identity is stale: {key}")
    # ``require_p53`` remains accepted for callers compiled against the
    # earlier API, but a P35-E manifest is never certifiable without P53.2.
    del require_p53
    p53_summary = manifest.get("p53_live")
    if not isinstance(p53_summary, dict):
        raise CertificationGateError("P53 live Session evidence is missing")
    else:
        p53_path_value = p53_summary.get("path")
        p53_digest = p53_summary.get("manifest_sha256")
        if not isinstance(p53_path_value, str) or not _is_hex(p53_digest, 64):
            raise CertificationGateError("P53 live Session evidence reference is malformed")
        try:
            nested_p53 = p53_certification.verify_manifest(
                Path(p53_path_value).expanduser(),
                expected_sha=candidate["sha"],
                expected_lockfile_sha256=candidate["lockfile_sha256"],
            )
        except p53_certification.P53CertificationError as exc:
            raise CertificationGateError("P53 live Session evidence is stale") from exc
        if nested_p53.get("manifest_sha256") != p53_digest or p53_summary.get("run_id") != nested_p53.get("run_id"):
            raise CertificationGateError("P53 live Session evidence identity is stale")
    claims = manifest.get("claims")
    if claims != REQUIRED_CERTIFICATION_CLAIMS:
        raise CertificationGateError("P35-E certification claims are incomplete")
    artifacts = _validate_artifact_evidence(manifest.get("artifacts"))
    if dist is None:
        raise CertificationGateError("P35-E release distribution is required for artifact verification")
    _verify_artifact_bytes(dist, artifacts)
    return manifest


def verify_command(args: argparse.Namespace) -> int:
    """Verify a manifest against the current clean candidate."""
    current_sha, current_lockfile_sha256 = _current_identity()
    if args.sha is not None and args.sha != current_sha:
        raise CertificationGateError("explicit candidate SHA does not match current candidate")
    _require_certified_provider_configuration()
    manifest_path = _safe_exact_path(
        args.manifest,
        DEFAULT_OUTPUT,
        description="P35-E certification manifest",
    )
    dist_path = _safe_exact_path(
        args.dist,
        REPO_ROOT / "dist",
        description="release distribution",
        directory=True,
    )
    verify_manifest(
        manifest_path,
        expected_sha=current_sha,
        expected_lockfile_sha256=current_lockfile_sha256,
        require_p53=True,
        dist=dist_path,
    )
    print(f"P35-E/P53 certification verified: {args.manifest}")
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
    """Run serial deterministic/package gates and consume P35-D/P53.2 evidence."""
    sha, lockfile_sha256 = _current_identity()
    _require_certified_provider_configuration()
    baseline_path = _safe_exact_path(
        args.baseline,
        DEFAULT_BASELINE,
        description="golden baseline",
    )
    live_path = _safe_exact_path(
        args.live_manifest,
        DEFAULT_LIVE_MANIFEST,
        description="live certification manifest",
    )
    p53_live_path = _safe_exact_path(
        args.p53_live_manifest,
        DEFAULT_P53_LIVE_MANIFEST,
        description="P53 live certification manifest",
    )
    services_path = _safe_exact_path(
        args.services,
        DEFAULT_SERVICES,
        description="services manifest",
    )
    release_dist = _safe_exact_path(
        args.dist,
        REPO_ROOT / "dist",
        description="release distribution",
        directory=True,
    )
    service_result = _service_isolation_from_path(services_path)
    verify_golden_baseline(baseline_path, REPO_ROOT)
    live = _live_identity(live_path, sha=sha, lockfile_sha256=lockfile_sha256)
    try:
        p53_live = p53_certification.verify_manifest(
            p53_live_path,
            expected_sha=sha,
            expected_lockfile_sha256=lockfile_sha256,
        )
    except p53_certification.P53CertificationError as exc:
        raise CertificationGateError("P53 live Session evidence is invalid") from exc
    commands = (
        ("deterministic", "deterministic", ("make", "check", "PYTEST_XDIST_MAX_WORKERS=2")),
        ("security", "security", ("make", "check-security")),
        ("release-metadata", "package", ("make", "check-release")),
        ("package-build", "package", ("make", "build-release")),
        (
            "package-install-matrix",
            "package",
            (
                "env",
                f"FLEET_RELEASE_DIST={release_dist}",
                "uv",
                "run",
                "pytest",
                "tests/unit/backend/packaging",
                "-q",
                "-n",
                "0",
            ),
        ),
        ("whitespace", "release", ("git", "diff", "--check")),
    )
    results = [_run_gate(name, lane, command, timeout=args.timeout_seconds) for name, lane, command in commands]
    failed = [result.name for result in results if not result.passed]
    if failed:
        raise CertificationGateError("failed certification gates: " + ", ".join(failed))
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
            "live-session",
            "live",
            ("receipt", str(p53_live_path)),
            0,
            str(p53_live.get("manifest_sha256") or _sha256(p53_live_path.read_bytes())),
            True,
        )
    )
    results.append(
        GateResult(
            "service-isolation",
            "cross",
            ("services.yaml", str(args.services)),
            0,
            _sha256(_read_regular_bytes(services_path, description="services manifest")),
            True,
        )
    )
    final_sha, final_lockfile_sha256 = _current_identity()
    if (final_sha, final_lockfile_sha256) != (sha, lockfile_sha256):
        raise CertificationGateError("candidate identity changed during certification gates")
    artifact_manifest_path = release_dist / "artifact-manifest.json"
    artifact_manifest = _read_json(artifact_manifest_path, description="release artifact manifest")
    if artifact_manifest.get("schema") != validate_release.ARTIFACT_MANIFEST_SCHEMA:
        raise CertificationGateError("package build emitted an invalid artifact manifest")
    artifacts = _validate_artifact_evidence(artifact_manifest.get("artifacts"))
    _verify_artifact_bytes(release_dist, artifacts)
    manifest = build_certification_manifest(
        sha=sha,
        lockfile_sha256=lockfile_sha256,
        baseline_path=baseline_path,
        repo_root=REPO_ROOT,
        live_manifest_path=live_path,
        gate_results=results,
        p53_live_manifest_path=p53_live_path,
        artifacts=artifacts,
        service_isolation=service_result,
    )
    write_manifest(args.output, manifest)
    print(f"P35-E certification sealed: {args.output} ({manifest['manifest_sha256']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    run_parser.add_argument("--live-manifest", type=Path, default=DEFAULT_LIVE_MANIFEST)
    run_parser.add_argument("--p53-live-manifest", type=Path, default=DEFAULT_P53_LIVE_MANIFEST)
    run_parser.add_argument("--services", type=Path, default=DEFAULT_SERVICES)
    run_parser.add_argument("--dist", type=Path, default=REPO_ROOT / "dist")
    run_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    run_parser.add_argument("--timeout-seconds", type=int, default=1800)
    run_parser.set_defaults(func=run_gate)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT)
    verify_parser.add_argument("--sha")
    verify_parser.add_argument("--dist", type=Path, default=REPO_ROOT / "dist")
    verify_parser.set_defaults(func=verify_command)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (CertificationGateError, OSError, subprocess.SubprocessError) as exc:
        print(f"P35-E certification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
