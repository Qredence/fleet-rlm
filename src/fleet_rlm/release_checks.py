"""Preflight and smoke checks for guarded Fleet RLM Lite releases."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def _version() -> str:
    text = Path("pyproject.toml").read_text()
    match = re.search(r'(?ms)^\[project\]\s.*?^version\s*=\s*"([^"]+)"', text)
    if match is None:
        raise SystemExit("could not read [project].version from pyproject.toml")
    return match.group(1)


def preflight(version: str, sha: str, branch: str, pipeline_sha: str) -> None:
    """Check release inputs against the checked-out source tree."""
    if not version or version != _version():
        raise SystemExit(f"requested version {version!r} does not match pyproject.toml ({_version()})")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise SystemExit("--sha must be a full 40-character source commit SHA")
    if branch != "main":
        raise SystemExit("releases are restricted to the main branch")
    actual = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    if actual.lower() != sha.lower():
        raise SystemExit(f"requested source SHA {sha} does not match checked-out HEAD ({actual})")
    if actual.lower() != pipeline_sha.lower():
        raise SystemExit(f"checked-out HEAD {actual} does not match CircleCI pipeline SHA ({pipeline_sha})")
    sys.stdout.write(f"Release preflight passed for fleet-rlm {version} at {actual}\n")


def verify_artifacts(version: str, directory: Path) -> None:
    """Require exactly one matching wheel and source archive."""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)?", version):
        raise SystemExit(f"unsupported release version: {version}")
    wheels = list(directory.glob(f"fleet_rlm-{version}-*.whl"))
    sdists = list(directory.glob(f"fleet_rlm-{version}.tar.gz"))
    contents = [path for path in directory.iterdir() if path.name != ".gitignore"]
    if len(wheels) != 1 or len(sdists) != 1 or sorted(contents) != sorted([*wheels, *sdists]):
        raise SystemExit(
            f"expected one fleet_rlm {version} wheel and one sdist in {directory}; "
            f"found {len(wheels)} wheels and {len(sdists)} sdists"
        )
    sys.stdout.write(f"Verified release artifacts: {wheels[0].name}, {sdists[0].name}\n")


def create_manifest(directory: Path) -> None:
    """Write deterministic SHA-256 entries for every release archive."""
    archives = sorted([*directory.glob("fleet_rlm-*.whl"), *directory.glob("fleet_rlm-*.tar.gz")])
    if not archives:
        raise SystemExit(f"no release archives found in {directory}")
    lines = []
    for archive in archives:
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        lines.append(f"{digest}  {archive.name}")
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def verify_manifest(directory: Path) -> None:
    """Verify every archive against the saved release checksum manifest."""
    manifest = directory / "SHA256SUMS"
    if not manifest.is_file():
        raise SystemExit(f"release checksum manifest is missing from {directory}")
    entries: dict[str, str] = {}
    for line in manifest.read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (fleet_rlm-[^/]+(?:\.whl|\.tar\.gz))", line)
        if match is None or match.group(2) in entries:
            raise SystemExit("release checksum manifest is malformed")
        entries[match.group(2)] = match.group(1)
    archives = sorted([*directory.glob("fleet_rlm-*.whl"), *directory.glob("fleet_rlm-*.tar.gz")])
    if set(entries) != {archive.name for archive in archives}:
        raise SystemExit("release checksum manifest does not match the release archives")
    for archive in archives:
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != entries[archive.name]:
            raise SystemExit(f"release checksum mismatch for {archive.name}")


def smoke(version: str, index_url: str, wheel: Path | None, requirements: Path | None) -> None:
    """Install a release candidate in isolation and exercise its public entry points."""
    with tempfile.TemporaryDirectory(prefix="fleet-release-smoke-") as temp:
        env_dir = Path(temp) / "venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        pip = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
        if requirements is not None:
            subprocess.run([*pip, "--requirement", str(requirements.resolve())], check=True)
        command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
        command.append("--no-deps")
        if wheel is None:
            command.extend(
                [
                    "--index-url",
                    index_url,
                    "--extra-index-url",
                    "https://pypi.org/simple",
                    f"fleet-rlm=={version}",
                ]
            )
        else:
            command.append(str(wheel.resolve()))
        subprocess.run(command, check=True)
        subprocess.run([str(env_dir / ("Scripts/fleet.exe" if os.name == "nt" else "bin/fleet")), "--help"], check=True)
        subprocess.run(
            [
                str(python),
                "-c",
                "import sys; from importlib.metadata import version; "
                "assert version('fleet-rlm') == sys.argv[1]; "
                "from fleet_rlm.api.app import create_app; "
                "app=create_app(initialize_db=False, configure_observability=False); "
                "schema=app.openapi(); assert schema['info']['title']=='Fleet-RLM Lite API'; "
                "assert '/health' in schema['paths']",
                version,
            ],
            check=True,
        )
    sys.stdout.write(f"Release package smoke test passed for fleet-rlm {version}\n")


def main() -> None:
    """Run the selected release preflight, artifact, or package smoke check."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("preflight")
    check.add_argument("--version", required=True)
    check.add_argument("--sha", required=True)
    check.add_argument("--branch", required=True)
    check.add_argument("--pipeline-sha", required=True)
    artifacts = commands.add_parser("verify-artifacts")
    artifacts.add_argument("--version", required=True)
    artifacts.add_argument("--dist", type=Path, required=True)
    manifest = commands.add_parser("create-manifest")
    manifest.add_argument("--dist", type=Path, required=True)
    verify = commands.add_parser("verify-manifest")
    verify.add_argument("--dist", type=Path, required=True)
    smoke_parser = commands.add_parser("smoke")
    smoke_parser.add_argument("--version", required=True)
    smoke_parser.add_argument("--index-url", default="https://test.pypi.org/simple")
    smoke_parser.add_argument("--wheel", type=Path)
    smoke_parser.add_argument("--requirements", type=Path)
    args = parser.parse_args()
    if args.command == "preflight":
        preflight(args.version, args.sha, args.branch, args.pipeline_sha)
    elif args.command == "verify-artifacts":
        verify_artifacts(args.version, args.dist)
    elif args.command == "create-manifest":
        create_manifest(args.dist)
    elif args.command == "verify-manifest":
        verify_manifest(args.dist)
    else:
        smoke(args.version, args.index_url, args.wheel, args.requirements)


if __name__ == "__main__":
    main()
