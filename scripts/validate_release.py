#!/usr/bin/env python3
"""Backend-only release metadata, hygiene, and wheel validation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "fleet_rlm" / "__init__.py"
OPENAPI = ROOT / "openapi.yaml"
CHANGELOG = ROOT / "CHANGELOG.md"
REQUIRED_WHEEL_FILES = {
    "fleet_rlm/__init__.py",
    "fleet_rlm/app.py",
    "fleet_rlm/main.py",
    "fleet_rlm/py.typed",
    "fleet_rlm/daytona/snapshot-requirements.txt",
    "fleet_rlm/api/dependencies.py",
    "fleet_rlm/chat/turn_coordinator.py",
    "fleet_rlm/daytona/_cleanup.py",
    "fleet_rlm/daytona/_lease.py",
    "fleet_rlm/daytona/broker.py",
    "fleet_rlm/daytona/runtime.py",
    "fleet_rlm/daytona/workspace_agent/client.py",
    "fleet_rlm/daytona/workspace_agent/protocol.py",
    "fleet_rlm/daytona/workspace_agent/runtime.py",
    "fleet_rlm/daytona/workspace_gateway.py",
    "fleet_rlm/persistence/models.py",
    "fleet_rlm/rlm/runtime.py",
    "fleet_rlm/rlm/recursion.py",
    "fleet_rlm/skills/bundled/data-analysis/SKILL.md",
    "fleet_rlm/skills/bundled/dspy-rlm/SKILL.md",
    "fleet_rlm/skills/bundled/dspy-rlm/references/rlm-contract.md",
    "fleet_rlm/skills/bundled/long-context/SKILL.md",
    "fleet_rlm/skills/bundled/long-context/references/chunking-strategies.md",
    "fleet_rlm/skills/bundled/long-context/scripts/rank_chunks.py",
    "fleet_rlm/skills/bundled/long-context/scripts/semantic_chunk.py",
    "fleet_rlm/skills/bundled/workspace-files/SKILL.md",
    "fleet_rlm/skills/bundled/workspace-files/references/filesystem-contract.md",
    "fleet_rlm/skills/bundled/report-builder/SKILL.md",
}
ARTIFACT_MANIFEST_SCHEMA = "fleet.release-artifact-manifest/v1"
_VERSION_RE = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)$")
_FORBIDDEN_PORTS = (8000, 5001, 5432, 8010)
_ALLOWED_VALIDATOR_PORTS = frozenset({8020, 8021, 5010, 5011, 9010, 9011})


class ReleaseValidationError(ValueError):
    """Raised when release evidence or metadata is not safe to consume."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def artifact_manifest_digest(manifest: dict[str, Any]) -> str:
    """Return the digest of an artifact manifest without its self-reference."""
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    return _sha256_bytes(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON using a flush/fsync/replace sequence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{path.stat().st_ino if path.exists() else 'new'}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with temporary.open("rb") as handle:
            import os

            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def build_artifact_manifest(dist_dir: Path, version: str | None = None) -> dict[str, Any]:
    """Describe exactly one wheel and one sdist by content hash."""
    wheels = sorted(dist_dir.glob("fleet_rlm-*.whl"))
    sdists = sorted(dist_dir.glob("fleet_rlm-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseValidationError(
            f"expected exactly one wheel and one sdist in {dist_dir}, found {len(wheels)} and {len(sdists)}"
        )
    artifacts = []
    for path in (*wheels, *sdists):
        artifact_version = _artifact_version_from_name(path.name)
        if version is not None and artifact_version != version:
            raise ReleaseValidationError(f"artifact {path.name} version does not match {version}")
        artifacts.append(
            {
                "filename": path.name,
                "kind": "wheel" if path.suffix == ".whl" else "sdist",
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
                "version": artifact_version,
            }
        )
    artifact_version = _artifact_version_from_name(wheels[0].name)
    manifest: dict[str, Any] = {
        "schema": ARTIFACT_MANIFEST_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "version": artifact_version,
        "artifacts": sorted(artifacts, key=lambda item: str(item["filename"])),
    }
    manifest["manifest_sha256"] = artifact_manifest_digest(manifest)
    return manifest


def _artifact_version_from_name(filename: str) -> str:
    match = re.fullmatch(r"fleet_rlm-(?P<version>.+?)(?:-py3-none-any\.whl|\.tar\.gz)", filename)
    if match is None:
        raise ReleaseValidationError(f"unrecognized Fleet artifact filename: {filename}")
    return match.group("version")


def verify_artifact_manifest(path: Path, dist_dir: Path) -> dict[str, Any]:
    """Verify a content-addressed artifact manifest against downloaded bytes."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError("artifact manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != ARTIFACT_MANIFEST_SCHEMA:
        raise ReleaseValidationError("artifact manifest schema is invalid")
    if manifest.get("manifest_sha256") != artifact_manifest_digest(manifest):
        raise ReleaseValidationError("artifact manifest self-hash is invalid")
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("filename"), str) for item in raw_artifacts
    ):
        raise ReleaseValidationError("artifact manifest entries are invalid")
    expected = {str(item["filename"]): item for item in raw_artifacts}
    if len(expected) != 2 or len(expected) != len(raw_artifacts):
        raise ReleaseValidationError("artifact manifest must contain one wheel and one sdist")
    actual = build_artifact_manifest(dist_dir, str(manifest.get("version", "")))
    actual_by_name = {str(item["filename"]): item for item in actual["artifacts"]}
    if expected != actual_by_name:
        raise ReleaseValidationError("artifact hash or metadata does not match the certified manifest")
    return manifest


def validate_requested_version(requested: str, project_path: Path = PYPROJECT) -> str:
    """Normalize one optional leading ``v`` and compare it before release work."""
    if not isinstance(requested, str) or not requested.strip():
        raise ReleaseValidationError("release version must be a non-empty semantic version")
    match = _VERSION_RE.fullmatch(requested.strip())
    if match is None:
        raise ReleaseValidationError("release version must be a valid X.Y.Z version with an optional leading v")
    with project_path.open("rb") as handle:
        project_version = str(tomllib.load(handle)["project"]["version"])
    normalized = match.group("version")
    if normalized != project_version:
        raise ReleaseValidationError(
            f"Version mismatch: requested {normalized!r} does not match project {project_version!r}"
        )
    return normalized


def validate_service_isolation(services_path: Path) -> dict[str, Any]:
    """Validate mission services never target user-owned ports."""
    try:
        payload = yaml.safe_load(services_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ReleaseValidationError("services manifest is unreadable") from exc
    services = payload.get("services", {}) if isinstance(payload, dict) else {}
    if not isinstance(services, dict) or not services:
        raise ReleaseValidationError("services manifest has no services")
    observed: set[int] = set()
    forbidden: set[int] = set()
    malformed: list[str] = []
    for name, service in services.items():
        if not isinstance(service, dict):
            malformed.append(str(name))
            continue
        port = service.get("port")
        if isinstance(port, int) and not isinstance(port, bool):
            observed.add(port)
            if port in _FORBIDDEN_PORTS:
                forbidden.add(port)
            for key in ("start", "stop", "healthcheck"):
                command = str(service.get(key, ""))
                if str(port) not in command:
                    malformed.append(f"{name}.{key}")
                for forbidden_port in _FORBIDDEN_PORTS:
                    if re.search(rf"(?<!\d){forbidden_port}(?!\d)", command):
                        forbidden.add(forbidden_port)
        else:
            malformed.append(f"{name}.port")
    unexpected = sorted(observed - _ALLOWED_VALIDATOR_PORTS)
    forbidden.update(unexpected)
    return {
        "passed": not forbidden and not malformed,
        "approved_ports": sorted(observed & _ALLOWED_VALIDATOR_PORTS),
        "forbidden_ports": sorted(forbidden),
        "malformed_services": sorted(malformed),
        "serial_live_lanes": True,
        "xdist_max_workers": 2,
    }


def _project_version() -> str:
    with PYPROJECT.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def _package_version() -> str:
    spec = importlib.util.spec_from_file_location("fleet_rlm_release_probe", INIT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load fleet_rlm.__init__")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.__version__)


def metadata(_args: argparse.Namespace) -> int:
    project_version = _project_version()
    package_version = _package_version()
    schema = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    openapi_version = str(schema.get("info", {}).get("version", ""))
    if len({project_version, package_version, openapi_version}) != 1:
        print(
            f"ERROR: version drift project={project_version} package={package_version} openapi={openapi_version}",
            file=sys.stderr,
        )
        return 1
    changelog = CHANGELOG.read_text(encoding="utf-8")
    if not re.search(rf"^## (?:\[)?{re.escape(project_version)}(?:\])? - ", changelog, re.MULTILINE):
        print(f"ERROR: CHANGELOG has no {project_version} release heading", file=sys.stderr)
        return 1
    print(f"OK: backend release metadata is aligned at {project_version}")
    return 0


def hygiene(_args: argparse.Namespace) -> int:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    violations = [
        path
        for path in tracked
        if path
        and (
            (re.search(r"(^|/)\.env(?:\..+)?$", path) and not path.endswith(".env.example"))
            or path.endswith((".tmp", ".swp"))
            or "__pycache__" in path
        )
    ]
    if violations:
        print("ERROR: forbidden tracked files:\n" + "\n".join(violations), file=sys.stderr)
        return 1
    print("OK: tracked-file hygiene passed")
    return 0


def wheel(args: argparse.Namespace) -> int:
    wheels = sorted(args.dist_dir.glob("fleet_rlm-*.whl"))
    if not wheels:
        print(f"ERROR: no wheel in {args.dist_dir}", file=sys.stderr)
        return 1
    wheel_path = wheels[-1]
    with zipfile.ZipFile(wheel_path) as archive:
        files = {name for name in archive.namelist() if not name.endswith("/")}
    missing = sorted(REQUIRED_WHEEL_FILES - files)
    forbidden = sorted(
        name
        for name in files
        if name.startswith(("frontend/", "fleet_rlm/ui/", "fleet_rlm/skills/skills/")) or name.endswith(".pdf")
    )
    if missing or forbidden:
        if missing:
            print("ERROR: wheel missing:\n" + "\n".join(missing), file=sys.stderr)
        if forbidden:
            print("ERROR: forbidden wheel payload:\n" + "\n".join(forbidden), file=sys.stderr)
        return 1
    print(f"OK: canonical backend wheel validated: {wheel_path}")
    return 0


def artifacts(args: argparse.Namespace) -> int:
    try:
        manifest = build_artifact_manifest(args.dist_dir, args.version)
        output = args.output or args.dist_dir / "artifact-manifest.json"
        write_json_atomically(output, manifest)
    except (OSError, ReleaseValidationError) as exc:
        print(f"ERROR: artifact manifest validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"OK: content-addressed artifact manifest written: {output}")
    return 0


def verify_artifacts(args: argparse.Namespace) -> int:
    try:
        verify_artifact_manifest(args.manifest, args.dist_dir)
    except (OSError, ReleaseValidationError) as exc:
        print(f"ERROR: artifact identity validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"OK: downloaded artifacts match {args.manifest}")
    return 0


def version(args: argparse.Namespace) -> int:
    try:
        normalized = validate_requested_version(args.requested, args.project)
    except (OSError, ReleaseValidationError, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(normalized)
    return 0


def service_isolation(args: argparse.Namespace) -> int:
    try:
        result = validate_service_isolation(args.services)
    except (OSError, ReleaseValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("metadata").set_defaults(func=metadata)
    commands.add_parser("hygiene").set_defaults(func=hygiene)
    wheel_parser = commands.add_parser("wheel")
    wheel_parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    wheel_parser.set_defaults(func=wheel)
    artifacts_parser = commands.add_parser("artifacts")
    artifacts_parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    artifacts_parser.add_argument("--version")
    artifacts_parser.add_argument("--output", type=Path)
    artifacts_parser.set_defaults(func=artifacts)
    verify_artifacts_parser = commands.add_parser("verify-artifacts")
    verify_artifacts_parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    verify_artifacts_parser.add_argument("--manifest", type=Path, required=True)
    verify_artifacts_parser.set_defaults(func=verify_artifacts)
    version_parser = commands.add_parser("version")
    version_parser.add_argument("--requested", required=True)
    version_parser.add_argument("--project", type=Path, default=PYPROJECT)
    version_parser.set_defaults(func=version)
    isolation_parser = commands.add_parser("service-isolation")
    isolation_parser.add_argument("--services", type=Path, default=ROOT.parent / ".factory" / "missing-services.yaml")
    isolation_parser.set_defaults(func=service_isolation)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
