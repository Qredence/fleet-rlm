"""Preflight and smoke checks for guarded Fleet RLM Lite releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
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


def verify_registry_wheel(version: str, wheel: Path, manifest: Path) -> None:
    """Require a registry-downloaded wheel to match the built release manifest."""
    if not wheel.is_file() or not wheel.name.startswith(f"fleet_rlm-{version}-") or not wheel.name.endswith(".whl"):
        raise SystemExit(f"PyPI returned an unexpected wheel for fleet-rlm {version}: {wheel.name}")
    verify_manifest(manifest.parent)
    entries = {
        match.group(2): match.group(1)
        for line in manifest.read_text().splitlines()
        if (match := re.fullmatch(r"([0-9a-f]{64})  (fleet_rlm-[^/]+(?:\.whl|\.tar\.gz))", line))
    }
    expected = entries.get(wheel.name)
    if expected is None:
        raise SystemExit(f"PyPI wheel {wheel.name} is not present in the release checksum manifest")
    actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"PyPI wheel checksum does not match the release manifest: {wheel.name}")


def validate_webhook_context(token: str | None) -> None:
    """Refuse to publish unless the validation webhook credential is configured."""
    if not token or not token.strip():
        raise SystemExit("CIRCLECI_VALIDATION_WEBHOOK_TOKEN is required for PyPI release validation")


def _run_with_deadline(command: list[str], deadline: float | None, *, env: dict[str, str] | None = None) -> None:
    timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
    subprocess.run(command, check=True, timeout=timeout, env=env)


def download_pypi_wheel(version: str, destination: Path, deadline: float | None = None) -> Path:
    """Download only from the public PyPI index, ignoring pip index overrides."""
    environment = os.environ.copy()
    environment["PIP_CONFIG_FILE"] = os.devnull
    for key in (
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "UV_INDEX_URL",
        "UV_EXTRA_INDEX_URL",
    ):
        environment.pop(key, None)
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--no-deps",
        "--only-binary=:all:",
        "--index-url",
        "https://pypi.org/simple",
        "--dest",
        str(destination),
        f"fleet-rlm=={version}",
    ]
    for attempt in range(5):
        try:
            _run_with_deadline(command, deadline, env=environment)
            break
        except subprocess.CalledProcessError:
            if attempt == 4:
                raise
            delay = 5 * (2**attempt)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= delay:
                    raise
                delay = min(delay, remaining)
            time.sleep(delay)
    wheels = list(destination.glob("fleet_rlm-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one fleet-rlm {version} wheel from PyPI, found {len(wheels)}")
    return wheels[0]


def send_pypi_canary_failure(version: str, token: str) -> None:
    """Send a matching Smart Deployments failure signal for the PyPI canary."""
    payload = {
        "provider": "custom",
        "component_name": "fleet-rlm",
        "env": "pypi",
        "signal_name": "pypi_canary",
        "version": version,
        "alert_transition": "triggered",
    }
    request = urllib.request.Request(
        "https://circleci.com/api/v3/deploy/hooks/d0fc62ae-1f4a-4037-8ccb-d357adfdfda6/validate",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"CircleCI rejected the PyPI canary failure signal (HTTP {response.status})")


def pypi_canary(version: str, manifest: Path, requirements: Path, token: str) -> None:
    """Smoke-test the public PyPI artifact and signal failed health to CircleCI."""
    validate_webhook_context(token)
    try:
        deadline = time.monotonic() + 15 * 60
        with tempfile.TemporaryDirectory(prefix="fleet-pypi-canary-") as temp:
            wheel = download_pypi_wheel(version, Path(temp), deadline)
            verify_registry_wheel(version, wheel, manifest)
            smoke(version, "https://pypi.org/simple", wheel, requirements, deadline=deadline)
    except (Exception, SystemExit) as error:
        try:
            send_pypi_canary_failure(version, token)
        except Exception as signal_error:
            raise SystemExit(
                "PyPI canary failed and CircleCI could not receive its validation signal; "
                "the release is blocked. Inspect both errors in the job log."
            ) from signal_error
        sys.stdout.write(
            f"PyPI canary failed for fleet-rlm {version}; sent the pypi_canary validation signal "
            f"to CircleCI ({type(error).__name__}); blocking the GitHub release.\n"
        )
        raise SystemExit(f"PyPI canary failed for fleet-rlm {version}; release blocked") from None


def smoke(
    version: str,
    index_url: str,
    wheel: Path | None,
    requirements: Path | None,
    *,
    deadline: float | None = None,
) -> None:
    """Install a release candidate in isolation and exercise its public entry points."""
    with tempfile.TemporaryDirectory(prefix="fleet-release-smoke-") as temp:
        env_dir = Path(temp) / "venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        pip = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
        if requirements is not None:
            _run_with_deadline([*pip, "--requirement", str(requirements.resolve())], deadline)
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
        _run_with_deadline(command, deadline)
        _run_with_deadline(
            [str(env_dir / ("Scripts/fleet.exe" if os.name == "nt" else "bin/fleet")), "--help"], deadline
        )
        _run_with_deadline(
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
            deadline,
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
    canary = commands.add_parser("pypi-canary")
    canary.add_argument("--version", required=True)
    canary.add_argument("--manifest", type=Path, required=True)
    canary.add_argument("--requirements", type=Path, required=True)
    canary.add_argument("--webhook-token", default=os.environ.get("CIRCLECI_VALIDATION_WEBHOOK_TOKEN"))
    webhook = commands.add_parser("validate-webhook-context")
    webhook.add_argument("--webhook-token", default=os.environ.get("CIRCLECI_VALIDATION_WEBHOOK_TOKEN"))
    args = parser.parse_args()
    if args.command == "preflight":
        preflight(args.version, args.sha, args.branch, args.pipeline_sha)
    elif args.command == "verify-artifacts":
        verify_artifacts(args.version, args.dist)
    elif args.command == "create-manifest":
        create_manifest(args.dist)
    elif args.command == "verify-manifest":
        verify_manifest(args.dist)
    elif args.command == "pypi-canary":
        pypi_canary(args.version, args.manifest, args.requirements, args.webhook_token or "")
    elif args.command == "validate-webhook-context":
        validate_webhook_context(args.webhook_token)
    else:
        smoke(args.version, args.index_url, args.wheel, args.requirements)


if __name__ == "__main__":
    main()
